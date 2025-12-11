import math
from datetime import datetime, timedelta
import csv
from pathlib import Path

# Constantes
MEC_VALUE = 500
TAUX_INTERET_DEFAULT = 0.24
MAX_NIVEAUX_FILLEULS = 17

# Taux par cycle (on utilise uniquement 28 jours dans l'app)
RATES_BY_CYCLE = {
    28: 0.24,
    14: 0.095,
    7: 0.04,
    1: 0.004,
}

# Nom du fichier de tableau de bord
DASHBOARD_CSV = "Tableau_Bord.csv"


def standard_distribution_old(X, m=MEC_VALUE):
    """
    Répartition standard de départ (ancienne logique) pour déterminer n_réel.
    Utilisée uniquement pour estimer n_opt.
    """
    def find_n_th(X_local):
        n = 0
        # On augmente n tant que 50*(n+1)*(n+2) <= X
        while 50 * (n + 1) * (n + 2) <= X_local:
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

    mec = []
    caps = []
    for f in f_th:
        mec_i = math.ceil(f / m)
        mec.append(mec_i)
        caps.append(mec_i * m)

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


def compute_interets(caps, taux):
    """
    Intérêt propre de chaque niveau = taux * capital.
    """
    return [c * taux for c in caps]


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


def compute_mellion_result(X, reinvest_request: bool):
    """
    Calcule tous les résultats pour un montant X et un choix de réinvestissement.
    Retourne un dictionnaire prêt pour l'interface web.
    """
    if X <= 0:
        raise ValueError("X doit être strictement positif.")
    if X % MEC_VALUE != 0:
        raise ValueError(f"X doit être un multiple de {MEC_VALUE} USD.")

    cycle = 28
    taux_cycle = RATES_BY_CYCLE[28]
    now = datetime.now()

    # Répartition optimisée
    n_opt, mec, caps = optimized_distribution(X)

    # Intérêts & commissions
    interets = compute_interets(caps, taux_cycle)
    commissions = compute_commissions(caps, taux_interet=taux_cycle)

    total_I = sum(interets)
    total_C = sum(commissions)
    total_R = total_I + total_C
    total_MEC_initial = sum(mec)

    reinvest_possible = X >= 3000
    reinvest_effective = False

    # Valeurs par défaut
    C_tm = 0.0
    Sa = 0.0
    Com_supp = 0.0
    mec_commission_reinvestie = 0.0

    if not reinvest_possible:
        # X < 3000 : pas de réinvestissement possible
        Revenu_global = total_I + total_C
        total_MEC_global = total_MEC_initial
        montant_investi = X
        denom = montant_investi
        r = Revenu_global / denom if denom != 0 else 0.0
    else:
        if reinvest_request:
            # Cas réinvestissement
            reinvest_effective = True
            C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE
            mec_commission_reinvestie = C_tm / MEC_VALUE
            Sa = C_tm - total_C
            Com_supp = 1.24 * C_tm
            Revenu_global = Com_supp + total_I
            total_MEC_global = total_MEC_initial + mec_commission_reinvestie
            montant_investi = X + Sa
            denom = montant_investi
            r = Revenu_global / denom if denom != 0 else 0.0
        else:
            # X ≥ 3000 mais réinvestissement refusé
            Revenu_global = total_I + total_C
            total_MEC_global = total_MEC_initial
            montant_investi = X
            denom = montant_investi
            r = Revenu_global / denom if denom != 0 else 0.0

    date_fin_cycle = now + timedelta(days=cycle)
    # circulation = Revenu_global + montant_investi - Sa
    circulation = Revenu_global + montant_investi - Sa

    # Données par niveau (pour affichage)
    levels = []
    for idx, (m, cap, I, C) in enumerate(zip(mec, caps, interets, commissions)):
        levels.append({
            "niveau": idx,
            "role": "Parrain" if idx == 0 else "Filleul",
            "mec": m,
            "capital": cap,
            "interet": I,
            "commission": C,
            "revenu": I + C,
        })

    result = {
        "X": float(X),
        "cycle": cycle,
        "taux_cycle": taux_cycle,
        "now": now,
        "date_fin_cycle": date_fin_cycle,
        "n_opt": n_opt,
        "mec": mec,
        "caps": caps,
        "interets": interets,
        "commissions": commissions,
        "levels": levels,
        "total_I": total_I,
        "total_C": total_C,
        "total_R": total_R,
        "total_MEC_initial": total_MEC_initial,
        "total_MEC_global": total_MEC_global,
        "Revenu_global": Revenu_global,
        "rendement": r * 100,
        "montant_investi": montant_investi,
        "circulation": circulation,
        "reinvest_possible": reinvest_possible,
        "reinvest_effective": reinvest_effective,
        "C_tm": C_tm,
        "Sa": Sa,
        "Com_supp": Com_supp,
        "mec_commission_reinvestie": mec_commission_reinvestie,
    }
    return result


def save_dashboard_row(data, filename: str = DASHBOARD_CSV) -> None:
    """
    Ajoute une ligne dans Tableau_Bord.csv à chaque calcul.

    Colonnes :
    - Date et heure du calcul de commission
    - Date de fin du cycle
    - Montant de l'investissement
    - Les intérêts
    - Commissions
    - Com_supp
    - Revenu global
    - MEC (total)
    - Circulation
    - Rendement (en %)
    """
    file_path = Path(filename)
    file_exists = file_path.exists()

    with file_path.open(mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=",")
        if not file_exists:
            writer.writerow([
                "date_calcul",
                "date_fin_cycle",
                "montant_investi",
                "interets",
                "commissions",
                "com_supp",
                "revenu_global",
                "MEC_total",
                "circulation",
                "rendement_pct",
            ])

        writer.writerow([
            data["now"].strftime("%Y-%m-%d %H:%M:%S"),
            data["date_fin_cycle"].strftime("%Y-%m-%d %H:%M:%S"),
            f"{data['montant_investi']:.2f}",
            f"{data['total_I']:.2f}",
            f"{data['total_C']:.2f}",
            f"{data['Com_supp']:.2f}",
            f"{data['Revenu_global']:.2f}",
            int(round(data["total_MEC_global"])),
            f"{data['circulation']:.2f}",
            f"{data['rendement']:.2f}",
        ])
