"""
Momentum intraday "zone de bruit" (inspiré de Zarattini, Aziz & Barbon, 2024,
"Beat the Market: An Effective Intraday Momentum Strategy for SPY").

Idée : chaque jour, le prix oscille autour de l'ouverture dans une "zone de
bruit" dont la largeur, à chaque heure de la journée, est la moyenne des
écarts |prix/ouverture - 1| observés à cette même heure sur les 14 séances
précédentes. Tant que le prix reste dans la zone : bruit, on ne fait rien.
S'il en sort par le haut (ou le bas), un vrai déséquilibre acheteurs/vendeurs
est probable -> on suit le mouvement.

Règles (vérifiées seulement toutes les 30 min : 10h00, 10h30, ... 15h30 NY) :
- haut = max(ouverture, clôture veille) x (1 + bruit), bas = min(...) x (1 - bruit)
- clôture > haut -> achat ; clôture < bas -> vente à découvert
- sortie (stop suiveur) : long si clôture < max(haut, VWAP) ; short miroir
- exécution à l'ouverture de la bougie 5 min suivante ; tout fermé à 15h50
- exposition = min(EXPO_MAX, 2% / volatilité quotidienne sur 14 jours)

Usage : venv\\Scripts\\python research\\noise_area.py [--holdout]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import common
import data

LOOKBACK = 14
TARGET_VOL = 0.02
COST_BPS = 2          # par exécution, comme le backtest officiel
BARS_PER_DAY = 78
CHECK_EVERY = 6       # 6 x 5 min = 30 min
FLATTEN_IDX = 75      # bougie qui se termine à 15h50


def simulate(df: pd.DataFrame, allow_short=True, expo_max=1.0, check_every=CHECK_EVERY, use_vwap_stop=True) -> pd.Series:
    """Rendement journalier (fraction du capital alloué à ce symbole)."""
    sessions = {d: g for d, g in data.split_sessions(df).items() if len(g) == BARS_PER_DAY}
    dates = sorted(sessions)
    opens = pd.Series({d: sessions[d]["Open"].iloc[0] for d in dates})
    closes = pd.Series({d: sessions[d]["Close"].iloc[-1] for d in dates})
    moves = pd.DataFrame({d: (sessions[d]["Close"].values / opens[d] - 1) for d in dates}).T.abs()
    sigma = moves.rolling(LOOKBACK).mean().shift(1)          # uniquement les jours PRÉCÉDENTS
    daily_vol = closes.pct_change().rolling(LOOKBACK).std().shift(1)
    prev_close = closes.shift(1)

    cost = COST_BPS / 10_000
    out = {}
    for d in dates:
        if pd.isna(sigma.loc[d]).any() or pd.isna(daily_vol[d]) or pd.isna(prev_close[d]):
            continue
        s = sessions[d]
        o, h, l, c, v = (s[k].values for k in ("Open", "High", "Low", "Close", "Volume"))
        typical = (h + l + c) / 3
        vwap = np.cumsum(typical * v) / np.maximum(np.cumsum(v), 1)
        up = max(opens[d], prev_close[d]) * (1 + sigma.loc[d].values)
        dn = min(opens[d], prev_close[d]) * (1 - sigma.loc[d].values)
        expo = min(expo_max, TARGET_VOL / daily_vol[d]) if daily_vol[d] > 0 else 0.0

        pos, entry, pnl = 0, 0.0, 0.0
        for i in range(BARS_PER_DAY):
            if i == FLATTEN_IDX:
                if pos:
                    pnl += pos * (c[i] * (1 - pos * cost) / entry - 1) * expo
                    pos = 0
                break
            if (i + 1) % check_every or i < 5:
                continue
            want = pos
            if pos == 1:
                stop = max(up[i], vwap[i]) if use_vwap_stop else up[i]
                if c[i] < stop:
                    want = 0
            elif pos == -1:
                stop = min(dn[i], vwap[i]) if use_vwap_stop else dn[i]
                if c[i] > stop:
                    want = 0
            if want == 0:
                if c[i] > up[i]:
                    want = 1
                elif allow_short and c[i] < dn[i]:
                    want = -1
            if want != pos:
                fill = o[i + 1]
                if pos:
                    pnl += pos * (fill * (1 - pos * cost) / entry - 1) * expo
                if want:
                    entry = fill * (1 + want * cost)
                pos = want
        out[pd.Timestamp(d)] = pnl
    return pd.Series(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--holdout", action="store_true", help="période d'examen final (à n'utiliser qu'une fois)")
    args = p.parse_args()
    period = common.HOLDOUT if args.holdout else common.DEV
    market = common.load(period=period)

    rows, per_sym = [], {}
    for s, df in market.items():
        r = simulate(df)
        per_sym[s] = r
        rows.append(common.stats(r, f"{s}"))
    port_idx = pd.concat(per_sym, axis=1).fillna(0.0)
    rows.append(common.stats(port_idx[["SPY", "QQQ"]].mean(axis=1), "PORTEFEUILLE SPY+QQQ"))
    rows.append(common.stats(port_idx.mean(axis=1), "PORTEFEUILLE 10"))

    for label, kw in {
        "SPY long seulement": dict(allow_short=False),
        "SPY sans stop VWAP": dict(use_vwap_stop=False),
        "SPY expo max 2x": dict(expo_max=2.0),
    }.items():
        rows.append(common.stats(simulate(market["SPY"], **kw), label))
    print(f"Période : {period}")
    common.show(rows)


if __name__ == "__main__":
    main()
