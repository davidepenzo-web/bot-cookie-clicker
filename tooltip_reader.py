"""
Cookie Clicker Bot - Tooltip Reader
=====================================
Legge i dati in tempo reale dai tooltip del negozio.

Coordinate calibrate su screenshot reale 1920x1080:
  - Tooltip X: 775-1145 (zona centrale, sempre fissa)
  - Tooltip Y: dinamica, calcolata come offset dal centro della riga
    - Offset top: -130px dal centro riga
    - Offset bot:  +69px dal centro riga

Il tooltip contiene:
  - Prezzo (es. "40.744 million") in alto a destra
  - "each X produces N cookies per second" (CPS singolo)
  - "N X producing M cookies per second" (CPS totale)
"""

import re
import time
import logging
import numpy as np
from PIL import Image, ImageGrab, ImageEnhance

log = logging.getLogger(__name__)

try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    TESSERACT_AVAILABLE = False

# Coordinate X del tooltip (relative alla finestra, base 1920)
TOOLTIP_X_LEFT  = 775
TOOLTIP_X_RIGHT = 1145

# Offset verticale del tooltip rispetto al centro della riga (base 1080)
TOOLTIP_OFFSET_TOP = -130
TOOLTIP_OFFSET_BOT =  +69

# Attesa dopo hover prima di fare screenshot (ms)
TOOLTIP_WAIT_MS = 200

# Suffissi numerici inglesi
NUMBER_SUFFIXES = {
    "quintillion": 1_000_000_000_000_000_000,
    "quadrillion": 1_000_000_000_000_000,
    "trillion":    1_000_000_000_000,
    "billion":     1_000_000_000,
    "million":     1_000_000,
}


class TooltipReader:
    def __init__(self, window, clicker):
        self.window  = window
        self.clicker = clicker

    # ── API pubblica ────────────────────────────────────────────────────────

    def read_building_data(self, building: dict) -> dict:
        """
        Legge prezzo e CPS reali di una struttura hovering il mouse.
        Restituisce dict con: name, price, cps_single, cps_total, raw_text.
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

        # Calcola posizione hover (icona della struttura, a sinistra del bottone)
        rect = self.window.rect
        sx   = rect.width  / 1920
        sy   = rect.height / 1080

        # Hover a sinistra del negozio, sulla riga corretta
        _, click_y = click_pos
        hover_x = rect.left + int(1165 * sx)  # bordo sinistro del negozio
        hover_y = click_y

        self._move_mouse(hover_x, hover_y)
        time.sleep(TOOLTIP_WAIT_MS / 1000)

        # Screenshot zona tooltip
        tooltip_img = self._capture_tooltip(click_y)
        if tooltip_img is None:
            self.clicker.move_away()
            return result

        # Debug: salva tooltip per verifica
        try:
            from pathlib import Path
            Path("logs").mkdir(exist_ok=True)
            tooltip_img.save(f"logs/tooltip_{building.get('name','?').replace(' ','_')}.png")
        except Exception:
            pass

        # OCR
        text = self._ocr(tooltip_img)
        result["raw_text"] = text
        log.debug(f"[TOOLTIP] {building['name']} raw:\n{text}")

        # Parsing
        result["price"]      = self._parse_price(text)
        result["cps_single"] = self._parse_cps_single(text)
        result["cps_total"]  = self._parse_cps_total(text)

        self.clicker.move_away()
        return result

    def read_all_visible(self, buildings: list) -> list:
        """Legge i dati di tutte le strutture visibili."""
        results = []
        for b in buildings:
            if not b.get("visible", True):
                continue
            data = self.read_building_data(b)
            merged = {**b, **data}
            results.append(merged)
            log.info(
                f"[TOOLTIP] {b['name']:<20} "
                f"price={data['price']:>15,.0f}  "
                f"cps_single={data['cps_single']:>12,.1f}"
            )
        return results

    # ── Mouse ────────────────────────────────────────────────────────────────

    def _move_mouse(self, x: int, y: int):
        import pyautogui
        try:
            pyautogui.moveTo(x, y, duration=0.05)
        except Exception as e:
            log.debug(f"[TOOLTIP] moveTo error: {e}")

    # ── Screenshot ───────────────────────────────────────────────────────────

    def _capture_tooltip(self, row_center_y: int) -> Image.Image | None:
        """
        Cattura la zona del tooltip calcolando le coordinate dinamiche
        in base al centro verticale della riga hovrata.
        """
        rect = self.window.rect
        sx   = rect.width  / 1920
        sy   = rect.height / 1080

        # Coordinate assolute X (fisse)
        abs_x_left  = rect.left + int(TOOLTIP_X_LEFT  * sx)
        abs_x_right = rect.left + int(TOOLTIP_X_RIGHT * sx)

        # Coordinate assolute Y (dinamiche in base alla riga)
        abs_y_top = row_center_y + int(TOOLTIP_OFFSET_TOP * sy)
        abs_y_bot = row_center_y + int(TOOLTIP_OFFSET_BOT * sy)

        # Clamp ai bordi dello schermo
        abs_y_top = max(rect.top,    abs_y_top)
        abs_y_bot = min(rect.bottom, abs_y_bot)

        if abs_y_bot - abs_y_top < 30:
            log.debug("[TOOLTIP] Zona tooltip troppo piccola")
            return None

        try:
            img = ImageGrab.grab(bbox=(abs_x_left, abs_y_top, abs_x_right, abs_y_bot))
            log.debug(f"[TOOLTIP] Screenshot {img.size} @ ({abs_x_left},{abs_y_top})-({abs_x_right},{abs_y_bot})")
            return img
        except Exception as e:
            log.warning(f"[TOOLTIP] Screenshot fallito: {e}")
            return None

    # ── OCR ──────────────────────────────────────────────────────────────────

    def _ocr(self, img: Image.Image) -> str:
        if not TESSERACT_AVAILABLE or pytesseract is None:
            return ""

        w, h = img.size
        # Upscale x2
        img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        img = img.convert("L")

        # Il tooltip ha testo chiaro su sfondo scuro: inverti
        arr = np.array(img)
        if arr.mean() < 128:
            arr = 255 - arr
            img = Image.fromarray(arr)

        img = ImageEnhance.Contrast(img).enhance(2.5)
        lut = [0] * 90 + [255] * 166
        img = img.point(lut)

        try:
            text = pytesseract.image_to_string(img, config="--psm 6 -l eng")
            return text.strip()
        except Exception as e:
            log.debug(f"[TOOLTIP] OCR error: {e}")
            return ""

    # ── Parsing ──────────────────────────────────────────────────────────────

    @staticmethod
    def parse_number(text: str) -> float:
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
        """Estrae il prezzo: prima occorrenza di numero + suffisso."""
        pattern = r"([\d,]+\.?\d*)\s*(million|billion|trillion|quadrillion|quintillion)"
        matches = re.findall(pattern, text.lower())
        if matches:
            num, suf = matches[0]
            return self.parse_number(f"{num} {suf}")
        match = re.search(r"([\d,]+)", text)
        if match:
            return self.parse_number(match.group(1))
        return 0.0

    def _parse_cps_single(self, text: str) -> float:
        """Estrae CPS singolo: 'each X produces N cookies per second'"""
        pattern = r"each\s+\w+\s+produces?\s+([\d,]+\.?\d*)\s*(?:(million|billion|trillion|quadrillion|quintillion)\s+)?cookies?\s+per\s+second"
        match = re.search(pattern, text.lower())
        if match:
            num = match.group(1).replace(",", "")
            suf = match.group(2) or ""
            return self.parse_number(f"{num} {suf}".strip())
        return 0.0

    def _parse_cps_total(self, text: str) -> float:
        """Estrae CPS totale: 'N X producing M cookies per second'"""
        pattern = r"producing\s+([\d,]+\.?\d*)\s*(?:(million|billion|trillion|quadrillion|quintillion)\s+)?cookies?\s+per\s+second"
        match = re.search(pattern, text.lower())
        if match:
            num = match.group(1).replace(",", "")
            suf = match.group(2) or ""
            return self.parse_number(f"{num} {suf}".strip())
        return 0.0