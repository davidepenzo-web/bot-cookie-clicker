"""
Cookie Clicker Bot - Strategy
================================
Il cervello del bot. Decide cosa comprare e quando,
usando algoritmi di ottimizzazione basati sul payoff time.

Strategia principale: "Minimum Payoff Time"
============================================
Per ogni acquisto possibile calcoliamo:

    payoff_time = costo / cps_guadagnato

Dove cps_guadagnato è il CPS aggiuntivo che quella struttura porterebbe.
L'acquisto con il payoff_time più basso è sempre quello che massimizza
la produzione nel lungo periodo.

Strategie secondarie:
  - Early game: compra sempre la struttura più economica disponibile
    per non restare mai fermo ad aspettare
  - Upgrade priority: gli upgrade hanno quasi sempre payoff time bassissimo,
    quindi vengono comprati appena disponibili
  - Anti-stallo: se siamo bloccati da troppo tempo, compra la struttura
    più economica anche se non è ottimale
  - Golden Cookie: gestita da main.py/screen_reader, non da qui
"""

import logging

log = logging.getLogger(__name__)

# ── CPS base aggiunto da ogni struttura (valori vanilla Cookie Clicker) ──────
# Questi sono i CPS "base" di ogni struttura al livello 1.
# Il gioco scala il CPS con moltiplicatori legati agli upgrade posseduti,
# ma come stima iniziale questi valori sono sufficienti.
# Fonte: Cookie Clicker wiki
BUILDING_BASE_CPS = {
    "Cursore":          0.1,
    "Nonna":            0.5,
    "Fattoria":         4.0,
    "Miniera":          10.0,
    "Fabbrica":         40.0,
    "Banca":            100.0,
    "Tempio":           400.0,
    "Torre del mago":   6_666.0,
    "Nave spaziale":    100_000.0,
    "Alchimia":         400_000.0,
    "Portale":          1_666_666.0,
    "Macchina del tempo": 98_888_888.0,
    "Antimateria":      999_999_999.0,
    "Prisma":           999_999_999_999.0,
    "Cervello":         99_999_999_999_999.0,
    "Idiosfera":        99_999_999_999_999_999.0,
    "Frattale":         999_999_999_999_999_999_999.0,
}

# ── Costi base delle strutture (prezzi al primo acquisto, vanilla) ───────────
# Usati come stima quando l'OCR non e' disponibile.
# Fonte: Cookie Clicker wiki
BUILDING_BASE_COST = {
    "Cursor":             15,
    "Grandma":           100,
    "Farm":              1_100,
    "Mine":             12_000,
    "Factory":         130_000,
    "Bank":          1_400_000,
    "Temple":       20_000_000,
    "Wizard tower":330_000_000,
    "Shipment":   5_100_000_000,
    "Alchemy lab":75_000_000_000,
    "Portal":  1_000_000_000_000,
    "Time machine": 14_000_000_000_000,
    "Antimatter condenser": 170_000_000_000_000,
    "Prism":    2_100_000_000_000_000,
    "Chancemaker": 26_000_000_000_000_000,
    "Fractal engine": 310_000_000_000_000_000,
    "Javascript console": 71_000_000_000_000_000_000,
    # Nomi italiani (fallback)
    "Cursore":            15,
    "Nonna":             100,
    "Fattoria":        1_100,
    "Miniera":        12_000,
    "Fabbrica":      130_000,
    "Banca":       1_400_000,
    "Tempio":     20_000_000,
    "Torre del mago": 330_000_000,
    "Nave spaziale": 5_100_000_000,
    "Alchimia":   75_000_000_000,
    "Portale": 1_000_000_000_000,
    "Macchina del tempo": 14_000_000_000_000,
    "Antimateria": 170_000_000_000_000,
    "Prisma":  2_100_000_000_000_000,
    "Cervello": 26_000_000_000_000_000,
    "Idiosfera": 310_000_000_000_000_000,
    "Frattale": 71_000_000_000_000_000_000,
}

# Moltiplicatore CPS stimato per ogni struttura aggiuntiva della stessa tipologia
# (la prima Nonna vale 0.5 CPS, la seconda 1.0, ecc. — scalatura lineare)
# Nella realtà il gioco usa moltiplicatori da upgrade, ma questa è una buona stima.
CPS_SCALE_FACTOR = 1.0   # lineare di default

# Soglia: se il payoff time supera questi minuti, considera l'acquisto "non urgente"
MAX_PAYOFF_MINUTES = 60.0

# Moltiplicatore priorità per gli upgrade (payoff artificialmente basso)
UPGRADE_PRIORITY_MULTIPLIER = 0.1


class Strategy:
    """
    Decide cosa comprare ad ogni ciclo del buy_loop.

    Uso:
        strategy = Strategy()
        decision = strategy.get_best_purchase(game_state)
        if decision:
            clicker.click_buy(decision)
            clicker.click_upgrade(decision)  # se è un upgrade
    """

    def __init__(self):
        # Moltiplicatori CPS stimati per struttura (aggiornati dinamicamente)
        # Tengono conto degli upgrade posseduti approssimando il boost
        self._cps_multipliers = {name: 1.0 for name in BUILDING_BASE_CPS}

    # ── Entry point principale ──────────────────────────────────────────────

    def get_best_purchase(self, game_state) -> dict | None:
        """
        Analizza lo stato del gioco e restituisce il miglior acquisto.

        Returns:
            dict con: name, cost, click_pos, payoff_time, is_upgrade
            oppure None se non c'è nulla da comprare
        """
        if not game_state.buildings:
            log.debug("[STRATEGY] Nessuna struttura disponibile.")
            return None

        # 1. Priorità massima: upgrade disponibili
        upgrade_decision = self._check_upgrades(game_state)
        if upgrade_decision:
            return upgrade_decision

        # 2. Calcola il miglior acquisto tra le strutture
        best = self._best_building(game_state)

        # 3. Anti-stallo: se siamo bloccati, compra la più economica acquistabile
        if game_state.is_stalling():
            log.info("[STRATEGY] Stallo rilevato — compro la struttura più economica.")
            cheapest = self._cheapest_affordable(game_state)
            if cheapest:
                return cheapest

        return best

    # ── Upgrade ─────────────────────────────────────────────────────────────

    def _check_upgrades(self, game_state) -> dict | None:
        """
        Controlla se ci sono upgrade acquistabili.
        Gli upgrade hanno sempre priorità perché moltiplicano il CPS globale.

        Restituisce il primo upgrade disponibile (sono sempre convenienti).
        """
        for upgrade in game_state.upgrades:
            click_pos = upgrade.get("click_pos")
            if not click_pos:
                continue

            # Non abbiamo il costo degli upgrade dall'OCR per ora,
            # quindi proviamo a cliccare e vediamo (il gioco non deduce
            # cookie se non puoi permetterti l'upgrade)
            return {
                "name":        f"Upgrade #{upgrade.get('index', '?')}",
                "cost":        0,       # sconosciuto
                "click_pos":   click_pos,
                "payoff_time": 0.0,     # priorità massima
                "is_upgrade":  True,
            }

        return None

    # ── Strutture ────────────────────────────────────────────────────────────

    def _best_building(self, game_state) -> dict | None:
        """
        Trova la struttura migliore da comprare.

        Poiche' il costo non e' piu' letto dall'OCR (inaffidabile),
        usiamo i prezzi base da BUILDING_BASE_CPS come stima e
        diamo priorita' alle strutture che il gioco ha gia' marcato
        come acquistabili (sfondo luminoso nel negozio).

        Tra le strutture acquistabili, scegliamo quella con il
        payoff time piu' basso usando i prezzi stimati.
        """
        # Strutture visibili e acquistabili secondo la color detection
        affordable = [b for b in game_state.buildings if b.get("affordable")]
        visible    = [b for b in game_state.buildings if b.get("visible", True)]

        if not affordable and not visible:
            return None

        # Calcola payoff stimato per ogni struttura acquistabile
        candidates = []
        pool = affordable if affordable else visible

        for building in pool:
            name     = building.get("name", "")
            # Usa il costo base dal wiki come stima (o 0 se non noto)
            est_cost = BUILDING_BASE_COST.get(name, 0)
            cps_gain = self._estimate_cps_gain(name, game_state)

            if cps_gain <= 0:
                continue

            payoff_time = est_cost / cps_gain if est_cost > 0 else 9999

            candidates.append({
                "name":        name,
                "cost":        est_cost,
                "click_pos":   building.get("click_pos"),
                "payoff_time": payoff_time,
                "cps_gain":    cps_gain,
                "affordable":  building.get("affordable", False),
                "is_upgrade":  False,
            })

            log.debug(
                f"[STRATEGY] {name:<22} "
                f"costo_stimato: {est_cost:>15,.0f}  "
                f"cps+: {cps_gain:>12,.1f}  "
                f"payoff: {payoff_time/60:>8.1f} min"
            )

        if not candidates:
            # Nessun candidato con dati utili: compra il primo acquistabile
            if affordable:
                b = affordable[0]
                log.info(f"[STRATEGY] Fallback: primo acquistabile '{b['name']}'")
                return {
                    "name":        b["name"],
                    "cost":        0,
                    "click_pos":   b.get("click_pos"),
                    "payoff_time": 9999,
                    "is_upgrade":  False,
                }
            return None

        candidates.sort(key=lambda c: c["payoff_time"])
        best = candidates[0]
        log.info(
            f"[STRATEGY] Miglior acquisto: '{best['name']}' "
            f"(payoff stimato {best['payoff_time']/60:.1f} min, "
            f"acquistabile: {best['affordable']})"
        )
        return best

    def _cheapest_affordable(self, game_state) -> dict | None:
        """
        Restituisce la struttura acquistabile con il costo più basso.
        Usata come fallback anti-stallo.
        """
        affordable = game_state.get_affordable_buildings()
        if not affordable:
            return None

        cheapest = min(affordable, key=lambda b: b["cost"])
        return {
            "name":        cheapest["name"],
            "cost":        cheapest["cost"],
            "click_pos":   cheapest.get("click_pos"),
            "payoff_time": float("inf"),
            "is_upgrade":  False,
        }

    # ── Stima CPS ───────────────────────────────────────────────────────────

    def _estimate_cps_gain(self, building_name: str, game_state) -> float:
        """
        Stima il CPS aggiuntivo che otterremmo comprando una unità
        della struttura indicata.

        La stima tiene conto di:
          1. CPS base della struttura (da BUILDING_BASE_CPS)
          2. Moltiplicatore stimato dagli upgrade (self._cps_multipliers)
          3. Moltiplicatore globale stimato dal CPS attuale del gioco
             rispetto al CPS "teorico" senza upgrade

        Args:
            building_name: nome della struttura
            game_state:    stato attuale del gioco

        Returns:
            CPS aggiuntivo stimato (float)
        """
        base_cps = BUILDING_BASE_CPS.get(building_name, 0.0)
        if base_cps <= 0:
            return 0.0

        # Moltiplicatore locale della struttura
        local_mult = self._cps_multipliers.get(building_name, 1.0)

        # Stima del moltiplicatore globale:
        # confrontiamo il CPS reale del gioco con quello "teorico"
        # sommando tutti i contributi base delle strutture possedute
        global_mult = self._estimate_global_multiplier(game_state)

        return base_cps * local_mult * global_mult

    def _estimate_global_multiplier(self, game_state) -> float:
        """
        Stima il moltiplicatore globale del CPS confrontando
        il CPS reale con quello teorico basato sulle strutture possedute.

        Se il CPS reale è 10x quello teorico, significa che abbiamo
        molti upgrade attivi — usiamo questo rapporto per scalare
        la stima dei guadagni futuri.

        Returns:
            Moltiplicatore (minimo 1.0)
        """
        if not game_state.buildings or game_state.cps <= 0:
            return 1.0

        # CPS teorico = somma(count_struttura * cps_base_struttura)
        theoretical_cps = 0.0
        for b in game_state.buildings:
            name  = b.get("name", "")
            count = b.get("count", 0)
            base  = BUILDING_BASE_CPS.get(name, 0.0)
            theoretical_cps += count * base

        if theoretical_cps <= 0:
            return 1.0

        ratio = game_state.cps / theoretical_cps
        # Cappato a 10000x per evitare stime assurde nelle fasi avanzate
        return max(1.0, min(ratio, 10_000.0))

    # ── Aggiornamento dinamico ───────────────────────────────────────────────

    def update_multipliers(self, building_name: str, new_multiplier: float):
        """
        Aggiorna il moltiplicatore CPS stimato per una struttura.
        Chiamabile dall'esterno se riesci a leggere il moltiplicatore
        direttamente dal gioco (feature avanzata futura).

        Args:
            building_name:   nome della struttura
            new_multiplier:  nuovo valore del moltiplicatore
        """
        if building_name in self._cps_multipliers:
            self._cps_multipliers[building_name] = new_multiplier
            log.info(f"[STRATEGY] Moltiplicatore '{building_name}' -> {new_multiplier:.2f}x")

    # ── Statistiche ─────────────────────────────────────────────────────────

    def payoff_report(self, game_state) -> str:
        """
        Genera un report testuale del payoff time di tutte le strutture.
        Utile per debug e per valutare la bontà della strategia.

        Returns:
            Stringa formattata con la classifica delle strutture per payoff
        """
        lines = ["── Payoff Report ──────────────────────────────────"]
        lines.append(f"{'Struttura':<22} {'Costo':>15} {'CPS+':>12} {'Payoff':>10}")
        lines.append("─" * 62)

        rows = []
        for b in game_state.buildings:
            name = b.get("name", "")
            cost = b.get("cost", 0)
            if cost <= 0:
                continue
            cps_gain = self._estimate_cps_gain(name, game_state)
            payoff   = cost / cps_gain if cps_gain > 0 else float("inf")
            rows.append((name, cost, cps_gain, payoff))

        rows.sort(key=lambda r: r[3])

        for name, cost, cps_gain, payoff in rows:
            payoff_str = f"{payoff/60:.1f} min" if payoff < float("inf") else "∞"
            lines.append(
                f"{name:<22} {cost:>15,.0f} {cps_gain:>12,.1f} {payoff_str:>10}"
            )

        lines.append("─" * 62)
        return "\n".join(lines)