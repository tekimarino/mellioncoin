import math
import os
import csv
from datetime import datetime

# ==============================
# Paramètres globaux
# ==============================

MEC_VALUE = 500        # Valeur d'une MEC (Mellion Coin)
TAUX_INTERET = 0.24    # 24 % d'intérêt
MAX_NIVEAUX_FILLEULS = 17  # max 17 filleuls → max 18 niveaux (0 à 17)

# Dossier du script et fichier d'historique (toujours au même endroit)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "historique_investissements.csv")


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
# 3. Intérêts propres (24 %)
# ==============================

def compute_interets(caps, taux=TAUX_INTERET):
    """
    Intérêt propre de chaque niveau = 24 % du capital.
    """
    return [c * taux for c in caps]


# ==============================
# 4. Commissions par niveau
# ==============================

def taux_commission_par_distance(d):
    """
    Retourne le pourcentage de commission en fonction de la distance
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


def compute_commissions(caps, taux_interet=TAUX_INTERET):
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
# 5. Affichage des tableaux
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
# 6. Gestion de l'historique (CSV)
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
    Ajoute une ligne dans le fichier CSV d'historique,
    sans jamais effacer les anciennes.
    """
    file_exists = os.path.exists(filename)
    fieldnames = [
        "date",
        "investissement",
        "interets",
        "commissions_totales",          # sauvegardé, mais non affiché dans l'historique
        "commission_supplementaire",
        "revenu_global",
        "nombre_MEC",
        "rendement"
    ]
    with open(filename, "a", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def print_history_table(history):
    """
    Affiche l'historique des investissements (sans afficher les commissions totales).
    """
    if not history:
        print("\n=== HISTORIQUE DES INVESTISSEMENTS ===")
        print("Aucun investissement enregistré pour le moment.")
        return

    print("\n=== HISTORIQUE DES INVESTISSEMENTS ===")
    print(
        f"{'Date':>12}  "
        f"{'Invest.':>10}  "
        f"{'Intérêts':>10}  "
        f"{'Com_supp':>10}  "
        f"{'Rev.Global':>12}  "
        f"{'MEC':>8}  "
        f"{'Rendement':>10}"
    )
    print("-" * 80)
    for row in history:
        print(
            f"{row['date']:>12}  "
            f"{row['investissement']:>10}  "
            f"{row['interets']:>10}  "
            f"{row['commission_supplementaire']:>10}  "
            f"{row['revenu_global']:>12}  "
            f"{row['nombre_MEC']:>8}  "
            f"{row['rendement']:>10}"
        )


# ==============================
# 7. Programme principal
# ==============================

def main():
    print("=== CALCUL MEC, INTÉRÊTS, COMMISSIONS ET RENDEMENT GLOBAL ===")

    try:
        # 1) Saisie de la date
        date_str = input("Entrez la date de votre investissement (JJ-MM-AAAA) : ").strip()
        try:
            dt = datetime.strptime(date_str, "%d-%m-%Y")
            date_norm = dt.strftime("%d-%m-%Y")
        except ValueError:
            print("Format de date invalide. Utilisez JJ-MM-AAAA.")
            return

        # 2) Saisie de X
        X_str = input("Entrez le montant de votre investissement initial X (en USD) : ").strip()
        X = float(X_str)

        if X <= 0:
            print("X doit être strictement positif.")
            return

        if X % MEC_VALUE != 0:
            print(f"⚠️  X doit être un multiple de {MEC_VALUE}.")
            return

        # 3) Répartition optimisée
        n_opt, mec, caps = optimized_distribution(X)

        # 4) Intérêts propres
        interets = compute_interets(caps)

        # 5) Commissions
        commissions = compute_commissions(caps)

        # 6) Affichages principaux
        print(f"\nDate : {date_norm}  |  X = {X:,.0f}  |  n_opt = {n_opt}  |  niveaux de 0 à {n_opt - 1}")
        print_distribution_table(mec, caps)
        total_I, total_C, total_R = print_revenus_table(caps, interets, commissions)

        # ============================
        # 7) Réinvestissement des commissions (SEULEMENT si X >= 3000)
        # ============================

        total_MEC_initial = sum(mec)

        if X >= 3000:
            # Commission totale transformée en multiple de 500 -> C_tm
            C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE

            # Commission à réinvestir en MEC
            mec_commission_reinvestie = C_tm / MEC_VALUE

            # Sa = montant ajouté pour atteindre ce multiple
            Sa = C_tm - total_C

            # Commission supplémentaire générée par le réinvestissement
            Com_supp = 1.24 * C_tm

            # Revenu Global (logique de réinvestissement)
            Revenu_global = Com_supp + total_I

            # Nombre total de MEC (capital initial + MEC de la commission réinvestie)
            total_MEC_global = total_MEC_initial + mec_commission_reinvestie

            denom = X + Sa

        else:
            # ❗ Aucun réinvestissement quand X < 3000
            C_tm = 0.0
            Sa = 0.0
            Com_supp = 0.0
            mec_commission_reinvestie = 0.0

            # Revenu global = intérêts + commissions totales
            Revenu_global = total_I + total_C

            # Pas de MEC supplémentaires
            total_MEC_global = total_MEC_initial

            denom = X  # pas de Sa

        # Rendement
        r = Revenu_global / denom

        print("\n=== SYNTHÈSE RÉINVESTISSEMENT COMMISSION ===")
        print(f"\nCommission totale à réinvestir               : {C_tm:,.2f}")
        print(f"Commission à réinvestir en MEC               : {mec_commission_reinvestie:,.0f} MEC")
        print(f"Somme ajoutée                                : {Sa:,.2f}")
        print(f"Commission supplémentaire générée            : {Com_supp:,.2f}")
        print(f"\nRevenu Global                                : {Revenu_global:,.2f}")
        print(f"Nombre total de MEC                          : {total_MEC_global:,.0f} MEC")
        print(f"Rendement                                    : {r*100:,.2f} %")

        # ============================
        # 8) Sauvegarde dans l'historique
        # ============================

        history = load_history()

        row = {
            "date": date_norm,
            "investissement": f"{X:,.2f}",
            "interets": f"{total_I:,.2f}",
            "commissions_totales": f"{total_C:,.2f}",           # stocké mais non affiché
            "commission_supplementaire": f"{Com_supp:,.2f}",
            "revenu_global": f"{Revenu_global:,.2f}",
            "nombre_MEC": f"{total_MEC_global:,.0f}",
            "rendement": f"{r*100:,.2f} %"
        }

        append_history(row)
        history.append(row)

        # 9) Afficher le tableau historique
        print_history_table(history)

    except ValueError as e:
        print("Erreur :", e)
    except Exception as e:
        print("Erreur inattendue :", e)


if __name__ == "__main__":
    main()
