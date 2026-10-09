"""Étape 1 : liste figée des 300 actions (voir PROTOCOLE_STOCKS_IN_PLAY.md).

    venv\\Scripts\\python research\\sip_universe.py   -> research/sip_universe.csv
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import common  # noqa: F401  (charge .env)

OUT = Path(__file__).resolve().parent / "sip_universe.csv"
N = 300
# l'API Alpaca ne dit pas si un titre est un ETF : on filtre sur le nom
ETF_WORDS = re.compile(
    r"\b(ETF|ETN|Fund|Trust|iShares|SPDR|ProShares|Direxion|Invesco|Vanguard|Shares|Index|"
    r"Leveraged|Bull|Bear|2X|3X|Ultra|Daily|Portfolio|Notes?)\b", re.I,
)


def main():
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    key, secret = os.getenv("ALPACA_API_KEY").strip(), os.getenv("ALPACA_SECRET_KEY").strip()
    assets = TradingClient(key, secret, paper=True).get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    syms = sorted(
        a.symbol for a in assets
        if a.tradable and str(getattr(a.exchange, "value", a.exchange)) in ("NYSE", "NASDAQ")
        and a.symbol.isalpha() and not ETF_WORDS.search(a.name or "")
    )
    print(f"{len(syms)} actions candidates")

    client = StockHistoricalDataClient(key, secret)
    rows = []
    for i in range(0, len(syms), 400):
        batch = syms[i:i + 400]
        df = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=batch, timeframe=TimeFrame.Day,
            start=datetime(2024, 1, 1, tzinfo=timezone.utc), end=datetime(2024, 12, 31, 23, tzinfo=timezone.utc),
            adjustment=Adjustment.SPLIT, feed="sip",
        )).df
        if df.empty:
            continue
        df = df.reset_index()
        df["dollar_vol"] = df["close"] * df["volume"]
        g = df.groupby("symbol").agg(dv=("dollar_vol", "median"), px=("close", "median"), n=("close", "size"))
        rows.append(g)
        print(f"  {min(i + 400, len(syms))}/{len(syms)}", flush=True)
    stats = pd.concat(rows)
    stats = stats[(stats["px"] > 10) & (stats["n"] >= 200)]
    top = stats.sort_values("dv", ascending=False).head(N)
    top.to_csv(OUT)
    print(f"-> {OUT.name} : {len(top)} actions (de {top.index[0]} à {top.index[-1]}, "
          f"volume médian {top['dv'].min() / 1e6:,.0f} M$ à {top['dv'].max() / 1e9:,.1f} Md$ par jour)")


if __name__ == "__main__":
    main()
