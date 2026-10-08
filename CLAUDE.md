# CLAUDE.md

Agent de **day trading** (intraday) en paper trading Alpaca. Projet frère de
`../trading-agent` (swing/long terme) — mêmes principes : répondre en
français, utilisateur débutant en trading, rester honnête sur les risques.
Dépôt PUBLIC : n'y écrire aucune information personnelle ni aucune clé.

## Règles à ne pas casser

- Paper trading uniquement (`TradingClient(..., paper=True)`). Jamais d'argent
  réel sans demande explicite et informée.
- Jamais de position overnight : fermeture `FLATTEN_MINUTES_BEFORE_CLOSE`
  avant la clôture, en backtest comme en live.
- `strategy.evaluate_entry` est la SEULE source de décision, partagée par le
  backtest et le live. Elle ne doit voir que des bougies terminées.
- Backtest : exécution à l'ouverture de la bougie suivante, stop avant
  objectif si les deux sont touchés. Le test `test_random_walk_is_not_profitable`
  sert de détecteur de lookahead : il doit rester vert.
- Le bot live ne touche qu'à ses propres ordres (préfixe `ORDER_PREFIX` dans
  `client_order_id`) : le compte peut être partagé avec l'autre bot.
- Ne pas sur-optimiser les paramètres sur 60 jours de yfinance.

## Commandes

```bash
venv\Scripts\python -m pytest -q
venv\Scripts\python run_backtest.py [--alpaca --days 730 | --synthetic] [--plot] [--csv]
venv\Scripts\python live_daytrade.py [--phase all|morning|close] [--once] [--dry-run]
```

pandas est épinglé en 2.2.3 : pandas 3.x est bloqué par Smart App Control
sur le PC de l'utilisateur (DLL `nattype`). Sous Windows, définir
`PYTHONIOENCODING=utf-8` pour les accents dans la console.

## État au 2026-10-08

- Backtest réel yfinance (60 séances) : -1,46 %, PF 0,78, 95 trades.
- Backtest Alpaca 2 ans (501 séances, 1028 trades) : -3,69 %, PF 0,95,
  max DD -9,5 %. ~+5,9 % brut avant ~9 600 $ de slippage estimé -> pas
  d'edge net démontré. Long et short tous deux légèrement négatifs.
- `.env` configuré (compte paper dédié, 100 000 $, via `configurer_cles.bat`).
  Connexion et lecture des bougies IEX live vérifiées.
- 2026-10-09 : dépôt public github.com/dolm-eng/daytrading-agent, secrets
  configurés, test workflow_dispatch (dry-run) vert. Tâche planifiée Windows
  supprimée. Première séance live prévue le 2026-10-09 (stratégie ORB 6 actions).

## Exécution

GitHub Actions (`.github/workflows/daytrade.yml`, dépôt public = minutes
illimitées) : job `morning` (13h20 UTC -> fin des entrées) + job `close`
(19h07 UTC -> flatten 15h50 NY + email + commit de `journal/journal.csv`).
La tâche planifiée Windows locale ne doit PAS tourner en même temps.

## Recherche (research/)

Protocole : DEV 2020-10 -> 2024-12, HOLDOUT 2025-01 -> 2026-10 (déjà
consommé pour "zone de bruit"). Zone de bruit SPY+QQQ (coût 1 bp) :
DEV Sharpe 1,35 / +9 %/an ; HOLDOUT Sharpe 0,17 / +0,9 %/an. Aucune
stratégie n'a d'edge prouvé. Variantes ORB sur DEV (coût 2 bp) : seule
"ORB15 SPY+QQQ" est positive (Sharpe 0,80, +2,2 %/an, DD 2,6 %) ; les autres
perdent. Piste : se limiter à SPY+QQQ. Le paper trading oct. 2026 -> janv. 2027 est
le seul test propre restant.
