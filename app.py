from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash, abort, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import math
import re
import os
import csv
import json
import shutil
import io
import zipfile
import tempfile
from pathlib import Path



from mellion_core import (
    MEC_VALUE, RATES_BY_CYCLE,
    optimized_distribution, compute_interets, compute_commissions,
    append_history, append_dashboard, load_dashboard
)

app = Flask(__name__)

# Chemin de données (par utilisateur)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app.secret_key = "change_this_secret_key_for_production"

# ✅ Auth (2 comptes uniquement)
AUTH_DIR = os.path.join(DATA_DIR, "_auth")
USERS_FILE = os.path.join(AUTH_DIR, "users.json")
SECURITY_FILE = os.path.join(AUTH_DIR, "security.json")

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 10
FAIL_WINDOW_MINUTES = 15
INACTIVITY_TIMEOUT_MINUTES = 15

DEFAULT_USERS = {
    "marinoteki": "Ivan2012@",
    "Arkad": "Compta225@"
}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: str, default):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_users():
    data = _load_json(USERS_FILE, {"users": {}})
    users = data.get("users", {})
    # Initialisation si fichier absent/vide
    if not users:
        users = {u: {"password_hash": generate_password_hash(p)} for u, p in DEFAULT_USERS.items()}
        _save_json(USERS_FILE, {"users": users})
    # Forcer 2 comptes max (si quelqu’un modifie le fichier)
    allowed = list(DEFAULT_USERS.keys())
    users = {k: v for k, v in users.items() if k in allowed}
    # S'assurer que les 2 comptes existent
    for u in allowed:
        if u not in users:
            users[u] = {"password_hash": generate_password_hash(DEFAULT_USERS[u])}
    _save_json(USERS_FILE, {"users": users})
    return users


def load_security_state():
    return _load_json(SECURITY_FILE, {"failed": {}, "last_login": {}, "audit": []})


def save_security_state(state):
    # éviter un fichier énorme
    audit = state.get("audit", [])
    if len(audit) > 500:
        state["audit"] = audit[-500:]
    _save_json(SECURITY_FILE, state)


def audit_event(user: str, event: str, ip: str, ua: str = "", detail: str = ""):
    state = load_security_state()
    state.setdefault("audit", []).append({
        "when": _now_iso(),
        "user": user,
        "event": event,
        "ip": ip,
        "ua": (ua or "")[:160],
        "detail": (detail or "")[:300],
    })
    save_security_state(state)


def _client_ip():
    # Si tu es derrière un proxy, tu peux activer X-Forwarded-For (avec prudence)
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _is_locked(username: str, ip: str):
    state = load_security_state()
    key = f"{username}|{ip}"
    entry = state.get("failed", {}).get(key)
    if not entry:
        return False, 0
    locked_until = entry.get("locked_until")
    if not locked_until:
        return False, 0
    try:
        until = datetime.fromisoformat(locked_until)
    except Exception:
        return False, 0
    if until > datetime.now():
        remaining = int((until - datetime.now()).total_seconds())
        return True, remaining
    return False, 0


def _register_failed_login(username: str, ip: str):
    state = load_security_state()
    failed = state.setdefault("failed", {})
    key = f"{username}|{ip}"
    entry = failed.get(key, {"count": 0, "first": _now_iso(), "locked_until": None})

    try:
        first_dt = datetime.fromisoformat(entry.get("first", _now_iso()))
    except Exception:
        first_dt = datetime.now()

    # reset si fenêtre dépassée
    if (datetime.now() - first_dt) > timedelta(minutes=FAIL_WINDOW_MINUTES):
        entry = {"count": 0, "first": _now_iso(), "locked_until": None}

    entry["count"] = int(entry.get("count", 0)) + 1

    if entry["count"] >= MAX_LOGIN_ATTEMPTS:
        entry["locked_until"] = (datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")

    failed[key] = entry
    save_security_state(state)
    return entry


def _clear_failed_login(username: str, ip: str):
    state = load_security_state()
    key = f"{username}|{ip}"
    if key in state.get("failed", {}):
        state["failed"].pop(key, None)
        save_security_state(state)


DATE_FMT = "%d-%m-%Y %H:%M:%S"
# DATA_DIR est défini plus haut

@app.before_request
def _session_timeout_guard():
    # Timeout d'inactivité
    if "user" in session:
        last = session.get("last_activity")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
            except Exception:
                last_dt = datetime.now()
            if (datetime.now() - last_dt) > timedelta(minutes=INACTIVITY_TIMEOUT_MINUTES):
                u = session.get("user")
                ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
                audit_event(u, "session_timeout", ip, request.headers.get("User-Agent", ""))
                session.clear()
                flash("Session expirée (inactivité). Merci de vous reconnecter.", "warning")
                return redirect(url_for("login"))
        # refresh activity
        session["last_activity"] = datetime.now().isoformat(timespec="seconds")



def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


def _parse_money(value: str) -> float:
    if value is None:
        return 0.0
    s = str(value).strip()
    s = s.replace(" ", "").replace("\u00A0", "")
    # enlève séparateurs de milliers possibles
    s = s.replace(",", "")
    s = re.sub(r"[^0-9\.\-]", "", s)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0



def _parse_int(value: str) -> int:
    """Parse un entier depuis une valeur CSV (ex: '1 234', '1,234', '1234.0')."""
    try:
        return int(round(_parse_money(value)))
    except Exception:
        return 0


def _round_to_multiple(amount: float, step: int = 500) -> float:
    """Arrondi au multiple de `step` le plus proche (half-up).

    Exemple: 48_980 -> 49_000 (step=500).
    """
    try:
        x = float(amount)
    except Exception:
        return 0.0
    if x <= 0:
        return 0.0

    # On travaille en centimes pour éviter les surprises des floats
    cents = int(round(x * 100))
    step_cents = int(step) * 100
    res_cents = ((cents + (step_cents // 2)) // step_cents) * step_cents
    return float(res_cents) / 100.0


def project_reinvest_growth(start_x: float, cycles: int = 12, reinvest: bool = True):
    """Projection multi-cycles.
    Règle : à chaque cycle, la circulation (fin) devient le nouveau X (début).
    Pour rester compatible avec la répartition (multiple de 500), on ajuste X au multiple de 500 le plus proche si nécessaire.
    Retourne une liste de dicts utilisables directement par Jinja.
    """
    try:
        X = float(start_x)
    except Exception:
        raise ValueError("Montant de départ invalide.")

    if cycles <= 0:
        return []

    taux_cycle = RATES_BY_CYCLE[28]  # cycle fixé à 28 jours
    out = []
    for k in range(1, cycles + 1):
        if X <= 0:
            break

        # Ajustement au multiple de 500 le plus proche (arrondi half-up)
        X_adj = _round_to_multiple(X, 500)
        if X_adj <= 0:
            break

        # Répartition optimisée
        n, distances, caps = optimized_distribution(int(X_adj))

        # Détails par niveau
        mec_levels = [c / MEC_VALUE for c in caps]
        interets_levels = compute_interets(caps, taux_cycle)
        commissions_levels = compute_commissions(caps, taux_cycle)

        total_I = float(sum(interets_levels))
        total_C = float(sum(commissions_levels))
        total_MEC_initial = float(sum(mec_levels))

        # Même logique que run_simulation()
        if (X_adj < 3000) or (not reinvest):
            C_tm = 0.0
            Sa = 0.0
            Com_supp = 0.0
            revenu_global = total_I + total_C
            mec_total = total_MEC_initial
            montant_investi = float(X_adj)
        else:
            C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE
            Sa = C_tm - total_C
            Com_supp = 1.24 * C_tm

            # Formule validée
            revenu_global = Com_supp + total_I
            mec_total = total_MEC_initial + (C_tm / MEC_VALUE)
            montant_investi = float(X_adj) + Sa

        # Circulation (fin) = X (début) + revenu_global (cohérent avec le détail d'ordre)
        circulation_brute = float(X_adj) + float(revenu_global)
        circulation_arrondie = _round_to_multiple(circulation_brute, 500)
        rendement = (revenu_global / montant_investi) if montant_investi else 0.0

        out.append({
            "cycle_n": k,
            "X": round(float(X_adj), 2),
            "revenu_global": round(float(revenu_global), 2),
            # Valeur effectivement réutilisée pour le cycle suivant
            "circulation": round(float(circulation_arrondie), 2),
            "circulation_brute": round(float(circulation_brute), 2),
            "circulation_arrondie": round(float(circulation_arrondie), 2),
            "x_next": round(float(circulation_arrondie), 2),
            "mec": round(float(mec_total), 0),
            "rendement": float(rendement),
            "niveaux": int(n),
        })

        # Circulation arrondie devient le X du cycle suivant
        X = circulation_arrondie

    return out


def required_initial_for_target(target: float, cycles: int) -> dict:
    """Calcule le X minimal (approx) pour atteindre au moins 'target' en 'cycles' cycles,
    avec la règle de réinvestissement: circulation(fin) -> X(début) au cycle suivant.
    Retourne {"required_x":..., "final":..., "cycles":...}
    """
    try:
        target = float(target)
    except Exception:
        raise ValueError("Objectif invalide.")
    if target <= 0:
        raise ValueError("Objectif invalide.")
    if cycles <= 0:
        raise ValueError("Nombre de cycles invalide.")

    def final_circulation(x: float) -> float:
        rows = project_reinvest_growth(x, cycles=cycles, reinvest=True)
        if not rows:
            return 0.0
        return float(rows[-1].get("circulation", 0.0))

    # borne haute: on double jusqu'à dépasser l'objectif
    low = 0.0
    high = max(500.0, target)
    fc = final_circulation(high)
    guard = 0
    while fc < target and guard < 60:
        high *= 2.0
        fc = final_circulation(high)
        guard += 1

    # dichotomie (tolérance ~ 1 USDT)
    for _ in range(60):
        mid = (low + high) / 2.0
        if final_circulation(mid) >= target:
            high = mid
        else:
            low = mid
    req_x = max(0.0, high)

    # ajustement à la règle multiple 500 (comme le moteur)
    # on prend un multiple de 500, et on garantit d'atteindre l'objectif (arrondi puis éventuel +500)
    req_x_adj = _round_to_multiple(req_x, 500)
    if req_x_adj <= 0:
        req_x_adj = 500.0

    guard = 0
    while final_circulation(req_x_adj) < target and guard < 50:
        req_x_adj += 500.0
        guard += 1

    final_val = final_circulation(req_x_adj)
    return {"required_x": req_x_adj, "final": final_val, "cycles": cycles}




def _make_csv_bytes(fieldnames, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, delimiter=';')
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return buf.getvalue().encode("utf-8")


def _make_pdf_bytes(title, headers, rows):
    """Génère un PDF. Si reportlab n'est pas installé, lève RuntimeError."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
    except Exception as e:
        raise RuntimeError("PDF indisponible : installe 'reportlab' avec: python -m pip install reportlab") from e

    pdf = io.BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(pdf, pagesize=letter, rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    story = []
    story.append(Paragraph(title, styles['Title']))
    story.append(Spacer(1, 12))
    data = [headers]
    for r in rows:
        data.append([str(r.get(h, '')) for h in headers])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(t)
    doc.build(story)
    pdf.seek(0)
    return pdf.getvalue()

def _parse_dt(s: str):
    try:
        return datetime.strptime((s or "").strip(), DATE_FMT)
    except Exception:
        return None

def _month_add(d: datetime, months: int) -> datetime:
    y = d.year
    m = d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return datetime(y, m, 1)

def _month_range(end_dt: datetime, months_back: int):
    """Retourne une liste de labels YYYY-MM du mois (end_dt) vers -months_back+1."""
    end_month = datetime(end_dt.year, end_dt.month, 1)
    start_month = _month_add(end_month, -(months_back - 1))
    labels = []
    cur = start_month
    while cur <= end_month:
        labels.append(cur.strftime("%Y-%m"))
        cur = _month_add(cur, 1)
    return labels


def _safe_extract_zip(zip_file, dest_dir: str):
    # protège contre zip-slip
    with zipfile.ZipFile(zip_file, "r") as z:
        for member in z.namelist():
            if member.startswith("/") or member.startswith("\\"):
                raise ValueError("Archive invalide (chemin absolu).")
            if ".." in Path(member).parts:
                raise ValueError("Archive invalide (chemin relatif interdit).")
        z.extractall(dest_dir)


def _read_csv_any_delimiter(file_bytes: bytes):
    # accepte ; ou ,
    text = file_bytes.decode("utf-8", errors="ignore")
    sample = text[:4096]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    f = io.StringIO(text)
    reader = csv.DictReader(f, delimiter=delim)
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return reader.fieldnames or [], rows


EXPECTED_DASHBOARD_HEADERS = [
    "date_calcul", "date_fin_cycle", "montant_investi", "interets", "commissions",
    "commission_supplementaire", "revenu_global", "MEC", "circulation", "rendement"
]

EXPECTED_HISTORY_HEADERS = [
    "date", "investissement", "interets", "commissions_totales", "commission_supplementaire",
    "revenu_global", "nombre_MEC", "rendement", "cycle", "taux_interet"
]

def user_dir(username: str) -> str:
    path = os.path.join(DATA_DIR, username)
    os.makedirs(path, exist_ok=True)
    return path


def orders_dir(username: str) -> str:
    path = os.path.join(user_dir(username), "orders")
    os.makedirs(path, exist_ok=True)
    return path


def dashboard_path(username: str) -> str:
    return os.path.join(user_dir(username), "Tableau_Bord.csv")



def favorites_path(username: str) -> str:
    return os.path.join(user_dir(username), "favorites.json")


def load_favorites(username: str) -> set:
    """Retourne un set d'order_idx épinglés."""
    path = favorites_path(username)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        fav = data.get("favorites", [])
        return set(int(x) for x in fav)
    except Exception:
        return set()


def save_favorites(username: str, fav_set: set) -> None:
    path = favorites_path(username)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"favorites": sorted(list(fav_set))}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def history_path(username: str) -> str:
    return os.path.join(user_dir(username), "historique_investissements.csv")


def reset_user_order_data(username: str) -> None:
    """Réinitialise toutes les données liées aux ordres pour un utilisateur.
    - Supprime ordres (JSON), favoris, CSV (dashboard/historique/répartitions), exports, etc.
    - Ne touche pas aux comptes (auth) ni au journal global de sécurité.
    """
    base = os.path.join(DATA_DIR, username)
    try:
        shutil.rmtree(base)
    except FileNotFoundError:
        pass
    except Exception:
        # fallback: suppression sélective
        try:
            for root, dirs, files in os.walk(base):
                for fn in files:
                    try:
                        os.remove(os.path.join(root, fn))
                    except Exception:
                        pass
                for d in dirs:
                    try:
                        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass

    os.makedirs(base, exist_ok=True)
    os.makedirs(os.path.join(base, "orders"), exist_ok=True)


def order_json_path(username: str, order_idx: int) -> str:
    return os.path.join(orders_dir(username), f"order_{order_idx}.json")

def existing_order_indices(username: str):
    idxs = []
    try:
        for name in os.listdir(orders_dir(username)):
            m = re.match(r"order_(\d+)\.json$", name)
            if m:
                idxs.append(int(m.group(1)))
    except FileNotFoundError:
        pass
    return sorted(set(idxs))


def next_order_idx(username: str) -> int:
    # ⚠️ Ne jamais se baser uniquement sur Tableau_Bord.csv (peut être supprimé)
    idxs = existing_order_indices(username)
    rows = load_dashboard(filename=dashboard_path(username))
    max_from_csv = len(rows) - 1
    max_from_json = max(idxs) if idxs else -1
    return max(max_from_csv, max_from_json) + 1


def migrate_dashboard_to_orders_json(username: str):
    """Crée des JSON d'ordres (minimaux) à partir du Tableau_Bord.csv si besoin.
    Objectif : garder les ordres visibles même si les CSV sont supprimés.
    """
    rows = load_dashboard(filename=dashboard_path(username))
    for idx, r in enumerate(rows):
        p = order_json_path(username, idx)
        if os.path.exists(p):
            continue

        start_dt = _safe_dt(r.get("date_calcul", ""))
        end_dt = _safe_dt(r.get("date_fin_cycle", ""))
        cycle_days = 0
        if start_dt and end_dt:
            cycle_days = max(1, (end_dt - start_dt).days)

        detail = {
            "order_idx": idx,
            "legacy": True,
            "date_calcul": r.get("date_calcul", ""),
            "date_fin_cycle": r.get("date_fin_cycle", ""),
            "cycle": cycle_days or 28,

            # champs legacy (issus du CSV)
            "montant_investi": r.get("montant_investi", ""),
            "interets": r.get("interets", ""),
            "commissions": r.get("commissions", ""),
            "commission_supplementaire": r.get("commission_supplementaire", ""),
            "revenu_global": r.get("revenu_global", ""),
            "MEC": r.get("MEC", ""),
            "circulation": r.get("circulation", ""),
            "circulation_arrondie": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)) if str(r.get("circulation", "")).strip() else "",
            "x_next": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)) if str(r.get("circulation", "")).strip() else "",
            "rendement": r.get("rendement", ""),

            # champs “moteur” (compat template détail)
            "Revenu_global": float(_parse_money(r.get("revenu_global", "0"))),
            "circulation_val": float(_parse_money(r.get("circulation", "0"))),
            "circulation": float(_parse_money(r.get("circulation", "0"))),
            "circulation_arrondie": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)),
            "x_next": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)),
            "total_I": float(_parse_money(r.get("interets", "0"))),
            "total_C": float(_parse_money(r.get("commissions", "0"))),
            "Com_supp": float(_parse_money(r.get("commission_supplementaire", "0"))),
            "total_MEC_global": float(_parse_int(r.get("MEC", "0"))),
            "montant_investi_val": float(_parse_money(r.get("montant_investi", "0"))),
            "montant_investi": float(_parse_money(r.get("montant_investi", "0"))),
            "rendement": float(_parse_money(r.get("rendement", "0"))),
        }
        # Nettoyage : double clé circulation_val pour éviter confusion
        detail.pop("circulation_val", None)

        with open(p, "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)


def next_repartition_filename(username: str) -> str:
    """
    Repartition_MEC_n.csv incrémental, par utilisateur.
    """
    base_dir = user_dir(username)
    prefix = "Repartition_MEC_"
    suffix = ".csv"
    max_idx = 0
    for name in os.listdir(base_dir):
        if name.startswith(prefix) and name.endswith(suffix):
            middle = name[len(prefix):-len(suffix)]
            try:
                idx = int(middle)
                max_idx = max(max_idx, idx)
            except ValueError:
                pass
    return os.path.join(base_dir, f"{prefix}{max_idx + 1}{suffix}")


def save_repartition_csv_user(username: str, mec, caps):
    filename = next_repartition_filename(username)
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
    return filename


def _safe_dt(s: str):
    try:
        return datetime.strptime(s, DATE_FMT)
    except Exception:
        return None


def run_simulation(username: str, X: float, reinvest: bool):
    if X <= 0:
        raise ValueError("Le montant X doit être strictement positif.")
    if X % 500 != 0:
        raise ValueError("Le montant X doit être un multiple de 500.")

    # ✅ Cycle fixé à 28 jours
    cycle = 28
    taux_cycle = RATES_BY_CYCLE[28]

    now = datetime.now()
    date_norm = now.strftime(DATE_FMT)
    date_fin_cycle = (now + timedelta(days=cycle)).strftime(DATE_FMT)

    # ✅ index de l’ordre (robuste même si les CSV sont supprimés)
    migrate_dashboard_to_orders_json(username)
    order_idx = next_order_idx(username)

    n_opt, mec, caps = optimized_distribution(X)
    repart_file = save_repartition_csv_user(username, mec, caps)

    interets = compute_interets(caps, taux_cycle)
    commissions = compute_commissions(caps, taux_interet=taux_cycle)

    total_I = sum(interets)
    total_C = sum(commissions)
    total_R = total_I + total_C
    total_MEC_initial = sum(mec)

    # (mêmes règles que ta V1.0)
    if X < 3000:
        C_tm = 0.0
        Sa = 0.0
        Com_supp = 0.0

        Revenu_global = total_I + total_C
        total_MEC_global = total_MEC_initial
        montant_investi = X
        r = Revenu_global / montant_investi if montant_investi else 0.0

    else:
        if reinvest:
            C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE
            Sa = C_tm - total_C
            Com_supp = 1.24 * C_tm

            # ✅ Formule validée
            Revenu_global = Com_supp + total_I

            total_MEC_global = total_MEC_initial + (C_tm / MEC_VALUE)
            montant_investi = X + Sa
            r = Revenu_global / montant_investi if montant_investi else 0.0
        else:
            C_tm = 0.0
            Sa = 0.0
            Com_supp = 0.0

            Revenu_global = total_I + total_C
            total_MEC_global = total_MEC_initial
            montant_investi = X
            r = Revenu_global / montant_investi if montant_investi else 0.0

    # ✅ Circulation (cohérent avec ton tableau de bord)
    circulation = Revenu_global + montant_investi - Sa

    # ✅ Circulation arrondie (multiple de 500) : utilisée pour l'ordre suivant et les projections
    circulation_arrondie = _round_to_multiple(circulation, 500)
    x_next = circulation_arrondie

    niveaux = []
    for idx, (m, cap, i_val, c_val) in enumerate(zip(mec, caps, interets, commissions)):
        role = "Parrain" if idx == 0 else "Filleul"
        niveaux.append({
            "niveau": idx,
            "role": role,
            "mec": int(m),
            "capital": float(cap),
            "interet": float(i_val),
            "commission": float(c_val),
            "revenu": float(i_val + c_val)
        })

    # ✅ CSV historiques + dashboard (par utilisateur)
    append_history({
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
    }, filename=history_path(username))

    append_dashboard({
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
    }, filename=dashboard_path(username))

    # ✅ Sauvegarde détaillée (JSON) pour la page “Détail d’un ordre”
    detail = {
        "order_idx": order_idx,
        "date_calcul": date_norm,
        "date_fin_cycle": date_fin_cycle,
        "cycle": cycle,
        "taux_cycle": taux_cycle,
        "X": float(X),
        "reinvest": bool(reinvest),
        "repartition_csv": os.path.basename(repart_file),
        "niveaux": niveaux,

        "total_I": float(total_I),
        "total_C": float(total_C),
        "total_R": float(total_R),
        "C_tm": float(C_tm),
        "Sa": float(Sa),
        "Com_supp": float(Com_supp),
        "Revenu_global": float(Revenu_global),
        "circulation": float(circulation),
        "circulation_arrondie": float(circulation_arrondie),
        "x_next": float(x_next),
        "total_MEC_initial": float(total_MEC_initial),
        "total_MEC_global": float(total_MEC_global),
        "montant_investi": float(montant_investi),
        "rendement": float(r),
    }
    with open(order_json_path(username, order_idx), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)

    return detail


@app.route("/login", methods=["GET", "POST"])
def login():
    users = load_users()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = _client_ip()
        ua = request.headers.get("User-Agent", "")

        # Vérifier lockout
        locked, remaining = _is_locked(username, ip)
        if locked:
            mins = max(1, int(math.ceil(remaining / 60)))
            audit_event(username or "-", "login_blocked_lockout", ip, ua, f"remaining_s={remaining}")
            flash(f"Trop de tentatives. Compte verrouillé pour ~{mins} min.", "danger")
            return render_template("login.html", users=list(users.keys()))

        # Auth
        if username in users and check_password_hash(users[username]["password_hash"], password):
            session["user"] = username
            session["last_activity"] = datetime.now().isoformat(timespec="seconds")
            _clear_failed_login(username, ip)

            # last login
            state = load_security_state()
            state.setdefault("last_login", {})[username] = {
                "when": _now_iso(),
                "ip": ip,
                "ua": (ua or "")[:160],
            }
            save_security_state(state)

            audit_event(username, "login_success", ip, ua)
            flash("Connexion réussie.", "success")
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)

        # Échec
        entry = _register_failed_login(username or "-", ip)
        audit_event(username or "-", "login_failed", ip, ua, f"count={entry.get('count')}")
        remaining_tries = max(0, MAX_LOGIN_ATTEMPTS - int(entry.get("count", 0)))
        if entry.get("locked_until"):
            flash(f"Identifiants incorrects. Compte verrouillé {LOCKOUT_MINUTES} min.", "danger")
        else:
            flash(f"Identifiants incorrects. Il reste {remaining_tries} tentative(s).", "danger")

    return render_template("login.html", users=list(users.keys()))



@app.post("/orders/favorite/<int:order_idx>")
@login_required
def orders_toggle_favorite(order_idx: int):
    username = session["user"]
    fav_set = load_favorites(username)
    if order_idx in fav_set:
        fav_set.remove(order_idx)
    else:
        fav_set.add(order_idx)
    save_favorites(username, fav_set)
    return redirect(request.referrer or url_for("orders"))


@app.route("/logout")
@login_required
def logout():
    u = session.get("user")
    ip = _client_ip()
    audit_event(u, "logout", ip, request.headers.get("User-Agent", ""))
    session.clear()
    flash("Déconnecté(e).", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    # Permet de pré-remplir X depuis la page Résultats/Détail d'ordre
    raw = (request.args.get("prefill_x") or "").strip()
    prefill_x = ""
    if raw:
        try:
            val = float(raw.replace(",", ""))
            if val > 0:
                # Respecter la règle : X doit être un multiple de 500
                prefill_x = str(int(_round_to_multiple(val, 500)))
        except Exception:
            prefill_x = ""
    return render_template("index.html", prefill_x=prefill_x)


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    try:
        X = float(request.form.get("montant", "0"))
    except ValueError:
        flash("Montant invalide.", "danger")
        return redirect(url_for("index"))

    reinvest = (request.form.get("reinvest", "oui").lower() in ("oui", "o", "yes", "y"))

    try:
        detail = run_simulation(session["user"], X, reinvest)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("index"))

    return render_template("resultats.html", result=detail)



@app.route("/dashboard")
@login_required
def dashboard():
    rows = load_dashboard(filename=dashboard_path(session["user"]))
    return render_template("dashboard.html", rows=rows)


@app.route("/analytics")
@login_required
def analytics():
    username = session["user"]
    rows = load_dashboard(filename=dashboard_path(username))
    now = datetime.now()

    # range in months: 1 / 6 / 12 (default 12)
    range_m = request.args.get("range", "12").strip()
    if range_m not in {"1", "6", "12"}:
        range_m = "12"
    months_back = int(range_m)

    labels = _month_range(now, months_back)
    by_month = {k: {"circulation": 0.0, "revenu_global": 0.0, "mec": 0.0, "commissions": 0.0, "interets": 0.0, "rendements": []} for k in labels}

    # KPIs sur la fenêtre
    total_commissions = 0.0
    total_interets = 0.0

    # Retour attendu (cycles en cours) sur tout l'historique
    retour_attendu = 0.0
    cycles_en_cours = 0

    # Dernière circulation (pour projection)
    last_circ = 0.0
    last_dt = None

    for r in rows:
        dt = _parse_dt(r.get("date_calcul", ""))
        end_dt = _parse_dt(r.get("date_fin_cycle", ""))
        circ = _parse_money(r.get("circulation", "0"))
        revg = _parse_money(r.get("revenu_global", "0"))
        mec = _parse_money(r.get("MEC", "0"))
        comm = _parse_money(r.get("commissions", "0"))
        inter = _parse_money(r.get("interets", "0"))
        rend = _parse_money(str(r.get("rendement", "0")).replace("%",""))  # ex "12,3 %"

        if dt:
            # last circulation
            if (last_dt is None) or (dt > last_dt):
                last_dt = dt
                last_circ = circ

            month_key = dt.strftime("%Y-%m")
            if month_key in by_month:
                by_month[month_key]["circulation"] += circ
                by_month[month_key]["revenu_global"] += revg
                by_month[month_key]["mec"] += mec
                by_month[month_key]["commissions"] += comm
                by_month[month_key]["interets"] += inter
                if rend:
                    by_month[month_key]["rendements"].append(rend)

                total_commissions += comm
                total_interets += inter

        # cycles en cours: payé si end_dt <= now
        if end_dt and end_dt > now:
            retour_attendu += circ
            cycles_en_cours += 1

    # séries chart
    series_circulation = [round(by_month[k]["circulation"], 2) for k in labels]
    series_revenu = [round(by_month[k]["revenu_global"], 2) for k in labels]
    series_mec = [round(by_month[k]["mec"], 2) for k in labels]

    # rendement moyen + meilleur mois (revenu global)
    all_rends = []
    best_month = None
    best_month_val = -1.0
    for k in labels:
        all_rends += by_month[k]["rendements"]
        if by_month[k]["revenu_global"] > best_month_val:
            best_month_val = by_month[k]["revenu_global"]
            best_month = k

    rendement_moyen = (sum(all_rends) / len(all_rends)) if all_rends else 0.0

    # Projection reinvest (3/6/12)
    start_x_raw = request.args.get("start_x", "").strip()
    if start_x_raw:
        start_x = _parse_money(start_x_raw)
    else:
        start_x = last_circ or 0.0

    adjusted = False
    if start_x > 0 and (start_x % 500 != 0):
        start_x = _round_to_multiple(start_x, 500)
        adjusted = True

    projection_rows = []
    proj_3 = proj_6 = proj_12 = None
    proj_err = None

    # Objectif: atteindre un montant cible en N cycles
    obj_target_raw = request.args.get("target", "").strip()
    obj_cycles_raw = request.args.get("target_cycles", "12").strip()
    if obj_cycles_raw not in {"3", "6", "12"}:
        obj_cycles_raw = "12"
    obj_cycles = int(obj_cycles_raw)
    obj_required_x = None
    obj_final = None
    obj_err = None
    obj_target_val = None
    if start_x > 0:
        # règle reinvest : activé si X >= 3000
        reinvest_flag = True
        try:
            projection_rows = project_reinvest_growth(start_x, 12, reinvest=reinvest_flag)
            proj_3 = projection_rows[2]["circulation"] if len(projection_rows) >= 3 else None
            proj_6 = projection_rows[5]["circulation"] if len(projection_rows) >= 6 else None
            proj_12 = projection_rows[11]["circulation"] if len(projection_rows) >= 12 else None
        except Exception as e:
            proj_err = str(e)

    # calcul objectif (si renseigné)
    if obj_target_raw:
        try:
            target_val = _parse_money(obj_target_raw)
            obj_target_val = target_val
            out_obj = required_initial_for_target(target_val, cycles=obj_cycles)
            obj_required_x = out_obj["required_x"]
            obj_final = out_obj["final"]
        except Exception as e:
            obj_err = str(e)

    return render_template(
        "analytics.html",
        range_m=range_m,
        labels=labels,
        series_circulation=series_circulation,
        series_revenu=series_revenu,
        series_mec=series_mec,
        total_commissions=round(total_commissions, 2),
        total_interets=round(total_interets, 2),
        rendement_moyen=round(rendement_moyen, 2),
        best_month=best_month,
        best_month_val=round(best_month_val, 2) if best_month is not None else 0.0,
        retour_attendu=round(retour_attendu, 2),
        cycles_en_cours=cycles_en_cours,
        start_x=start_x,
        adjusted=adjusted,
        projection_rows=projection_rows,
        proj_3=proj_3,
        proj_6=proj_6,
        proj_12=proj_12,
        proj_err=proj_err,
        obj_target=obj_target_raw,
        obj_cycles=obj_cycles_raw,
        obj_required_x=obj_required_x,
        obj_final=obj_final,
        obj_err=obj_err,
        obj_target_val=obj_target_val,
    )


@app.route("/tools")
@login_required
def tools():
    username = session["user"]
    base = user_dir(username)
    csv_files = []
    try:
        csv_files = sorted([f for f in os.listdir(base) if f.lower().endswith(".csv")])
    except Exception:
        csv_files = []
    return render_template("tools.html", csv_files=csv_files)



@app.route("/cleanup/csv", methods=["POST"])
@login_required
def cleanup_csv():
    username = session["user"]
    action = (request.form.get("action") or "").strip().lower()
    base = user_dir(username)

    removed = []
    errors = []

    def _safe_remove(path):
        try:
            os.remove(path)
            removed.append(os.path.basename(path))
        except FileNotFoundError:
            pass
        except Exception as e:
            errors.append(f"{os.path.basename(path)}: {e}")

    if action == "repartition":
        for name in os.listdir(base):
            if name.startswith("Repartition_MEC_") and name.lower().endswith(".csv"):
                _safe_remove(os.path.join(base, name))

    elif action == "dashboard_history":
        _safe_remove(os.path.join(base, "Tableau_Bord.csv"))
        _safe_remove(os.path.join(base, "historique_investissements.csv"))

    elif action == "all":
        for name in os.listdir(base):
            if name.lower().endswith(".csv"):
                _safe_remove(os.path.join(base, name))

    else:
        flash("Action de nettoyage invalide.", "danger")
        return redirect(url_for("tools"))

    if removed:
        flash(f"{len(removed)} fichier(s) CSV supprimé(s) : " + ", ".join(removed[:8]) + ("..." if len(removed) > 8 else ""), "success")
    else:
        flash("Aucun fichier CSV à supprimer pour cette action.", "info")

    if errors:
        flash("Certains fichiers n'ont pas pu être supprimés : " + "; ".join(errors[:3]) + ("..." if len(errors) > 3 else ""), "warning")

    return redirect(url_for("tools"))

@app.route("/backup/download")
@login_required
def backup_download():
    username = session["user"]
    base_dir = user_dir(username)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root_dir, dirs, files in os.walk(base_dir):
            for fn in files:
                full = os.path.join(root_dir, fn)
                rel = os.path.relpath(full, base_dir)
                z.write(full, rel)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"backup_{username}.zip"
    )


@app.route("/backup/restore", methods=["GET", "POST"])
@login_required
def backup_restore():
    username = session["user"]
    if request.method == "POST":
        f = request.files.get("backup_zip")
        if not f or not f.filename.lower().endswith(".zip"):
            flash("Veuillez choisir une archive .zip valide.", "danger")
            return redirect(url_for("backup_restore"))

        # extraction dans un dossier temporaire, puis copie
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = os.path.join(tmp, "extract")
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                _safe_extract_zip(f, tmp_dir)
            except Exception as e:
                flash(f"Archive invalide : {e}", "danger")
                return redirect(url_for("backup_restore"))

            target = user_dir(username)
            # copie (écrase)
            for root_dir, dirs, files in os.walk(tmp_dir):
                rel_root = os.path.relpath(root_dir, tmp_dir)
                for d in dirs:
                    os.makedirs(os.path.join(target, rel_root, d), exist_ok=True)
                for fn in files:
                    src = os.path.join(root_dir, fn)
                    dst = os.path.join(target, rel_root, fn)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

        flash("Restauration terminée.", "success")
        return redirect(url_for("tools"))

    return render_template("restore.html")


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_data():
    username = session["user"]
    if request.method == "POST":
        dash_file = request.files.get("dashboard_csv")
        hist_file = request.files.get("history_csv")

        if not dash_file and not hist_file:
            flash("Veuillez sélectionner au moins un fichier CSV.", "danger")
            return redirect(url_for("import_data"))

        # Dashboard
        if dash_file and dash_file.filename:
            headers, rows = _read_csv_any_delimiter(dash_file.read())
            # normalisation d'entêtes : on garde uniquement l'attendu
            if not set(EXPECTED_DASHBOARD_HEADERS).issubset(set([h.strip() for h in headers])):
                flash("Dashboard CSV : entêtes manquantes. Attendu : " + ", ".join(EXPECTED_DASHBOARD_HEADERS), "danger")
                return redirect(url_for("import_data"))

            out_rows = []
            for r in rows:
                out_rows.append({h: (r.get(h, "") or "").strip() for h in EXPECTED_DASHBOARD_HEADERS})

            out = _make_csv_bytes(EXPECTED_DASHBOARD_HEADERS, out_rows)
            Path(dashboard_path(username)).write_bytes(out)

        # Historique
        if hist_file and hist_file.filename:
            headers, rows = _read_csv_any_delimiter(hist_file.read())
            if not set(EXPECTED_HISTORY_HEADERS).issubset(set([h.strip() for h in headers])):
                flash("Historique CSV : entêtes manquantes. Attendu : " + ", ".join(EXPECTED_HISTORY_HEADERS), "danger")
                return redirect(url_for("import_data"))

            out_rows = []
            for r in rows:
                out_rows.append({h: (r.get(h, "") or "").strip() for h in EXPECTED_HISTORY_HEADERS})

            out = _make_csv_bytes(EXPECTED_HISTORY_HEADERS, out_rows)
            Path(history_path(username)).write_bytes(out)

        flash("Import terminé.", "success")
        return redirect(url_for("tools"))

    return render_template("import.html")


@app.route("/export/dashboard.<string:fmt>")
@login_required
def export_dashboard(fmt: str):
    username = session["user"]
    rows = load_dashboard(filename=dashboard_path(username))

    fieldnames = EXPECTED_DASHBOARD_HEADERS
    out_rows = []
    for r in rows:
        out_rows.append({h: r.get(h, "") for h in fieldnames})

    if fmt.lower() == "csv":
        data = _make_csv_bytes(fieldnames, out_rows)
        return send_file(
            io.BytesIO(data),
            mimetype="text/csv",
            as_attachment=True,
            download_name="Tableau_Bord.csv"
        )
    elif fmt.lower() == "pdf":
        headers = [
            "date_calcul","date_fin_cycle","montant_investi","interets","commissions",
            "commission_supplementaire","revenu_global","MEC","circulation","rendement"
        ]
        try:
            pdf = _make_pdf_bytes("Tableau de bord — MellionCoin", headers, out_rows)
        except RuntimeError as e:
            return str(e), 400
        return send_file(
            io.BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="Tableau_Bord.pdf"
        )
    abort(404)


@app.route("/export/orders.<string:fmt>")
@login_required
def export_orders(fmt: str):
    # export = tous les ordres filtrés (mêmes paramètres que /orders)
    username = session["user"]
    rows = load_dashboard(filename=dashboard_path(username))
    now = datetime.now()

    # construit la liste brute
    all_orders = []
    for idx, r in enumerate(rows):
        start_dt = _safe_dt(r.get("date_calcul", ""))
        end_dt = _safe_dt(r.get("date_fin_cycle", ""))
        if not start_dt or not end_dt:
            continue

        cycle_days = max(1, (end_dt - start_dt).days)
        revenu = _parse_money(r.get("revenu_global", "0"))
        circ = _parse_money(r.get("circulation", "0"))
        investi_initial = max(0.0, circ - revenu)

        is_paid = end_dt <= now
        remaining = 0 if is_paid else max(0, int((end_dt - now).total_seconds()))

        all_orders.append({
            "id": idx,
            "start_dt": start_dt,
            "date_calcul": r.get("date_calcul", ""),
            "date_fin_cycle": r.get("date_fin_cycle", ""),
            "cycle_days": cycle_days,
            "investi_initial": investi_initial,
            "revenu_global": revenu,
            "circulation": circ,
            "remaining": remaining,
            "paid": is_paid,
            "status": "PAYÉ" if is_paid else "EN COURS",
            "MEC": _parse_int(r.get("MEC", "0"))
        })

    all_orders.sort(key=lambda x: x["start_dt"], reverse=True)

    # applique les filtres (comme /orders)
    status = (request.args.get("status") or "").strip().lower()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    amount_min = _parse_money(request.args.get("amount_min", ""))
    amount_max = _parse_money(request.args.get("amount_max", ""))
    mec_min = _parse_int(request.args.get("mec_min", ""))
    mec_max = _parse_int(request.args.get("mec_max", ""))
    q = (request.args.get("q") or "").strip().lower()

    df = None
    dt = None
    try:
        if date_from:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
    except Exception:
        df = None
    try:
        if date_to:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
    except Exception:
        dt = None

    filtered = []
    for o in all_orders:
        if status == "open" and o["paid"]:
            continue
        if status == "paid" and not o["paid"]:
            continue

        od = o["start_dt"].date()
        if df and od < df:
            continue
        if dt and od > dt:
            continue

        if request.args.get("amount_min", "").strip() and o["investi_initial"] < amount_min:
            continue
        if request.args.get("amount_max", "").strip() and o["investi_initial"] > amount_max:
            continue

        if request.args.get("mec_min", "").strip() and o["MEC"] < mec_min:
            continue
        if request.args.get("mec_max", "").strip() and o["MEC"] > mec_max:
            continue

        if q:
            hay = f"{o['id']} {o['date_calcul']} {o['date_fin_cycle']} {o['status']} {o['MEC']}".lower()
            if q not in hay:
                continue

        filtered.append(o)

    # format export
    out_rows = []
    for o in filtered:
        out_rows.append({
            "id": o["id"],
            "date_calcul": o["date_calcul"],
            "date_fin_cycle": o["date_fin_cycle"],
            "cycle_jours": o["cycle_days"],
            "investi_initial": f"{o['investi_initial']:.2f}",
            "revenu_global": f"{o['revenu_global']:.2f}",
            "circulation": f"{o['circulation']:.2f}",
            "MEC": str(o["MEC"]),
            "statut": o["status"]
        })

    fieldnames = ["id","date_calcul","date_fin_cycle","cycle_jours","investi_initial","revenu_global","circulation","MEC","statut"]

    if fmt.lower() == "csv":
        data = _make_csv_bytes(fieldnames, out_rows)
        return send_file(
            io.BytesIO(data),
            mimetype="text/csv",
            as_attachment=True,
            download_name="Ordres.csv"
        )
    elif fmt.lower() == "pdf":
        try:
            pdf = _make_pdf_bytes("Mes ordres — MellionCoin", fieldnames, out_rows)
        except RuntimeError as e:
            return str(e), 400
        return send_file(
            io.BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="Ordres.pdf"
        )
    abort(404)
@app.route("/orders")
@login_required
def orders():
    username = session["user"]

    # ✅ on migre le CSV → JSON (si besoin), pour que les ordres restent visibles même après suppression des CSV
    migrate_dashboard_to_orders_json(username)

    now = datetime.now()
    fav_set = load_favorites(username)

    def _as_float(v, default=0.0):
        try:
            if v is None:
                return float(default)
            if isinstance(v, (int, float)):
                return float(v)
            return float(_parse_money(str(v)))
        except Exception:
            return float(default)

    def _as_int(v, default=0):
        try:
            if v is None:
                return int(default)
            if isinstance(v, int):
                return int(v)
            if isinstance(v, float):
                return int(v)
            return int(_parse_int(str(v)))
        except Exception:
            return int(default)

    all_orders = []
    for order_idx in existing_order_indices(username):
        p = order_json_path(username, order_idx)
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f) or {}
        except Exception:
            continue

        start_dt = _safe_dt(d.get("date_calcul", ""))
        end_dt = _safe_dt(d.get("date_fin_cycle", ""))
        if not start_dt or not end_dt:
            continue

        cycle_days = int(d.get("cycle") or max(1, (end_dt - start_dt).days))

        circ = _as_float(d.get("circulation", 0))
        revenu = _as_float(d.get("Revenu_global", d.get("revenu_global", 0)))
        montant_investi = _as_float(d.get("montant_investi", max(0.0, circ - revenu)))
        investi_initial_val = montant_investi

        mec_val = _as_int(d.get("total_MEC_global", d.get("MEC", 0)))

        is_paid = end_dt <= now
        remaining = 0 if is_paid else max(0, int((end_dt - now).total_seconds()))

        # ✅ Alertes fin de cycle (J-3 / J-1 / aujourd'hui)
        alert_badge = ""
        if not is_paid:
            days_left = (end_dt.date() - now.date()).days
            if days_left == 0:
                alert_badge = "AUJOURD'HUI"
            elif days_left == 1:
                alert_badge = "J-1"
            elif days_left == 3:
                alert_badge = "J-3"

        all_orders.append({
            "order_idx": order_idx,
            "start_dt": start_dt,
            "date_calcul": d.get("date_calcul", ""),
            "date_fin_cycle": d.get("date_fin_cycle", ""),
            "cycle_days": cycle_days,
            "investi_initial": f"{investi_initial_val:,.2f}",
            "investi_initial_val": investi_initial_val,
            "circulation": f"{circ:,.2f}",
            "circulation_val": circ,
            "remaining": remaining,
            "paid": is_paid,
            "MEC": str(mec_val),
            "mec_val": mec_val,
            "favorite": (order_idx in fav_set),
            "alert_badge": alert_badge,
            "detail_ok": True
        })

    # ✅ plus récent → plus ancien
    all_orders.sort(key=lambda x: x["start_dt"], reverse=True)

    # -----------------------------
    # ✅ Recherche + filtres (GET)
    # -----------------------------
    status = (request.args.get("status", "all") or "all").lower()
    q_raw = (request.args.get("q", "") or "").strip()
    q = q_raw.lower()

    date_from = (request.args.get("date_from", "") or "").strip()
    date_to = (request.args.get("date_to", "") or "").strip()

    min_amount_raw = (request.args.get("min_amount", "") or "").strip()
    max_amount_raw = (request.args.get("max_amount", "") or "").strip()

    min_mec_raw = (request.args.get("min_mec", "") or "").strip()
    max_mec_raw = (request.args.get("max_mec", "") or "").strip()

    def _parse_date_ymd(s: str):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    df = _parse_date_ymd(date_from) if date_from else None
    dt = _parse_date_ymd(date_to) if date_to else None

    min_amt = _parse_money(min_amount_raw) if min_amount_raw else None
    max_amt = _parse_money(max_amount_raw) if max_amount_raw else None

    min_mec = _parse_int(min_mec_raw) if min_mec_raw else None
    max_mec = _parse_int(max_mec_raw) if max_mec_raw else None

    filtered = []
    for o in all_orders:
        # statut
        if status == "open" and o["paid"]:
            continue
        if status == "paid" and not o["paid"]:
            continue

        # date (sur la date de calcul)
        od = o["start_dt"].date()
        if df and od < df:
            continue
        if dt and od > dt:
            continue

        # montant (investi initial estimé)
        if min_amt is not None and o["investi_initial_val"] < min_amt:
            continue
        if max_amt is not None and o["investi_initial_val"] > max_amt:
            continue

        # MEC
        if min_mec is not None and o["mec_val"] < min_mec:
            continue
        if max_mec is not None and o["mec_val"] > max_mec:
            continue

        # recherche
        if q:
            hay = f'{o["order_idx"]} {o["date_calcul"]} {o["date_fin_cycle"]} {"PAYE" if o["paid"] else "EN_COURS"}'.lower()
            if q not in hay:
                continue

        filtered.append(o)

    # ✅ épinglés en haut, puis plus récent → plus ancien
    filtered.sort(key=lambda x: (0 if x.get('favorite') else 1, -x['start_dt'].timestamp()))

    total_all = len(all_orders)
    total = len(filtered)

    # ✅ pagination (10 ordres par page)
    per_page = 10
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(1, page)

    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)

    start = (page - 1) * per_page
    end = start + per_page
    page_orders = filtered[start:end]

    # ✅ params à conserver dans les liens de pagination
    url_params = {}
    if status and status != "all":
        url_params["status"] = status
    if q_raw:
        url_params["q"] = q_raw
    if date_from:
        url_params["date_from"] = date_from
    if date_to:
        url_params["date_to"] = date_to
    if min_amount_raw:
        url_params["min_amount"] = min_amount_raw
    if max_amount_raw:
        url_params["max_amount"] = max_amount_raw
    if min_mec_raw:
        url_params["min_mec"] = min_mec_raw
    if max_mec_raw:
        url_params["max_mec"] = max_mec_raw

    filters = {
        "status": status,
        "q": q_raw,
        "date_from": date_from,
        "date_to": date_to,
        "min_amount": min_amount_raw,
        "max_amount": max_amount_raw,
        "min_mec": min_mec_raw,
        "max_mec": max_mec_raw
    }

    return render_template(
        "orders.html",
        orders=page_orders,
        page=page,
        total_pages=total_pages,
        total=total,
        total_all=total_all,
        filters=filters,
        url_params=url_params
    )


@app.route("/order/<int:order_idx>")
@login_required
def order_detail(order_idx: int):
    username = session["user"]

    # ✅ assure que les JSON d'ordres existent (migration CSV → JSON)
    migrate_dashboard_to_orders_json(username)

    # sécurité de base
    if order_idx < 0:
        abort(404)

    # si le JSON existe, on l'utilise
    p = order_json_path(username, order_idx)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            detail = json.load(f)
    else:
        # fallback : on affiche au moins la synthèse depuis le dashboard
        rows = load_dashboard(filename=dashboard_path(username))
        if order_idx >= len(rows):
            abort(404)
        r = rows[order_idx]
        detail = {
            "order_idx": order_idx,
            "legacy": True,
            "date_calcul": r.get("date_calcul", ""),
            "date_fin_cycle": r.get("date_fin_cycle", ""),
            "montant_investi": r.get("montant_investi", ""),
            "interets": r.get("interets", ""),
            "commissions": r.get("commissions", ""),
            "commission_supplementaire": r.get("commission_supplementaire", ""),
            "revenu_global": r.get("revenu_global", ""),
            "MEC": r.get("MEC", ""),
            "circulation": r.get("circulation", ""),
            "circulation_arrondie": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)),
            "x_next": float(_round_to_multiple(_parse_money(r.get("circulation", "0")), 500)),
            "rendement": r.get("rendement", ""),
            "niveaux": []
        }

    # statut
    now = datetime.now()
    end_dt = _safe_dt(detail.get("date_fin_cycle", ""))
    paid = bool(end_dt and end_dt <= now)

    return render_template("order_detail.html", detail=detail, paid=paid)


@app.route("/security")
@login_required
def security():
    username = session["user"]
    ip = _client_ip()
    state = load_security_state()
    last = state.get("last_login", {}).get(username, {})
    # derniers événements (utilisateur seulement)
    audit = [e for e in state.get("audit", []) if e.get("user") == username]
    audit = list(reversed(audit))[:50]

    locked, remaining = _is_locked(username, ip)
    return render_template(
        "security.html",
        last=last,
        audit=audit,
        locked=locked,
        remaining=remaining,
        max_attempts=MAX_LOGIN_ATTEMPTS,
        lock_minutes=LOCKOUT_MINUTES,
        timeout_minutes=INACTIVITY_TIMEOUT_MINUTES,
    )


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    username = session["user"]
    users = load_users()
    ip = _client_ip()
    ua = request.headers.get("User-Agent", "")

    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not check_password_hash(users[username]["password_hash"], current):
            audit_event(username, "password_change_failed", ip, ua, "wrong_current")
            flash("Mot de passe actuel incorrect.", "danger")
            return redirect(url_for("change_password"))

        if len(new) < 8:
            flash("Nouveau mot de passe trop court (minimum 8 caractères).", "danger")
            return redirect(url_for("change_password"))

        if new != confirm:
            flash("La confirmation ne correspond pas.", "danger")
            return redirect(url_for("change_password"))

        users[username]["password_hash"] = generate_password_hash(new)
        _save_json(USERS_FILE, {"users": users})
        audit_event(username, "password_changed", ip, ua)
        flash("Mot de passe modifié avec succès.", "success")
        return redirect(url_for("security"))

    return render_template("change_password.html")


@app.route("/reset-orders", methods=["GET", "POST"])
@login_required
def reset_orders():
    """Réinitialise TOUTES les données d'ordres (pour le compte connecté)."""
    username = session["user"]

    if request.method == "POST":
        if request.form.get("confirm", "") != "yes":
            flash("Réinitialisation annulée.", "info")
            return redirect(url_for("tools"))

        ip = _client_ip()
        ua = request.headers.get("User-Agent", "")
        audit_event(username, "reset_orders_data", ip, ua)

        reset_user_order_data(username)
        flash("Données réinitialisées : ordres, favoris et CSV supprimés pour ce compte.", "success")
        return redirect(url_for("orders"))

    # GET: afficher un avertissement + un petit résumé
    try:
        order_count = len([n for n in os.listdir(orders_dir(username)) if n.lower().endswith(".json")])
    except Exception:
        order_count = 0

    try:
        csv_count = len([n for n in os.listdir(user_dir(username)) if n.lower().endswith(".csv")])
    except Exception:
        csv_count = 0

    return render_template("reset_orders.html", order_count=order_count, csv_count=csv_count)

if __name__ == "__main__":
    app.run(debug=True)