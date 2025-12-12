# MellionCoin (clé en main) — V4 (Ordre détail cliquable)

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
