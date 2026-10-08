# Agent de day trading (paper trading)

Le petit frère « intraday » de `../trading-agent`. Même philosophie —
stratégie explicable, gestion du risque stricte, **argent fictif uniquement** —
mais ici **toutes les positions sont ouvertes et fermées dans la même
journée**. Rien n'est gardé pendant la nuit.

## ⚠️ À lire avant tout

- **Le day trading est plus dur que le trading long terme.** La majorité des
  particuliers qui font du day trading perdent de l'argent. Chaque aller-retour
  coûte un peu (écart achat/vente, glissement de prix), et ces petits coûts
  s'additionnent vite quand on trade souvent.
- **Premier backtest sur données réelles (60 dernières séances, au 8 octobre
  2026) : -1,46 %**, profit factor 0,78, 42 % de trades gagnants. Autrement
  dit : sur cette période, la stratégie **perd** un peu d'argent. 60 jours,
  c'est trop court pour conclure dans un sens ou dans l'autre — d'où le
  backtest sur 2 ans via Alpaca (voir plus bas), à faire avant toute chose.
- Ce n'est ni un système de gains garantis, ni un conseil financier.

## La stratégie : cassure du range d'ouverture (ORB)

1. On note le **plus haut** et le **plus bas** des 15 premières minutes de la
   séance (9h30-9h45 New York = 15h30-15h45 Paris). C'est le *range
   d'ouverture*.
2. Ensuite, à la clôture de chaque bougie de 5 minutes, jusqu'à 11h30 New York :
   - **Achat** si le prix clôture au-dessus du plus haut du range, au-dessus du
     **VWAP** (prix moyen de la journée pondéré par le volume) et avec un
     volume au moins 1,2x supérieur à la moyenne.
   - **Vente à découvert** (pari à la baisse) : le miroir exact, sous le plus bas.
3. **Stop-loss** au milieu du range (si le prix y revient, la cassure a raté),
   **objectif** à 2x la distance au stop.
4. **Tout est fermé à 15h50 New York** (21h50 Paris), quoi qu'il arrive.

Garde-fous (dans `config.py`) : 0,5 % du capital risqué par trade, 3
positions max en même temps, 6 trades max par jour, une seule entrée par
action et par jour, **coupe-circuit à -2 % sur la journée** (on ferme tout et
on arrête), coupe-circuit global à -10 % depuis le plus haut du compte.

Les ordres sont des **ordres bracket** : l'achat, le stop et l'objectif
partent ensemble chez Alpaca. Si ton PC plante en pleine séance, le stop
reste actif chez le courtier.

## Installation

Double-clique sur `setup_test.bat`, ou :

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
```

> Note : `pandas` est épinglé en 2.2.3 car, sur ce PC, Smart App Control
> (Windows) bloque une DLL de pandas 3.x. C'est sûrement aussi pour ça que le
> venv de `trading-agent` plante à l'import de pandas.

## 1. Backtest

```bash
python run_backtest.py --plot --csv               # 60 derniers jours (yfinance, sans clé)
python run_backtest.py --alpaca --days 730 --plot --csv   # 2 ans via Alpaca (clés dans .env) — LE test qui compte
python run_backtest.py --synthetic                # données simulées : vérifie juste que le code tourne
```

Le backtest est volontairement pessimiste : décision à la clôture d'une
bougie, exécution à l'ouverture de la suivante avec glissement, et si stop et
objectif sont touchés dans la même bougie, on suppose que c'est le stop.
Vérification anti-triche : sur une marche aléatoire pure
(`trend_strength=0`), la stratégie perd de l'argent, comme elle le doit.

À regarder : **profit factor** (< 1 = perd de l'argent), **R moyen par
trade** (> 0 = gagne en moyenne), **max drawdown**, et **% de jours
positifs** (même une stratégie rentable a beaucoup de jours perdants).

## 2. Paper trading live

1. **Crée un deuxième compte paper Alpaca** dédié au day trading et récupère
   ses clés API. Tu peux réutiliser celles de l'agent long terme, mais alors
   les deux bots partagent le même argent et le même coupe-circuit quotidien,
   et le day trader ignore les actions déjà détenues par l'autre bot.
2. Copie `.env.example` en `.env`, mets-y les clés (et l'email si tu veux le
   récapitulatif).
3. Teste sans risque :
   ```bash
   python live_daytrade.py --once --dry-run
   ```
4. Séance complète (à lancer vers 15h20 heure de Paris, le bot attend
   l'ouverture tout seul) :
   ```bash
   python live_daytrade.py
   ```

### Automatisation (sans ton PC) : GitHub Actions

Le dépôt est public (minutes GitHub Actions illimitées et gratuites). Tes
clés ne sont **jamais** dans le code : elles sont dans les *secrets* GitHub
(Settings → Secrets and variables → Actions) : `ALPACA_API_KEY`,
`ALPACA_SECRET_KEY`, et en option `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `EMAIL_TO`.

Chaque jour de bourse :
- **13h20 UTC (job du matin)** : le bot attend l'ouverture, prend ses
  positions jusqu'à 11h30 New York, puis s'arrête. Les positions restent
  protégées par leurs stops/objectifs chez Alpaca.
- **19h07 UTC (job du soir)** : il attend 15h50 New York, ferme tout,
  t'envoie le bilan par email et ajoute une ligne à `journal/journal.csv`.

Pour tester à la main : onglet **Actions** → *Day trading (paper)* → **Run
workflow** (laisse `dry_run = true` pour ne passer aucun ordre).

Sur ton PC, `python live_daytrade.py` (phase `all`) fait toute la séance
d'un coup — mais ne le lance pas en même temps que GitHub.

## La règle PDT (si un jour tu passes en argent réel)

Aux États-Unis, la règle *Pattern Day Trader* limite les comptes sur marge
de moins de 25 000 $ à 3 allers-retours dans la journée sur 5 jours ouvrés.
Cette règle est en cours de révision par la FINRA : vérifie celle en vigueur
au moment où tu lis ça. Ça ne concerne pas le compte paper (100 000 $ fictifs).

## Structure

```
config.py          paramètres (watchlist, stratégie, risque)
data.py            données 5 min : yfinance, Alpaca, ou simulées
indicators.py      VWAP, volume relatif, range d'ouverture
strategy.py        règles d'entrée ORB (partagées backtest / live)
risk.py            taille de position, coupe-circuits
backtest.py        moteur de backtest intraday
run_backtest.py    CLI du backtest
live_daytrade.py   bot live Alpaca (paper) + email
notifier.py        email récapitulatif (copié de trading-agent)
tests/             tests automatiques (sans réseau)
```

## Prochaines étapes possibles

- Lancer le backtest 2 ans (`--alpaca --days 730`) et en discuter **avant**
  de toucher aux paramètres.
- Comparer avec une variante sans vente à découvert (`ALLOW_SHORT = False`)
  et avec un range d'ouverture de 30 min.
- Journal CSV des trades live pour comparer paper trading et backtest.
- Faire tourner le bot sur un petit serveur (VPS) pour ne pas dépendre du PC.
