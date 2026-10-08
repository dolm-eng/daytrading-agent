"""
Configuration centrale de l'agent de DAY TRADING (intraday).
Modifie ces valeurs pour ajuster le comportement sans toucher au reste du code.

Différence clé avec l'agent long terme (../trading-agent) : ici, TOUTES les
positions sont fermées avant la clôture du marché. On ne garde jamais rien
pendant la nuit.
"""

# --- Univers d'actifs ---
# Pour du day trading, la liquidité est encore plus importante qu'en swing :
# on entre et sort plusieurs fois, donc le spread (écart achat/vente) et le
# slippage pèsent sur chaque trade. On reste sur des actifs ultra-liquides.
WATCHLIST = [
    "SPY",   # ETF S&P 500
    "QQQ",   # ETF Nasdaq 100
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
]

# --- Bougies ---
BAR_MINUTES = 5               # bougies de 5 minutes
MARKET_TZ = "America/New_York"
MARKET_OPEN = "09:30"         # heure de New York (15h30 à Paris la plupart de l'année)

# --- Stratégie : cassure du range d'ouverture (Opening Range Breakout) ---
OPENING_RANGE_MINUTES = 15    # le "range d'ouverture" = plus haut / plus bas des 15 premières minutes
ENTRY_CUTOFF = "11:30"        # pas de nouvelle entrée après 11h30 New York (l'élan du matin s'essouffle)
ALLOW_SHORT = True            # autorise aussi les ventes à découvert (pari à la baisse)
RELVOL_LOOKBACK_BARS = 20     # nb de bougies pour calculer le volume moyen
RELVOL_MIN = 1.2              # la bougie de cassure doit avoir >= 1.2x le volume moyen (cassure "convaincue")
OR_MIN_RANGE_PCT = 0.0015     # range d'ouverture trop étroit (<0.15%) -> bruit, on ignore
OR_MAX_RANGE_PCT = 0.02       # range d'ouverture trop large (>2%) -> stop trop loin, on ignore
REWARD_RISK_MULTIPLE = 2.0    # objectif = 2x la distance au stop

# --- Gestion du risque ---
INITIAL_CAPITAL = 100_000.0      # capital de départ simulé (backtest)
RISK_PER_TRADE_PCT = 0.005       # 0.5% du capital risqué par trade (moins qu'en swing : plus de trades)
MAX_ALLOCATION_PER_POSITION_PCT = 0.25  # jamais plus de 25% du capital sur une ligne
MAX_OPEN_POSITIONS = 3           # positions ouvertes en même temps
MAX_TRADES_PER_DAY = 6           # au-delà, on arrête pour la journée (évite le sur-trading)
MAX_DAILY_LOSS_PCT = 0.02        # coupe-circuit : -2% sur la journée -> on ferme tout et on arrête
MAX_DRAWDOWN_PCT = 0.10          # coupe-circuit global : -10% depuis le plus haut -> plus aucune entrée
FLATTEN_MINUTES_BEFORE_CLOSE = 10  # on ferme tout 10 min avant la clôture (15h50 New York)

# --- Coûts de transaction (backtest) ---
COMMISSION_PER_TRADE = 0.0
SLIPPAGE_BPS = 2              # 0.02% par exécution au marché (actifs très liquides)

# --- Exécution live ---
# Préfixe des ordres passés par CE bot : permet de ne jamais toucher aux
# positions d'un autre bot (ex: l'agent long terme) s'il partage le compte.
ORDER_PREFIX = "dt"
BAR_SETTLE_SECONDS = 20       # attente après la fin d'une bougie avant de la lire (le temps qu'elle soit publiée)
