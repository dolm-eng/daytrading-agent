"""
Stratégie « stocks in play » (actions « en jeu ») — logique pure, sans réseau,
pour pouvoir la tester. Mêmes règles que research/stocks_in_play.py :

1. À 9h35 NY, volume relatif = volume de la 1re bougie 5 min du jour /
   moyenne de ce même volume sur les 14 séances précédentes.
2. Filtres (séances précédentes uniquement) : prix > 5 $, volume moyen
   > 1 M d'actions, ATR(14) > 0,50 $, volume relatif >= 1.
3. On garde les 20 plus forts volumes relatifs.
4. 1re bougie haussière -> achat stop au plus haut de la bougie ;
   baissière -> vente à découvert stop au plus bas ; neutre -> rien.
5. Stop-loss à 10 % de l'ATR depuis le niveau d'entrée ; sortie à 15h50.

⚠️ Expérience de mesure : cette stratégie a un avantage avant frais dans le
backtest, mais devient perdante au-delà d'environ 2,7 points de base de frais
par exécution. Le bot mesure ces frais réels (journal/executions.csv).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import config


@dataclass
class Pick:
    symbol: str
    side: str          # "buy" ou "sell"
    level: float       # prix de déclenchement de l'entrée
    stop: float        # stop-loss
    atr: float
    relvol: float


def load_universe() -> list[str]:
    path = Path(__file__).resolve().parent / config.SIP_UNIVERSE_FILE
    return [s.strip() for s in path.read_text(encoding="utf-8").split() if s.strip()]


def daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    """daily : bougies JOURNALIÈRES jusqu'à HIER inclus, colonnes symbol, High,
    Low, Close, Volume, indexées par date. -> par symbole : atr, avg_vol, prev_close."""
    out = {}
    n = config.SIP_LOOKBACK
    for sym, d in daily.groupby("symbol"):
        d = d.sort_index()
        if len(d) < n + 1:
            continue
        prev = d["Close"].shift(1)
        tr = np.maximum(d["High"] - d["Low"], np.maximum((d["High"] - prev).abs(), (d["Low"] - prev).abs()))
        out[sym] = {
            "atr": float(tr.iloc[-n:].mean()),
            "avg_vol": float(d["Volume"].iloc[-n:].mean()),
            "prev_close": float(d["Close"].iloc[-1]),
        }
    return pd.DataFrame.from_dict(out, orient="index")


def select(features: pd.DataFrame, first_bars: pd.DataFrame, first_vol_history: pd.DataFrame) -> list[Pick]:
    """first_bars : 1re bougie 5 min du jour par symbole (Open, High, Low, Close, Volume).
    first_vol_history : volume de la 1re bougie, lignes = séances précédentes, colonnes = symboles."""
    hist = first_vol_history.sort_index().tail(config.SIP_LOOKBACK)
    avg_first = hist.mean().where(hist.count() >= max(5, config.SIP_LOOKBACK // 2))
    df = first_bars.join(features, how="inner")
    df["relvol"] = df["Volume"] / avg_first.reindex(df.index)
    ok = (
        (df["prev_close"] > config.SIP_MIN_PRICE)
        & (df["avg_vol"] > config.SIP_MIN_AVG_VOLUME)
        & (df["atr"] > config.SIP_MIN_ATR)
        & (df["relvol"] >= config.SIP_MIN_RELVOL)
        & (df["Close"] != df["Open"])
    )
    top = df[ok].sort_values("relvol", ascending=False).head(config.SIP_TOP_N)
    picks = []
    for sym, r in top.iterrows():
        side = "buy" if r["Close"] > r["Open"] else "sell"
        level = float(r["High"] if side == "buy" else r["Low"])
        dist = config.SIP_STOP_ATR * float(r["atr"])
        stop = level - dist if side == "buy" else level + dist
        picks.append(Pick(sym, side, round(level, 2), round(stop, 2), float(r["atr"]), float(r["relvol"])))
    return picks


def position_size(pick: Pick, equity: float) -> int:
    """0,5 % de risque, plafonné à 1/20 du capital par position (levier total <= 1x)."""
    per_share_risk = abs(pick.level - pick.stop)
    if per_share_risk <= 0 or pick.level <= 0:
        return 0
    by_risk = equity * config.SIP_RISK_PCT / per_share_risk
    by_alloc = equity * config.SIP_MAX_POSITION_FRACTION / pick.level
    return max(0, math.floor(min(by_risk, by_alloc)))


def slippage_bps(side: str, reference: float, fill: float) -> float:
    """Coût d'exécution en points de base, POSITIF quand l'exécution est
    moins bonne que le prix de référence (achat plus cher / vente moins chère)."""
    if not reference:
        return float("nan")
    sign = 1 if side == "buy" else -1
    return sign * (fill - reference) / reference * 10_000
