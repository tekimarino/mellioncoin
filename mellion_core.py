import math
import os
import csv
import sys
from datetime import datetime, timedelta

# ==============================
# Import optionnel de matplotlib
# ==============================

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False

# ==============================
# Paramètres globaux & BASE_DIR
# ==============================

# En mode normal (.py), on utilise le dossier du fichier .py
# En mode compilé (.exe), on utilise le dossier de l'exécutable (dist)
if getattr(sys, "frozen", False):
    # Programme compilé (PyInstaller, auto-py-to-exe, etc.)
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Exécution normale du script .py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MEC_VALUE = 500        # Valeur d'une MEC (Mellion Coin)
TAUX_INTERET_DEFAULT = 0.24
MAX_NIVEAUX_FILLEULS = 17      # max 17 filleuls → max 18 niveaux (0 à 17)

# Taux d'intérêt par cycle
# On garde le dictionnaire complet mais on n'utilise plus que 28 jours.
RATES_BY_CYCLE = {
    28: 0.24,    # 24 %
    14: 0.095,   # 9,5 %
    7: 0.04,     # 4 %
    1: 0.004,    # 0,4 %
}

# Fichiers CSV créés dans le même dossier que le .py / .exe
HISTORY_FILE = os.path.join(BASE_DIR, "historique_investissements.csv")
DASHBOARD_FILE = os.path.join(BASE_DIR, "Tableau_Bord.csv")


# ==============================
# 1. Répartition "standard" pour trouver n_opt
# ==============================

def standard_distribution_old(X, m=MEC_VALUE):
    """
    Répartition standard de départ (ancienne logique) pour déterminer n_réel.
    Utilisée uniquement pour estimer n_opt.
    """
    def find_n_th(X):
        n = 0
        # On augmente n tant que 50*(n+1)*(n+2) <= X
        while 50 * (n + 1) * (n + 2) <= X:
            n += 1
        return n

    n = find_n_th(X)
    levels = []
    cumul = 0

    for i in range(n):
        f_brut = 100 * (n - i)
        mec = math.ceil(f_brut / m)
        cap = mec * m
        if cumul + cap <= X:
            levels.append((i, mec, cap))
            cumul += cap
        else:
            break

    return levels


def compute_n_opt(X, m=MEC_VALUE):
    """
    Détermine n_opt (nombre total de niveaux incluant le parrain),
    à partir de la répartition standard, borné à 18 (0 à 17).
    """
    levels = standard_distribution_old(X, m)
    if not levels:
        raise ValueError("X est trop petit pour générer au moins une MEC.")

    n_opt0 = len(levels)
    n_opt = min(n_opt0, MAX_NIVEAUX_FILLEULS + 1)  # max 18 niveaux
    return n_opt


# ==============================
# 2. Répartition optimisée (nouveau modèle)
# ==============================

def optimized_distribution(X, m=MEC_VALUE):
    """
    Calcule la répartition optimisée :
    - calcule n_opt,
    - construit le tableau théorique optimisé (écart 100),
    - arrondit en MEC pour les niveaux 0..(n_opt-2),
    - met tout le reste au dernier niveau (n_opt-1).
    Retourne : n_opt, liste_MEC, liste_capitaux
    """
    n_opt = compute_n_opt(X, m)

    # Tableau théorique pour i = 0..n_opt-2
    f_th = []
    for i in range(n_opt - 1):
        f = 100 * (n_opt - 1 - i)
        f_th.append(f)

    # Conversion en MEC (arrondi au supérieur) pour les premiers niveaux
    mec = []
    caps = []
    for f in f_th:
        mec_i = math.ceil(f / m)
        mec.append(mec_i)
        caps.append(mec_i * m)

    # Reste pour le dernier niveau
    S_base = sum(caps)
    R = X - S_base
    if R < 0:
        raise ValueError(
            f"Incohérence : la somme de base {S_base} dépasse X={X}."
        )

    if R % m != 0:
        raise ValueError(
            f"Le reste {R} n'est pas un multiple de {m}. "
            "X doit être multiple de 500."
        )

    mec_last = R // m
    mec.append(mec_last)
    caps.append(mec_last * m)

    if sum(caps) != X:
        raise ValueError("La somme des capitaux ne correspond pas à X.")

    return n_opt, mec, caps


# ==============================
# 3. Intérêts propres (taux selon cycle)
# ==============================

def compute_interets(caps, taux):
    """
    Intérêt propre de chaque niveau = taux * capital.
    """
    return [c * taux for c in caps]


# ==============================
# 4. Commissions par niveau
# ==============================

def taux_commission_par_distance(d):
    """
    Pourcentage de commission en fonction de la distance
    (en niveaux) entre parrain et filleul.
    """
    if d == 1:
        return 0.20
    elif d == 2:
        return 0.10
    elif 3 <= d <= 7:
        return 0.05
    elif 8 <= d <= 10:
        return 0.03
    elif 11 <= d <= 17:
        return 0.01
    else:
        return 0.0


def compute_commissions(caps, taux_interet):
    """
    Commissions totales par niveau, sur les gains (intérêts) des niveaux en dessous.
    """
    n = len(caps)
    commissions = [0.0] * n

    for u in range(n):              # niveau parrain
        for v in range(u + 1, n):   # niveau filleul
            d = v - u
            t = taux_commission_par_distance(d)
            if t == 0:
                continue
            gain_v = caps[v] * taux_interet
            c_uv = gain_v * t
            commissions[u] += c_uv

    return commissions


# ==============================
# 5. Export CSV répartition MEC
# ==============================

def get_next_repartition_filename(base_dir=BASE_DIR):
    """
    Cherche les fichiers Repartition_MEC_*.csv dans le dossier,
    trouve le numéro max, et renvoie le prochain nom.
    """
    prefix = "Repartition_MEC_"
    suffix = ".csv"
    max_idx = 0

    for name in os.listdir(base_dir):
        if name.startswith(prefix) and name.endswith(suffix):
            middle = name[len(prefix):-len(suffix)]
            try:
                idx = int(middle)
                if idx > max_idx:
                    max_idx = idx
            except ValueError:
                continue

    next_idx = max_idx + 1
    return os.path.join(base_dir, f"{prefix}{next_idx}{suffix}")


def save_repartition_csv(mec, caps):
    """
    Sauvegarde la répartition des MEC dans un fichier CSV
    Repartition_MEC_n.csv, avec un numéro incrémental.
    (Aucune information n'est affichée à l'écran.)
    """
    filename = get_next_repartition_filename()
    fieldnames = ["niveau", "role", "MEC", "capital"]

    with open(filename, "w", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        for i, (m, cap) in enumerate(zip(mec, caps)):
            role = "Parrain" if i == 0 else "Filleul"
            writer.writerow({
                "niveau": i,
                "role": role,
                "MEC": m,
                "capital": f"{cap:.0f}"
            })


# ==============================
# 6. Affichage des tableaux
# ==============================

def print_distribution_table(mec, caps):
    """
    Affiche la répartition optimisée des MEC et capitaux par niveau.
    """
    n = len(mec)
    print("\n=== RÉPARTITION OPTIMISÉE DES MEC ===")
    print(f"{'Niveau':>6}  {'Rôle':>8}  {'MEC':>5}  {'Capital':>10}")
    print("-" * 40)
    for i in range(n):
        role = "Parrain" if i == 0 else "Filleul"
        print(f"{i:>6}  {role:>8}  {mec[i]:>5}  {caps[i]:>10,.0f}")


def print_revenus_table(caps, interets, commissions):
    """
    Affiche intérêts, commissions et revenus par niveau.
    Retourne : total_I, total_C, total_R.
    """
    n = len(caps)
    print("\n=== INTÉRÊTS, COMMISSIONS ET REVENUS PAR NIVEAU ===")
    print(f"{'Niveau':>6}  {'Rôle':>8}  {'Capital':>10}  {'Intérêt':>10}  {'Commiss.':>10}  {'Revenu':>10}")
    print("-" * 70)
    for i in range(n):
        role = "Parrain" if i == 0 else "Filleul"
        cap = caps[i]
        I = interets[i]
        C = commissions[i]
        R = I + C
        print(
            f"{i:>6}  {role:>8}  {cap:>10,.0f}  {I:>10.2f}  {C:>10.2f}  {R:>10.2f}"
        )

    total_I = sum(interets)
    total_C = sum(commissions)
    total_R = total_I + total_C

    print("-" * 70)
    print(
        f"{'TOTAL':>6}  {'':>8}  {sum(caps):>10,.0f}  {total_I:>10.2f}  {total_C:>10.2f}  {total_R:>10.2f}"
    )

    return total_I, total_C, total_R


# ==============================
# 7. Gestion de l'historique (CSV)
# ==============================

def load_history(filename=HISTORY_FILE):
    """
    Charge l'historique depuis le CSV s'il existe.
    """
    if not os.path.exists(filename):
        return []
    with open(filename, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        return list(reader)


def append_history(row, filename=HISTORY_FILE):
    """
    Ajoute une ligne dans le fichier CSV d'historique.
    """
    file_exists = os.path.exists(filename)
    fieldnames = [
        "date",
        "investissement",
        "interets",
        "commissions_totales",
        "commission_supplementaire",
        "revenu_global",
        "nombre_MEC",
        "rendement",
        "cycle",
        "taux_interet"
    ]
    with open(filename, "a", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ==============================
# 8. Tableau de bord (Tableau_Bord.csv)
# ==============================

def append_dashboard(row, filename=DASHBOARD_FILE):
    """
    Ajoute une ligne dans le fichier Tableau_Bord.csv
    avec les informations de synthèse sur les commissions.
    """
    file_exists = os.path.exists(filename)
    fieldnames = [
        "date_calcul",
        "date_fin_cycle",
        "montant_investi",
        "interets",
        "commissions",
        "commission_supplementaire",
        "revenu_global",
        "MEC",
        "circulation",
        "rendement"
    ]
    with open(filename, "a", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_dashboard(filename=DASHBOARD_FILE):
    """
    Charge le tableau de bord depuis le CSV s'il existe.
    """
    if not os.path.exists(filename):
        return []
    with open(filename, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=';')
        return list(reader)


def print_dashboard_table(dashboard):
    """
    Affiche le Tableau de Bord (au lieu de l'historique).
    """
    if not dashboard:
        print("\n=== TABLEAU DE BORD ===")
        print("Aucune donnée disponible pour le moment.")
        return

    print("\n=== TABLEAU DE BORD ===")
    print(
        f"{'Date calcul':>20}  "
        f"{'Fin cycle':>20}  "
        f"{'Investi':>12}  "
        f"{'Intérêts':>10}  "
        f"{'Comm.':>10}  "
        f"{'Com_supp':>10}  "
        f"{'Rev.Global':>12}  "
        f"{'MEC':>8}  "
        f"{'Circulation':>12}  "
        f"{'Rendement':>10}"
    )
    print("-" * 140)
    for row in dashboard:
        date_calcul = row.get("date_calcul", "")
        date_fin_cycle = row.get("date_fin_cycle", "")
        montant_investi = row.get("montant_investi", "")
        interets = row.get("interets", "")
        commissions = row.get("commissions", "")
        com_supp = row.get("commission_supplementaire", "")
        rev_global = row.get("revenu_global", "")
        mec = row.get("MEC", "")
        circulation = row.get("circulation", "")
        rendement = row.get("rendement", "")

        print(
            f"{date_calcul:>20}  "
            f"{date_fin_cycle:>20}  "
            f"{montant_investi:>12}  "
            f"{interets:>10}  "
            f"{commissions:>10}  "
            f"{com_supp:>10}  "
            f"{rev_global:>12}  "
            f"{mec:>8}  "
            f"{circulation:>12}  "
            f"{rendement:>10}"
        )


# ==============================
# 8bis. Diagramme à bandes Revenu Global vs Investissement
# ==============================

def show_revenu_global_bar_chart(dashboard):
    """
    Génère un diagramme à bandes pour visualiser l'évolution du Revenu Global
    en fonction de l'investissement réalisé, à partir des données du tableau de bord.
    """
    if not MATPLOTLIB_OK:
        print("\nMatplotlib n’est pas installé : le diagramme ne peut pas être généré.")
        return

    if not dashboard:
        print("\nAucune donnée dans le tableau de bord pour générer le graphique.")
        return

    investissements = []
    revenus = []
    labels = []

    for idx, row in enumerate(dashboard, start=1):
        montant_str = row.get("montant_investi", "").replace(" ", "")
        revenu_str = row.get("revenu_global", "").replace(" ", "")

        # On enlève les séparateurs de milliers éventuels
        montant_str = montant_str.replace(",", "")
        revenu_str = revenu_str.replace(",", "")

        try:
            montant = float(montant_str)
            revenu = float(revenu_str)
        except ValueError:
            # Si une ligne est mal formattée, on la saute proprement
            continue

        investissements.append(montant)
        revenus.append(revenu)
        # Label sur l'axe X : montant investi formaté
        labels.append(f"{montant:,.0f}")

    if not revenus:
        print("\nImpossible de générer le graphique : aucune donnée numérique valide.")
        return

    x = list(range(len(revenus)))

    plt.figure()
    plt.bar(x, revenus)
    plt.title("Évolution du Revenu Global en fonction de l'investissement réalisé")
    plt.xlabel("Montant investi (USD)")
    plt.ylabel("Revenu Global (USD)")
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.tight_layout()
    plt.show()


# ==============================
# 9. Programme principal – Cycle unique 28 jours
# ==============================

def main():
    print("=== CALCUL MEC, INTÉRÊTS, COMMISSIONS ET RENDEMENT GLOBAL – V1.0 (cycle 28 jours fixe) ===")
    print(f"(Fichiers CSV générés dans : {BASE_DIR})")

    while True:
        try:
            # 1) Cycle fixé à 28 jours
            cycle = 28
            taux_cycle = RATES_BY_CYCLE[28]

            # 2) Saisie de X - CONTRÔLE ENTIER
            while True:
                X_str = input("Entrez le montant de votre investissement initial X (en USD) : ").strip()
                if not X_str.isdigit():
                    print("Veuillez entrer un nombre entier sans virgule ni point (ex : 500, 1000, 1500).")
                    continue
                X = int(X_str)
                break

            if X <= 0:
                print("X doit être strictement positif.")
                continue

            if X % MEC_VALUE != 0:
                print(f"⚠️  X doit être un multiple de {MEC_VALUE}.")
                continue

            # 3) Date/heure automatique de l'investissement
            now = datetime.now()
            date_norm = now.strftime("%d-%m-%Y %H:%M:%S")

            # 4) Répartition optimisée
            n_opt, mec, caps = optimized_distribution(X)

            # Sauvegarde de la répartition MEC dans un CSV dédié (silencieux)
            save_repartition_csv(mec, caps)

            # 5) Intérêts propres avec le taux du cycle
            interets = compute_interets(caps, taux_cycle)

            # 6) Commissions basées sur ces intérêts
            commissions = compute_commissions(caps, taux_interet=taux_cycle)

            # 7) Affichages principaux
            print(
                f"\nDate/heure : {date_norm}  |  Cycle : {cycle} jours  |  "
                f"Taux : {taux_cycle*100:.2f}%  |  X = {X:,.0f}  |  "
                f"n_opt = {n_opt}  |  niveaux de 0 à {n_opt - 1}"
            )
            print_distribution_table(mec, caps)
            total_I, total_C, total_R = print_revenus_table(caps, interets, commissions)

            total_MEC_initial = sum(mec)

            # ============================
            # 8) Logique de réinvestissement & Revenu global
            # ============================

            if X < 3000:
                # Cas 1 : X < 3000 → pas de réinvestissement possible
                C_tm = 0.0
                Sa = 0.0
                Com_supp = 0.0
                mec_commission_reinvestie = 0.0

                # Pas de réinvestissement : Revenu_global = intérêts + commissions
                Revenu_global = total_I + total_C
                total_MEC_global = total_MEC_initial
                montant_investi = X
                denom = montant_investi
                r = Revenu_global / denom if denom != 0 else 0.0

            else:
                # Cas 2 : X >= 3000 → on propose le réinvestissement
                choix_reinvest = input("\nVoulez-vous réinvestir vos commissions ? (Oui/Non) : ").strip().lower()
                reinvest = choix_reinvest in ("oui", "o", "yes", "y")

                if reinvest:
                    # Commission totale transformée en multiple de 500 -> C_tm
                    C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE

                    # Commission à réinvestir en MEC
                    mec_commission_reinvestie = C_tm / MEC_VALUE

                    # Sa = montant ajouté pour atteindre ce multiple
                    Sa = C_tm - total_C

                    # Commission supplémentaire générée par le réinvestissement
                    Com_supp = 1.24 * C_tm

                    # Cas réinvestissement : Revenu_global = Com_supp + intérêts
                    Revenu_global = Com_supp + total_I

                    # Nombre total de MEC (capital initial + MEC de la commission réinvestie)
                    total_MEC_global = total_MEC_initial + mec_commission_reinvestie

                    montant_investi = X + Sa
                    denom = montant_investi
                    r = Revenu_global / denom if denom != 0 else 0.0

                    print("\n=== SYNTHÈSE RÉINVESTISSEMENT DES COMMISSIONS ===")
                    print(f"Commission totale à réinvestir (C_tm)        : {C_tm:,.2f}")
                    print(f"Commission à réinvestir en MEC               : {mec_commission_reinvestie:,.0f} MEC")
                    print(f"Somme ajoutée (Sa)                           : {Sa:,.2f}")
                    print(f"Commission supplémentaire générée (Com_supp) : {Com_supp:,.2f}")
                    print(f"Revenu Global                                : {Revenu_global:,.2f}")
                    print(f"Nombre total de MEC                          : {total_MEC_global:,.0f} MEC")
                    print(f"Rendement                                    : {r*100:,.2f} %")
                else:
                    # Pas de réinvestissement même si X >= 3000
                    C_tm = 0.0
                    Sa = 0.0
                    Com_supp = 0.0
                    mec_commission_reinvestie = 0.0

                    # Pas de réinvestissement : Revenu_global = intérêts + commissions
                    Revenu_global = total_I + total_C
                    total_MEC_global = total_MEC_initial
                    montant_investi = X
                    denom = montant_investi
                    r = Revenu_global / denom if denom != 0 else 0.0

            # 9) Sauvegarde dans l'historique
            history = load_history()

            row_history = {
                "date": date_norm,
                "investissement": f"{X:,.2f}",
                "interets": f"{total_I:,.2f}",
                "commissions_totales": f"{total_C:,.2f}",
                "commission_supplementaire": f"{Com_supp:,.2f}",
                "revenu_global": f"{Revenu_global:,.2f}",
                "nombre_MEC": f"{total_MEC_global:,.0f}",
                "rendement": f"{r*100:,.2f} %",
                "cycle": str(cycle),
                "taux_interet": f"{taux_cycle*100:.2f}%"
            }

            append_history(row_history)
            history.append(row_history)

            # 10) Mise à jour du Tableau de bord (Tableau_Bord.csv)
            date_fin_cycle = (now + timedelta(days=cycle)).strftime("%d-%m-%Y %H:%M:%S")
            circulation = Revenu_global + montant_investi - Sa

            row_dashboard = {
                "date_calcul": date_norm,
                "date_fin_cycle": date_fin_cycle,
                "montant_investi": f"{montant_investi:,.2f}",
                "interets": f"{total_I:,.2f}",
                "commissions": f"{total_C:,.2f}",
                "commission_supplementaire": f"{Com_supp:,.2f}",
                "revenu_global": f"{Revenu_global:,.2f}",
                "MEC": f"{total_MEC_global:,.0f}",
                "circulation": f"{circulation:,.2f}",
                "rendement": f"{r*100:,.2f} %"
            }

            append_dashboard(row_dashboard)

            # 11) Affichage du Tableau de Bord
            dashboard = load_dashboard()
            print_dashboard_table(dashboard)

            # 12) Diagramme à bandes Revenu Global vs Investissement
            if MATPLOTLIB_OK:
                try:
                    show_revenu_global_bar_chart(dashboard)
                except Exception as e:
                    print("\nImpossible de générer le diagramme à bandes :", e)
            else:
                print("\nMatplotlib n’est pas installé : aucun diagramme ne sera affiché.")

        except ValueError as e:
            print("Erreur :", e)
        except Exception as e:
            print("Erreur inattendue :", e)

        # Demander si on veut effectuer un autre investissement
        while True:
            rep = input("\nVoulez-vous effectuer un autre investissement ? (Oui/Non) : ").strip().lower()
            if rep in ("oui", "o", "yes", "y"):
                refaire = True
                break
            if rep in ("non", "n", "no"):
                refaire = False
                break
            print("Réponse invalide. Veuillez répondre par Oui ou Non.")

        if not refaire:
            break

    # Pause finale pour éviter que la fenêtre se ferme immédiatement
    input("\nAppuyez sur Entrée pour fermer le programme...")


if __name__ == "__main__":
    main()
