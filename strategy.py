"""
Règles d'entrée de la stratégie : cassure du range d'ouverture (ORB,
"Opening Range Breakout"), filtrée par le VWAP et le volume.

Idée : les 15 premières minutes de la séance concentrent les réactions aux
nouvelles de la nuit et fixent un range (plus haut / plus bas). Quand le prix
sort franchement de ce range, avec du volume, il a tendance à continuer dans
ce sens pendant un moment. C'est une stratégie connue, simple et explicable —
ce qui ne garantit PAS qu'elle soit rentable : c'est au backtest et au paper
trading de le dire.

Achat (long) quand une bougie 5 min CLÔTURE :
  1. au-dessus du plus haut du range d'ouverture,
  2. au-dessus du VWAP (les acheteurs dominent la journée),
  3. avec un volume >= RELVOL_MIN x le volume moyen,
  4. avant ENTRY_CUTOFF (11h30 New York).
Vente à découvert (short) : le miroir exact, sous le plus bas du range.

Stop-loss = milieu du range d'ouverture (si le prix y revient, la cassure a
échoué). Objectif = REWARD_RISK_MULTIPLE x la distance au stop.
Sortie forcée dans tous les cas avant la clôture (gérée par backtest/live).

Ce module est utilisé TEL QUEL par le backtest et par le bot live : les deux
prennent exactement les mêmes décisions sur les mêmes données.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config
import indicators as ind


@dataclass
class Signal:
    side: str          # "buy" (long) ou "sell" (short)
    ref_price: float   # clôture de la bougie de cassure
    stop_price: float
    target_price: float
    reason: str


def evaluate_entry(day: pd.DataFrame) -> Signal | None:
    """Évalue la DERNIÈRE bougie (terminée) de `day`, qui doit contenir
    uniquement les bougies déjà clôturées de la séance en cours, avec les
    colonnes ajoutées par indicators.add_intraday_indicators."""
    if len(day) < 2:
        return None
    orng = ind.opening_range(day)
    if orng is None:
        return None
    or_high, or_low, or_end = orng

    last_start = day.index[-1]
    bar_end = last_start + pd.Timedelta(minutes=config.BAR_MINUTES)
    cutoff = last_start.normalize() + pd.Timedelta(config.ENTRY_CUTOFF + ":00")
    if last_start < or_end or bar_end > cutoff:
        return None

    last = day.iloc[-1]
    close = float(last["Close"])
    or_range_pct = (or_high - or_low) / close
    if not (config.OR_MIN_RANGE_PCT <= or_range_pct <= config.OR_MAX_RANGE_PCT):
        return None
    if float(last["relvol"]) < config.RELVOL_MIN:
        return None

    or_mid = (or_high + or_low) / 2
    vwap = float(last["vwap"])

    if close > or_high and close > vwap:
        risk = close - or_mid
        if risk <= 0:
            return None
        return Signal(
            "buy", close, round(or_mid, 2), round(close + config.REWARD_RISK_MULTIPLE * risk, 2),
            f"cassure haussière du range {or_low:.2f}-{or_high:.2f}, au-dessus du VWAP {vwap:.2f}, "
            f"volume x{last['relvol']:.1f}",
        )

    if config.ALLOW_SHORT and close < or_low and close < vwap:
        risk = or_mid - close
        if risk <= 0:
            return None
        return Signal(
            "sell", close, round(or_mid, 2), round(close - config.REWARD_RISK_MULTIPLE * risk, 2),
            f"cassure baissière du range {or_low:.2f}-{or_high:.2f}, sous le VWAP {vwap:.2f}, "
            f"volume x{last['relvol']:.1f}",
        )

    return None
