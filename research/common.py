"""
Outils partagés pour la recherche de stratégie.

PROTOCOLE (pour ne pas sur-optimiser) :
- DEV     : 2020-10 -> 2024-12. Seule période utilisée pour comparer les idées.
- HOLDOUT : 2025-01 -> aujourd'hui. Regardé UNE fois, pour la stratégie finale.
- Puis paper trading réel (oct. 2026 -> janv. 2027) = le vrai test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

import data  # noqa: E402

load_dotenv(ROOT / ".env")

UNIVERSE = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD"]
DEV = ("2020-10-01", "2024-12-31")
HOLDOUT = ("2025-01-01", "2026-12-31")
HISTORY_DAYS = 2200


def load(symbols=UNIVERSE, period=DEV) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        df = data.fetch_alpaca(s, HISTORY_DAYS, os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
        out[s] = df.loc[period[0]:period[1]]
    return out


def stats(daily_ret: pd.Series, label: str = "") -> dict:
    """Statistiques sur une série de rendements journaliers (en fraction du capital)."""
    r = daily_ret.fillna(0.0)
    eq = (1 + r).cumprod()
    years = max(len(r) / 252, 1e-9)
    out = {
        "strat": label,
        "jours": len(r),
        "total": eq.iloc[-1] - 1,
        "CAGR": eq.iloc[-1] ** (1 / years) - 1,
        "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0,
        "maxDD": ((eq.cummax() - eq) / eq.cummax()).max(),
        "jours+": (r > 0).sum() / max((r != 0).sum(), 1),
    }
    by_year = (1 + r).groupby(r.index.year).prod() - 1
    out.update({str(y): v for y, v in by_year.items()})
    return out


def show(rows: list[dict]):
    df = pd.DataFrame(rows).set_index("strat")
    pct = [c for c in df.columns if c not in ("jours", "sharpe")]
    fmt = df.copy()
    for c in pct:
        fmt[c] = df[c].map(lambda v: f"{v:+.1%}" if pd.notna(v) else "")
    fmt["sharpe"] = df["sharpe"].map(lambda v: f"{v:.2f}")
    print(fmt.to_string())
