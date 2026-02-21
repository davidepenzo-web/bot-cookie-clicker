"""
Cookie Clicker Bot - Clicker
==============================
Gestisce tutti i click del bot tramite pyautogui.

Caratteristiche:
  - Click sul cookie principale (veloce, ripetuto)
  - Click su strutture e upgrade nel negozio
  - Click su Golden Cookie (priorità massima)
  - Movimenti del mouse leggermente randomici per sembrare più umano
  - Pausa di sicurezza: se il mouse viene spostato in alto a sinistra
    (failsafe di pyautogui) il bot si ferma automaticamente
"""

import time
import random
import logging
import pyautogui

log = logging.getLogger(__name__)

# ── Configurazione pyautogui ────────────────────────────────────────────────
# IMPORTANTE: se muovi il mouse in alto a sinistra dello schermo il bot si ferma
pyautogui.FAILSAFE = True
# Pausa minima tra ogni azione pyautogui (secondi). 0 = massima velocità
pyautogui.PAUSE    = 0.0


class Clicker:
    """
    Gestisce tutti i click del bot.

    Args:
        window: istanza di WindowManager
    """

    def __init__(self, window):
        self.window = window

        # Posizione assoluta del centro del cookie principale
        # Calcolata una volta sola e aggiornata se la finestra si sposta
        self._cookie_pos = None
        self._update_cookie_pos()

        # Timestamp dell'ultimo click su ogni struttura (per evitare doppi click)
        self._last_buy_time = {}

        # Cooldown minimo tra due acquisti della stessa struttura (secondi)
        self.buy_cooldown = 0.5

    # ── Cookie principale ───────────────────────────────────────────────────

    def _update_cookie_pos(self):
        """
        Calcola la posizione assoluta del cookie principale.
        Il cookie è centrato nel pannello sinistro, circa a x=215, y=430
        su una finestra 1456x800.
        """
        rect = self.window.rect
        if rect is None:
            return

        # Coordinate relative scalate alle dimensioni reali della finestra
        rel_x = int(215 * rect.width  / 1456)
        rel_y = int(430 * rect.height / 800)
        self._cookie_pos = rect.abs(rel_x, rel_y)

    def click_main_cookie(self):
        """
        Clicca il cookie principale con un piccolo offset casuale
        per sembrare più umano e coprire tutta la superficie del cookie.
        """
        if self._cookie_pos is None:
            self._update_cookie_pos()
            return

        # Offset casuale entro ±30px
        jitter_x = random.randint(-30, 30)
        jitter_y = random.randint(-30, 30)

        x = self._cookie_pos[0] + jitter_x
        y = self._cookie_pos[1] + jitter_y

        try:
            pyautogui.click(x, y)
        except pyautogui.FailSafeException:
            raise   # Rilancia per fermare il thread
        except Exception as e:
            log.warning(f"[CLICK] Errore click cookie: {e}")

    # ── Golden Cookie ───────────────────────────────────────────────────────

    def click_at(self, position: tuple):
        """
        Clicca a una posizione assoluta specifica.
        Usato principalmente per il Golden Cookie.

        Args:
            position: (abs_x, abs_y)
        """
        x, y = position
        try:
            # Movimento rapido verso il golden cookie
            pyautogui.moveTo(x, y, duration=0.05)
            pyautogui.click()
            log.debug(f"[CLICK] click_at ({x}, {y})")
        except pyautogui.FailSafeException:
            raise
        except Exception as e:
            log.warning(f"[CLICK] Errore click_at {position}: {e}")

    # ── Acquisti negozio ────────────────────────────────────────────────────

    def click_buy(self, decision: dict):
        """
        Clicca per acquistare una struttura o un upgrade nel negozio.

        Args:
            decision: dict con almeno 'click_pos' e 'name'
                      (formato restituito da strategy.get_best_purchase)
        """
        name      = decision.get("name", "?")
        click_pos = decision.get("click_pos")

        if not click_pos:
            log.warning(f"[CLICK] click_buy: nessuna click_pos per '{name}'")
            return

        # Cooldown: evita di cliccare troppo spesso sulla stessa struttura
        now = time.time()
        last = self._last_buy_time.get(name, 0)
        if now - last < self.buy_cooldown:
            return

        x, y = click_pos

        # Piccolo jitter per sembrare umano
        x += random.randint(-5, 5)
        y += random.randint(-3, 3)

        try:
            pyautogui.moveTo(x, y, duration=0.08)
            pyautogui.click()
            self._last_buy_time[name] = time.time()
            log.debug(f"[CLICK] Acquistato '{name}' @ ({x}, {y})")
        except pyautogui.FailSafeException:
            raise
        except Exception as e:
            log.warning(f"[CLICK] Errore click_buy '{name}': {e}")

    def click_upgrade(self, upgrade: dict):
        """
        Clicca su un upgrade disponibile nel negozio.

        Args:
            upgrade: dict con 'click_pos' e 'index'
                     (formato restituito da ScreenReader.read_upgrades)
        """
        click_pos = upgrade.get("click_pos")
        if not click_pos:
            return

        name = f"upgrade_{upgrade.get('index', '?')}"
        now  = time.time()
        last = self._last_buy_time.get(name, 0)
        if now - last < self.buy_cooldown:
            return

        x, y = click_pos
        try:
            pyautogui.moveTo(x, y, duration=0.08)
            pyautogui.click()
            self._last_buy_time[name] = time.time()
            log.debug(f"[CLICK] Upgrade {upgrade.get('index')} @ ({x}, {y})")
        except pyautogui.FailSafeException:
            raise
        except Exception as e:
            log.warning(f"[CLICK] Errore click_upgrade: {e}")

    # ── Utilità ─────────────────────────────────────────────────────────────

    def refresh_cookie_pos(self):
        """
        Ricalcola la posizione del cookie principale.
        Chiamalo dopo window.refresh() se la finestra è stata spostata.
        """
        self._update_cookie_pos()
        log.debug(f"[CLICK] Cookie pos aggiornata: {self._cookie_pos}")

    def move_away(self):
        """
        Sposta il mouse in una posizione neutra (angolo in basso a destra
        della finestra del gioco) così non copre elementi importanti
        durante le letture OCR.
        """
        rect = self.window.rect
        if rect is None:
            return
        try:
            # Angolo in basso a destra, appena dentro la finestra
            x = rect.right  - 20
            y = rect.bottom - 20
            pyautogui.moveTo(x, y, duration=0.1)
        except Exception:
            pass

    def __repr__(self):
        return f"Clicker(cookie_pos={self._cookie_pos})"