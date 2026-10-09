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


# ---------------------------------------------------------------- stocks in play

import sip_strategy as sip  # noqa: E402
import live_sip  # noqa: E402


def _features(syms, atr=2.0, avg_vol=5e6, prev_close=100.0):
    return pd.DataFrame({"atr": atr, "avg_vol": avg_vol, "prev_close": prev_close}, index=syms)


def test_sip_selects_top_relvol_and_direction():
    syms = [f"S{i}" for i in range(30)]
    hist = pd.DataFrame(1000.0, index=pd.date_range("2026-09-01", periods=14), columns=syms)
    first = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0,
        "Close": [100.5 if i % 2 else 99.5 for i in range(30)],   # impairs haussiers, pairs baissiers
        "Volume": [1000.0 * (1 + i) for i in range(30)],          # S29 = volume relatif le plus fort
    }, index=syms)
    picks = sip.select(_features(syms), first, hist)
    assert len(picks) == config.SIP_TOP_N
    assert picks[0].symbol == "S29" and picks[0].side == "buy" and picks[0].level == 101.0
    assert picks[0].stop == pytest.approx(101.0 - config.SIP_STOP_ATR * 2.0)
    short = next(p for p in picks if p.symbol == "S28")
    assert short.side == "sell" and short.level == 99.0 and short.stop > short.level


def test_sip_filters():
    syms = ["OK", "CHEAP", "ILLIQ", "CALM", "QUIET", "DOJI"]
    feats = _features(syms)
    feats.loc["CHEAP", "prev_close"] = 3
    feats.loc["ILLIQ", "avg_vol"] = 5e5
    feats.loc["CALM", "atr"] = 0.3
    hist = pd.DataFrame(1000.0, index=pd.date_range("2026-09-01", periods=14), columns=syms)
    first = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 3000.0}, index=syms)
    first.loc["QUIET", "Volume"] = 500.0      # volume relatif < 1
    first.loc["DOJI", "Close"] = 100.0        # bougie neutre
    assert [p.symbol for p in sip.select(feats, first, hist)] == ["OK"]


def test_sip_position_size_caps_exposure():
    p = sip.Pick("X", "buy", 100.0, 99.8, 2.0, 3.0)   # stop très proche -> le plafond 1/20 s'applique
    assert sip.position_size(p, 100_000) == 50       # 5 000 $ / 100 $


def test_sip_slippage_sign():
    assert sip.slippage_bps("buy", 100, 100.05) == pytest.approx(5)    # acheté plus cher = coût
    assert sip.slippage_bps("sell", 100, 99.95) == pytest.approx(5)    # vendu moins cher = coût
    assert sip.slippage_bps("sell", 100, 100.05) == pytest.approx(-5)  # mieux que prévu


class FakeSipTrading(FakeTrading):
    def get_portfolio_history(self, req):
        return SimpleNamespace(equity=[100_000, 100_000])


def test_sip_morning_sends_oto_stop_orders(monkeypatch):
    picks = [sip.Pick("AAA", "buy", 50.0, 49.8, 2.0, 4.0), sip.Pick("BBB", "sell", 80.0, 80.3, 3.0, 3.0)]
    monkeypatch.setattr(live_sip, "daily_features", lambda dc, syms, so: (pd.DataFrame(), []))
    monkeypatch.setattr(live_sip, "first_volume_history", lambda dc, syms, dates: pd.DataFrame())
    monkeypatch.setattr(live_sip, "first_bars", lambda dc, syms, day_open: pd.DataFrame())
    monkeypatch.setattr(sip, "select", lambda f, t, h: picks)
    tc = FakeSipTrading()
    state = _state()
    state.session_open = pd.Timestamp.now(tz="UTC").to_pydatetime() - pd.Timedelta(minutes=10)
    live_sip.morning(tc, None, state, SimpleNamespace(dry_run=False), lambda s: None)
    assert len(tc.submitted) == 2
    buy, sell = tc.submitted
    assert buy.type.value == "stop" and buy.order_class.value == "oto" and buy.stop_price == 50.0
    assert buy.stop_loss.stop_price == 49.8 and buy.time_in_force.value == "day"
    assert sell.side.value == "sell" and sell.stop_loss.stop_price == 80.3
    assert buy.client_order_id.startswith(config.ORDER_PREFIX + "-")


def test_flatten_cancels_unfilled_entries_too(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    cancelled = []

    class TC(FakeTrading):
        def get_orders(self, req):
            return [SimpleNamespace(id="entry-not-triggered")]

        def cancel_order_by_id(self, oid):
            cancelled.append(oid)

    tc = TC()  # aucune position : l'ordre d'entrée non déclenché doit quand même être annulé
    live_daytrade.flatten(tc, {"AAA"}, "test", dry_run=False)
    assert cancelled == ["entry-not-triggered"]
