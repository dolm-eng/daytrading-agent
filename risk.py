"""
Gestion du risque intraday. Règles :
1. Taille de position : on risque au max RISK_PER_TRADE_PCT du capital si le
   stop est touché. actions = (capital x risque%) / distance au stop.
   Plafonné à MAX_ALLOCATION_PER_POSITION_PCT du capital. Nombre d'actions
   ENTIER (les ordres bracket et la vente à découvert n'acceptent pas les
   fractions d'action).
2. Coupe-circuit quotidien : -MAX_DAILY_LOSS_PCT sur la journée -> on ferme
   tout et on ne trade plus jusqu'au lendemain.
3. Coupe-circuit global : -MAX_DRAWDOWN_PCT depuis le plus haut du compte ->
   plus aucune nouvelle entrée tant que l'humain n'a pas regardé.
4. Limites de fréquence : MAX_OPEN_POSITIONS, MAX_TRADES_PER_DAY, une seule
   entrée par symbole et par jour.
5. Tout est fermé avant la clôture : jamais de position overnight.
"""

from __future__ import annotations

import math

import config


def position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade_pct: float = config.RISK_PER_TRADE_PCT,
    max_allocation_pct: float = config.MAX_ALLOCATION_PER_POSITION_PCT,
) -> int:
    per_share_risk = abs(entry_price - stop_price)
    if per_share_risk <= 0 or entry_price <= 0:
        return 0
    shares_by_risk = (capital * risk_per_trade_pct) / per_share_risk
    shares_by_alloc = (capital * max_allocation_pct) / entry_price
    return max(0, math.floor(min(shares_by_risk, shares_by_alloc)))


def apply_slippage(price: float, side: str) -> float:
    """Exécution au marché un peu moins bonne que le prix affiché."""
    bps = config.SLIPPAGE_BPS / 10_000
    return price * (1 + bps) if side == "buy" else price * (1 - bps)


def daily_loss_exceeded(day_start_equity: float, equity: float) -> bool:
    if day_start_equity <= 0:
        return False
    return (equity - day_start_equity) / day_start_equity <= -config.MAX_DAILY_LOSS_PCT


def check_account_circuit_breaker(equity_history: list[float]) -> dict:
    """Drawdown du compte à partir de son historique d'équité (API Alpaca),
    sans état stocké localement."""
    clean = [e for e in equity_history if e]
    if len(clean) < 2:
        return {"halted": False, "reason": "", "drawdown_pct": 0.0}
    peak, current = max(clean), clean[-1]
    dd = (peak - current) / peak if peak > 0 else 0.0
    halted = dd >= config.MAX_DRAWDOWN_PCT
    reason = (
        f"Coupe-circuit de drawdown : -{dd:.1%} depuis le plus haut du compte "
        f"({peak:,.2f} -> {current:,.2f}). Nouvelles entrées suspendues."
        if halted else ""
    )
    return {"halted": halted, "reason": reason, "drawdown_pct": dd}
