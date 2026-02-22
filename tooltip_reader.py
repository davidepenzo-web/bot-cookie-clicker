"""
Cookie Clicker Bot - Tooltip Reader
=====================================
Legge i dati in tempo reale dai tooltip del negozio.

Quando il mouse passa sopra una struttura, il gioco mostra un tooltip
nella zona centrale dello schermo con:
  - Nome struttura e prezzo corrente (in alto)
  - "each X produces N cookies per second" (CPS singolo)
  - "N X producing M cookies per second (P% of total CpS)" (CPS totale)

Strategia di lettura:
  1. Muovi il mouse sulla struttura target
  2. Aspetta che il tooltip appaia (150-200ms)
  3. Screenshot della zona tooltip (x: 780-1145 della finestra)
  4. Rileva automaticamente i bordi verticali del tooltip
  5. OCR per estrarre prezzo e CPS singolo
  6. Sposta il mouse via immediatamente dopo

Layout tooltip rilevato dagli screenshot reali:
  - Sfondo scuro semi-trasparente
  - Riga titolo: nome + prezzo (testo arancione/bianco)
  - Riga "owned: N"
  - Riga corsivo (descrizione)
  - Bullet CPS singolo (testo bianco con numero in grassetto)
  - Bullet CPS totale (testo bianco con numero in grassetto)
"""

import re
import time
import logging
import numpy as np
from PIL import Image, ImageGrab, ImageEnhance

log = logging.getLogger(__name__)

try:
    import pytesseract as pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None  # type: ignore
    TESSERACT_AVAILABLE = False

# Zona X del tooltip nella finestra (coordinate relative, base 1920x1080)
# Il tooltip appare sempre in questa banda orizzontale
TOOLTIP_X_LEFT  = 780
TOOLTIP_X_RIGHT = 1145

# Quanti ms aspettare dopo aver spostato il mouse prima di fare lo screenshot
TOOLTIP_WAIT_MS = 180

# Suffissi numerici inglesi
NUMBER_SUFFIXES = {
    "million":     1_000_000,
    "billion":     1_000_000_000,
    "trillion":    1_000_000_000_000,
    "quadrillion": 1_000_000_000_000_000,
    "quintillion": 1_000_000_000_000_000_000,
}


class TooltipReader:
    """
    Legge i tooltip del negozio spostando il mouse su ogni struttura.

    Args:
        window:  istanza di WindowManager
        clicker: istanza di Clicker (per muovere il mouse)
    """

    def __init__(self, window, clicker):
        self.window  = window
        self.clicker = clicker

    # ── API pubblica ────────────────────────────────────────────────────────

    def read_building_data(self, building: dict) -> dict:
        """
        Legge prezzo e CPS reali di una struttura hovering il mouse.

        Args:
            building: dict con almeno 'name' e 'click_pos'

        Returns:
            dict con: name, price, cps_single, cps_total, raw_text
            I valori sono 0.0 se non riusciti a leggere.
        """
        result = {
            "name":       building.get("name", "?"),
            "price":      0.0,
            "cps_single": 0.0,
            "cps_total":  0.0,
            "raw_text":   "",
        }

        click_pos = building.get("click_pos")
        if not click_pos:
            return result

        # Sposta il mouse sulla struttura (sulla sua icona, parte sinistra)
        hover_x, hover_y = self._get_hover_pos(building)
        self._move_mouse(hover_x, hover_y)

        # Aspetta che il tooltip appaia
        time.sleep(TOOLTIP_WAIT_MS / 1000)

        # Screenshot e rilevamento tooltip
        tooltip_img = self._capture_tooltip()
        if tooltip_img is None:
            self.clicker.move_away()
            return result

        # OCR
        text = self._ocr_tooltip(tooltip_img)
        result["raw_text"] = text
        log.debug(f"[TOOLTIP] {building['name']} OCR:\n{text}")

        # Parsing
        result["price"]      = self._parse_price(text)
        result["cps_single"] = self._parse_cps_single(text)
        result["cps_total"]  = self._parse_cps_total(text)

        # Sposta il mouse via subito
        self.clicker.move_away()

        return result

    def read_all_visible(self, buildings: list) -> list:
        """
        Legge i dati di tutte le strutture visibili nel negozio.

        Args:
            buildings: lista di dict da GameState.buildings

        Returns:
            Lista di dict arricchiti con price, cps_single, cps_total
        """
        results = []
        for b in buildings:
            if not b.get("visible", True):
                continue
            data = self.read_building_data(b)
            # Merge con i dati originali (affordable, click_pos, ecc.)
            merged = {**b, **data}
            results.append(merged)
            log.debug(
                f"[TOOLTIP] {b['name']:<20} "
                f"price={data['price']:>15,.0f}  "
                f"cps_single={data['cps_single']:>12,.1f}  "
                f"cps_total={data['cps_total']:>12,.1f}"
            )

        return results

    # ── Posizionamento mouse ────────────────────────────────────────────────

    def _get_hover_pos(self, building: dict) -> tuple:
        """
        Calcola la posizione di hover per il tooltip.
        Usa la metà sinistra della riga (icona) per evitare
        di triggerare altri elementi.
        """
        click_x, click_y = building["click_pos"]
        rect = self.window.rect

        # Sposta leggermente a sinistra del centro del bottone
        # così il mouse è sulla icona e non sul testo
        hover_x = rect.left + int((1160 + 40) * rect.width / 1920)
        hover_y = click_y

        return (hover_x, hover_y)

    def _move_mouse(self, x: int, y: int):
        """Muove il mouse senza cliccare."""
        import pyautogui
        try:
            pyautogui.moveTo(x, y, duration=0.05)
        except Exception as e:
            log.debug(f"[TOOLTIP] Errore moveTo: {e}")

    # ── Screenshot tooltip ──────────────────────────────────────────────────

    def _capture_tooltip(self) -> Image.Image | None:
        """
        Cattura lo screenshot della zona dove appare il tooltip
        e trova automaticamente i bordi verticali del box.
        """
        rect = self.window.rect
        sx   = rect.width  / 1920
        sy   = rect.height / 1080

        # Coordinate assolute della zona tooltip
        abs_left  = rect.left + int(TOOLTIP_X_LEFT  * sx)
        abs_right = rect.left + int(TOOLTIP_X_RIGHT * sx)

        try:
            full = ImageGrab.grab(bbox=(
                abs_left,
                rect.top,
                abs_right,
                rect.bottom,
            ))
        except Exception as e:
            log.warning(f"[TOOLTIP] Screenshot fallito: {e}")
            return None

        arr = np.array(full)
        h   = arr.shape[0]

        # Trova i bordi del tooltip cercando zone scure (sfondo < 100)
        row_brightness = arr.mean(axis=(1, 2))
        dark_rows = np.where(row_brightness < 100)[0]

        if len(dark_rows) < 20:
            log.debug("[TOOLTIP] Tooltip non trovato (nessuna zona scura)")
            return None

        y_top = int(dark_rows[0])
        y_bot = int(dark_rows[-1])

        # Aggiungi un piccolo margine
        y_top = max(0, y_top - 5)
        y_bot = min(h, y_bot + 5)

        tooltip = full.crop((0, y_top, full.width, y_bot))
        log.debug(f"[TOOLTIP] Tooltip ritagliato: y={y_top}-{y_bot} ({y_bot-y_top}px)")

        return tooltip

    # ── OCR ────────────────────────────────────────────────────────────────

    def _ocr_tooltip(self, img: Image.Image) -> str:
        """
        Esegue OCR sul tooltip.
        Il testo è chiaro su sfondo scuro — invertiamo prima.
        """
        if not TESSERACT_AVAILABLE or pytesseract is None:
            return ""

        w, h = img.size
        # Upscale x2
        img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)

        # Converti in scala di grigi
        img = img.convert("L")

        # Il testo è chiaro su sfondo scuro: inverti per Tesseract
        arr = np.array(img)
        if arr.mean() < 128:
            arr = 255 - arr
            img = Image.fromarray(arr)

        # Contrasto
        img = ImageEnhance.Contrast(img).enhance(2.0)

        # Soglia
        lut = [0] * 100 + [255] * 156
        img = img.point(lut)

        try:
            text = pytesseract.image_to_string(
                img,
                config="--psm 6 -l eng"
            )
            return text.strip()
        except Exception as e:
            log.debug(f"[TOOLTIP] OCR error: {e}")
            return ""

    # ── Parsing ─────────────────────────────────────────────────────────────

    @staticmethod
    def parse_number(text: str) -> float:
        """
        Converte una stringa numerica in float.
        Gestisce suffissi: million, billion, trillion, ecc.
        Formato inglese: virgola = migliaia (rimossa), punto = decimale.
        """
        if not text:
            return 0.0

        text = text.lower().strip().replace(",", "")

        multiplier = 1.0
        for suffix, mult in NUMBER_SUFFIXES.items():
            if suffix in text:
                multiplier = mult
                text = text.replace(suffix, "").strip()
                break

        try:
            return float(re.sub(r"[^\d.]", "", text)) * multiplier
        except (ValueError, TypeError):
            return 0.0

    def _parse_price(self, text: str) -> float:
        """
        Estrae il prezzo dal testo del tooltip.
        Il prezzo appare nella prima riga dopo il nome, formato:
        "40.744 million" o "5.1 billion"
        """
        # Cerca il pattern del prezzo: numero seguito da suffisso
        pattern = r"([\d,]+\.?\d*)\s*(million|billion|trillion|quadrillion|quintillion)"
        matches = re.findall(pattern, text.lower())
        if matches:
            num_str, suffix = matches[0]
            return self.parse_number(f"{num_str} {suffix}")

        # Fallback: numero semplice
        match = re.search(r"([\d,]+)", text)
        if match:
            return self.parse_number(match.group(1))

        return 0.0

    def _parse_cps_single(self, text: str) -> float:
        """
        Estrae il CPS singolo dal testo del tooltip.
        Formato: "each cursor produces 2,029 cookies per second"
        """
        pattern = r"each\s+\w+\s+produces?\s+([\d,]+\.?\d*)\s*(?:(million|billion|trillion|quadrillion|quintillion)\s+)?cookies?\s+per\s+second"
        match = re.search(pattern, text.lower())
        if match:
            num  = match.group(1).replace(",", "")
            suf  = match.group(2) or ""
            return self.parse_number(f"{num} {suf}".strip())
        return 0.0

    def _parse_cps_total(self, text: str) -> float:
        """
        Estrae il CPS totale dal testo del tooltip.
        Formato: "106 cursors producing 215,130 cookies per second"
        """
        pattern = r"producing\s+([\d,]+\.?\d*)\s*(?:(million|billion|trillion|quadrillion|quintillion)\s+)?cookies?\s+per\s+second"
        match = re.search(pattern, text.lower())
        if match:
            num = match.group(1).replace(",", "")
            suf = match.group(2) or ""
            return self.parse_number(f"{num} {suf}".strip())
        return 0.0