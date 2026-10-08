"""
Envoi d'un email récapitulatif à la fin de chaque séance du bot de day trading (paper trading).

Utilise SMTP standard (compatible Gmail avec un "mot de passe d'application",
Outlook, ou n'importe quel fournisseur SMTP). Toute erreur d'envoi est
capturée et affichée dans les logs plutôt que de faire planter le script —
un email qui ne part pas ne doit jamais empêcher le bot de finir sa run
correctement (ou de fermer une position à temps).
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

def _clean_env(name: str, default: str | None = None) -> str | None:
    """Comme os.getenv, mais traite une variable vide ('') comme absente --
    utile car GitHub Actions transmet un secret non défini comme une chaîne
    vide plutôt que de ne pas la définir du tout, ce qui casserait int()
    ci-dessous si on utilisait juste os.getenv(name, default)."""
    value = (os.getenv(name) or "").strip()
    return value if value else default


def send_email(subject: str, body: str) -> bool:
    """Envoie un email texte simple. Retourne True si l'envoi a réussi."""
    # lu au moment de l'envoi (et pas à l'import) pour tenir compte du .env
    SMTP_HOST = _clean_env("SMTP_HOST", "smtp.gmail.com")
    try:
        SMTP_PORT = int(_clean_env("SMTP_PORT", "587"))
    except ValueError:
        SMTP_PORT = 587
    SMTP_USER = _clean_env("SMTP_USER")
    SMTP_PASSWORD = _clean_env("SMTP_PASSWORD")
    EMAIL_TO = _clean_env("EMAIL_TO")
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        print(
            "[notifier] Variables SMTP_USER / SMTP_PASSWORD / EMAIL_TO manquantes "
            "-> email non envoyé (voir .env.example)."
        )
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())
        print(f"[notifier] Email envoyé à {EMAIL_TO}.")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notifier] Échec de l'envoi de l'email: {exc}")
        return False
