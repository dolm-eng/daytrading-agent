"""
Données intraday (bougies 5 min, heures normales de marché, fuseau New York).

Trois sources :
- yfinance : gratuit, sans clé, mais limité aux ~60 derniers jours en 5 min.
  Suffisant pour un premier backtest rapide.
- Alpaca : avec tes clés paper (gratuites), plusieurs années d'historique
  5 min -> backtest bien plus fiable. Utilisé aussi par le bot live.
- Synthétique : données simulées pour tester le code sans réseau. Aucune
  valeur prédictive.

Toutes renvoient un DataFrame OHLCV indexé par l'heure de DÉBUT de chaque
bougie (tz America/New_York), filtré sur 9h30-16h00.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config

COLS = ["Open", "High", "Low", "Close", "Volume"]


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    df = df.tz_convert(config.MARKET_TZ) if df.index.tz is not None else df.tz_localize(config.MARKET_TZ)
    times = df.index.strftime("%H:%M")
    df = df[(times >= config.MARKET_OPEN) & (times < "16:00")]
    return df[COLS].astype(float).sort_index()


def split_sessions(df: pd.DataFrame) -> dict:
    """{date: DataFrame de la séance}"""
    return {d: g for d, g in df.groupby(df.index.date) if not g.empty}


def fetch_yfinance(symbol: str, period: str = "60d") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        symbol, period=period, interval=f"{config.BAR_MINUTES}m",
        progress=False, auto_adjust=False, prepost=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"Aucune donnée yfinance pour {symbol} (connexion internet ?).")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return regular_hours(df)


CACHE_DIR = Path(__file__).resolve().parent / "data_cache"


def fetch_alpaca(symbol: str, days: int, api_key: str, secret_key: str, feed: str = "sip") -> pd.DataFrame:
    """Historique 5 min via Alpaca (ajusté des splits). Le flux 'sip' (toutes
    les bourses US) est accessible gratuitement pour l'historique (hors 15
    dernières minutes). Mis en cache dans data_cache/ pour la journée."""
    cache = CACHE_DIR / f"{symbol}_{days}d_{datetime.now():%Y%m%d}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)

    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(config.BAR_MINUTES, TimeFrameUnit.Minute),
        start=datetime.now(timezone.utc) - timedelta(days=days),
        end=datetime.now(timezone.utc) - timedelta(minutes=20),
        feed=feed,
        adjustment=Adjustment.SPLIT,
    )
    bars = client.get_stock_bars(req).df
    if bars.empty:
        raise RuntimeError(f"Aucune donnée Alpaca pour {symbol}.")
    bars = bars.reset_index(level=0, drop=True)
    bars = bars.rename(columns={c.lower(): c for c in COLS})
    out = regular_hours(bars)
    CACHE_DIR.mkdir(exist_ok=True)
    out.to_pickle(cache)
    return out


def generate_synthetic_intraday(
    n_days: int = 120, start_price: float = 100.0, seed: int | None = None, trend_strength: float = 0.3
) -> pd.DataFrame:
    """Séances simulées : marche aléatoire avec un peu d'élan intraday et un
    volume en forme de U (fort à l'ouverture et à la clôture). Sert
    UNIQUEMENT à vérifier que le code tourne.

    trend_strength=0 -> marche aléatoire pure : aucune stratégie ne devrait
    y gagner de l'argent (utile pour détecter un biais dans le backtest)."""
    rng = np.random.default_rng(seed)
    bars_per_day = int(390 / config.BAR_MINUTES)
    bar_vol = 0.012 / np.sqrt(bars_per_day)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)
    frames, price = [], start_price
    for d in days:
        price *= np.exp(rng.normal(0, 0.006))  # gap d'ouverture
        drift = rng.normal(0, bar_vol * trend_strength)    # tendance du jour
        rets = drift + rng.normal(0, bar_vol, bars_per_day)
        close = price * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[price], close[:-1]])
        wick = np.abs(rng.normal(0, bar_vol, bars_per_day)) * close
        high = np.maximum(open_, close) + wick * rng.uniform(0.1, 0.6, bars_per_day)
        low = np.minimum(open_, close) - wick * rng.uniform(0.1, 0.6, bars_per_day)
        u = np.linspace(-1, 1, bars_per_day) ** 2
        volume = (200_000 * (0.5 + u) * rng.uniform(0.6, 1.6, bars_per_day)).astype(int)
        idx = pd.date_range(
            pd.Timestamp(d.date()).tz_localize(config.MARKET_TZ) + pd.Timedelta(config.MARKET_OPEN + ":00"),
            periods=bars_per_day, freq=f"{config.BAR_MINUTES}min",
        )
        frames.append(pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx))
        price = close[-1]
    return pd.concat(frames)
