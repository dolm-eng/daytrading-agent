"""
Bot de DAY TRADING en paper trading sur Alpaca (argent fictif uniquement :
le client est créé avec paper=True, ce script ne peut pas toucher d'argent réel).

Contrairement à l'agent long terme (lancé une fois par jour), ce script
tourne PENDANT toute la séance américaine :
  1. Il attend l'ouverture (15h30 heure de Paris la plupart de l'année).
  2. Toutes les 5 minutes, à la clôture de chaque bougie, il évalue la
     stratégie (strategy.py, la même que le backtest) sur chaque symbole.
  3. Chaque entrée est un ordre BRACKET : achat/vente au marché + stop-loss +
     objectif attachés côté Alpaca. Même si ton PC plante, le stop reste actif.
  4. 10 minutes avant la clôture, il ferme TOUTES ses positions. Rien ne reste
     ouvert pendant la nuit.
  5. Il t'envoie un email récapitulatif de la journée.

Utilisation :
    python live_daytrade.py             # séance complète (à lancer vers 15h20 heure de Paris)
    python live_daytrade.py --once      # une seule évaluation maintenant, puis s'arrête
    python live_daytrade.py --dry-run   # n'envoie aucun ordre, affiche seulement les signaux

Le bot est SANS ÉTAT : s'il est relancé en cours de journée, il relit les
ordres et positions du jour chez Alpaca et reprend là où il en était.
"""

from __future__ import annotations

import argparse
import os
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config
import data
import indicators as ind
import notifier
import risk
import strategy

load_dotenv()

API_KEY = (os.getenv("ALPACA_API_KEY") or "").strip()  # strip : un copier-coller ajoute souvent un retour à la ligne
SECRET_KEY = (os.getenv("ALPACA_SECRET_KEY") or "").strip()
MAX_WAIT_FOR_OPEN = timedelta(hours=3)
NY = ZoneInfo(config.MARKET_TZ)

LOG: list[str] = []


def say(line: str = ""):
    text = f"[{datetime.now(NY):%H:%M:%S} NY] {line}" if line else ""
    print(text, flush=True)
    LOG.append(text)


# ---------------------------------------------------------------- Alpaca

def get_clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    if not API_KEY or not SECRET_KEY:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY manquantes. "
            "Copie .env.example en .env et renseigne tes clés PAPER Alpaca."
        )
    return (
        TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=True),
        StockHistoricalDataClient(api_key=API_KEY, secret_key=SECRET_KEY),
    )


def fetch_today_bars(data_client, session_open: datetime, now: datetime) -> dict[str, pd.DataFrame]:
    """Bougies 5 min TERMINÉES de la séance du jour (flux IEX, gratuit)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    req = StockBarsRequest(
        symbol_or_symbols=config.WATCHLIST,
        timeframe=TimeFrame(config.BAR_MINUTES, TimeFrameUnit.Minute),
        start=session_open,
        feed=DataFeed.IEX,
    )
    df = data_client.get_stock_bars(req).df
    out = {}
    if df.empty:
        return out
    bar = pd.Timedelta(minutes=config.BAR_MINUTES)
    for sym in df.index.get_level_values(0).unique():
        d = df.xs(sym, level=0).rename(columns={c.lower(): c for c in data.COLS})
        d = data.regular_hours(d)
        d = d[d.index + bar <= pd.Timestamp(now)]  # on ignore la bougie en cours
        if not d.empty:
            out[sym] = d
    return out


def bot_orders_today(trading_client, session_open: datetime) -> list:
    """Ordres passés par CE bot aujourd'hui (reconnus à leur préfixe)."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = trading_client.get_orders(
        GetOrdersRequest(status=QueryOrderStatus.ALL, after=session_open - timedelta(minutes=1), limit=500, nested=True)
    )
    return [o for o in orders if (o.client_order_id or "").startswith(config.ORDER_PREFIX + "-")]


def submit_bracket(trading_client, symbol: str, qty: int, sig: strategy.Signal):
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if sig.side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=sig.target_price),
        stop_loss=StopLossRequest(stop_price=sig.stop_price),
        client_order_id=f"{config.ORDER_PREFIX}-{symbol}-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}",
    )
    return trading_client.submit_order(order_data=req)


def flatten(trading_client, symbols: set[str], reason: str, dry_run: bool):
    """Annule les ordres ouverts et ferme les positions de CE bot uniquement."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    positions = {p.symbol: p for p in trading_client.get_all_positions()}
    targets = [s for s in symbols if s in positions]
    if not targets:
        say(f"[sortie] {reason} : aucune position du bot à fermer.")
        return
    for sym in targets:
        say(f"[sortie] {reason} : fermeture de {sym} ({positions[sym].qty} actions)")
        if dry_run:
            continue
        try:
            for o in trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym], nested=True)):
                trading_client.cancel_order_by_id(o.id)
            time.sleep(1)  # laisse Alpaca libérer les actions réservées par le stop/objectif
            trading_client.close_position(sym)
        except Exception as exc:  # noqa: BLE001
            say(f"  ERREUR à la fermeture de {sym} : {exc} -> vérifie le dashboard Alpaca !")


# ---------------------------------------------------------------- logique

class DayState:
    def __init__(self, session_open: datetime, session_close: datetime):
        self.session_open = session_open
        self.flatten_at = session_close - timedelta(minutes=config.FLATTEN_MINUTES_BEFORE_CLOSE)
        self.halted = False
        self.halt_reason = ""


def tick(trading_client, data_client, state: DayState, dry_run: bool):
    now = datetime.now(timezone.utc)
    account = trading_client.get_account()
    equity, day_start = float(account.equity), float(account.last_equity)
    orders = bot_orders_today(trading_client, state.session_open)
    bot_symbols = {o.symbol for o in orders}
    positions = {p.symbol: p for p in trading_client.get_all_positions()}
    bot_positions = {s for s in bot_symbols if s in positions}
    entries_today = [o for o in orders if getattr(o.status, "value", o.status) not in ("rejected", "canceled")]

    say(
        f"Équité {equity:,.2f} $ (jour : {equity - day_start:+,.2f} $) | "
        f"positions du bot : {sorted(bot_positions) or 'aucune'} | entrées aujourd'hui : {len(entries_today)}"
    )

    if not state.halted and risk.daily_loss_exceeded(day_start, equity):
        state.halted = True
        state.halt_reason = f"perte du jour > {config.MAX_DAILY_LOSS_PCT:.0%}"
        flatten(trading_client, bot_symbols, "coupe-circuit quotidien", dry_run)
    if state.halted:
        say(f"[risque] Trading arrêté pour aujourd'hui ({state.halt_reason}).")
        return

    if len(entries_today) >= config.MAX_TRADES_PER_DAY:
        say(f"[risque] {config.MAX_TRADES_PER_DAY} entrées atteintes aujourd'hui, plus de nouvelle entrée.")
        return

    bars = fetch_today_bars(data_client, state.session_open, now)
    for sym in config.WATCHLIST:
        if sym in bot_symbols:
            continue  # une seule entrée par symbole et par jour
        if sym in positions:
            say(f"  {sym}: déjà détenu hors de ce bot (autre stratégie ?) -> ignoré.")
            continue
        if len(bot_positions) >= config.MAX_OPEN_POSITIONS:
            say(f"[risque] {config.MAX_OPEN_POSITIONS} positions ouvertes, pas de nouvelle entrée.")
            break
        day = bars.get(sym)
        if day is None or day.empty:
            continue
        sig = strategy.evaluate_entry(ind.add_intraday_indicators(day))
        if sig is None:
            continue

        qty = risk.position_size(equity, sig.ref_price, sig.stop_price)
        side_fr = "ACHAT" if sig.side == "buy" else "VENTE À DÉCOUVERT"
        say(f"  SIGNAL {side_fr} {sym} x{qty} @~{sig.ref_price:.2f} | stop {sig.stop_price} | objectif {sig.target_price}")
        say(f"    raison : {sig.reason}")
        if qty <= 0:
            say("    taille calculée = 0 -> ignoré.")
            continue
        if sig.side == "sell":
            asset = trading_client.get_asset(sym)
            if not (asset.shortable and asset.easy_to_borrow):
                say("    action non empruntable pour la vente à découvert -> ignoré.")
                continue
        if dry_run:
            say("    (dry-run : aucun ordre envoyé)")
            continue
        try:
            order = submit_bracket(trading_client, sym, qty, sig)
            say(f"    ordre bracket envoyé : id={order.id} statut={order.status}")
            bot_symbols.add(sym)
            bot_positions.add(sym)
        except Exception as exc:  # noqa: BLE001
            say(f"    ERREUR d'envoi de l'ordre : {exc}")


def day_summary(trading_client, state: DayState) -> str:
    """P&L réalisé par symbole à partir des exécutions du jour."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    bot_symbols = {o.symbol for o in bot_orders_today(trading_client, state.session_open)}
    if not bot_symbols:
        return "Aucun trade aujourd'hui."
    filled = trading_client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.CLOSED, after=state.session_open - timedelta(minutes=1),
        symbols=sorted(bot_symbols), limit=500,
    ))
    lines, total = [], 0.0
    for sym in sorted(bot_symbols):
        cash = 0.0
        for o in filled:
            if o.symbol != sym or not o.filled_avg_price:
                continue
            notional = float(o.filled_qty) * float(o.filled_avg_price)
            cash += notional if getattr(o.side, "value", o.side) == "sell" else -notional
        total += cash
        lines.append(f"  {sym:<6} {cash:+10,.2f} $")
    return "P&L réalisé par symbole :\n" + "\n".join(lines) + f"\n  {'TOTAL':<6} {total:+10,.2f} $"


def sleep_until_next_bar(limit: datetime):
    now = datetime.now(timezone.utc)
    step = config.BAR_MINUTES * 60
    next_bar = datetime.fromtimestamp((now.timestamp() // step + 1) * step, tz=timezone.utc)
    wake = min(next_bar + timedelta(seconds=config.BAR_SETTLE_SECONDS), limit)
    time.sleep(max(1.0, (wake - now).total_seconds()))


def open_session(trading_client, args) -> DayState | None:
    """Attend l'ouverture si besoin. Retourne None si pas de séance aujourd'hui."""
    clock = trading_client.get_clock()
    if not clock.is_open:
        wait = clock.next_open - clock.timestamp
        if wait > MAX_WAIT_FOR_OPEN or args.once:
            say(f"Marché fermé. Prochaine ouverture : {clock.next_open.astimezone(NY):%Y-%m-%d %H:%M} NY.")
            return None
        say(f"Marché fermé, attente de l'ouverture ({int(wait.total_seconds() // 60)} min)...")
        time.sleep(wait.total_seconds() + 5)
        clock = trading_client.get_clock()

    session_close = clock.next_close
    session_open = pd.Timestamp(session_close).tz_convert(config.MARKET_TZ).normalize() + pd.Timedelta(config.MARKET_OPEN + ":00")
    state = DayState(session_open.to_pydatetime(), session_close)
    say(f"Séance ouverte. Fermeture forcée des positions à {state.flatten_at.astimezone(NY):%H:%M} NY.")
    return state


def trade_until(trading_client, data_client, state: DayState, end: datetime, args) -> None:
    history = trading_client.get_portfolio_history(_portfolio_request())
    breaker = risk.check_account_circuit_breaker(list(history.equity or []))
    if breaker["halted"]:
        state.halted, state.halt_reason = True, breaker["reason"]
        say(f"[risque] {breaker['reason']}")

    while datetime.now(timezone.utc) < end:
        try:
            tick(trading_client, data_client, state, args.dry_run)
        except Exception as exc:  # noqa: BLE001
            say(f"ERREUR pendant l'évaluation (on continue) : {exc!r}")
        if args.once:
            return
        sleep_until_next_bar(end)


def close_day(trading_client, state: DayState, args) -> str:
    wait = (state.flatten_at - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        say(f"Attente de l'heure de fermeture ({int(wait // 60)} min)...")
        time.sleep(wait)
    bot_symbols = {o.symbol for o in bot_orders_today(trading_client, state.session_open)}
    flatten(trading_client, bot_symbols, "fin de journée", args.dry_run)
    time.sleep(5)
    say("")
    say(day_summary(trading_client, state))
    account = trading_client.get_account()
    equity, day_pnl = float(account.equity), float(account.equity) - float(account.last_equity)
    say(f"Équité finale : {equity:,.2f} $ (jour : {day_pnl:+,.2f} $)")
    if not args.dry_run:
        write_journal(state, equity, day_pnl, sorted(bot_symbols))
    return "ARRÊT COUPE-CIRCUIT" if state.halted else "SÉANCE TERMINÉE"


def write_journal(state: DayState, equity: float, day_pnl: float, symbols: list[str]) -> None:
    """Une ligne par séance dans journal/journal.csv (commité par GitHub Actions)."""
    path = Path(__file__).resolve().parent / "journal" / "journal.csv"
    path.parent.mkdir(exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write("date,equite,pnl_jour,symboles_trades\n")
        f.write(f"{state.session_open:%Y-%m-%d},{equity:.2f},{day_pnl:.2f},{' '.join(symbols)}\n")


def run(args) -> str:
    """Phases :
    - all     : toute la séance d'un coup (PC allumé ou serveur)
    - morning : ouverture -> fin des entrées (11h30 NY). Les positions restent
                protégées par leurs stops/objectifs chez Alpaca.
    - close   : attend 15h50 NY, ferme tout, envoie le bilan.
    (GitHub Actions limite un job à 6 h, d'où le découpage matin / soir.)
    """
    trading_client, data_client = get_clients()
    state = open_session(trading_client, args)
    if state is None:
        if args.phase == "close":
            # job du soir lancé trop tard (après la clôture) ou jour férié partiel
            leftovers = [p.symbol for p in trading_client.get_all_positions() if p.symbol in config.WATCHLIST]
            if leftovers:
                say(f"!! ALERTE : positions encore ouvertes après la clôture : {leftovers}. "
                    f"Vérifie le dashboard Alpaca (le bot ne peut plus fermer avant la prochaine ouverture).")
                return "ALERTE POSITIONS OUVERTES"
        return "MARCHÉ FERMÉ"

    if args.phase == "close":
        return close_day(trading_client, state, args)

    end = state.flatten_at
    if args.phase == "morning":
        entry_end = pd.Timestamp(state.session_open).tz_convert(config.MARKET_TZ).normalize() + pd.Timedelta(config.ENTRY_CUTOFF + ":00")
        end = min(end, entry_end.to_pydatetime() + timedelta(minutes=config.BAR_MINUTES))
    trade_until(trading_client, data_client, state, end, args)
    if args.once:
        return "TEST --once"

    half_day = state.flatten_at - datetime.now(timezone.utc) <= timedelta(hours=2)
    if args.phase == "all" or half_day:
        if half_day and args.phase == "morning":
            say("Séance écourtée (jour férié partiel) : fermeture gérée par le job du matin.")
        return close_day(trading_client, state, args)
    say("Fin de la période d'entrées. Positions protégées par leurs stops ; fermeture par le job du soir.")
    return "MATIN TERMINÉ"


def _portfolio_request():
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    return GetPortfolioHistoryRequest(period="3M", timeframe="1D")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="une seule évaluation puis arrêt (test)")
    p.add_argument("--dry-run", action="store_true", help="n'envoie aucun ordre")
    p.add_argument("--phase", choices=["all", "morning", "close"], default="all")
    args = p.parse_args()

    today = datetime.now(timezone.utc).date().isoformat()
    try:
        status = run(args)
    except Exception as exc:  # noqa: BLE001
        say(f"Le bot a planté : {exc!r}")
        notifier.send_email(f"[Day Trading Bot] {today} — ERREUR", "\n".join(LOG))
        raise
    if not args.once and status != "MATIN TERMINÉ":
        notifier.send_email(f"[Day Trading Bot] {today} — {status}", "\n".join(LOG))


if __name__ == "__main__":
    main()
