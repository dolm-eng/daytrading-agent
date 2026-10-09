r"""Variante d'IMPLÉMENTATION (DEV uniquement) : la sélection utilise la 1re
bougie du flux IEX (seule donnée disponible à 9h35 avec le plan gratuit
d'Alpaca), comparée à l'historique IEX ; l'exécution est simulée sur les
bougies 1 min SIP (le vrai marché).

    venv\Scripts\python research\sip_iex.py download
    venv\Scripts\python research\sip_iex.py dev
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
from sip_1m import CACHE1, NY, simulate_1m
from stocks_in_play import CACHE, LOOKBACK, STOP_ATR, TOP_N, UNIVERSE, daily_table, report

IEX_FIRST = data.CACHE_DIR / "iex_first"
CACHE1_EXTRA = data.CACHE_DIR / "sip1m_iex"
HERE = Path(__file__).resolve().parent


def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(os.getenv("ALPACA_API_KEY").strip(), os.getenv("ALPACA_SECRET_KEY").strip())


def _get(client, syms, start, end, tf, feed):
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    for attempt in range(5):
        try:
            return client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=syms, timeframe=TimeFrame(tf, TimeFrameUnit.Minute),
                start=start, end=end, adjustment=Adjustment.SPLIT, feed=feed,
            )).df
        except Exception as exc:  # noqa: BLE001
            err = exc
            time.sleep(10 * (attempt + 1))
    raise err


def fetch_first(client, day, syms):
    out = IEX_FIRST / f"{day:%Y-%m-%d}.pkl"
    if out.exists():
        return
    start = pd.Timestamp(f"{day:%Y-%m-%d} 09:30", tz=NY)
    df = _get(client, syms, start.to_pydatetime(), (start + pd.Timedelta(minutes=5)).to_pydatetime(), 5, "iex")
    if not df.empty:
        df = df.reset_index()
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(NY)
        df = df[df["timestamp"] == start].set_index("symbol").rename(columns={c.lower(): c for c in data.COLS})[data.COLS]
    df.to_pickle(out)


def sessions_list():
    d = pd.read_pickle(CACHE / "SPY.pkl") if (CACHE / "SPY.pkl").exists() else pd.read_pickle(CACHE / "AAPL.pkl")
    days = pd.Series(d.index.normalize().tz_localize(None)).value_counts()
    return sorted(days[days == 78].index)


def tables():
    syms = pd.read_csv(UNIVERSE)["symbol"].tolist()
    return pd.concat([daily_table(s) for s in syms]).sort_index()


def select_iex(table: pd.DataFrame, period) -> pd.DataFrame:
    days = [d for d in sessions_list() if (IEX_FIRST / f"{d:%Y-%m-%d}.pkl").exists()]
    first = {d: pd.read_pickle(IEX_FIRST / f"{d:%Y-%m-%d}.pkl") for d in days}
    vol = pd.DataFrame({d: f["Volume"] for d, f in first.items() if len(f)}).T.sort_index()
    avg = vol.rolling(LOOKBACK, min_periods=max(5, LOOKBACK // 2)).mean().shift(1)
    picks = []
    for d in [x for x in days if period[0] <= f"{x:%Y-%m-%d}" <= period[1]]:
        if d not in table.index or d not in avg.index:
            continue
        t = table.loc[[d]].set_index("symbol")
        f = first[d]
        df = f.join(t[["atr", "avg_vol", "prev_close"]], how="inner")
        df["relvol"] = df["Volume"] / avg.loc[d].reindex(df.index)
        ok = (df.prev_close > 5) & (df.avg_vol > 1e6) & (df.atr > 0.5) & (df.relvol >= 1) & (df.Close != df.Open)
        top = df[ok].sort_values("relvol", ascending=False).head(TOP_N)
        for s, r in top.iterrows():
            picks.append({"date": d, "symbol": s, "f_open": r.Open, "f_high": r.High, "f_low": r.Low,
                          "f_close": r.Close, "atr": r.atr, "relvol": r.relvol})
    return pd.DataFrame(picks).set_index("date")


def fetch_1m_extra(client, day, syms):
    have = set()
    f = CACHE1 / f"{day:%Y-%m-%d}.pkl"
    if f.exists():
        have = set(pd.read_pickle(f).index.get_level_values(0))
    need = sorted(set(syms) - have)
    out = CACHE1_EXTRA / f"{day:%Y-%m-%d}.pkl"
    if not need or out.exists():
        return
    start = pd.Timestamp(f"{day:%Y-%m-%d} 09:30", tz=NY)
    df = _get(client, need, start.to_pydatetime(), (start + pd.Timedelta(minutes=390)).to_pydatetime(), 1, "sip")
    df = df.rename(columns={c.lower(): c for c in data.COLS})[data.COLS].astype("float32")
    df.to_pickle(out)


def download():
    IEX_FIRST.mkdir(parents=True, exist_ok=True)
    CACHE1_EXTRA.mkdir(parents=True, exist_ok=True)
    client = _client()
    syms = pd.read_csv(UNIVERSE)["symbol"].tolist()
    days = [d for d in sessions_list() if f"{d:%Y-%m-%d}" <= common.DEV[1]]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(fetch_first, client, d, syms) for d in days]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            if i % 200 == 0:
                print(f"[1res bougies IEX {i}/{len(days)} | {(time.time() - t0) / 60:.0f} min]", flush=True)
    picks = select_iex(tables(), common.DEV)
    picks.to_pickle(HERE / "sip_picks_iex_dev.pkl")
    by_day = {d: sorted(g["symbol"]) for d, g in picks.groupby(level=0)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(fetch_1m_extra, client, d, s) for d, s in by_day.items()]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            if i % 200 == 0:
                print(f"[1 min SIP {i}/{len(by_day)} | {(time.time() - t0) / 60:.0f} min]", flush=True)
    print("terminé", flush=True)


def run(costs_bps=(1, 3)):
    picks = pd.read_pickle(HERE / "sip_picks_iex_dev.pkl")
    rows, missing = [], 0
    for day, grp in picks.groupby(level=0):
        frames = [pd.read_pickle(p) for p in (CACHE1 / f"{day:%Y-%m-%d}.pkl", CACHE1_EXTRA / f"{day:%Y-%m-%d}.pkl") if p.exists()]
        bars = pd.concat(frames) if frames else None
        for _, r in grp.iterrows():
            try:
                b = bars.xs(r["symbol"], level=0).tz_convert(NY)
            except (KeyError, AttributeError):
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
    print(f"candidats sans données : {missing}")
    return pd.DataFrame(rows)


if __name__ == "__main__":
    if sys.argv[1] == "download":
        download()
    else:
        report(run(), "DEV sélection IEX")
