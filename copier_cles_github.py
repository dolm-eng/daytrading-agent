"""
Copie tes clés Alpaca (depuis .env) dans le presse-papier, UNE PAR UNE,
sans espace ni retour à la ligne, pour les coller dans les secrets GitHub.
Les clés ne sont jamais affichées à l'écran.
"""

import tkinter
from pathlib import Path

from dotenv import dotenv_values

root = tkinter.Tk()
root.withdraw()


def copy(text: str) -> None:
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()


values = dotenv_values(Path(__file__).resolve().parent / ".env")
URL = "https://github.com/dolm-eng/daytrading-agent/settings/secrets/actions"

print("=== Copie des clés pour GitHub ===")
print(f"Page des secrets : {URL}\n")
for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
    value = (values.get(name) or "").strip()
    if not value or "ta_cle" in value:
        print(f"{name} introuvable dans .env -> relance configurer_cles.bat d'abord.")
        break
    copy(value)
    print(f">>> {name} est copiée dans le presse-papier ({len(value)} caractères).")
    print(f"    Sur GitHub : clique sur le crayon à côté de {name} (ou 'New repository secret'),")
    print("    efface tout dans la case 'Secret', fais Ctrl+V, puis 'Update secret' / 'Add secret'.")
    input("    Appuie sur Entrée ici quand c'est fait...\n")
copy("")
print("Terminé, presse-papier vidé. Relance le test 'Run workflow' sur GitHub.")
input("Appuie sur Entrée pour fermer.")
