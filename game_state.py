"""
Cookie Clicker Bot - Game State
=================================
Tiene traccia dello stato del gioco aggiornandosi periodicamente
tramite ScreenReader.

Fornisce a Strategy tutti i dati necessari per prendere decisioni:
  - Cookie correnti
  - CPS (cookie al secondo)
  - Lista strutture con costi e quantità possedute
  - Upgrade disponibili
  - Storico CPS (per rilevare progressi)
"""

import time
import logging
from collections import deque

log = logging.getLogger(__name__)


class GameState:
    """
    Rappresenta lo stato attuale del gioco.

    Viene aggiornato periodicamente dal buy_loop in main.py
    chiamando game_state.update().

    Args:
        screen_reader: istanza di ScreenReader
    """

    def __init__(self, screen_reader):
        self.screen_reader = screen_reader

        # ── Valori principali ───────────────────────────────────────────────
        self.cookies       = 0.0    # cookie attualmente posseduti
        self.cps           = 0.0    # cookie al secondo
        self.total_cookies = 0.0    # cookie totali prodotti (se leggibile)

        # ── Strutture nel negozio ───────────────────────────────────────────
        # Lista di dict: { name, cost, count, affordable, row_index, click_pos }
        self.buildings: list[dict] = []

        # ── Upgrade disponibili ─────────────────────────────────────────────
        # Lista di dict: { index, click_pos }
        self.upgrades: list[dict] = []

        # ── Storico CPS (ultimi 10 campionamenti) ───────────────────────────
        # Utile per calcolare il tasso di crescita e rilevare stalli
        self._cps_history = deque(maxlen=10)

        # ── Metadati ────────────────────────────────────────────────────────
        self.last_update     = 0.0   # timestamp dell'ultimo update()
        self.update_count    = 0     # quante volte è stato chiamato update()
        self.building_count  = 0     # totale strutture possedute (somma di tutti i count)

    # ── Aggiornamento stato ─────────────────────────────────────────────────

    def update(self):
        """
        Legge lo stato attuale del gioco tramite ScreenReader e aggiorna
        tutti i campi dell'oggetto.

        Chiamato periodicamente dal buy_loop in main.py.
        """
        t_start = time.time()

        try:
            self._update_cookies()
            self._update_cps()
            self._update_buildings()
            self._update_upgrades()
            self._update_affordability()
            self._update_building_count()

        except Exception as e:
            log.error(f"[STATE] Errore durante update(): {e}", exc_info=True)

        self.last_update  = time.time()
        self.update_count += 1

        elapsed = self.last_update - t_start
        log.debug(f"[STATE] Update #{self.update_count} completato in {elapsed:.2f}s")

    def _update_cookies(self):
        """Legge il numero di cookie correnti."""
        val = self.screen_reader.read_cookie_count()
        if val > 0:
            self.cookies = val
        log.debug(f"[STATE] cookies = {self.cookies:,.0f}")

    def _update_cps(self):
        """Legge il CPS e lo aggiunge allo storico."""
        val = self.screen_reader.read_cps()
        if val > 0:
            self.cps = val
            self._cps_history.append((time.time(), val))
        log.debug(f"[STATE] cps = {self.cps:,.2f}")

    def _update_buildings(self):
        """Legge la lista di strutture dal negozio."""
        buildings = self.screen_reader.read_shop()
        if buildings:
            self.buildings = buildings
        log.debug(f"[STATE] {len(self.buildings)} strutture lette")

    def _update_upgrades(self):
        """Legge gli upgrade disponibili."""
        self.upgrades = self.screen_reader.read_upgrades()

    def _update_affordability(self):
        """
        Marca ogni struttura come affordable (acquistabile)
        in base ai cookie correnti.
        """
        for b in self.buildings:
            b["affordable"] = (b["cost"] > 0 and self.cookies >= b["cost"])

    def _update_building_count(self):
        """Aggiorna il conteggio totale delle strutture possedute."""
        self.building_count = sum(b.get("count", 0) for b in self.buildings)

    # ── Query sullo stato ───────────────────────────────────────────────────

    def get_building(self, name: str) -> dict | None:
        """
        Restituisce i dati di una struttura per nome.

        Args:
            name: es. "Nonna", "Fattoria"

        Returns:
            dict della struttura o None se non trovata
        """
        for b in self.buildings:
            if b["name"].lower() == name.lower():
                return b
        return None

    def get_affordable_buildings(self) -> list[dict]:
        """Restituisce solo le strutture che possiamo permetterci."""
        return [b for b in self.buildings if b.get("affordable")]

    def get_cheapest_building(self) -> dict | None:
        """Restituisce la struttura con il costo più basso (acquistabile o no)."""
        valid = [b for b in self.buildings if b["cost"] > 0]
        return min(valid, key=lambda b: b["cost"]) if valid else None

    def time_to_afford(self, cost: float) -> float:
        """
        Calcola quanti secondi mancano per permettersi un acquisto.

        Args:
            cost: prezzo dell'acquisto

        Returns:
            Secondi necessari (0 se già abbastanza cookie, inf se cps=0)
        """
        if self.cookies >= cost:
            return 0.0
        if self.cps <= 0:
            return float("inf")
        return (cost - self.cookies) / self.cps

    def cps_growth_rate(self) -> float:
        """
        Calcola il tasso di crescita del CPS confrontando il primo
        e l'ultimo campione nello storico.

        Returns:
            CPS guadagnato al minuto (positivo = crescita)
        """
        if len(self._cps_history) < 2:
            return 0.0

        t0, cps0 = self._cps_history[0]
        t1, cps1 = self._cps_history[-1]
        dt = t1 - t0

        if dt <= 0:
            return 0.0

        return (cps1 - cps0) / dt * 60   # per minuto

    def is_stalling(self, threshold_minutes: float = 5.0) -> bool:
        """
        Controlla se il CPS non cresce da un po' di tempo.
        Utile per rilevare situazioni in cui il bot è bloccato
        ad aspettare cookie senza comprare nulla.

        Args:
            threshold_minutes: minuti senza crescita prima di considerare stallo

        Returns:
            True se siamo in stallo
        """
        if len(self._cps_history) < 2:
            return False

        t0, cps0 = self._cps_history[0]
        t1, cps1 = self._cps_history[-1]

        minutes_elapsed = (t1 - t0) / 60
        if minutes_elapsed < threshold_minutes:
            return False

        # Crescita percentuale
        if cps0 <= 0:
            return False
        growth = (cps1 - cps0) / cps0
        return growth < 0.01   # meno dell'1% di crescita

    # ── Rappresentazione ────────────────────────────────────────────────────

    def __repr__(self):
        return (
            f"GameState("
            f"cookies={self.cookies:,.0f}, "
            f"cps={self.cps:,.1f}, "
            f"buildings={self.building_count}, "
            f"upgrades={len(self.upgrades)})"
        )

    def summary(self) -> str:
        """Stringa di riepilogo leggibile per il logging."""
        lines = [
            f"🍪 Cookie:    {self.cookies:>20,.0f}",
            f"⚡ CPS:       {self.cps:>20,.1f}",
            f"📈 Crescita:  {self.cps_growth_rate():>19,.2f} CPS/min",
            f"🏗️  Strutture: {self.building_count:>20}",
            "",
            "Negozio:",
        ]
        for b in self.buildings:
            affordable = "✅" if b.get("affordable") else "  "
            lines.append(
                f"  {affordable} {b['name']:<20} "
                f"costo: {b['cost']:>15,.0f}  "
                f"possedute: {b.get('count', 0):>3}"
            )
        return "\n".join(lines)