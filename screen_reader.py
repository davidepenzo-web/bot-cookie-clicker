"""
Cookie Clicker Bot - Screen Reader
====================================
Gestisce screenshot, OCR e template matching.

Responsabilità:
  - Catturare screenshot dell'intera finestra o di zone specifiche
  - Leggere il numero di cookie correnti tramite OCR (pytesseract)
  - Leggere il CPS (cookie al secondo)
  - Leggere nomi, prezzi e quantità delle strutture nel negozio
  - Trovare il Golden Cookie tramite template matching OpenCV
  - Trovare gli upgrade disponibili (fascia alta del negozio)

Layout rilevato dallo screenshot (coordinate RELATIVE in pixel,
origin = top-left della finestra di gioco):
  ┌─────────────────────────────────────────────────────────────┐
  │  [0, 0]                                          [1456, 0]  │
  │                                                             │
  │  Pannello SX (cookie)     Centro (minipanels)  Negozio DX  │
  │  x: 0-430                 x: 430-1160          x: 1160+    │
  │                                                             │
  │  Cookie counter: y 100-145                                  │
  │  CPS label:      y 155-175                                  │
  │  Cookie (click): centro ~(215, 430)                         │
  │                                                             │
  │  Shop buildings: da y=185, ogni riga ~60px                  │
  └─────────────────────────────────────────────────────────────┘
"""

import re
import time
import logging
import numpy as np
from pathlib import Path
from PIL import Image, ImageGrab, ImageFilter, ImageEnhance

log = logging.getLogger(__name__)

# ── Tentativo import OpenCV (opzionale ma raccomandato) ─────────────────────
try:
    import cv2 as cv2
    OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    OPENCV_AVAILABLE = False
    log.warning("[SCREEN] OpenCV non disponibile — Golden Cookie detection disabilitata.")

# ── Tentativo import pytesseract (opzionale) ────────────────────────────────
try:
    import pytesseract as pytesseract
    # Percorso Tesseract su Windows — modifica se l'hai installato altrove
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    TESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None  # type: ignore
    TESSERACT_AVAILABLE = False
    log.warning("[SCREEN] pytesseract non disponibile — OCR disabilitato.")

# ── Moltiplicatori per i suffissi numerici italiani ─────────────────────────
NUMBER_SUFFIXES = {
    "million":   1_000_000,
    "billion":   1_000_000_000,
    "trillion":  1_000_000_000_000,
    "quadrillion": 1_000_000_000_000_000,
    "milione":   1_000_000,
    "miliardo":  1_000_000_000,
}

# ── Zone di cattura (coordinate relative alla finestra, in pixel) ───────────
# Basate sullo screenshot 1456x800. Se la finestra ha dimensioni diverse
# verranno scalate automaticamente da _crop_region().
REGIONS = {
    # Testo grande con il totale cookie
    "cookie_count":  (85,  95,  395, 150),   # (left, top, right, bottom)
    # Testo piccolo "al secondo: X"
    "cps":           (85,  150, 395, 178),
    # Intero pannello negozio
    "shop":          (1160, 60, 1456, 780),
    # Fascia upgrade (icone in alto nel negozio)
    "upgrades":      (1160, 90, 1456, 145),
    # Zona dove può apparire il golden cookie (tutta la finestra escluso negozio)
    "golden_area":   (0,   60, 1160, 780),
}

# Altezza approssimativa di ogni riga struttura nel negozio
BUILDING_ROW_HEIGHT = 60
# Y di partenza della prima struttura (Cursore)
BUILDING_START_Y    = 185
# X del pannello negozio
SHOP_LEFT_X         = 1160

# Nomi strutture nell'ordine in cui appaiono nel negozio (versione italiana)
BUILDING_NAMES = [
    "Cursore",
    "Nonna",
    "Fattoria",
    "Miniera",
    "Fabbrica",
    "Banca",
    "Tempio",
    "Torre del mago",
    "Nave spaziale",
    "Alchimia",
    "Portale",
    "Macchina del tempo",
    "Antimateria",
    "Prisma",
    "Cervello",
    "Idiosfera",
    "Frattale",
]


class ScreenReader:
    """
    Cattura e interpreta lo schermo del gioco.

    Args:
        window:  istanza di WindowManager
        config:  dict di configurazione (da main.py)
    """

    def __init__(self, window, config=None):
        self.window = window
        self.config = config or {}
        self._golden_template = None
        self._load_golden_template()

    # ── Template Golden Cookie ──────────────────────────────────────────────

    def _load_golden_template(self):
        """
        Carica il template del Golden Cookie per il matching OpenCV.
        Il file 'golden_cookie_template.png' deve trovarsi nella stessa
        cartella dello script. Se non esiste, la detection verrà saltata
        con un avviso.

        Come creare il template:
          1. Aspetta che appaia un Golden Cookie
          2. Fai uno screenshot e ritaglia solo il cookie dorato
          3. Salvalo come 'golden_cookie_template.png' nella cartella del bot
        """
        if not OPENCV_AVAILABLE or cv2 is None:
            return

        template_path = Path(__file__).parent / "golden_cookie_template.png"
        if template_path.exists():
            img = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
            if img is not None:
                self._golden_template = img
                log.info(f"[SCREEN] Template Golden Cookie caricato: {template_path}")
            else:
                log.warning("[SCREEN] Template trovato ma non leggibile.")
        else:
            log.warning(
                "[SCREEN] 'golden_cookie_template.png' non trovato. "
                "Golden Cookie detection disabilitata finché non lo crei. "
                "Vedi docstring _load_golden_template() per le istruzioni."
            )

    def reload_golden_template(self):
        """Ricarica il template a runtime (utile se lo crei mentre il bot gira)."""
        self._golden_template = None
        self._load_golden_template()

    # ── Screenshot ──────────────────────────────────────────────────────────

    def screenshot(self, region_name: str | None = None) -> Image.Image:
        """
        Cattura uno screenshot dell'intera finestra o di una regione specifica.

        Args:
            region_name: chiave in REGIONS, oppure None per l'intera finestra

        Returns:
            Immagine PIL
        """
        rect = self.window.rect
        if rect is None:
            raise RuntimeError("WindowRect non inizializzato.")

        if region_name:
            left, top, right, bottom = self._scale_region(region_name)
            bbox = (
                rect.left + left,
                rect.top  + top,
                rect.left + right,
                rect.top  + bottom,
            )
        else:
            bbox = (rect.left, rect.top, rect.right, rect.bottom)

        img = ImageGrab.grab(bbox=bbox)

        if self.config.get("debug_screenshots"):
            ts = int(time.time() * 1000)
            name = region_name or "full"
            img.save(f"debug_{name}_{ts}.png")

        return img

    def _scale_region(self, region_name: str) -> tuple:
        """
        Scala le coordinate di REGIONS (definite per 1456x800) alle
        dimensioni reali della finestra.
        """
        base_w, base_h = 1456, 800
        rect = self.window.rect
        sx = rect.width  / base_w
        sy = rect.height / base_h

        l, t, r, b = REGIONS[region_name]
        return (int(l * sx), int(t * sy), int(r * sx), int(b * sy))

    # ── OCR ─────────────────────────────────────────────────────────────────

    def _ocr(self, img: Image.Image, config_str: str = "") -> str:
        """
        Esegue OCR su un'immagine PIL. Preprocessa l'immagine per migliorare
        l'accuratezza (scala, contrasto, conversione in bianco/nero).
        """
        if not TESSERACT_AVAILABLE or pytesseract is None:
            return ""

        # Upscale x2 per migliorare l'OCR su testi piccoli
        w, h = img.size
        img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)

        # Contrasto aumentato
        img = ImageEnhance.Contrast(img).enhance(2.5)

        # Converti in scala di grigi
        img = img.convert("L")

        # Soglia: rende il testo nero su bianco
        lut = [0] * 128 + [255] * 128
        img = img.point(lut)

        text = pytesseract.image_to_string(
            img,
            config=f"--psm 7 {config_str}".strip()
        )
        return text.strip()

    # ── Parsing numeri ──────────────────────────────────────────────────────

    @staticmethod
    def parse_number(text: str) -> float:
        """
        Converte una stringa numerica del gioco in float.

        Gestisce:
          - "55.430 million biscotti"  -> 55_430_000_000  (punto = migliaia in IT)
          - "374,961"                  -> 374.961          (virgola = decimale)
          - "5.1 billion"              -> 5_100_000_000
          - "85"                       -> 85.0

        Nota: il gioco italiano usa il punto come separatore delle migliaia
        e la virgola come separatore decimale, MA nei suffissi come "million"
        usa il formato anglosassone.
        """
        if not text:
            return 0.0

        text = text.lower().strip()

        # Rimuovi parole non numeriche tranne suffissi
        # es. "al secondo: 374,961" -> "374,961"
        text = re.sub(r"(al secondo|biscotti|cookie|:|\n)", "", text).strip()

        # Cerca suffisso
        multiplier = 1.0
        for suffix, mult in NUMBER_SUFFIXES.items():
            if suffix in text:
                multiplier = mult
                text = text.replace(suffix, "").strip()
                break

        # Normalizza il numero:
        # Se c'è sia punto che virgola, il punto è migliaia e virgola è decimale
        # Es: "55.430,12" -> 55430.12
        if "." in text and "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            # Solo virgola: è il decimale (es. "374,961" -> "374.961")
            text = text.replace(",", ".")
        elif "." in text:
            # Solo punto: potrebbe essere migliaia (es. "55.430") o decimale
            # Se ci sono esattamente 3 cifre dopo il punto -> migliaia
            parts = text.split(".")
            if len(parts) == 2 and len(parts[1]) == 3:
                text = text.replace(".", "")
            # Altrimenti lo lasciamo com'è (es. "5.1")

        try:
            return float(re.sub(r"[^\d.]", "", text)) * multiplier
        except ValueError:
            log.debug(f"[SCREEN] parse_number fallito su: '{text}'")
            return 0.0

    # ── Lettura stato gioco ─────────────────────────────────────────────────

    def read_cookie_count(self) -> float:
        """Legge il numero totale di cookie tramite OCR."""
        img  = self.screenshot("cookie_count")
        text = self._ocr(img)
        log.debug(f"[OCR] cookie_count raw: '{text}'")
        return self.parse_number(text)

    def read_cps(self) -> float:
        """Legge il CPS (cookie al secondo) tramite OCR."""
        img  = self.screenshot("cps")
        text = self._ocr(img)
        log.debug(f"[OCR] cps raw: '{text}'")
        return self.parse_number(text)

    def read_shop(self) -> list[dict]:
        """
        Legge il pannello negozio e restituisce una lista di strutture con:
          - name:       nome della struttura
          - cost:       prezzo attuale (float)
          - count:      quantità posseduta (int)
          - affordable: True se abbiamo abbastanza cookie
          - row_index:  indice della riga (0 = Cursore, 1 = Nonna, ...)
          - click_pos:  coordinata assoluta per cliccarla (tuple x, y)

        Nota: l'OCR sul negozio può essere impreciso per prezzi molto grandi.
        In futuro si può migliorare con color detection (testo verde = acquistabile).
        """
        buildings = []

        rect  = self.window.rect
        sx    = rect.width  / 1456
        sy    = rect.height / 800

        for i, name in enumerate(BUILDING_NAMES):
            # Calcola la zona di questa riga nel negozio
            row_top    = int((BUILDING_START_Y + i * BUILDING_ROW_HEIGHT) * sy)
            row_bottom = int(row_top + BUILDING_ROW_HEIGHT * sy)
            row_left   = int(SHOP_LEFT_X * sx)

            # Crop della riga intera
            bbox = (
                rect.left + row_left,
                rect.top  + row_top,
                rect.right,
                rect.top  + row_bottom,
            )
            img = ImageGrab.grab(bbox=bbox)

            # OCR sulla riga
            text = self._ocr(img)
            log.debug(f"[OCR] shop row {i} '{name}' raw: '{text}'")

            # Il prezzo è la prima cosa numerica che troviamo
            cost = self.parse_number(text)

            # Il numero posseduto è solitamente a destra (testo piccolo)
            # Per ora lo estraiamo con regex semplice
            count_match = re.search(r"\b(\d+)\s*$", text)
            count = int(count_match.group(1)) if count_match else 0

            # Posizione di click: centro della riga nel negozio
            click_x = rect.left + row_left + int((rect.right - rect.left - row_left) // 2)
            click_y = rect.top  + row_top  + int(BUILDING_ROW_HEIGHT * sy // 2)

            buildings.append({
                "name":       name,
                "cost":       cost,
                "count":      count,
                "affordable": False,   # verrà aggiornato da GameState
                "row_index":  i,
                "click_pos":  (click_x, click_y),
            })

        return buildings

    # ── Golden Cookie ───────────────────────────────────────────────────────

    def find_golden_cookie(self) -> tuple | None:
        """
        Cerca il Golden Cookie nello screenshot usando template matching OpenCV.

        Returns:
            (abs_x, abs_y) del centro del golden cookie se trovato,
            None altrimenti.
        """
        if not OPENCV_AVAILABLE or cv2 is None or self._golden_template is None:
            return self._find_golden_cookie_by_color()

        # Screenshot della zona dove può apparire il golden cookie
        img_pil = self.screenshot("golden_area")
        img_np  = np.array(img_pil)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

        template    = self._golden_template
        result      = cv2.matchTemplate(img_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        confidence = self.config.get("golden_cookie_confidence", 0.75)
        if max_val >= confidence:
            # max_loc è top-left del match; aggiungiamo metà template per il centro
            th, tw = template.shape[:2]
            center_rel_x = max_loc[0] + tw // 2
            center_rel_y = max_loc[1] + th // 2

            # Converti in coordinate assolute
            region_l, region_t, _, _ = self._scale_region("golden_area")
            rect = self.window.rect
            abs_x = rect.left + region_l + center_rel_x
            abs_y = rect.top  + region_t + center_rel_y

            log.debug(f"[SCREEN] Golden Cookie match: {max_val:.2f} @ ({abs_x}, {abs_y})")
            return (abs_x, abs_y)

        return None

    def _find_golden_cookie_by_color(self) -> tuple | None:
        """
        Fallback: cerca il Golden Cookie per colore (tono dorato).
        Meno preciso del template matching ma non richiede il template.

        Il Golden Cookie ha un colore giallo-arancio distintivo
        in un'area normalmente marrone/blu.
        """
        try:
            img_pil = self.screenshot("golden_area")
            img_np  = np.array(img_pil)

            if not OPENCV_AVAILABLE or cv2 is None:
                return None

            img_hsv = cv2.cvtColor(img_np, cv2.COLOR_RGB2HSV)

            # Range HSV per il giallo-dorato del Golden Cookie
            lower_gold = np.array([15,  150, 150])
            upper_gold = np.array([35,  255, 255])

            mask    = cv2.inRange(img_hsv, lower_gold, upper_gold)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filtra per dimensione: il golden cookie è abbastanza grande
            min_area = 1000
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area >= min_area:
                    M  = cv2.moments(cnt)
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])

                    region_l, region_t, _, _ = self._scale_region("golden_area")
                    rect = self.window.rect
                    abs_x = rect.left + region_l + cx
                    abs_y = rect.top  + region_t + cy

                    log.debug(f"[SCREEN] Golden Cookie (color) @ ({abs_x}, {abs_y}), area={area:.0f}")
                    return (abs_x, abs_y)

        except Exception as e:
            log.debug(f"[SCREEN] Errore color detection: {e}")

        return None

    # ── Upgrade disponibili ─────────────────────────────────────────────────

    def read_upgrades(self) -> list[dict]:
        """
        Legge gli upgrade disponibili (icone in cima al negozio).
        Restituisce una lista con la posizione di click di ogni upgrade visibile.

        Per ora usa coordinate fisse: gli upgrade sono icone da ~45x45px
        disposte orizzontalmente partendo da x=1168.
        """
        rect  = self.window.rect
        sx    = rect.width  / 1456

        upgrades = []
        upgrade_y = int(112 * rect.height / 800)  # Y centrale degli upgrade

        # Le icone upgrade iniziano a x≈1168 e sono larghe ~50px
        for i in range(8):   # max 8 upgrade visibili contemporaneamente
            rel_x     = int((1168 + i * 50) * sx)
            abs_x     = rect.left + rel_x
            abs_y     = rect.top  + upgrade_y

            # Verifica se c'è qualcosa (non trasparente) in questa posizione
            # Per semplicità includiamo tutte le 8 posizioni e lasciamo
            # che il clicker gestisca i click a vuoto
            upgrades.append({
                "index":     i,
                "click_pos": (abs_x, abs_y),
            })

        return upgrades