"""
Assistant pour enregistrer tes clés PAPER Alpaca dans .env sans éditer le
fichier à la main. Lance : python configurer_cles.py (ou double-clique sur
configurer_cles.bat). Les clés ne sont jamais affichées à l'écran.
"""

from __future__ import annotations

import shutil
from getpass import getpass
from pathlib import Path

from dotenv import set_key

ENV = Path(__file__).resolve().parent / ".env"


def ask(label: str) -> str:
    while True:
        value = getpass(f"{label} (colle avec clic droit ou Ctrl+V, rien ne s'affiche, puis Entrée) : ").strip().strip('"')
        if len(value) >= 10 and " " not in value:
            return value
        print("  -> ça ne ressemble pas à une clé, réessaie.")


def main():
    if not ENV.exists():
        shutil.copy(ENV.with_name(".env.example"), ENV)
    print("=== Configuration des clés PAPER Alpaca (compte day trading) ===\n")
    key = ask("API Key (commence souvent par PK)")
    secret = ask("Secret Key")
    set_key(str(ENV), "ALPACA_API_KEY", key, quote_mode="never")
    set_key(str(ENV), "ALPACA_SECRET_KEY", secret, quote_mode="never")
    print(f"\nClés enregistrées dans {ENV} (Key commençant par {key[:2]}..., {len(key)} caractères).")

    try:
        from alpaca.trading.client import TradingClient

        acct = TradingClient(api_key=key, secret_key=secret, paper=True).get_account()
        print(f"Connexion OK au compte paper {acct.account_number} — équité : {float(acct.equity):,.2f} $")
    except Exception as exc:  # noqa: BLE001
        print(f"Connexion à Alpaca ÉCHOUÉE : {exc}\n-> vérifie que ce sont bien des clés PAPER et relance ce script.")


if __name__ == "__main__":
    main()
    input("\nAppuie sur Entrée pour fermer.")
