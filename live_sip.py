"""
Partie live de la stratégie « stocks in play » (voir sip_strategy.py).

Matin (9h35 NY) : scan des ~300 actions, sélection des 20 plus « en jeu »,
envoi d'ordres d'entrée STOP avec stop-loss attaché (ordre OTO). Ensuite
Alpaca gère tout seul : l'entrée se déclenche si le prix franchit le niveau,
le stop-loss protège la position. Le job du soir ferme tout à 15h50.

Mesure des frais réels : chaque exécution est comparée à son prix de
référence (niveau d'entrée, niveau du stop, dernier prix avant la clôture)
et enregistrée dans journal/executions.csv.

Limite connue : le plan gratuit d'Alpaca interdit les données complètes
(SIP) des 15 dernières minutes. La 1re bougie du jour vient donc du flux IEX
(une seule bourse), comparée à l'historique IEX pour que le volume relatif
reste cohérent. La sélection diffère donc un peu de celle du backtest (SIP).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config
import data
import sip_strategy as sip

NY = config.MARKET_TZ
JOURNAL = Path(__file__).resolve().parent / "journal" / "executions.csv"


def _bars(data_client, symbols, start, end, timeframe_minutes: int, feed: str) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    df = data_client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
        start=start, end=end, adjustment=Adjustment.SPLIT,
        feed=DataFeed.IEX if feed == "iex" else DataFeed.SIP,
    )).df
    if df.empty:
        return df
    return df.rename(columns={c.lower(): c for c in data.COLS})[data.COLS]


def daily_features(data_client, symbols, session_open: datetime) -> tuple[pd.DataFrame, list]:
    """ATR, volume moyen et clôture de la veille, calculés comme dans le
    backtest : à partir des bougies 5 min SIP des heures de marché des
    séances précédentes. Retourne aussi la liste des dates de ces séances."""
    df = _bars(data_client, symbols, session_open - timedelta(days=40), session_open - timedelta(minutes=1), 5, "sip")
    rows = []
    for sym, d in df.groupby(level=0):
        d = data.regular_hours(d.xs(sym, level=0))
        g = d.groupby(d.index.date)
        daily = pd.DataFrame({"High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
        daily["symbol"] = sym
        rows.append(daily)
    daily = pd.concat(rows)
    dates = sorted(set(daily.index))
    return sip.daily_features(daily), dates


def first_bars(data_client, symbols, day_open: pd.Timestamp) -> pd.DataFrame:
    """1re bougie 5 min (9h30-9h35) de la séance, flux IEX, une ligne par symbole."""
    df = _bars(data_client, symbols, day_open.to_pydatetime(), (day_open + pd.Timedelta(minutes=5)).to_pydatetime(), 5, "iex")
    if df.empty:
        return pd.DataFrame(columns=data.COLS)
    df = df.reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(NY)
    df = df[df["timestamp"] == day_open]
    return df.set_index("symbol")[data.COLS]


def first_volume_history(data_client, symbols, dates) -> pd.DataFrame:
    """Volume IEX de la 1re bougie des séances passées (lignes = dates)."""
    hist = {}
    for d in dates[-config.SIP_LOOKBACK - 2:]:
        day_open = pd.Timestamp(f"{d} {config.MARKET_OPEN}", tz=NY)
        fb = first_bars(data_client, symbols, day_open)
        hist[pd.Timestamp(d)] = fb["Volume"]
    return pd.DataFrame(hist).T


def scan(data_client, session_open: datetime, symbols=None):
    """Sélection du jour. Utilisable à 9h35 en live, ou sur une séance passée pour tester."""
    symbols = symbols or sip.load_universe()
    day_open = pd.Timestamp(session_open).tz_convert(NY)
    features, dates = daily_features(data_client, symbols, session_open)
    hist = first_volume_history(data_client, symbols, dates)
    today = first_bars(data_client, symbols, day_open)
    return sip.select(features, today, hist), len(features), len(today)


def submit_entry(trading_client, pick: sip.Pick, qty: int):
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import StopLossRequest, StopOrderRequest

    return trading_client.submit_order(order_data=StopOrderRequest(
        symbol=pick.symbol,
        qty=qty,
        side=OrderSide.BUY if pick.side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        stop_price=pick.level,
        order_class=OrderClass.OTO,
        stop_loss=StopLossRequest(stop_price=pick.stop),
        client_order_id=f"{config.ORDER_PREFIX}-{pick.symbol}-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}",
    ))


def morning(trading_client, data_client, state, args, say) -> None:
    """Scan à 9h35 NY puis envoi des ordres. Ne surveille rien ensuite :
    entrées et stops sont gérés par Alpaca."""
    import time

    from risk import check_account_circuit_breaker

    history = trading_client.get_portfolio_history(_portfolio_request())
    breaker = check_account_circuit_breaker(list(history.equity or []))
    if breaker["halted"]:
        say(f"[risque] {breaker['reason']}")
        return

    # préparation AVANT 9h35 (ATR, volumes, historique IEX) pour envoyer les ordres sans délai
    symbols = sip.load_universe()
    say(f"Préparation des données de {len(symbols)} actions...")
    features, dates = daily_features(data_client, symbols, state.session_open)
    hist = first_volume_history(data_client, symbols, dates)

    scan_at = pd.Timestamp(state.session_open) + pd.Timedelta(minutes=5, seconds=config.BAR_SETTLE_SECONDS)
    wait = (scan_at - pd.Timestamp.now(tz="UTC")).total_seconds()
    if wait > 0:
        say(f"Attente de la fin de la 1re bougie (9h35 NY) : {int(wait)} s...")
        time.sleep(wait)

    today = first_bars(data_client, symbols, pd.Timestamp(state.session_open).tz_convert(NY))
    picks = sip.select(features, today, hist)
    say(f"{len(features)} actions avec historique, {len(today)} avec une 1re bougie IEX -> {len(picks)} sélectionnées.")

    equity = float(trading_client.get_account().equity)
    held = {p.symbol for p in trading_client.get_all_positions()}
    sent = 0
    for p in picks:
        qty = sip.position_size(p, equity)
        sens = "ACHAT" if p.side == "buy" else "VENTE À DÉCOUVERT"
        line = (f"  {p.symbol:<5} volume x{p.relvol:4.1f} | {sens} si le prix passe {p.level:.2f} | "
                f"stop {p.stop:.2f} | {qty} actions (~{qty * p.level:,.0f} $)")
        if p.symbol in held:
            say(line + " -> déjà détenu, ignoré")
            continue
        if qty <= 0:
            say(line + " -> taille nulle, ignoré")
            continue
        if p.side == "sell":
            asset = trading_client.get_asset(p.symbol)
            if not (asset.shortable and asset.easy_to_borrow):
                say(line + " -> non empruntable pour la vente à découvert, ignoré")
                continue
        if args.dry_run:
            say(line + " (dry-run)")
            continue
        try:
            o = submit_entry(trading_client, p, qty)
            sent += 1
            say(line + f" -> ordre {o.status.value if hasattr(o.status, 'value') else o.status}")
        except Exception as exc:  # noqa: BLE001
            say(line + f" -> ERREUR {exc}")
    say(f"{sent} ordres d'entrée envoyés. Alpaca gère les déclenchements et les stops ; fermeture à 15h50 par le job du soir.")


def latest_prices(data_client, symbols) -> dict:
    """Dernier prix (flux IEX) juste avant de fermer : référence pour mesurer
    le coût des clôtures de fin de journée."""
    if not symbols:
        return {}
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestTradeRequest

    try:
        trades = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sorted(symbols), feed=DataFeed.IEX))
        return {s: float(t.price) for s, t in trades.items()}
    except Exception:  # noqa: BLE001
        return {}


def executions(trading_client, session_open: datetime, closed: dict, flatten_time: datetime) -> pd.DataFrame:
    """Toutes les exécutions du jour avec leur prix de référence et leur coût en bp."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = trading_client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.ALL, after=session_open - timedelta(minutes=1), limit=500, nested=True))
    rows = []

    def add(kind, o, ref):
        if o.filled_avg_price and float(o.filled_qty or 0) > 0 and ref:
            side = getattr(o.side, "value", o.side)
            fill = float(o.filled_avg_price)
            rows.append({"date": f"{pd.Timestamp(session_open):%Y-%m-%d}", "symbole": o.symbol, "type": kind,
                         "sens": side, "qty": float(o.filled_qty), "reference": float(ref), "execution": fill,
                         "cout_bp": round(sip.slippage_bps(side, float(ref), fill), 2)})

    for o in orders:
        cid = o.client_order_id or ""
        if cid.startswith(config.ORDER_PREFIX + "-"):
            add("entree", o, o.stop_price)
            for leg in o.legs or []:
                add("stop", leg, leg.stop_price)
        elif o.symbol in closed and o.submitted_at and o.submitted_at >= flatten_time - timedelta(minutes=1):
            add("cloture", o, closed[o.symbol][1])
    return pd.DataFrame(rows)


def record(df: pd.DataFrame) -> str:
    """Ajoute au journal et renvoie un résumé lisible."""
    if df.empty:
        return "Aucune exécution aujourd'hui."
    JOURNAL.parent.mkdir(exist_ok=True)
    df.to_csv(JOURNAL, mode="a", header=not JOURNAL.exists(), index=False)
    allj = pd.read_csv(JOURNAL)
    lines = ["Coût d'exécution (points de base, positif = moins bon que le prix visé) :"]
    for label, d in (("aujourd'hui", df), ("depuis le début", allj)):
        by = d.groupby("type")["cout_bp"].mean()
        parts = " | ".join(f"{k} {v:+.1f}" for k, v in by.items())
        lines.append(f"  {label:<16} moyenne {d['cout_bp'].mean():+.2f} bp sur {len(d)} exécutions ({parts})")
    lines.append(f"  Seuil de rentabilité du backtest : ~{config.SIP_BREAKEVEN_BPS} bp par exécution.")
    return "\n".join(lines)


def _portfolio_request():
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    return GetPortfolioHistoryRequest(period="3M", timeframe="1D")
