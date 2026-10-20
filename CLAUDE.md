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
- ORB : `strategy.evaluate_entry` est la SEULE source de décision, partagée par
  le backtest et le live. Stocks in play : `sip_strategy.select` (même logique
  que research/stocks_in_play.py). Ne voir que des bougies terminées.
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

2026-10-14 : le planning GitHub partait avec des heures de retard (12 et
13/10 : job du matin après la clôture). Déclencheur principal = cron-job.org
(heure de NY : 9h15 morning, 15h30 close) via l'API workflow_dispatch ; les
crons GitHub restent en secours. Garde-fous : aucun ordre d'entrée après
`MORNING_MAX_DELAY_MINUTES` (9h40 NY), matin ignoré si des ordres `dt-` existent
déjà, clôture ignorée si le journal a déjà la ligne du jour (checkout `ref: main`).

2026-10-20 : le job du matin lancé à 9h15 NY plantait chaque jour depuis le
15/10 : l'historique SIP était demandé jusqu'à 9h29, or le plan gratuit
refuse les 15 dernières minutes SIP. `live_sip.sip_history_end` borne la
requête à maintenant - 16 min (les jobs lancés tard ne voyaient pas le bug).
L'historique (ATR, volumes, ~4 min 30 pour 300 actions) est désormais chargé
PENDANT l'attente de l'ouverture (`live_sip.prepare`), pour que les ordres
partent à 9h35:20 et non vers 9h36-9h37 (limite 9h40).
`tests/test_morning_rehearsal.py` rejoue tout le matin (horloge simulée, faux
Alpaca qui refuse le SIP récent) pour des départs à 9h00/9h15/9h25/9h31/9h36/9h45.

## Recherche (research/)

Protocole : DEV 2020-10 -> 2024-12, HOLDOUT 2025-01 -> 2026-10 (déjà
consommé pour "zone de bruit"). Zone de bruit SPY+QQQ (coût 1 bp) :
DEV Sharpe 1,35 / +9 %/an ; HOLDOUT Sharpe 0,17 / +0,9 %/an. Aucune
stratégie n'a d'edge prouvé. Variantes ORB sur DEV (coût 2 bp) : seule
"ORB15 SPY+QQQ" est positive (Sharpe 0,80, +2,2 %/an, DD 2,6 %) ; les autres
perdent. Piste : se limiter à SPY+QQQ. Le paper trading oct. 2026 -> janv. 2027 est
le seul test propre restant.
- Stocks in play (research/PROTOCOLE_STOCKS_IN_PLAY.md, 300 actions, exécution
  1 min) : avantage brut réel (Sharpe ~1,5 à 1 bp, DEV et HOLDOUT) mais point
  mort ~2,6-2,9 bp/exécution ; ÉCHEC au critère pré-enregistré (3 bp). Non
  déployé. HOLDOUT désormais consommé : toute nouvelle variante se valide en
  paper trading. Données en cache : data_cache/sip5m (946 Mo), sip1m.
- 2026-10-09 : STRATEGY = "stocks_in_play" activée comme EXPÉRIENCE de mesure
  des coûts (live_sip.py : ordres STOP OTO à 9h35 NY, sélection sur 1re
  bougie IEX ; journal/executions.csv). Sélection IEX vérifiée sur DEV
  (Sharpe 1,98 à 1 bp, +0,4 %/an à 3 bp). Ordres OTO stop testés sur le
  compte paper (2 ordres test SPY/QQQ annulés, prix inatteignables).
