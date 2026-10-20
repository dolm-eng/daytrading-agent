"""Répétition complète du job du matin, comme lancé par cron-job.org à 9h15 NY.

Horloge simulée + faux Alpaca qui applique les MÊMES refus que le vrai :
- plan gratuit : aucune donnée SIP des 15 dernières minutes (le bug du 15 au 20/10/2026) ;
- flux IEX : rien après l'heure actuelle (pas de bougie du futur).
Vérifie que le bot attend l'ouverture, charge les données sans erreur et
envoie ses ordres d'entrée AVANT la limite de 9h40 NY.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import config
import live_daytrade
import live_sip
import sip_strategy

NY = config.MARKET_TZ
DAY = pd.Timestamp("2026-10-21 09:30", tz=NY)  # mercredi
SYMBOLS = [f"T{i:02d}" for i in range(25)]


class Clock:
    def __init__(self, start):
        self.now = start.to_pydatetime().astimezone(timezone.utc)


def make_fake_datetime(clock):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)
    return FakeDT


class FakeData:
    def __init__(self, clock):
        self.clock = clock
        self.requests = []

    def get_stock_bars(self, req):
        from alpaca.data.enums import DataFeed

        now = self.clock.now
        start, end = (pd.Timestamp(t) if pd.Timestamp(t).tzinfo else pd.Timestamp(t, tz="UTC") for t in (req.start, req.end))
        self.requests.append((req.feed, end, now))
        if req.feed == DataFeed.SIP and end > pd.Timestamp(now) - pd.Timedelta(minutes=15):
            raise RuntimeError("subscription does not permit querying recent SIP data")
        end = min(end, pd.Timestamp(now))
        rows = []
        for day in pd.bdate_range(start.tz_convert(NY).normalize(), end.tz_convert(NY).normalize()):
            day = day.tz_localize(NY) if day.tzinfo is None else day
            for k in range(78):
                ts = day + pd.Timedelta(hours=9, minutes=30 + 5 * k)
                if ts < start or ts + pd.Timedelta(minutes=5) > end + pd.Timedelta(minutes=5) or ts > end:
                    continue
                if req.feed == DataFeed.IEX and ts + pd.Timedelta(minutes=5) > pd.Timestamp(now):
                    continue  # bougie pas encore terminée
                for i, s in enumerate(req.symbol_or_symbols):
                    rng = np.random.default_rng(abs(hash((s, ts.value))) % 2**32)
                    base = 50 + i
                    o = base + rng.normal(0, 0.3)
                    c = o + (0.4 if i % 2 else -0.4)
                    vol = 400_000.0
                    if ts == DAY:
                        vol *= 3  # aujourd'hui, 1re bougie très active -> sélection
                    rows.append((s, ts.tz_convert("UTC"), o, max(o, c) + 0.5, min(o, c) - 0.5, c, vol))
        if not rows:
            return SimpleNamespace(df=pd.DataFrame())
        df = pd.DataFrame(rows, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        return SimpleNamespace(df=df.set_index(["symbol", "timestamp"]))


class FakeTrading:
    def __init__(self, clock):
        self.clock = clock
        self.submitted = []

    def get_clock(self):
        now = self.clock.now
        is_open = DAY <= pd.Timestamp(now) < DAY + pd.Timedelta(hours=6, minutes=30)
        return SimpleNamespace(
            is_open=is_open, timestamp=now,
            next_open=DAY.to_pydatetime(),
            next_close=(DAY + pd.Timedelta(hours=6, minutes=30)).to_pydatetime(),
        )

    def get_portfolio_history(self, _req):
        return SimpleNamespace(equity=[100_000.0] * 20)

    def get_account(self):
        return SimpleNamespace(equity="100000", last_equity="100000")

    def get_all_positions(self):
        return []

    def get_orders(self, *_a, **_k):
        return []

    def get_asset(self, _s):
        return SimpleNamespace(shortable=True, easy_to_borrow=True)

    def submit_order(self, order_data):
        self.submitted.append((self.clock.now, order_data))
        return SimpleNamespace(status="accepted")


def rehearse(monkeypatch, start_ny: str):
    clock = Clock(pd.Timestamp(f"2026-10-21 {start_ny}", tz=NY))
    fake_dt = make_fake_datetime(clock)
    data_client, trading_client = FakeData(clock), FakeTrading(clock)

    monkeypatch.setattr(time, "sleep", lambda s: setattr(clock, "now", clock.now + timedelta(seconds=max(0, s))))
    monkeypatch.setattr(live_sip, "datetime", fake_dt)
    monkeypatch.setattr(live_daytrade, "datetime", fake_dt)
    monkeypatch.setattr(live_daytrade, "get_clients", lambda: (trading_client, data_client))
    monkeypatch.setattr(sip_strategy, "load_universe", lambda: list(SYMBOLS))
    monkeypatch.setattr(config, "STRATEGY", "stocks_in_play")

    args = SimpleNamespace(phase="morning", dry_run=False, once=False, replay=None)
    status = live_daytrade.run(args)
    return status, trading_client, data_client


def test_morning_job_started_at_915_sends_orders_before_cutoff(monkeypatch):
    status, trading, data = rehearse(monkeypatch, "09:15")
    assert status == "MATIN TERMINÉ"
    assert trading.submitted, "aucun ordre envoyé"
    cutoff = (DAY + pd.Timedelta(minutes=config.MORNING_MAX_DELAY_MINUTES)).to_pydatetime()
    sip_requests = [now for feed, _end, now in data.requests if str(getattr(feed, "value", feed)) == "sip"]
    assert sip_requests and max(sip_requests) < DAY.to_pydatetime(), "historique chargé avant l'ouverture"
    for when, _ in trading.submitted:
        assert when <= cutoff
        assert when >= (DAY + pd.Timedelta(minutes=5)).to_pydatetime()  # après la 1re bougie


def test_old_code_would_have_crashed(monkeypatch):
    """Contrôle du test lui-même : avec l'ancienne borne (9h29), le faux Alpaca refuse."""
    monkeypatch.setattr(live_sip, "sip_history_end", lambda session_open, now=None: session_open - timedelta(minutes=1))
    with pytest.raises(RuntimeError, match="recent SIP"):
        rehearse(monkeypatch, "09:15")


@pytest.mark.parametrize("start", ["09:00", "09:25", "09:31", "09:36"])
def test_morning_other_start_times(monkeypatch, start):
    status, trading, _ = rehearse(monkeypatch, start)
    assert status == "MATIN TERMINÉ"
    assert trading.submitted


def test_morning_started_too_late_sends_nothing(monkeypatch):
    status, trading, _ = rehearse(monkeypatch, "09:45")
    assert status == "MATIN ANNULÉ (RETARD)"
    assert not trading.submitted
