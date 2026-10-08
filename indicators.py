"""
Indicateurs intraday. Toutes les fonctions travaillent sur UNE séance (une
journée de bougies 5 min, heures normales de marché uniquement) : en day
trading, le VWAP et le range d'ouverture repartent de zéro chaque matin.

- VWAP (prix moyen pondéré par le volume) : le "prix juste" de la journée
  selon le volume échangé. Au-dessus = les acheteurs dominent, en dessous =
  les vendeurs dominent. Très suivi par les traders institutionnels.
- Volume relatif : volume de la bougie / volume moyen des N dernières
  bougies. Une cassure avec beaucoup de volume est plus fiable.
- Range d'ouverture : plus haut et plus bas des X premières minutes.
"""

from __future__ import annotations

import pandas as pd

import config


def add_intraday_indicators(day: pd.DataFrame) -> pd.DataFrame:
    """Ajoute vwap et relvol à une séance (DataFrame OHLCV indexé par l'heure
    de DÉBUT de chaque bougie, fuseau New York). Chaque valeur à la ligne i
    n'utilise que les bougies 0..i : aucun regard vers le futur."""
    df = day.copy()
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum().replace(0, float("nan"))
    df["vwap"] = (typical * df["Volume"]).cumsum() / cum_vol
    df["vwap"] = df["vwap"].fillna(typical)

    avg_vol = (
        df["Volume"].shift(1).rolling(config.RELVOL_LOOKBACK_BARS, min_periods=3).mean()
    )
    df["relvol"] = (df["Volume"] / avg_vol).fillna(0.0)
    return df


def opening_range(day: pd.DataFrame, minutes: int = config.OPENING_RANGE_MINUTES):
    """Retourne (plus_haut, plus_bas, heure_de_fin) du range d'ouverture, ou
    None si la séance ne contient pas encore toutes les bougies du range."""
    if day.empty:
        return None
    session_open = day.index[0].normalize() + pd.Timedelta(config.MARKET_OPEN + ":00")
    or_end = session_open + pd.Timedelta(minutes=minutes)
    bar = pd.Timedelta(minutes=config.BAR_MINUTES)
    or_bars = day[(day.index >= session_open) & (day.index + bar <= or_end)]
    expected = minutes // config.BAR_MINUTES
    if len(or_bars) < expected:
        return None
    return float(or_bars["High"].max()), float(or_bars["Low"].min()), or_end
