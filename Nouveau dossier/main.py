# main.py
import math
from datetime import datetime

from kivy.app import App
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.uix.popup import Popup
from kivy.uix.label import Label

# On importe ton moteur Mellion
from mellioncoin import (
    MEC_VALUE,
    optimized_distribution,
    compute_interets,
    compute_commissions,
)

KV = """
BoxLayout:
    orientation: "vertical"
    padding: dp(16)
    spacing: dp(10)

    Label:
        text: "Simulateur MellionCoin"
        font_size: "20sp"
        size_hint_y: None
        height: self.texture_size[1]

    TextInput:
        id: date_input
        hint_text: "Date de l'investissement (JJ-MM-AAAA)"
        multiline: False
        size_hint_y: None
        height: dp(48)

    TextInput:
        id: amount_input
        hint_text: "Montant de l'investissement (multiple de 500)"
        multiline: False
        input_filter: "int"
        size_hint_y: None
        height: dp(48)

    Button:
        text: "Calculer"
        size_hint_y: None
        height: dp(48)
        on_release: app.calculer()

    ScrollView:
        do_scroll_x: False
        do_scroll_y: True

        Label:
            id: result_label
            text: ""
            markup: True
            size_hint_y: None
            text_size: self.width, None
            height: self.texture_size[1]
            font_size: "14sp"
"""


class MellionApp(App):
    def build(self):
        self.title = "MellionCoin"
        return Builder.load_string(KV)

    def show_error(self, message: str):
        popup = Popup(
            title="Erreur",
            content=Label(text=message),
            size_hint=(0.8, 0.4),
        )
        popup.open()

    def calculer(self):
        root = self.root
        date_str = root.ids.date_input.text.strip()
        montant_str = root.ids.amount_input.text.strip()

        # 1) Validation de la date
        try:
            dt = datetime.strptime(date_str, "%d-%m-%Y")
            date_norm = dt.strftime("%d-%m-%Y")
        except ValueError:
            self.show_error("Date invalide. Utilise le format JJ-MM-AAAA.")
            return

        # 2) Validation du montant X
        try:
            X = float(montant_str.replace(",", "."))
        except ValueError:
            self.show_error("Montant invalide. Saisis un nombre.")
            return

        if X <= 0:
            self.show_error("Le montant doit être strictement positif.")
            return

        if X % MEC_VALUE != 0:
            self.show_error(f"Le montant doit être un multiple de {MEC_VALUE:.0f} USD.")
            return

        # 3) Appel de ta logique de répartition
        try:
            n_opt, mec, caps = optimized_distribution(X)
        except Exception as e:
            self.show_error(f"Erreur de répartition : {e}")
            return

        # 4) Intérêts et commissions (exactement comme dans ton script)
        interets = compute_interets(caps)
        commissions = compute_commissions(caps)

        total_I = sum(interets)
        total_C = sum(commissions)
        total_MEC_initial = sum(mec)

        # 5) Logique de réinvestissement identique à ton main()
        if X >= 3000:
            C_tm = math.ceil(total_C / MEC_VALUE) * MEC_VALUE
            mec_commission_reinvestie = C_tm / MEC_VALUE
            Sa = C_tm - total_C
            Com_supp = 1.24 * C_tm
            Revenu_global = Com_supp + total_I
            total_MEC_global = total_MEC_initial + mec_commission_reinvestie
            denom = X + Sa
        else:
            C_tm = 0.0
            mec_commission_reinvestie = 0.0
            Sa = 0.0
            Com_supp = 0.0
            Revenu_global = total_I + total_C
            total_MEC_global = total_MEC_initial
            denom = X

        r = Revenu_global / denom if denom != 0 else 0.0

        # 6) Construction d’un résumé texte pour l’écran
        lines = []
        lines.append(f"[b]Date :[/b] {date_norm}")
        lines.append(f"[b]Investissement :[/b] {X:,.2f} USD")
        lines.append(f"[b]Nombre de niveaux (n_opt) :[/b] {n_opt} (0 à {n_opt - 1})")
        lines.append("")

        lines.append("[b]Répartition par niveau[/b]")
        lines.append("Niv | Rôle      | MEC | Capital")
        for i in range(n_opt):
            role = "Parrain" if i == 0 else "Filleul"
            lines.append(f"{i:>3} | {role:<8} | {mec[i]:>3} | {caps[i]:>8.0f}")

        lines.append("")
        lines.append("[b]Intérêts et commissions[/b]")
        lines.append(f"Intérêts totaux       : {total_I:,.2f} USD")
        lines.append(f"Commissions totales   : {total_C:,.2f} USD")

        if X >= 3000:
            lines.append("")
            lines.append("[b]Réinvestissement des commissions[/b]")
            lines.append(f"C_tm (multiple de 500)     : {C_tm:,.2f} USD")
            lines.append(f"Somme ajoutée (Sa)         : {Sa:,.2f} USD")
            lines.append(f"MEC issues des commissions : {mec_commission_reinvestie:,.0f} MEC")
            lines.append(f"Commission supplémentaire  : {Com_supp:,.2f} USD")
        else:
            lines.append("")
            lines.append("[b]Pas de réinvestissement (X < 3000 USD)[/b]")

        lines.append("")
        lines.append("[b]Synthèse[/b]")
        lines.append(f"Nombre total de MEC : {total_MEC_global:,.0f} MEC")
        lines.append(f"Revenu global       : {Revenu_global:,.2f} USD")
        lines.append(f"Rendement           : {r * 100:,.2f} %")

        root.ids.result_label.text = "\\n".join(lines)


if __name__ == "__main__":
    MellionApp().run()
