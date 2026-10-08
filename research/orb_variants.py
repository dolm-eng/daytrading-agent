"""Variantes de l'ORB actuel, testées avec le moteur de backtest officiel (DEV uniquement).

    venv\\Scripts\\python research\\orb_variants.py
"""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

import common
import backtest
import config

VARIANTS = {
    "ORB15 actuel (6 actions)": dict(symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN"]),
    "ORB15 10 actions": dict(),
    "ORB15 long seulement": dict(ALLOW_SHORT=False),
    "ORB15 sortie fin de journée": dict(REWARD_RISK_MULTIPLE=99.0),
    "ORB30": dict(OPENING_RANGE_MINUTES=30),
    "ORB15 SPY+QQQ": dict(symbols=["SPY", "QQQ"]),
}


@contextmanager
def overrides(**kw):
    old = {k: getattr(config, k) for k in kw}
    for k, v in kw.items():
        setattr(config, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(config, k, v)


def main():
    market = common.load()
    rows = []
    for name, kw in VARIANTS.items():
        kw = dict(kw)
        syms = kw.pop("symbols", common.UNIVERSE)
        with overrides(**kw):
            res = backtest.run_backtest({s: market[s] for s in syms})
        eq = res.daily_equity
        r = eq.pct_change().fillna(eq.iloc[0] / config.INITIAL_CAPITAL - 1)
        row = common.stats(r, name)
        row["trades"] = len(res.trades)
        rows.append(row)
        print(f"fini : {name}", flush=True)
    common.show(rows)


if __name__ == "__main__":
    main()
