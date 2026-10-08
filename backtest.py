"""
Moteur de backtest intraday multi-actifs, capital partagé.

Règles de réalisme (pour ne pas se mentir) :
- Une décision est prise à la CLÔTURE d'une bougie 5 min, avec uniquement les
  bougies déjà terminées. L'ordre est exécuté à l'OUVERTURE de la bougie
  suivante, avec slippage.
- Stop-loss et objectif sont vérifiés sur le plus haut / plus bas de chaque
  bougie. Si les deux sont touchés dans la même bougie, on suppose que le
  STOP a été touché en premier (hypothèse pessimiste).
- Si le prix ouvre déjà au-delà du stop (trou de cotation), on sort au prix
  d'ouverture, pas au stop.
- Tout est fermé FLATTEN_MINUTES_BEFORE_CLOSE avant la clôture.
- Coupe-circuit quotidien et global appliqués comme en live.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
import data
import indicators as ind
import risk
import strategy

BAR = pd.Timedelta(minutes=config.BAR_MINUTES)


@dataclass
class Trade:
    symbol: str
    side: str
    qty: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""

    @property
    def pnl(self) -> float:
        sign = 1 if self.side == "buy" else -1
        return sign * (self.exit_price - self.entry_price) * self.qty - 2 * config.COMMISSION_PER_TRADE

    @property
    def r_multiple(self) -> float:
        risk_per_share = abs(self.entry_price - self.stop_price)
        return self.pnl / (risk_per_share * self.qty) if risk_per_share > 0 and self.qty else 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    daily_equity: pd.Series = None
    halted_on: object = None


def _close_trade(trade: Trade, time, price: float, reason: str) -> Trade:
    trade.exit_time, trade.exit_price, trade.exit_reason = time, price, reason
    return trade


def _check_exit(trade: Trade, bar) -> tuple[float, str] | None:
    o, h, l = float(bar["Open"]), float(bar["High"]), float(bar["Low"])
    if trade.side == "buy":
        if o <= trade.stop_price:
            return risk.apply_slippage(o, "sell"), "stop (gap)"
        if l <= trade.stop_price:
            return risk.apply_slippage(trade.stop_price, "sell"), "stop"
        if o >= trade.target_price:
            return o, "objectif (gap)"
        if h >= trade.target_price:
            return trade.target_price, "objectif"
    else:
        if o >= trade.stop_price:
            return risk.apply_slippage(o, "buy"), "stop (gap)"
        if h >= trade.stop_price:
            return risk.apply_slippage(trade.stop_price, "buy"), "stop"
        if o <= trade.target_price:
            return o, "objectif (gap)"
        if l <= trade.target_price:
            return trade.target_price, "objectif"
    return None


def run_backtest(market_data: dict[str, pd.DataFrame], initial_capital: float = config.INITIAL_CAPITAL) -> BacktestResult:
    sessions = {sym: data.split_sessions(df) for sym, df in market_data.items()}
    all_dates = sorted({d for s in sessions.values() for d in s})

    equity = initial_capital
    peak = initial_capital
    result = BacktestResult()
    daily_points = {}
    global_halt = False

    for date in all_dates:
        days = {
            sym: ind.add_intraday_indicators(s[date])
            for sym, s in sessions.items() if date in s
        }
        if not days:
            continue
        session_close = max(df.index[-1] for df in days.values()) + BAR
        flatten_at = session_close - pd.Timedelta(minutes=config.FLATTEN_MINUTES_BEFORE_CLOSE)
        timeline = sorted({t for df in days.values() for t in df.index})

        day_start_equity = equity
        open_trades: dict[str, Trade] = {}
        pending: dict[str, tuple[strategy.Signal, int]] = {}
        traded_today: set[str] = set()
        day_halted = global_halt
        last_close: dict[str, float] = {}

        def close_all(time, reason):
            nonlocal equity
            for sym, tr in list(open_trades.items()):
                exit_side = "sell" if tr.side == "buy" else "buy"
                _close_trade(tr, time, risk.apply_slippage(last_close[sym], exit_side), reason)
                equity += tr.pnl
                result.trades.append(tr)
                del open_trades[sym]
            pending.clear()

        for t in timeline:
            bar_end = t + BAR
            if bar_end > flatten_at:
                break

            for sym, df in days.items():
                if t not in df.index:
                    continue
                bar = df.loc[t]
                if sym in pending:
                    sig, qty = pending.pop(sym)
                    fill = risk.apply_slippage(float(bar["Open"]), sig.side)
                    open_trades[sym] = Trade(sym, sig.side, qty, t, fill, sig.stop_price, sig.target_price)
                if sym in open_trades:
                    hit = _check_exit(open_trades[sym], bar)
                    if hit:
                        tr = _close_trade(open_trades.pop(sym), t, hit[0], hit[1])
                        equity += tr.pnl
                        result.trades.append(tr)
                last_close[sym] = float(bar["Close"])

            # coupe-circuit quotidien (pertes réalisées + latentes)
            unrealized = sum(
                (1 if tr.side == "buy" else -1) * (last_close[s] - tr.entry_price) * tr.qty
                for s, tr in open_trades.items()
            )
            if not day_halted and risk.daily_loss_exceeded(day_start_equity, equity + unrealized):
                close_all(bar_end, "coupe-circuit quotidien")
                day_halted = True

            if bar_end == flatten_at:
                break
            if day_halted:
                continue

            for sym, df in days.items():
                if t not in df.index or sym in traded_today or sym in open_trades:
                    continue
                if len(open_trades) + len(pending) >= config.MAX_OPEN_POSITIONS:
                    break
                if len(traded_today) >= config.MAX_TRADES_PER_DAY:
                    break
                k = df.index.get_loc(t)
                sig = strategy.evaluate_entry(df.iloc[: k + 1])
                if sig is None:
                    continue
                qty = risk.position_size(day_start_equity, sig.ref_price, sig.stop_price)
                if qty > 0:
                    pending[sym] = (sig, qty)
                    traded_today.add(sym)

        close_all(flatten_at, "clôture de fin de journée")
        daily_points[pd.Timestamp(date)] = equity

        peak = max(peak, equity)
        if not global_halt and (peak - equity) / peak >= config.MAX_DRAWDOWN_PCT:
            global_halt = True
            result.halted_on = date

    result.daily_equity = pd.Series(daily_points, name="equity")
    return result


def summarize(result: BacktestResult, initial_capital: float = config.INITIAL_CAPITAL) -> dict:
    trades = result.trades
    eq = result.daily_equity
    if eq is None or eq.empty:
        return {"trades": 0}
    pnls = np.array([t.pnl for t in trades]) if trades else np.array([])
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    daily_ret = eq.pct_change().fillna(eq.iloc[0] / initial_capital - 1)
    curve = pd.concat([pd.Series([initial_capital]), eq.reset_index(drop=True)])
    max_dd = float(((curve.cummax() - curve) / curve.cummax()).max())
    return {
        "jours": len(eq),
        "trades": len(trades),
        "rendement_total": eq.iloc[-1] / initial_capital - 1,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "gain_moyen": wins.mean() if len(wins) else 0.0,
        "perte_moyenne": losses.mean() if len(losses) else 0.0,
        "profit_factor": wins.sum() / -losses.sum() if len(losses) else float("inf"),
        "R_moyen": float(np.mean([t.r_multiple for t in trades])) if trades else 0.0,
        "sharpe": float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0,
        "max_drawdown": max_dd,
        "jours_positifs": float((daily_ret > 0).mean()),
        "coupe_circuit_global": result.halted_on,
    }


def trades_frame(result: BacktestResult) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "symbole": t.symbol, "sens": "long" if t.side == "buy" else "short", "qty": t.qty,
            "entree": t.entry_time, "prix_entree": round(t.entry_price, 2),
            "sortie": t.exit_time, "prix_sortie": round(t.exit_price, 2),
            "raison_sortie": t.exit_reason, "pnl": round(t.pnl, 2), "R": round(t.r_multiple, 2),
        }
        for t in result.trades
    ])
