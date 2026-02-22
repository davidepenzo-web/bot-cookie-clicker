"""
Cookie Clicker Bot - Strategy (v2 - Tooltip-based)
====================================================
Strategia completamente riscritta usando dati reali letti dai tooltip.

Algoritmo principale: "Payoff Time Minimo"
==========================================
Per ogni struttura acquistabile leggiamo dal tooltip:
  - price:      prezzo REALE aggiornato
  - cps_single: CPS che aggiunge UNA unità di questa struttura

Calcoliamo:
  payoff_time = price / cps_single

La struttura con payoff_time più basso è sempre la scelta ottimale
perché massimizza la crescita del CPS nel lungo periodo.

Perché questo è meglio dei valori fissi:
  - Il prezzo scala esponenzialmente ad ogni acquisto (+15% circa)
  - Il CPS singolo cresce con ogni upgrade comprato
  - I valori fissi diventano sbagliati dopo pochi acquisti
  - I tooltip riflettono sempre lo stato REALE del gioco

Strategie aggiuntive:
  - Upgrade priority: gli upgrade vengono comprati sempre prima
  - Cache: i dati tooltip vengono cachati per evitare hover inutili
  - Fallback: se l'OCR fallisce, usa l'ultima lettura valida nota
"""

import time
import logging

log = logging.getLogger(__name__)

# Intervallo minimo tra due letture complete dei tooltip (secondi)
TOOLTIP_REFRESH_INTERVAL = 15.0

# Dopo quanti secondi i dati cachati diventano "vecchi" e vanno riletti
TOOLTIP_CACHE_EXPIRY = 30.0


class Strategy:
    """
    Decide cosa comprare usando dati reali dai tooltip del negozio.

    Args:
        tooltip_reader: istanza di TooltipReader (può essere None,
                        in quel caso usa fallback su color detection)
    """

    def __init__(self, tooltip_reader=None):
        self.tooltip_reader = tooltip_reader
        self._cache: dict = {}
        self._last_full_read = 0.0

    # ── Entry point principale ──────────────────────────────────────────────

    def get_best_purchase(self, game_state) -> dict | None:
        """
        Analizza lo stato del gioco e restituisce il miglior acquisto.

        Flusso:
          1. Se ci sono upgrade disponibili, comprali subito
          2. Aggiorna i dati tooltip se necessario
          3. Calcola il payoff time per ogni struttura acquistabile
          4. Restituisce quella con payoff time più basso
        """
        if not game_state.buildings:
            log.debug("[STRATEGY] Nessuna struttura disponibile.")
            return None

        # 1. Priorità massima: upgrade disponibili
        upgrade = self._check_upgrades(game_state)
        if upgrade:
            return upgrade

        # 2. Aggiorna cache tooltip se necessario
        self._maybe_refresh_cache(game_state)

        # 3. Trova il miglior acquisto
        return self._best_building(game_state)

    # ── Upgrade ─────────────────────────────────────────────────────────────

    def _check_upgrades(self, game_state) -> dict | None:
        for upgrade in game_state.upgrades:
            click_pos = upgrade.get("click_pos")
            if not click_pos:
                continue
            return {
                "name":        f"Upgrade #{upgrade.get('index', '?')}",
                "price":       0,
                "cps_single":  0,
                "payoff_time": 0.0,
                "click_pos":   click_pos,
                "is_upgrade":  True,
            }
        return None

    # ── Cache tooltip ────────────────────────────────────────────────────────

    def _maybe_refresh_cache(self, game_state):
        """Aggiorna la cache dei tooltip se necessario."""
        if self.tooltip_reader is None:
            return

        now = time.time()
        affordable = [b for b in game_state.buildings if b.get("affordable")]
        missing = [
            b for b in affordable
            if b["name"] not in self._cache
            or now - self._cache[b["name"]].get("timestamp", 0) > TOOLTIP_CACHE_EXPIRY
        ]

        if not missing and now - self._last_full_read < TOOLTIP_REFRESH_INTERVAL:
            return

        targets = missing if missing else affordable[:5]
        log.info(f"[STRATEGY] Aggiorno tooltip per: {[b['name'] for b in targets]}")

        for building in targets:
            data = self.tooltip_reader.read_building_data(building)
            if data["price"] > 0 and data["cps_single"] > 0:
                self._cache[building["name"]] = {
                    "price":      data["price"],
                    "cps_single": data["cps_single"],
                    "cps_total":  data["cps_total"],
                    "timestamp":  time.time(),
                }
                log.debug(
                    f"[CACHE] {building['name']}: "
                    f"price={data['price']:,.0f} "
                    f"cps_single={data['cps_single']:,.1f}"
                )
            else:
                log.debug(f"[CACHE] {building['name']}: OCR fallito")

        self._last_full_read = time.time()

    # ── Selezione miglior struttura ──────────────────────────────────────────

    def _best_building(self, game_state) -> dict | None:
        """
        Calcola il payoff time per ogni struttura acquistabile
        e restituisce quella con il valore più basso.
        """
        affordable = [b for b in game_state.buildings if b.get("affordable")]
        if not affordable:
            log.debug("[STRATEGY] Nessuna struttura acquistabile.")
            return None

        candidates = []
        fallback_only = []

        for building in affordable:
            name      = building["name"]
            click_pos = building.get("click_pos")
            cache     = self._cache.get(name)

            if cache and cache["cps_single"] > 0:
                payoff = cache["price"] / cache["cps_single"]
                candidates.append({
                    "name":        name,
                    "price":       cache["price"],
                    "cps_single":  cache["cps_single"],
                    "payoff_time": payoff,
                    "click_pos":   click_pos,
                    "is_upgrade":  False,
                })
                log.debug(
                    f"[STRATEGY] {name:<22} "
                    f"price={cache['price']:>15,.0f}  "
                    f"cps+={cache['cps_single']:>12,.1f}  "
                    f"payoff={payoff/60:>8.1f}min"
                )
            else:
                fallback_only.append({
                    "name":        name,
                    "price":       0,
                    "cps_single":  0,
                    "payoff_time": float("inf"),
                    "click_pos":   click_pos,
                    "is_upgrade":  False,
                })

        if candidates:
            candidates.sort(key=lambda c: c["payoff_time"])
            best = candidates[0]
            log.info(
                f"[STRATEGY] Miglior acquisto: '{best['name']}' "
                f"payoff={best['payoff_time']/60:.1f}min "
                f"(price={best['price']:,.0f} cps+={best['cps_single']:,.1f})"
            )
            return best

        if fallback_only:
            best = fallback_only[0]
            log.info(f"[STRATEGY] Fallback (no cache): '{best['name']}'")
            return best

        return None

    # ── Statistiche ─────────────────────────────────────────────────────────

    def payoff_report(self, game_state) -> str:
        lines = ["── Payoff Report (dati reali tooltip) ────────────────"]
        lines.append(f"{'Struttura':<22} {'Prezzo':>15} {'CPS+':>12} {'Payoff':>10}")
        lines.append("─" * 65)
        rows = []
        for b in game_state.buildings:
            name  = b["name"]
            cache = self._cache.get(name)
            if cache and cache["cps_single"] > 0:
                payoff = cache["price"] / cache["cps_single"]
                rows.append((name, cache["price"], cache["cps_single"], payoff))
            else:
                rows.append((name, 0, 0, float("inf")))
        rows.sort(key=lambda r: r[3])
        for name, price, cps, payoff in rows:
            ps = f"{payoff/60:.1f}min" if payoff < float("inf") else "∞"
            lines.append(f"{name:<22} {price:>15,.0f} {cps:>12,.1f} {ps:>10}")
        lines.append("─" * 65)
        return "\n".join(lines)