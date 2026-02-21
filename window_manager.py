"""
Cookie Clicker Bot - Window Manager
=====================================
Si occupa di trovare, agganciare e mantenere il riferimento
alla finestra di Cookie Clicker su Steam.

Fornisce anche metodi di utilità per convertire coordinate
relative (rispetto alla finestra) in coordinate assolute
dello schermo, così gli altri moduli non devono preoccuparsi
di dove si trova la finestra.
"""

import time
import logging
import pygetwindow as gw

log = logging.getLogger(__name__)

# Titoli possibili della finestra di Cookie Clicker su Steam
# (può variare leggermente in base alla versione/OS)
WINDOW_TITLES = [
    "Cookie Clicker",
    "cookie clicker",
]


class WindowRect:
    """
    Rappresenta il rettangolo della finestra con proprietà comode.
    Tutti i valori sono in pixel assoluti dello schermo.
    """

    def __init__(self, left, top, width, height):
        self.left   = left
        self.top    = top
        self.width  = width
        self.height = height

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.height

    @property
    def center(self):
        return (self.left + self.width // 2, self.top + self.height // 2)

    def abs(self, rel_x, rel_y):
        """
        Converte coordinate RELATIVE alla finestra in coordinate
        ASSOLUTE dello schermo.

        Esempio:
            rect.abs(0.5, 0.5)  ->  centro della finestra
            rect.abs(100, 200)  ->  pixel (100, 200) dalla top-left della finestra
        """
        # Se i valori sono float tra 0 e 1, li trattiamo come percentuali
        if isinstance(rel_x, float) and 0.0 <= rel_x <= 1.0:
            rel_x = int(rel_x * self.width)
        if isinstance(rel_y, float) and 0.0 <= rel_y <= 1.0:
            rel_y = int(rel_y * self.height)

        return (self.left + int(rel_x), self.top + int(rel_y))

    def contains(self, abs_x, abs_y):
        """Controlla se una coordinata assoluta è dentro la finestra."""
        return (self.left <= abs_x <= self.right and
                self.top  <= abs_y <= self.bottom)

    def __repr__(self):
        return (f"WindowRect(left={self.left}, top={self.top}, "
                f"width={self.width}, height={self.height})")


class WindowManager:
    """
    Trova e gestisce la finestra di Cookie Clicker su Steam.

    Uso:
        wm = WindowManager()
        if wm.find_and_focus():
            print(wm.rect)          # WindowRect(...)
            abs_pos = wm.to_abs(100, 200)
    """

    def __init__(self):
        self._window = None   # riferimento pygetwindow
        self.rect    = None   # WindowRect aggiornato

    # ── Ricerca finestra ────────────────────────────────────────────────────

    def find_and_focus(self, retries=5, delay=2.0) -> bool:
        """
        Cerca la finestra di Cookie Clicker, la porta in primo piano
        e aggiorna self.rect.

        Args:
            retries: quanti tentativi fare prima di arrendersi
            delay:   secondi di attesa tra un tentativo e l'altro

        Returns:
            True se la finestra è stata trovata, False altrimenti
        """
        for attempt in range(1, retries + 1):
            log.debug(f"[WINDOW] Tentativo {attempt}/{retries}...")
            window = self._search_window()

            if window:
                self._window = window
                self._focus()
                self._update_rect()
                log.info(f"[WINDOW] Finestra trovata: {self.rect}")
                return True

            if attempt < retries:
                log.warning(f"[WINDOW] Non trovata. Riprovo tra {delay}s...")
                time.sleep(delay)

        log.error("[WINDOW] Cookie Clicker non trovato dopo tutti i tentativi.")
        return False

    def _search_window(self):
        """Cerca tra le finestre aperte quella di Cookie Clicker."""
        all_windows = gw.getAllWindows()

        for title in WINDOW_TITLES:
            for w in all_windows:
                if title.lower() in w.title.lower():
                    log.debug(f"[WINDOW] Match: '{w.title}'")
                    return w

        # Fallback: stampa tutte le finestre aperte per debug
        if log.isEnabledFor(logging.DEBUG):
            titles = [w.title for w in all_windows if w.title.strip()]
            log.debug(f"[WINDOW] Finestre aperte: {titles}")

        return None

    # ── Focus e aggiornamento ───────────────────────────────────────────────

    def _focus(self):
        """Porta la finestra in primo piano."""
        if self._window is None:
            return
        try:
            self._window.activate()
            time.sleep(0.3)  # breve pausa per lasciarla in primo piano
        except Exception as e:
            log.warning(f"[WINDOW] Impossibile portare in primo piano: {e}")

    def _update_rect(self):
        """Aggiorna self.rect con la posizione attuale della finestra."""
        if self._window is None:
            log.error("[WINDOW] _update_rect chiamato ma _window è None.")
            return
        try:
            w = self._window
            self.rect = WindowRect(
                left   = w.left,
                top    = w.top,
                width  = w.width,
                height = w.height,
            )
        except Exception as e:
            log.error(f"[WINDOW] Errore aggiornamento rect: {e}")

    def refresh(self) -> bool:
        """
        Rilegge la posizione della finestra (utile se l'utente l'ha spostata).
        Chiamalo se noti click fuori posto.

        Returns:
            True se la finestra esiste ancora, False se è stata chiusa.
        """
        if self._window is None:
            return self.find_and_focus()

        try:
            self._update_rect()
            return True
        except Exception:
            log.warning("[WINDOW] Finestra persa, cerco di ritrovarla...")
            return self.find_and_focus()

    # ── Utilità coordinate ──────────────────────────────────────────────────

    def to_abs(self, rel_x, rel_y) -> tuple:
        """
        Shortcut per convertire coordinate relative in assolute.
        Delega a self.rect.abs().
        """
        if self.rect is None:
            raise RuntimeError("WindowRect non inizializzato. Chiama find_and_focus() prima.")
        return self.rect.abs(rel_x, rel_y)

    def is_alive(self) -> bool:
        """Controlla se la finestra esiste ancora (il gioco non è stato chiuso)."""
        try:
            return self._window is not None and self._window.title != ""
        except Exception:
            return False

    def __repr__(self):
        return f"WindowManager(window={self._window}, rect={self.rect})"