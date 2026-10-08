# Protocole — stratégie « stocks in play » (écrit AVANT tout test, le 2026-10-09)

Ce fichier fixe les règles à l'avance pour ne pas les ajuster après avoir vu
les résultats. Toute modification ultérieure doit être datée et justifiée ici.

## Source

Inspiré de Zarattini & Aziz (2023), *Can Day Trading Really Be Profitable?*
(ORB 5 minutes sur les « stocks in play »).

## Univers

- Actions US (hors ETF) cotées NYSE/NASDAQ, actives chez Alpaca.
- Liste figée **au 31/12/2024** : les 300 actions avec le plus gros volume
  quotidien médian en dollars sur 2024, prix médian > 10 $.
- Filtre quotidien (avec les données des 14 séances PRÉCÉDENTES uniquement) :
  prix > 5 $, volume moyen > 1 M d'actions, ATR(14) > 0,50 $.
- Biais connu : sur la période DEV (2020-2024), cette liste favorise les
  actions qui ont « survécu » jusqu'en 2024 -> résultats DEV un peu
  optimistes. Sur le HOLDOUT (2025-2026), la liste est connue à l'avance
  -> pas de biais de survie.

## Règles de trading

1. 9h35 NY : pour chaque action, volume relatif = volume de la 1re bougie
   5 min / moyenne de ce même volume sur les 14 séances précédentes.
2. On garde les 20 actions au volume relatif le plus élevé, avec un volume
   relatif >= 1.
3. 1re bougie haussière (clôture > ouverture) -> ordre d'achat stop au plus
   haut de cette bougie. Baissière -> ordre de vente à découvert stop au plus
   bas. Bougie neutre -> rien.
4. Stop-loss : 10 % de l'ATR(14) quotidien depuis le prix d'entrée.
5. Pas d'objectif : sortie à 15h50 NY si le stop n'a pas été touché.
6. Ordres non déclenchés annulés à 15h50.

## Taille des positions (choisie avant le test)

- Risque 0,5 % du capital par trade, ET montant par position plafonné à
  capital / 20 -> exposition totale <= 1x le capital (pas d'effet de levier).
- Variante « article » (1 % de risque, levier max 4x), calculée seulement
  pour information, pas candidate.

## Coûts (choisis avant le test)

- 3 points de base (0,03 %) par exécution, entrée comme sortie : actions
  plus volatiles que SPY/QQQ, donc écart achat/vente plus large.
- Sensibilité affichée à 1, 3 et 5 points de base.

## Exécution simulée

- Entrée : dès qu'une bougie 5 min traverse le niveau d'entrée, prix =
  max(niveau, ouverture de la bougie) pour un achat (min pour un short),
  + coût.
- Stop vérifié sur le plus haut / plus bas des bougies suivantes, et sur la
  bougie d'entrée elle-même (hypothèse pessimiste : si la bougie d'entrée
  touche aussi le stop, on est stoppé).
- Sortie au prix de clôture de la bougie qui se termine à 15h50.

## Périodes

- DEV : 2020-10 -> 2024-12, pour vérifier que tout fonctionne et comparer à l'ORB actuel.
- HOLDOUT : 2025-01 -> aujourd'hui, regardé UNE SEULE FOIS, avec les règles
  ci-dessus telles quelles.

## Critère de réussite (fixé à l'avance)

La stratégie remplace l'ORB actuel dans le bot SEULEMENT si, sur le HOLDOUT,
avec 3 points de base de coûts :
- rendement net > 0, ET
- Sharpe > 0,5, ET
- max drawdown < 15 %.
Sinon, on le dit franchement et on garde l'existant.
