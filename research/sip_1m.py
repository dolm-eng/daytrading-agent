r"""Amendement n°1 : exécution simulée en bougies de 1 minute.

    venv\Scripts\python research\sip_1m.py download   # 1 min des actions sélectionnées (DEV + HOLDOUT)
    venv\Scripts\python research\sip_1m.py dev
    venv\Scripts\python research\sip_1m.py holdout    # examen final (UNE fois)
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import common
import data
from stocks_in_play import STOP_ATR, TOP_N, report, select

CACHE1 = data.CACHE_DIR / "sip1m"
HERE = Path(__file__).resolve().parent
NY = "America/New_York"


def picks_for(name: str) -> pd.DataFrame:
    f = HERE / f"sip_picks_{name}.pkl"
    if not f.exists():
        select(common.DEV if name == "dev" else common.HOLDOUT).to_pickle(f)
    return pd.read_pickle(f)


def fetch_day(client, day: pd.Timestamp, syms: list[str]) -> str:
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    out = CACHE1 / f"{day:%Y-%m-%d}.pkl"
    if out.exists():
        return "cache"
    start = pd.Timestamp(f"{day:%Y-%m-%d} 09:30", tz=NY)
    for attempt in range(5):
        try:
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=syms, timeframe=TimeFrame.Minute,
                start=start.to_pydatetime(), end=(start + pd.Timedelta(minutes=390)).to_pydatetime(),
                adjustment=Adjustment.SPLIT, feed="sip",
            )).df
            break
        except Exception as exc:  # noqa: BLE001
            err = exc
            time.sleep(10 * (attempt + 1))
    else:
        return f"ÉCHEC {err!r}"
    df = df.rename(columns={c.lower(): c for c in data.COLS})[data.COLS].astype("float32")
    df.to_pickle(out)
    return f"{len(df)} bougies"


def download():
    from alpaca.data.historical import StockHistoricalDataClient

    CACHE1.mkdir(parents=True, exist_ok=True)
    picks = pd.concat([picks_for("dev"), picks_for("holdout")])
    days = {d: sorted(g["symbol"]) for d, g in picks.groupby(level=0)}
    client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY").strip(), os.getenv("ALPACA_SECRET_KEY").strip())
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_day, client, d, s): d for d, s in days.items()}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            if i % 100 == 0 or r.startswith("ÉCHEC"):
                print(f"[{i}/{len(days)} | {(time.time() - t0) / 60:.0f} min] {futs[f]:%Y-%m-%d} {r}", flush=True)
    print("téléchargement 1 min terminé", flush=True)


def simulate_1m(m: np.ndarray, side: int, level: float, atr: float, cost: float):
    """m : minutes 9h35 -> 15h49 [O,H,L,C]. Règles de l'amendement n°1."""
    for k in range(len(m)):
        o, h, l, c = m[k]
        if (side == 1 and h >= level) or (side == -1 and l <= level):
            gap = (side == 1 and o >= level) or (side == -1 and o <= level)
            fill = o if gap else level
            entry = fill * (1 + side * cost)
            stop = fill - side * STOP_ATR * atr
            hit = (side * (l if side == 1 else h) <= side * stop) if gap else (side * c <= side * stop)
            if hit:
                return entry, stop * (1 - side * cost)
            for j in range(k + 1, len(m)):
                oj, hj, lj, _ = m[j]
                if side * oj <= side * stop:
                    return entry, oj * (1 - side * cost)
                if (side == 1 and lj <= stop) or (side == -1 and hj >= stop):
                    return entry, stop * (1 - side * cost)
            return entry, m[-1, 3] * (1 - side * cost)
    return None


def run(name: str, costs_bps=(1, 3, 5)) -> pd.DataFrame:
    picks = picks_for(name)
    rows, missing = [], 0
    for day, grp in picks.groupby(level=0):
        f = CACHE1 / f"{day:%Y-%m-%d}.pkl"
        if not f.exists():
            missing += len(grp)
            continue
        bars = pd.read_pickle(f)
        for _, r in grp.iterrows():
            try:
                b = bars.xs(r["symbol"], level=0).tz_convert(NY)
            except KeyError:
                missing += 1
                continue
            t = b.index.strftime("%H:%M")
            m = b[(t >= "09:35") & (t <= "15:49")][["Open", "High", "Low", "Close"]].to_numpy(np.float64)
            if len(m) < 300:
                missing += 1
                continue
            side = 1 if r["f_close"] > r["f_open"] else -1
            level = r["f_high"] if side == 1 else r["f_low"]
            for bps in costs_bps:
                res = simulate_1m(m, side, level, r["atr"], bps / 10_000)
                if res is None:
                    continue
                entry, exit_ = res
                move = side * (exit_ - entry)
                rows.append({"date": day, "symbol": r["symbol"], "side": side, "bps": bps,
                             "ret_proto": min(0.005 / (STOP_ATR * r["atr"]), 1 / (TOP_N * entry)) * move,
                             "ret_paper": min(0.01 / (STOP_ATR * r["atr"]), 4 / (TOP_N * entry)) * move,
                             "R": move / (STOP_ATR * r["atr"])})
    print(f"candidats sans données 1 min exploitables : {missing}")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "download":
        download()
    else:
        trades = run(cmd)
        trades.to_pickle(HERE / f"sip1m_trades_{cmd}.pkl")
        report(trades, f"{cmd.upper()} (1 min)")
