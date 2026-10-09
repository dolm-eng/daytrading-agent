"""Étape 2 : télécharge les bougies 5 min (heures de marché) des 300 actions.
Reprend là où il s'est arrêté (un fichier par action dans data_cache/sip5m/).

    venv\\Scripts\\python research\\sip_download.py
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import common  # noqa: F401  (charge .env)
import data

UNIVERSE = Path(__file__).resolve().parent / "sip_universe.csv"
CACHE = data.CACHE_DIR / "sip5m"
START = datetime(2020, 9, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc) - timedelta(minutes=30)  # le plan gratuit refuse les 15 dernières minutes


def path_for(sym: str) -> Path:
    return CACHE / f"{sym}.pkl"


def download(client, sym: str) -> str:
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    for attempt in range(4):
        try:
            df = client.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=sym, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=START, end=END, adjustment=Adjustment.SPLIT, feed="sip",
            )).df
            break
        except Exception as exc:  # noqa: BLE001  (limite de requêtes, réseau...)
            time.sleep(10 * (attempt + 1))
            err = exc
    else:
        return f"{sym}: ÉCHEC {err!r}"
    if df.empty:
        return f"{sym}: aucune donnée"
    df = df.reset_index(level=0, drop=True).rename(columns={c.lower(): c for c in data.COLS})
    df = data.regular_hours(df).astype("float32")
    df.to_pickle(path_for(sym))
    return f"{sym}: {len(df)} bougies depuis {df.index[0].date()}"


def main():
    from alpaca.data.historical import StockHistoricalDataClient

    CACHE.mkdir(parents=True, exist_ok=True)
    syms = pd.read_csv(UNIVERSE)["symbol"].tolist()
    todo = [s for s in syms if not path_for(s).exists()]
    print(f"{len(syms) - len(todo)} déjà en cache, {len(todo)} à télécharger", flush=True)
    client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY").strip(), os.getenv("ALPACA_SECRET_KEY").strip())
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download, client, s): s for s in todo}
        for i, f in enumerate(as_completed(futures), 1):
            print(f"[{i}/{len(todo)} | {(time.time() - t0) / 60:.0f} min] {f.result()}", flush=True)


if __name__ == "__main__":
    main()
