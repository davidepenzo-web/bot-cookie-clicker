"""
Cookie Clicker Bot - Main Orchestrator
======================================
Punto di ingresso principale del bot.
Coordina tutti i moduli: click, screen reading, strategia, golden cookie.

Struttura del progetto:
    main.py           <- sei qui
    window_manager.py <- aggancia la finestra di Steam
    screen_reader.py  <- screenshot e OCR
    clicker.py        <- gestisce i click
    game_state.py     <- stato del gioco (cookie, CPS, strutture)
    strategy.py       <- logica decisionale e ottimizzazione acquisti
"""

import time
import threading
import logging
import sys
from pathlib import Path
from datetime import datetime

# Crea la cartella logs/ se non esiste
Path("logs").mkdir(exist_ok=True)

# ── Configurazione logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path("logs") / f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8"
        )
    ]
)
log = logging.getLogger(__name__)

# ── Costanti di configurazione ──────────────────────────────────────────────
CONFIG = {
    # Quante volte al secondo cliccare il cookie principale
    "cookie_cps": 20,

    # Ogni quanti secondi controllare se comprare strutture/upgrade
    "buy_check_interval": 2.0,

    # Ogni quanti secondi fare uno screenshot per cercare il Golden Cookie
    "golden_cookie_scan_interval": 0.5,

    # Ogni quanti secondi aggiornare lo stato del gioco (leggere cookie, CPS)
    "state_update_interval": 5.0,

    # Soglia di confidenza per il template matching del Golden Cookie (0-1)
    "golden_cookie_confidence": 0.75,

    # Se True il bot stampa debug visivo degli screenshot
    "debug_screenshots": False,
}


def cookie_clicker_loop(clicker, stop_event):
    """
    Thread dedicato: clicca il cookie principale il più veloce possibile.
    La frequenza è regolata da CONFIG['cookie_cps'].
    """
    interval = 1.0 / CONFIG["cookie_cps"]
    log.info(f"[CLICKER] Avviato — {CONFIG['cookie_cps']} click/sec")

    while not stop_event.is_set():
        clicker.click_main_cookie()
        time.sleep(interval)


def golden_cookie_loop(screen_reader, clicker, stop_event):
    """
    Thread dedicato: fa uno screenshot periodico e cerca il Golden Cookie
    con template matching OpenCV. Se trovato, clicca immediatamente.
    """
    interval = CONFIG["golden_cookie_scan_interval"]
    log.info(f"[GOLDEN] Scanner avviato — ogni {interval}s")

    while not stop_event.is_set():
        position = screen_reader.find_golden_cookie()
        if position:
            log.info(f"[GOLDEN] ✨ Golden Cookie trovato a {position}! Click!")
            clicker.click_at(position)
        time.sleep(interval)


def buy_loop(game_state, strategy, clicker, stop_event):
    """
    Thread dedicato: aggiorna lo stato del gioco e decide cosa comprare
    basandosi sulla strategia di payoff time ottimale.
    """
    interval = CONFIG["buy_check_interval"]
    log.info(f"[BUY] Loop acquisti avviato — ogni {interval}s")

    while not stop_event.is_set():
        # 1. Aggiorna lo stato leggendo la schermata
        game_state.update()

        # 2. Chiedi alla strategia cosa comprare
        decision = strategy.get_best_purchase(game_state)

        if decision:
            log.info(f"[BUY] Acquisto: {decision['name']} "
                     f"(costo: {decision['cost']:,.0f} | "
                     f"payoff: {decision['payoff_time']:.1f}s)")
            clicker.click_buy(decision)
        else:
            log.debug("[BUY] Nessun acquisto conveniente al momento.")

        time.sleep(interval)


def main():
    log.info("=" * 50)
    log.info("  🍪 Cookie Clicker Bot — Avvio")
    log.info("=" * 50)

    # ── Import moduli (verranno creati nei prossimi step) ───────────────────
    # Li importiamo qui dentro così se mancano il messaggio di errore è chiaro
    try:
        from window_manager import WindowManager
        from screen_reader import ScreenReader
        from clicker import Clicker
        from game_state import GameState
        from strategy import Strategy
    except ImportError as e:
        log.error(f"Modulo mancante: {e}")
        log.error("Assicurati di aver creato tutti i moduli del progetto.")
        sys.exit(1)

    # ── Inizializzazione ────────────────────────────────────────────────────
    log.info("[INIT] Cerco la finestra di Cookie Clicker su Steam...")
    window = WindowManager()

    if not window.find_and_focus():
        log.error("[INIT] Finestra di Cookie Clicker non trovata. "
                  "Assicurati che il gioco sia aperto e visibile.")
        sys.exit(1)

    log.info(f"[INIT] Finestra trovata: {window.rect}")

    # Passa il rect della finestra a tutti i moduli che ne hanno bisogno
    screen_reader = ScreenReader(window, config=CONFIG)
    clicker       = Clicker(window)
    game_state    = GameState(screen_reader)
    strategy      = Strategy()

    # Conto alla rovescia: dai tempo all'utente di passare al gioco
    countdown = 5
    print("")
    for i in range(countdown, 0, -1):
        print(f"  Il bot partirà tra {i} secondi... (passa al gioco adesso!)", end="\r")
        time.sleep(1)
    print("  Partenza!                                              ")
    print("")

    # Prima lettura dello stato prima di avviare i thread
    log.info("[INIT] Prima lettura dello stato del gioco...")
    game_state.update()
    log.info(f"[INIT] Stato iniziale: {game_state}")

    # ── Avvio thread ────────────────────────────────────────────────────────
    stop_event = threading.Event()

    threads = [
        threading.Thread(
            target=cookie_clicker_loop,
            args=(clicker, stop_event),
            name="CookieClicker",
            daemon=True
        ),
        threading.Thread(
            target=golden_cookie_loop,
            args=(screen_reader, clicker, stop_event),
            name="GoldenCookie",
            daemon=True
        ),
        threading.Thread(
            target=buy_loop,
            args=(game_state, strategy, clicker, stop_event),
            name="BuyLoop",
            daemon=True
        ),
    ]

    for t in threads:
        t.start()
        log.info(f"[THREAD] '{t.name}' avviato.")

    log.info("")
    log.info("✅ Bot attivo! Premi CTRL+C per fermarlo.")
    log.info("")

    # ── Loop principale: stampa statistiche ogni 30s ─────────────────────
    try:
        while True:
            time.sleep(30)
            log.info(f"[STATS] 🍪 Cookie: {game_state.cookies:,.0f} | "
                     f"CPS: {game_state.cps:,.1f} | "
                     f"Strutture: {game_state.building_count}")
    except KeyboardInterrupt:
        log.info("")
        log.info("[STOP] CTRL+C ricevuto — arresto del bot...")
        stop_event.set()

        for t in threads:
            t.join(timeout=2)

        log.info("[STOP] Bot fermato. Arrivederci!")


if __name__ == "__main__":
    main()