"""Étape 3 : backtest « stocks in play » selon PROTOCOLE_STOCKS_IN_PLAY.md.

    venv\\Scripts\\python research\\stocks_in_play.py            # période DEV
    venv\\Scripts\\python research\\stocks_in_play.py --holdout  # examen final (UNE fois)

Toutes les grandeurs de sélection (volume relatif, ATR, volume moyen, prix)
n'utilisent que les séances PRÉCÉDENTES + la 1re bougie du jour (connue à
9h35), donc rien du futur.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import common
from sip_download import CACHE, UNIVERSE

BARS = 78            # séance complète (les demi-séances sont ignorées)
LAST = 75            # bougie qui se termine à 15h50
LOOKBACK = 14
TOP_N = 20
STOP_ATR = 0.10


def sessions(df: pd.DataFrame):
    """-> (dates, cube jours x 78 x [O,H,L,C,V]) pour les séances complètes."""
    day = df.index.normalize()
    size = df.groupby(day)["Close"].transform("size").to_numpy()
    df = df[size == BARS]
    dates = df.index.normalize().unique().tz_localize(None)
    cube = df[["Open", "High", "Low", "Close", "Volume"]].to_numpy(np.float64).reshape(len(dates), BARS, 5)
    return dates, cube


def daily_table(sym: str) -> pd.DataFrame:
    dates, c = sessions(pd.read_pickle(CACHE / f"{sym}.pkl"))
    o, h, l, cl, v = (c[:, :, i] for i in range(5))
    d = pd.DataFrame(index=dates)
    d["f_open"], d["f_high"], d["f_low"], d["f_close"], d["f_vol"] = o[:, 0], h[:, 0], l[:, 0], cl[:, 0], v[:, 0]
    day_h, day_l, day_c = h.max(1), l.min(1), cl[:, -1]
    prev_c = pd.Series(day_c, index=dates).shift(1)
    tr = np.maximum(day_h - day_l, np.maximum(abs(day_h - prev_c), abs(day_l - prev_c)))
    # tout est décalé d'un jour : on n'utilise que les séances précédentes
    d["atr"] = pd.Series(tr, index=dates).rolling(LOOKBACK).mean().shift(1)
    d["avg_vol"] = pd.Series(v.sum(1), index=dates).rolling(LOOKBACK).mean().shift(1)
    d["prev_close"] = prev_c
    d["relvol"] = d["f_vol"] / pd.Series(v[:, 0], index=dates).rolling(LOOKBACK).mean().shift(1)
    d["symbol"] = sym
    return d


def simulate_trade(bars: np.ndarray, side: int, level: float, atr: float, cost: float):
    """bars : 78 x [O,H,L,C,V]. Retourne (prix d'entrée, prix de sortie) ou None."""
    for k in range(1, LAST + 1):
        o, h, l = bars[k, 0], bars[k, 1], bars[k, 2]
        if (side == 1 and h >= level) or (side == -1 and l <= level):
            fill = max(level, o) if side == 1 else min(level, o)
            entry = fill * (1 + side * cost)
            stop = fill - side * STOP_ATR * atr
            for j in range(k, LAST + 1):
                oj, hj, lj = bars[j, 0], bars[j, 1], bars[j, 2]
                if j > k and ((side == 1 and oj <= stop) or (side == -1 and oj >= stop)):
                    return entry, oj * (1 - side * cost)
                if (side == 1 and lj <= stop) or (side == -1 and hj >= stop):
                    return entry, stop * (1 - side * cost)
            return entry, bars[LAST, 3] * (1 - side * cost)
    return None


def select(period) -> pd.DataFrame:
    """Sélection du protocole : top 20 volume relatif par séance (sans regarder les résultats)."""
    syms = pd.read_csv(UNIVERSE)["symbol"].tolist()
    syms = [s for s in syms if (CACHE / f"{s}.pkl").exists()]
    tables = []
    for i, s in enumerate(syms, 1):
        tables.append(daily_table(s))
        if i % 50 == 0:
            print(f"  tables {i}/{len(syms)}", flush=True)
    table = pd.concat(tables).sort_index()
    t = table.loc[period[0]:period[1]]
    ok = (t["prev_close"] > 5) & (t["avg_vol"] > 1e6) & (t["atr"] > 0.5) & (t["relvol"] >= 1)
    t = t[ok & (t["f_close"] != t["f_open"])]
    picks = t.sort_values("relvol", ascending=False).groupby(level=0).head(TOP_N)
    print(f"{len(syms)} actions | {picks.index.nunique()} séances | {len(picks)} candidats sélectionnés", flush=True)
    return picks


def run(period, costs_bps=(1, 3, 5)):
    picks = select(period)
    rows = []
    for sym, grp in picks.groupby("symbol"):
        dates, cube = sessions(pd.read_pickle(CACHE / f"{sym}.pkl"))
        pos = {d: i for i, d in enumerate(dates)}
        for d, r in grp.iterrows():
            side = 1 if r["f_close"] > r["f_open"] else -1
            level = r["f_high"] if side == 1 else r["f_low"]
            for bps in costs_bps:
                res = simulate_trade(cube[pos[d]], side, level, r["atr"], bps / 10_000)
                if res is None:
                    continue
                entry, exit_ = res
                move = side * (exit_ - entry)
                # taille du protocole : 0,5 % de risque, plafond capital/20 (levier <= 1x)
                w_proto = min(0.005 / (STOP_ATR * r["atr"]), 1 / (TOP_N * entry))
                # variante « article » : 1 % de risque, plafond 4 x capital / 20 (pour info)
                w_paper = min(0.01 / (STOP_ATR * r["atr"]), 4 / (TOP_N * entry))
                rows.append({"date": d, "symbol": sym, "side": side, "bps": bps,
                             "ret_proto": w_proto * move, "ret_paper": w_paper * move,
                             "R": move / (STOP_ATR * r["atr"])})
    return pd.DataFrame(rows)


def report(trades: pd.DataFrame, label: str):
    out = []
    for bps, g in trades.groupby("bps"):
        for col, name in (("ret_proto", "protocole (levier<=1x)"), ("ret_paper", "variante article (levier<=4x, info)")):
            daily = g.groupby("date")[col].sum()
            row = common.stats(daily, f"{label} | {bps} bp | {name}")
            row["trades"] = len(g)
            out.append(row)
    common.show(out)
    g = trades[trades["bps"] == 3]
    print("\nÀ 3 bp :", f"{len(g)} trades, R moyen {g['R'].mean():+.3f},",
          f"gagnants {(g['R'] > 0).mean():.1%},",
          f"long {g[g.side == 1]['R'].mean():+.3f} R ({(g.side == 1).sum()}), short {g[g.side == -1]['R'].mean():+.3f} R ({(g.side == -1).sum()})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--holdout", action="store_true")
    args = p.parse_args()
    period = common.HOLDOUT if args.holdout else common.DEV
    trades = run(period)
    out = Path(__file__).resolve().parent / f"sip_trades_{'holdout' if args.holdout else 'dev'}.pkl"
    trades.to_pickle(out)
    report(trades, "HOLDOUT" if args.holdout else "DEV")


if __name__ == "__main__":
    main()
