from flask import Flask, render_template, request
from mellion_core import (
    compute_mellion_result,
    MEC_VALUE,
    save_dashboard_row,
    DASHBOARD_CSV,
)
import csv
from pathlib import Path

app = Flask(__name__)


# ==== Filtres Jinja pour formater les nombres (style FR) ====

def format_number(value, decimals=2):
    """
    Formatte un nombre flottant avec des espaces pour les milliers
    et une virgule comme séparateur décimal.
    Exemple : 1234.5 -> '1 234,50'
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    s = f"{value:,.{decimals}f}"
    s = s.replace(",", " ").replace(".", ",")
    return s


def format_int(value):
    """
    1234.5 -> '1 235'
    """
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return value
    s = f"{value:,d}".replace(",", " ")
    return s


app.jinja_env.filters["fmt2"] = format_number
app.jinja_env.filters["fmt0"] = format_int


# ==== Routes ====

@app.route("/", methods=["GET"])
def index():
    # Page d'accueil : formulaire de simulation
    return render_template("index.html", mec_value=MEC_VALUE)


@app.route("/result", methods=["POST"])
def result():
    # Récupération des données du formulaire
    montant = request.form.get("montant", "").strip()
    reinvest_str = request.form.get("reinvest", "oui")

    # Validation du montant
    try:
        X = int(montant)
    except ValueError:
        error = "Veuillez entrer un montant entier (ex : 500, 1000, 1500)."
        return render_template(
            "index.html",
            mec_value=MEC_VALUE,
            error=error,
            montant=montant,
            reinvest=reinvest_str,
        )

    # Réinvestissement demandé par l'utilisateur ?
    reinvest_request = reinvest_str.lower() in ("oui", "o", "yes", "y")

    # Calcul métier Mellion
    try:
        data = compute_mellion_result(X, reinvest_request)
    except ValueError as e:
        error = str(e)
        return render_template(
            "index.html",
            mec_value=MEC_VALUE,
            error=error,
            montant=montant,
            reinvest=reinvest_str,
        )

    # Sauvegarde dans le fichier CSV du tableau de bord
    save_dashboard_row(data)

    # Affichage de la page de résultats
    return render_template("results.html", data=data)


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    Affiche le contenu du Tableau_Bord.csv dans l'application.
    """
    csv_path = Path(DASHBOARD_CSV)
    headers = []
    rows = []

    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            try:
                headers = next(reader)
            except StopIteration:
                headers = []

            for r in reader:
                rows.append(r)

        # On inverse pour avoir les calculs les plus récents en haut
        rows = rows[::-1]

    return render_template("dashboard.html", headers=headers, rows=rows)


if __name__ == "__main__":
    # Lancement en local
    app.run(debug=True)
