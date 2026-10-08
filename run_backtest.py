"""
Lance un backtest de la stratégie intraday.

    python run_backtest.py                    # yfinance, ~60 derniers jours (sans clé)
    python run_backtest.py --alpaca --days 730   # Alpaca, 2 ans (clés paper dans .env)
    python run_backtest.py --synthetic        # données simulées (test du code uniquement)
    python run_backtest.py --plot --csv       # + courbe d'équité (PNG) + liste des trades (CSV)
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

import backtest
import config
import data


def load_market_data(args) -> dict:
    out = {}
    for i, sym in enumerate(config.WATCHLIST):
        try:
            if args.synthetic:
                out[sym] = data.generate_synthetic_intraday(n_days=args.days, start_price=100 + 50 * i, seed=i)
            elif args.alpaca:
                out[sym] = data.fetch_alpaca(sym, args.days, os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))
            else:
                out[sym] = data.fetch_yfinance(sym)
            print(f"[data] {sym}: {len(out[sym])} bougies, {len(data.split_sessions(out[sym]))} séances")
        except Exception as exc:  # noqa: BLE001
            print(f"[data] {sym} ignoré : {exc}")
    return out


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--synthetic", action="store_true", help="données simulées (aucune valeur prédictive)")
    p.add_argument("--alpaca", action="store_true", help="historique Alpaca (nécessite .env)")
    p.add_argument("--days", type=int, default=120, help="jours d'historique (--alpaca / --synthetic)")
    p.add_argument("--plot", action="store_true", help="enregistre equity_curve.png")
    p.add_argument("--csv", action="store_true", help="enregistre trades.csv")
    args = p.parse_args()

    if args.synthetic:
        print("ATTENTION : données SIMULÉES -> ce résultat ne dit rien sur le vrai marché.\n")
    market = load_market_data(args)
    if not market:
        raise SystemExit("Aucune donnée chargée.")

    result = backtest.run_backtest(market)
    s = backtest.summarize(result)

    print("\n" + "=" * 50)
    print("RÉSULTATS DU BACKTEST (day trading, ORB)")
    print("=" * 50)
    if not s.get("trades"):
        print("Aucun trade sur la période.")
        return
    print(f"Séances simulées      : {s['jours']}")
    print(f"Nombre de trades      : {s['trades']}")
    print(f"Rendement total       : {s['rendement_total']:+.2%}")
    print(f"Taux de réussite      : {s['win_rate']:.1%}")
    print(f"Gain moyen / perte moy: {s['gain_moyen']:+,.0f} $ / {s['perte_moyenne']:+,.0f} $")
    print(f"Profit factor         : {s['profit_factor']:.2f}   (< 1 = la stratégie perd de l'argent)")
    print(f"R moyen par trade     : {s['R_moyen']:+.2f}   (gain moyen exprimé en 'unités de risque')")
    print(f"Sharpe (annualisé)    : {s['sharpe']:.2f}")
    print(f"Max drawdown          : -{s['max_drawdown']:.2%}")
    print(f"Jours positifs        : {s['jours_positifs']:.1%}   (=> {1 - s['jours_positifs']:.0%} des jours sont nuls ou perdants)")
    if s["coupe_circuit_global"]:
        print(f"!! Coupe-circuit global déclenché le {s['coupe_circuit_global']}")

    trades = backtest.trades_frame(result)
    print("\nPar symbole :")
    print(trades.groupby("symbole")["pnl"].agg(["count", "sum", "mean"]).round(2).to_string())
    print("\nRaisons de sortie :")
    print(trades["raison_sortie"].value_counts().to_string())

    if args.csv:
        trades.to_csv("trades.csv", index=False)
        print("\n-> trades.csv enregistré")
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ax = result.daily_equity.plot(figsize=(10, 5), title="Équité en fin de journée (backtest day trading)")
        ax.set_ylabel("$")
        plt.tight_layout()
        plt.savefig("equity_curve.png", dpi=120)
        print("-> equity_curve.png enregistré")


if __name__ == "__main__":
    main()
