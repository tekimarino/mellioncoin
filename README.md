# MellionCoin (clé en main) — V10 (Objectifs + Alertes + Épingles)

## Nouveautés V10
- ⭐ Ordres favoris / épinglés (persistants par utilisateur) + affichage en haut.
- ⏰ Alertes fin de cycle : J-3 / J-1 / AUJOURD'HUI (badge sur les ordres EN COURS).
- 🎯 Projection avec objectif : « Atteindre X USDT → combien investir aujourd’hui ? » (dans Pilotage/Analytics).


## Comptes
Dans `app.py` :
```py
USERS = {"user1":"pass1", "user2":"pass2"}
```

## Données séparées par utilisateur
Chaque utilisateur a ses fichiers dans `data/<username>/`.

### Nouveau : détails d’ordres
À chaque simulation, l’app crée un JSON :
`data/<username>/orders/order_<index>.json`

C’est ce fichier qui alimente la page “Détail d’un ordre”.

## Lancer en local
```bash
python -m pip install -r requirements.txt
python app.py
```

## Pages
- /login : connexion
- / : simulation
- /orders : ordres (10 par page, du plus récent au plus ancien, statut PAYÉ si cycle terminé)
- /order/<id> : détail d’un ordre (cliquable depuis /orders)
- /dashboard : tableau de bord

## Recherche + filtres (Mes ordres)
- Statut : EN COURS / PAYÉ
- Dates : du / au (date de calcul)
- Montant : min / max (investi initial estimé)
- MEC : min / max
- Recherche texte (id, dates, statut)


## Nettoyage CSV
Dans **Outils**, section **Nettoyage (CSV générés)** :
- Supprimer répartitions MEC (Repartition_MEC_*.csv)
- Supprimer dashboard + historique (Tableau_Bord.csv, historique_investissements.csv)
- Supprimer tous les CSV

Les détails d’ordres en JSON et les favoris ne sont pas supprimés.


## Réinitialiser les données avant déploiement

- Menu **Outils** → **Réinitialiser les données d’ordres**.
- Une page d’avertissement te demande de confirmer avant suppression.
- Cette action supprime les ordres (JSON), favoris/épinglés et CSV pour le compte connecté.

## GitHub

Le dossier `data/` est ignoré via `.gitignore` pour éviter de publier tes données par erreur.
