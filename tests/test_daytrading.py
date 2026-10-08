"""Tests sans réseau : python -m pytest -q"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest  # noqa: E402
import config  # noqa: E402
import data  # noqa: E402
import indicators as ind  # noqa: E402
import live_daytrade  # noqa: E402
import risk  # noqa: E402
import strategy  # noqa: E402


def make_session(closes, volumes=None, date="2026-03-02"):
    idx = pd.date_range(pd.Timestamp(f"{date} 09:30", tz=config.MARKET_TZ), periods=len(closes), freq="5min")
    closes = pd.Series(closes, index=idx, dtype=float)
    opens = closes.shift(1).fillna(closes.iloc[0])
    return pd.DataFrame({
        "Open": opens,
        "High": pd.concat([opens, closes], axis=1).max(axis=1) + 0.05,
        "Low": pd.concat([opens, closes], axis=1).min(axis=1) - 0.05,
        "Close": closes,
        "Volume": volumes if volumes is not None else [1000] * len(closes),
    }, index=idx)


def breakout_session(direction=1):
    # range d'ouverture ~[99.45, 100.55], puis calme, puis cassure avec gros volume
    closes = [100, 100.5, 99.5] + [100.0] * 5 + [100 + direction * 1.0]
    vols = [1000] * 8 + [5000]
    return make_session(closes, vols)


def test_long_breakout_signal():
    sig = strategy.evaluate_entry(ind.add_intraday_indicators(breakout_session(+1)))
    assert sig is not None and sig.side == "buy"
    assert sig.stop_price < sig.ref_price < sig.target_price
    assert sig.target_price - sig.ref_price == pytest.approx(
        config.REWARD_RISK_MULTIPLE * (sig.ref_price - sig.stop_price), abs=0.02)


def test_short_breakout_signal():
    sig = strategy.evaluate_entry(ind.add_intraday_indicators(breakout_session(-1)))
    assert sig is not None and sig.side == "sell"
    assert sig.target_price < sig.ref_price < sig.stop_price


def test_no_signal_without_volume():
    s = breakout_session(+1)
    s.iloc[-1, s.columns.get_loc("Volume")] = 1000
    assert strategy.evaluate_entry(ind.add_intraday_indicators(s)) is None


def test_no_signal_during_opening_range_or_after_cutoff():
    s = ind.add_intraday_indicators(breakout_session(+1))
    assert strategy.evaluate_entry(s.iloc[:3]) is None  # range pas encore terminé
    late = make_session([100, 100.5, 99.5] + [100.0] * 30 + [101.0], [1000] * 33 + [5000])
    assert strategy.evaluate_entry(ind.add_intraday_indicators(late)) is None  # après 11h30


def test_position_size_respects_risk_and_allocation():
    qty = risk.position_size(100_000, 100, 99)  # 500$ de risque / 1$ par action
    assert qty == 250
    assert risk.position_size(100_000, 100, 99.99) == 250  # plafonné à 25% du capital


def test_random_walk_is_not_profitable():
    """Sans tendance exploitable, un backtest honnête ne doit pas gagner."""
    market = {s: data.generate_synthetic_intraday(150, 100 + 30 * i, seed=50 + i, trend_strength=0.0)
              for i, s in enumerate(config.WATCHLIST)}
    s = backtest.summarize(backtest.run_backtest(market))
    assert s["trades"] > 50
    assert s["rendement_total"] < 0.01


def test_backtest_always_flat_at_end_of_day():
    market = {s: data.generate_synthetic_intraday(40, 100, seed=i) for i, s in enumerate(config.WATCHLIST[:3])}
    res = backtest.run_backtest(market)
    for t in res.trades:
        assert t.exit_time.date() == t.entry_time.date()
        assert t.exit_time.strftime("%H:%M") <= "15:50"


# ---------------------------------------------------------------- bot live

class FakeTrading:
    def __init__(self, equity=100_000.0, last_equity=100_000.0, positions=()):
        self.account = SimpleNamespace(equity=str(equity), last_equity=str(last_equity))
        self.positions = list(positions)
        self.submitted = []
        self.closed = []

    def get_account(self):
        return self.account

    def get_orders(self, req):
        return []

    def get_all_positions(self):
        return self.positions

    def get_asset(self, sym):
        return SimpleNamespace(shortable=True, easy_to_borrow=True)

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(id="x", status="accepted")

    def cancel_order_by_id(self, _id):
        pass

    def close_position(self, sym):
        self.closed.append(sym)


def _state():
    s = breakout_session(+1)
    open_ = s.index[0].to_pydatetime()
    return live_daytrade.DayState(open_, open_ + pd.Timedelta(hours=6, minutes=30))


def test_live_tick_sends_bracket_order(monkeypatch):
    monkeypatch.setattr(live_daytrade, "fetch_today_bars", lambda *a: {"SPY": breakout_session(+1)})
    tc = FakeTrading()
    live_daytrade.tick(tc, None, _state(), dry_run=False)
    assert len(tc.submitted) == 1
    o = tc.submitted[0]
    assert o.symbol == "SPY" and o.qty > 0
    assert o.order_class.value == "bracket"
    assert o.client_order_id.startswith(config.ORDER_PREFIX + "-")
    assert o.stop_loss.stop_price < o.take_profit.limit_price


def test_live_tick_dry_run_sends_nothing(monkeypatch):
    monkeypatch.setattr(live_daytrade, "fetch_today_bars", lambda *a: {"SPY": breakout_session(+1)})
    tc = FakeTrading()
    live_daytrade.tick(tc, None, _state(), dry_run=True)
    assert tc.submitted == []


def test_live_tick_skips_symbol_held_by_other_bot(monkeypatch):
    monkeypatch.setattr(live_daytrade, "fetch_today_bars", lambda *a: {"SPY": breakout_session(+1)})
    tc = FakeTrading(positions=[SimpleNamespace(symbol="SPY", qty="10")])
    live_daytrade.tick(tc, None, _state(), dry_run=False)
    assert tc.submitted == []


def test_live_daily_loss_halts_trading(monkeypatch):
    monkeypatch.setattr(live_daytrade, "fetch_today_bars", lambda *a: {"SPY": breakout_session(+1)})
    tc = FakeTrading(equity=97_000, last_equity=100_000)
    state = _state()
    live_daytrade.tick(tc, None, state, dry_run=False)
    assert state.halted and tc.submitted == []
