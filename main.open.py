import json
import csv
from logging import log
import os
import re
import zipfile
import unicodedata
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from fillpdf import fillpdfs

# ============================================================
# CONSTANTES
# ============================================================

AVIS_OPTIONS_OPENADS = {
    "refus"                   : 2,
    "défavorable"             : 3,
    "favorable"               : 5,
    "favorable avec réserves" : 6,
    "incomplet"               : 9,
}

# ============================================================
# LOGGER
# ============================================================

class Logger:
    def __init__(self, dossier_logs):
        self.dossier_logs = Path(dossier_logs)
        self.dossier_logs.mkdir(parents=True, exist_ok=True)

        horodatage      = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        nom_fichier     = f"openads_{horodatage}.log"
        self.chemin_log = self.dossier_logs / nom_fichier

        self._ecrire(f"{'=' * 60}")
        self._ecrire(f"  SESSION OPENADS — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self._ecrire(f"{'=' * 60}")
        print(f"📝 Log de session : {self.chemin_log}")

    def _ecrire(self, message):
        horodatage = datetime.now().strftime("%H:%M:%S")
        ligne      = f"[{horodatage}] {message}"
        with open(self.chemin_log, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")

    def info(self, message):
        print(message)
        self._ecrire(message)

    def succes(self, message):
        print(message)
        self._ecrire(message)

    def warning(self, message):
        print(message)
        self._ecrire(message)

    def erreur(self, message):
        print(message)
        self._ecrire(message)

    def separateur(self, titre=""):
        ligne = f"{'=' * 60}"
        if titre:
            print(f"\n{ligne}")
            print(f"  {titre}")
            print(f"{ligne}")
            self._ecrire(ligne)
            self._ecrire(f"  {titre}")
            self._ecrire(ligne)
        else:
            print(ligne)
            self._ecrire(ligne)

    def fermer(self):
        self._ecrire(f"{'=' * 60}")
        self._ecrire(f"  FIN DE SESSION — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self._ecrire(f"{'=' * 60}")

# ============================================================
# UTILITAIRES
# ============================================================

def formater_numero_dossier(numero_brut):
    """
    CU0130552600676P0  →  CU 013055 26 00676P0
    PC0130552600123M01 →  PC 013055 26 00123M01
    Règle : XX XXXXXX XX XXXXX[reste]
    """
    numero  = numero_brut.strip().upper()
    pattern = r'^([A-Z]{2})(\d{6})(\d{2})(\d{5}.*)$'
    match   = re.match(pattern, numero)
    if match:
        return f"{match.group(1)} {match.group(2)} {match.group(3)} {match.group(4)}"
    else:
        print(f"⚠️ Format de numéro inattendu : {numero_brut}")
        return numero_brut

def nettoyer_nom_dossier(nom):
    return re.sub(r'[<>:"/\\|?*]', "_", nom).strip()

def normaliser_commune(commune):
    commune = commune.lower().strip()
    commune = ''.join(
        c for c in unicodedata.normalize('NFD', commune)
        if unicodedata.category(c) != 'Mn'
    )
    commune = commune.replace('-', ' ')
    commune = ' '.join(commune.split())
    return commune

# ============================================================
# CONFIGURATION
# ============================================================

def charger_config():
    with open("config_openads.json", "r", encoding="utf-8") as f:
        return json.load(f)

# ============================================================
# LECTURE DU CSV
# ============================================================

def lire_csv(chemin_csv, log):
    dossiers = []

    with open(chemin_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)

        for ligne in reader:
            if len(ligne) < 13:
                continue
            numero_brut = ligne[0].strip()
            if not numero_brut:
                continue

            num_voie         = ligne[4].strip()
            adresse          = ligne[5].strip()
            cp               = ligne[6].strip()
            ville            = ligne[7].strip()
            adresse_chantier = f"{num_voie} {adresse}, {cp} {ville}".strip()

            dossiers.append({
                "numero_brut"            : numero_brut,
                "numero_formate"         : formater_numero_dossier(numero_brut),
                "nom_petitionnaire"      : ligne[1].strip(),
                "adresse_petitionnaire"  : ligne[2].strip(),
                "adresse_travaux"        : adresse_chantier,
                "date_limite"            : ligne[8].strip(),
                "date_depart"            : ligne[9].strip(),
                "references_cadastrales" : ligne[10].strip(),
                "nature_travaux"         : ligne[12].strip(),
                "commune"                : ville,
            })

    log.succes(f"✅ CSV lu : {len(dossiers)} dossier(s) trouvé(s)")
    return dossiers

# ============================================================
# INTERFACE GRAPHIQUE
# ============================================================

class InterfaceOpenADS:

    def __init__(self, dossiers_csv):
        self.dossiers_csv  = dossiers_csv
        self.resultat      = {"dossiers": [], "action": "QUIT"}
        self.resultat_avis = {"avis": None, "action": None}
        self.avis_var      = None

        self.COULEURS = {
            "bg"      : "#F5F5F5",
            "titre"   : "#1565C0",
            "success" : "#4CAF50",
            "warning" : "#FF9800",
            "danger"  : "#f44336",
            "info"    : "#2196F3",
            "SEMM"    : "#2196F3",
        }

        self.STYLES_AVIS = {
            "favorable"               : ("#4CAF50", "✅  Favorable"),
            "favorable avec réserves" : ("#FF9800", "⚠️  Favorable avec réserves"),
            "défavorable"             : ("#f44336", "❌  Défavorable"),
            "incomplet"               : ("#9C27B0", "📋  Incomplet"),
            "refus"                   : ("#607D8B", "🚫  Refus"),
        }

        self.fenetre = tk.Tk()
        self.fenetre.title("AUTOMATISATION OPEN ADS — SEMM")
        self.fenetre.geometry("1200x650")
        self.fenetre.resizable(True, True)
        self.fenetre.configure(bg=self.COULEURS["bg"])

        self.fenetre.update_idletasks()
        x = (self.fenetre.winfo_screenwidth()  // 2) - (1200 // 2)
        y = (self.fenetre.winfo_screenheight() // 2) - (650  // 2)
        self.fenetre.geometry(f"1200x650+{x}+{y}")

        # Bandeau titre permanent
        cadre_titre = tk.Frame(self.fenetre, bg=self.COULEURS["titre"], pady=10)
        cadre_titre.pack(fill="x")

        tk.Label(
            cadre_titre,
            text="🏗️  AUTOMATISATION OPEN ADS — SEMM",
            font=("Arial", 14, "bold"),
            bg=self.COULEURS["titre"], fg="white"
        ).pack()

        self.label_sous_titre = tk.Label(
            cadre_titre,
            text=f"📋  {len(dossiers_csv)} dossier(s) en attente",
            font=("Arial", 10),
            bg=self.COULEURS["titre"], fg="#BBDEFB"
        )
        self.label_sous_titre.pack()

        # Zone de contenu
        self.cadre_contenu = tk.Frame(self.fenetre, bg=self.COULEURS["bg"])
        self.cadre_contenu.pack(fill="both", expand=True)

        self.afficher_etape_selection()

    # --------------------------------------------------------
    # UTILITAIRES
    # --------------------------------------------------------

    def vider_contenu(self):
        for widget in self.cadre_contenu.winfo_children():
            widget.destroy()

    def mettre_a_jour_sous_titre(self, texte, couleur="#BBDEFB"):
        self.label_sous_titre.config(text=texte, fg=couleur)

    # --------------------------------------------------------
    # ÉTAPE 1 : SÉLECTION
    # --------------------------------------------------------

    def afficher_etape_selection(self):
        self.vider_contenu()
        self.mettre_a_jour_sous_titre(
            f"📋  {len(self.dossiers_csv)} dossier(s) en attente"
        )

        # En-tête colonnes
        cadre_entete = tk.Frame(self.cadre_contenu, bg="#37474F")
        cadre_entete.pack(fill="x", padx=20, pady=(10, 0))

        for texte, largeur in [
            ("",                 3),
            ("Numéro",          24),
            ("Pétitionnaire",   22),
            ("Adresse travaux", 30),
            ("Parcelles",       18),
            ("Date limite",     12),
        ]:
            tk.Label(
                cadre_entete, text=texte, width=largeur,
                bg="#37474F", fg="white",
                font=("Arial", 9, "bold"),
                anchor="w", pady=5
            ).pack(side="left", padx=3)

        # Liste scrollable
        cadre_scroll = tk.Frame(self.cadre_contenu)
        cadre_scroll.pack(fill="both", expand=True, padx=20)

        scrollbar = tk.Scrollbar(cadre_scroll)
        scrollbar.pack(side="right", fill="y")

        canvas = tk.Canvas(
            cadre_scroll, yscrollcommand=scrollbar.set,
            bg=self.COULEURS["bg"]
        )
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=canvas.yview)

        cadre_liste = tk.Frame(canvas, bg=self.COULEURS["bg"])
        canvas.create_window((0, 0), window=cadre_liste, anchor="nw")
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        )

        self.variables = []

        for i, dossier in enumerate(self.dossiers_csv):
            var = tk.BooleanVar(value=False)
            self.variables.append(var)
            couleur_fond = "#f9f9f9" if i % 2 == 0 else "white"

            try:
                date_lim = datetime.strptime(dossier["date_limite"], "%d/%m/%Y")
                restant  = (date_lim - datetime.now()).days
                if restant < 0:
                    texte_restant   = f"⛔ {abs(restant)}j dépassé"
                    couleur_restant = "#f44336"
                elif restant <= 5:
                    texte_restant   = f"⚠️ {restant}j restants"
                    couleur_restant = "#FF9800"
                else:
                    texte_restant   = f"✅ {restant}j restants"
                    couleur_restant = "#4CAF50"
            except:
                texte_restant   = dossier["date_limite"]
                couleur_restant = "#9E9E9E"

            tk.Checkbutton(
                cadre_liste, variable=var, bg=couleur_fond
            ).grid(row=i, column=0, padx=5, pady=1)

            tk.Label(
                cadre_liste, text=dossier["numero_formate"],
                width=24, bg=couleur_fond,
                anchor="w", font=("Courier", 9)
            ).grid(row=i, column=1, padx=3, pady=1)

            tk.Label(
                cadre_liste, text=dossier["nom_petitionnaire"],
                width=22, bg=couleur_fond, anchor="w"
            ).grid(row=i, column=2, padx=3, pady=1)

            tk.Label(
                cadre_liste, text=dossier["adresse_travaux"],
                width=30, bg=couleur_fond, anchor="w"
            ).grid(row=i, column=3, padx=3, pady=1)

            tk.Label(
                cadre_liste, text=dossier["references_cadastrales"],
                width=18, bg=couleur_fond,
                anchor="w", font=("Courier", 8)
            ).grid(row=i, column=4, padx=3, pady=1)

            tk.Label(
                cadre_liste, text=texte_restant,
                width=12, bg=couleur_fond,
                fg=couleur_restant,
                anchor="w", font=("Arial", 9, "bold")
            ).grid(row=i, column=5, padx=3, pady=1)

        cadre_liste.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        # Compteur
        self.label_compteur = tk.Label(
            self.cadre_contenu,
            text="0 dossier(s) sélectionné(s)",
            font=("Arial", 10), fg="#666666",
            bg=self.COULEURS["bg"]
        )
        self.label_compteur.pack(pady=4)

        def mettre_a_jour_compteur(*args):
            n = sum(1 for v in self.variables if v.get())
            self.label_compteur.config(
                text=f"{n} dossier(s) sélectionné(s)",
                fg="#2196F3" if n > 0 else "#666666"
            )

        for var in self.variables:
            var.trace_add("write", mettre_a_jour_compteur)

        # Boutons
        cadre_boutons = tk.Frame(self.cadre_contenu, bg=self.COULEURS["bg"])
        cadre_boutons.pack(pady=8)

        tk.Button(
            cadre_boutons, text="✅  Tout sélectionner",
            command=lambda: [v.set(True) for v in self.variables],
            bg="#4CAF50", fg="white", font=("Arial", 10), width=18, pady=5
        ).grid(row=0, column=0, padx=6)

        tk.Button(
            cadre_boutons, text="☐  Tout désélectionner",
            command=lambda: [v.set(False) for v in self.variables],
            bg="#f44336", fg="white", font=("Arial", 10), width=18, pady=5
        ).grid(row=0, column=1, padx=6)

        tk.Button(
            cadre_boutons, text="▶  Traiter la sélection",
            command=self._confirmer_selection,
            bg="#2196F3", fg="white", font=("Arial", 11, "bold"), width=18, pady=5
        ).grid(row=0, column=2, padx=6)

        tk.Button(
            cadre_boutons, text="🔄  Rafraîchir",
            command=self._rafraichir,
            bg="#607D8B", fg="white", font=("Arial", 10), width=18, pady=5
        ).grid(row=0, column=3, padx=6)

        tk.Button(
            cadre_boutons, text="✖  Quitter",
            command=self._quitter,
            bg="#9E9E9E", fg="white", font=("Arial", 10), width=18, pady=5
        ).grid(row=0, column=4, padx=6)

        self.fenetre.bind("<Escape>", lambda e: self._quitter())
        self.fenetre.bind("<Return>", lambda e: self._confirmer_selection())

    def _confirmer_selection(self):
        dossiers = [
            self.dossiers_csv[i]
            for i, v in enumerate(self.variables) if v.get()
        ]
        if not dossiers:
            self.mettre_a_jour_sous_titre(
                "⚠️  Sélectionnez au moins un dossier !", couleur="#FF9800"
            )
            return
        self.resultat["dossiers"] = dossiers
        self.resultat["action"]   = "TRAITER"
        self.fenetre.update()
        self.fenetre.quit()

    def _rafraichir(self):
        self.resultat["action"] = "REFRESH"
        self.fenetre.update()
        self.fenetre.quit()

    def _quitter(self):
        self.resultat["action"] = "QUIT"
        self.fenetre.update()
        self.fenetre.quit()

    # --------------------------------------------------------
    # ÉTAPE 2 : SAISIE AVIS
    # --------------------------------------------------------

    def afficher_attente_dossier(self, donnees, message_statut):
        self.vider_contenu()
        self.mettre_a_jour_sous_titre(f"⏳ {message_statut}", couleur="#FF9800")

        # Carte d'identité du dossier
        cadre_carte = tk.Frame(self.cadre_contenu, bg="white", pady=15, padx=20)
        cadre_carte.pack(fill="x", padx=30, pady=15)

        tk.Label(cadre_carte, text="  SEMM  ", bg=self.COULEURS["SEMM"], fg="white", font=("Arial", 12, "bold"), padx=12, pady=5).grid(row=0, column=0, sticky="w", padx=(0, 15))
        tk.Label(cadre_carte, text=donnees["numero_formate"], font=("Courier", 14, "bold"), bg="white", fg="#1565C0").grid(row=0, column=1, sticky="w")

        infos = [
            ("👤 Pétitionnaire",   donnees.get("nom_petitionnaire", "")),
            ("🏠 Adresse travaux", donnees.get("adresse_travaux", "")),
            ("📅 Date dépôt",      donnees.get("date_depart", "")),
            ("⏱️ Date limite",     donnees.get("date_limite", "")),
            ("🗺️ Parcelles",       donnees.get("references_cadastrales", "")),
        ]

        for row, (label, valeur) in enumerate(infos, 1):
            tk.Label(cadre_carte, text=label, font=("Arial", 9, "bold"), bg="white", fg="#666666", width=18, anchor="w").grid(row=row, column=0, sticky="w", pady=2)
            tk.Label(cadre_carte, text=valeur, font=("Arial", 9), bg="white", fg="#333333", anchor="w").grid(row=row, column=1, sticky="w", pady=2)

        ttk.Separator(self.cadre_contenu, orient="horizontal").pack(fill="x", padx=30, pady=5)

        # Message d'attente à la place des boutons
        tk.Label(
            self.cadre_contenu, text=message_statut,
            font=("Arial", 11, "italic"), bg=self.COULEURS["bg"], fg="#607D8B"
        ).pack(pady=20)
        self.fenetre.update()

    def afficher_etape_avis(self, donnees):
        self.vider_contenu()
        self.mettre_a_jour_sous_titre(
            f"🏗️  Traitement en cours — {donnees['numero_formate']}",
            couleur="#BBDEFB"
        )

        # Carte dossier
        cadre_carte = tk.Frame(
            self.cadre_contenu, bg="white", pady=15, padx=20
        )
        cadre_carte.pack(fill="x", padx=30, pady=15)

        tk.Label(
            cadre_carte,
            text="  SEMM  ",
            bg=self.COULEURS["SEMM"], fg="white",
            font=("Arial", 12, "bold"), padx=12, pady=5
        ).grid(row=0, column=0, sticky="w", padx=(0, 15))

        tk.Label(
            cadre_carte,
            text=donnees["numero_formate"],
            font=("Courier", 14, "bold"),
            bg="white", fg="#1565C0"
        ).grid(row=0, column=1, sticky="w")

        infos = [
            ("👤 Pétitionnaire",   donnees.get("nom_petitionnaire", "")),
            ("🏠 Adresse travaux", donnees.get("adresse_travaux", "")),
            ("📅 Date dépôt",      donnees.get("date_depart", "")),
            ("⏱️ Date limite",     donnees.get("date_limite", "")),
            ("🗺️ Parcelles",       donnees.get("references_cadastrales", "")),
        ]

        for row, (label, valeur) in enumerate(infos, 1):
            tk.Label(
                cadre_carte, text=label,
                font=("Arial", 9, "bold"),
                bg="white", fg="#666666", width=18, anchor="w"
            ).grid(row=row, column=0, sticky="w", pady=2)
            tk.Label(
                cadre_carte, text=valeur,
                font=("Arial", 9),
                bg="white", fg="#333333", anchor="w"
            ).grid(row=row, column=1, sticky="w", pady=2)

        ttk.Separator(self.cadre_contenu, orient="horizontal").pack(
            fill="x", padx=30, pady=5
        )

        # Section avis
        cadre_avis = tk.Frame(self.cadre_contenu, bg=self.COULEURS["bg"])
        cadre_avis.pack(fill="x", padx=30, pady=5)

        tk.Label(
            cadre_avis,
            text="💧  AVIS AEP",
            font=("Arial", 12, "bold"),
            bg=self.COULEURS["bg"], fg="#1565C0"
        ).pack(anchor="w", pady=(5, 3))

        self.avis_var = tk.StringVar(value="")

        cadre_boutons_avis = tk.Frame(cadre_avis, bg=self.COULEURS["bg"])
        cadre_boutons_avis.pack(anchor="w")

        for i, (valeur, (couleur, texte)) in enumerate(self.STYLES_AVIS.items()):
            tk.Radiobutton(
                cadre_boutons_avis,
                text=texte,
                variable=self.avis_var,
                value=valeur,
                bg=self.COULEURS["bg"],
                activebackground=couleur,
                selectcolor=couleur,
                fg="#333333",
                font=("Arial", 10),
                indicatoron=0,
                width=22, pady=6,
                relief="groove", bd=1
            ).grid(row=0, column=i, padx=4)

        ttk.Separator(self.cadre_contenu, orient="horizontal").pack(
            fill="x", padx=30, pady=10
        )

        # Boutons d'action
        cadre_actions = tk.Frame(self.cadre_contenu, bg=self.COULEURS["bg"])
        cadre_actions.pack(pady=8)

        tk.Button(
            cadre_actions,
            text="📤  Envoyer le dossier",
            command=self._valider_avis,
            bg="#2196F3", fg="white",
            font=("Arial", 11, "bold"), width=22, pady=8
        ).grid(row=0, column=0, padx=10)

        tk.Button(
            cadre_actions,
            text="📦  Mettre en attente",
            command=self._mettre_en_attente,
            bg="#FF9800", fg="white",
            font=("Arial", 10), width=22, pady=8
        ).grid(row=0, column=1, padx=10)

        tk.Button(
            cadre_actions,
            text="⏭️  Passer ce dossier",
            command=self._passer_dossier,
            bg="#9E9E9E", fg="white",
            font=("Arial", 10), width=22, pady=8
        ).grid(row=0, column=2, padx=10)

        self.label_erreur_avis = tk.Label(
            self.cadre_contenu, text="",
            font=("Arial", 10), fg="#f44336",
            bg=self.COULEURS["bg"]
        )
        self.label_erreur_avis.pack()

    def _valider_avis(self):
        avis = self.avis_var.get() if self.avis_var else ""
        if not avis:
            self.label_erreur_avis.config(
                text="⚠️  Veuillez sélectionner un avis !"
            )
            return
        self.resultat_avis["avis"]   = avis
        self.resultat_avis["action"] = "ENVOYER"
        self.fenetre.update()
        self.fenetre.quit()

    def _mettre_en_attente(self):
        self.resultat_avis["action"] = "ATTENTE"
        self.fenetre.update()
        self.fenetre.quit()

    def _passer_dossier(self):
        self.resultat_avis["action"] = "PASSER"
        self.fenetre.update()
        self.fenetre.quit()

    # --------------------------------------------------------
    # ÉTAPE 3 : LOGS
    # --------------------------------------------------------

    def afficher_etape_traitement(self, numero_formate):
        self.vider_contenu()
        self.mettre_a_jour_sous_titre(
            f"⚙️  Traitement en cours — {numero_formate}", couleur="#BBDEFB"
        )

        tk.Label(
            self.cadre_contenu,
            text=f"⚙️  Traitement de {numero_formate}",
            font=("Arial", 13, "bold"),
            bg=self.COULEURS["bg"], fg="#1565C0"
        ).pack(pady=20)

        self.zone_logs = tk.Text(
            self.cadre_contenu,
            height=20, width=100,
            font=("Courier", 9),
            bg="#1E1E1E", fg="#FFFFFF",
            relief="flat"
        )
        self.zone_logs.pack(padx=30, pady=5)
        self.zone_logs.config(state="disabled")
        self.fenetre.update()

    def ajouter_log(self, message):
        if hasattr(self, "zone_logs"):
            self.zone_logs.config(state="normal")
            self.zone_logs.insert("end", message + "\n")
            self.zone_logs.see("end")
            self.zone_logs.config(state="disabled")
            self.fenetre.update()

    # --------------------------------------------------------
    # CONTRÔLE FENÊTRE
    # --------------------------------------------------------

    def lancer(self):
        self.fenetre.mainloop()

    def relancer(self):
        try:
            self.fenetre.update()
        except:
            pass
        self.fenetre.mainloop()

    def fermer(self):
        try:
            self.fenetre.destroy()
        except:
            pass

# ============================================================
# CONNEXION
# ============================================================

def verifier_et_connecter(page, config, log):
    log.info("🔍 Vérification de la connexion OpenADS...")

    page.goto(
        "https://openads.e-mrs.fr/app/index.php?module=tab&obj=demande_avis_encours",
        wait_until="domcontentloaded"
    )
    page.wait_for_timeout(2000)

    if "auth.e-mrs.fr" in page.url:
        log.info("🔐 Page SSO ADFS détectée — connexion réseau...")
        page.wait_for_selector("#userNameInput", timeout=10000)
        page.fill("#userNameInput", config["identifiant"])
        page.fill("#passwordInput", config["mot_de_passe"])
        page.click("#submitButton")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        log.succes("✅ SSO ADFS validé !")

    if "login" in page.url:
        log.info("🔐 Page login OpenADS détectée — connexion applicative...")
        page.wait_for_selector("#login", timeout=10000)
        page.fill("#login", config["identifiant"])
        page.fill("#password", config["mot_de_passe"])
        page.click("#login_form > div.formControls.formControls-bottom > input")
        page.wait_for_url("**/index.php?module=tab**", timeout=15000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        log.succes("✅ Connecté à OpenADS !")
    else:
        log.succes("✅ Déjà connecté !")

# ============================================================
# GESTION DES DÉCONNEXIONS INTEMPESTIVES
# ============================================================

def reconnecter_si_besoin(page, config, log):
    """Vérifie si la session a expiré pendant une pause et se reconnecte."""
    # Si l'URL de connexion est détectée (déconnexion pour inactivité)
    if "module=login" in page.url or "auth.e-mrs.fr" in page.url:
        log.warning("⚠️ Session OpenADS expirée (inactivité détectée). Reconnexion auto...")
        
        try:
            # Cas 1 : Redirection vers le SSO ADFS
            if "auth.e-mrs.fr" in page.url:
                page.wait_for_selector("#userNameInput", timeout=5000)
                page.fill("#userNameInput", config["identifiant"])
                page.fill("#passwordInput", config["mot_de_passe"])
                page.click("#submitButton")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1000)
            
            # Cas 2 : Page de login interne OpenADS
            if "module=login" in page.url:
                page.wait_for_selector("#login", timeout=5000)
                page.fill("#login", config["identifiant"])
                page.fill("#password", config["mot_de_passe"])
                page.click("#login_form > div.formControls.formControls-bottom > input")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2000)
                
            log.succes("✅ Reconnexion silencieuse réussie ! Reprise du script.")
            
        except Exception as e:
            log.erreur(f"❌ Échec de la reconnexion automatique : {e}")

# ============================================================
# TÉLÉCHARGEMENT CSV
# ============================================================

def telecharger_csv(page, config, log):
    log.info("📥 Téléchargement du CSV OpenADS...")

    if "module=tab&obj=demande_avis_encours" not in page.url:
        page.goto(
            "https://openads.e-mrs.fr/app/index.php?module=tab&obj=demande_avis_encours",
            wait_until="domcontentloaded"
        )
        page.wait_for_timeout(2000)

    chemin_csv = Path(config["dossier_telechargement"]) / "liste_openads.csv"

    with page.expect_download() as download_info:
        page.click(
            "#tab-demande_avis_encours > div.tab-container > div.tab-export > a > span"
        )

    download = download_info.value
    download.save_as(chemin_csv)
    log.succes(f"✅ CSV téléchargé : {chemin_csv}")
    return chemin_csv

# ============================================================
# TROUVER ET OUVRIR UN DOSSIER
# ============================================================

def trouver_et_ouvrir_dossier(page, dossier, log):
    numero_brut    = dossier["numero_brut"]
    numero_formate = dossier["numero_formate"]
    log.info(f"🔍 Recherche du dossier : {numero_formate}")

    if "module=tab&obj=demande_avis_encours" not in page.url:
        page.goto(
            "https://openads.e-mrs.fr/app/index.php?module=tab&obj=demande_avis_encours",
            wait_until="domcontentloaded"
        )
        page.wait_for_timeout(2000)

    while True:
        page.wait_for_selector(
            "#tab-demande_avis_encours > div.tab-container > section table > tbody",
            timeout=10000
        )
        page.wait_for_timeout(1000)

        lignes = page.locator(
            "#tab-demande_avis_encours > div.tab-container > section table > tbody > tr"
        )
        nombre_lignes = lignes.count()

        for i in range(nombre_lignes):
            ligne = lignes.nth(i)
            if dossier["numero_formate"] in ligne.inner_text():
                log.succes(f"✅ Dossier trouvé à la ligne {i + 1}")

                # Clic sur le lien de la colonne col-2 de cette ligne
                lien = page.locator(
                    f"#tab-demande_avis_encours > div.tab-container > section > "
                    f"table > tbody > tr:nth-child({i + 1}) > td.col-2 > a"
                )
                lien.click()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2000)
                return True, page.url

        # Pagination
        bouton_suivant = page.locator(
            "button[aria-label='Page suivante'], a[aria-label='Page suivante'], "
            "a.next, button.next"
        )
        if bouton_suivant.count() > 0 and bouton_suivant.first.is_enabled():
            log.info("➡️ Page suivante...")
            bouton_suivant.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)
        else:
            log.warning(f"⚠️ Dossier {dossier['numero_formate']} non trouvé dans le tableau !")
            return False, None

# ============================================================
# TÉLÉCHARGEMENT DES PIÈCES
# ============================================================

def telecharger_pieces(page, config, log):
    log.info("📥 Téléchargement des pièces...")

    page.click("#formulaire > ul > li:nth-child(2)")
    page.wait_for_timeout(2000)
    log.info("✅ Onglet pièces jointes sélectionné")

    page.wait_for_selector("#zip_download_link", timeout=10000)
    page.click("#zip_download_link")
    page.wait_for_timeout(2000)
    log.info("✅ Bouton ZIP cliqué")

    page.wait_for_selector(
        "#jqueryui > div.ui-dialog.ui-widget.ui-widget-content.ui-corner-all.ui-draggable > "
        "div.ui-dialog-buttonpane.ui-widget-content.ui-helper-clearfix > div > "
        "button.ui-dialog-button-confirm.ui-button.ui-widget.ui-state-default.ui-corner-all.ui-button-text-only",
        timeout=10000
    )
    page.click(
        "#jqueryui > div.ui-dialog.ui-widget.ui-widget-content.ui-corner-all.ui-draggable > "
        "div.ui-dialog-buttonpane.ui-widget-content.ui-helper-clearfix > div > "
        "button.ui-dialog-button-confirm.ui-button.ui-widget.ui-state-default.ui-corner-all.ui-button-text-only"
    )
    log.info("✅ Confirmation ZIP — compression en cours...")

    page.wait_for_selector("#archive_download_link", timeout=120000)
    log.info("✅ Archive prête — téléchargement...")

    chemin_zip = Path(config["dossier_telechargement"]) / "pieces_openads_temp.zip"

    with page.expect_download(timeout=120000) as download_info:
        page.click("#archive_download_link")

    download = download_info.value
    download.save_as(chemin_zip)
    log.succes("✅ ZIP téléchargé")

    try:
        page.click(
            "#jqueryui > div.ui-dialog.ui-widget.ui-widget-content.ui-corner-all.ui-draggable > "
            "div.ui-dialog-titlebar.ui-widget-header.ui-corner-all.ui-helper-clearfix > a > span",
            timeout=5000
        )
        log.info("✅ Fenêtre de dialogue fermée")
    except PlaywrightTimeoutError:
        log.warning("⚠️ Fenêtre de dialogue déjà fermée ou introuvable")

    page.click("#formulaire > ul > li:nth-child(1)")
    page.wait_for_timeout(1000)
    log.info("✅ Retour sur l'onglet principal du dossier")

    return chemin_zip

# ============================================================
# EXTRACTION DU ZIP
# ============================================================

def extraire_zip_et_creer_dossier(chemin_zip, donnees, config, log):
    nom_dossier    = nettoyer_nom_dossier(
        f"{donnees['numero_formate']}-{donnees['adresse_travaux']}"
    )
    chemin_dossier = Path(config["dossier_destination"]) / nom_dossier
    chemin_dossier.mkdir(parents=True, exist_ok=True)

    log.info(f"📁 Extraction dans : {chemin_dossier}")

    with zipfile.ZipFile(chemin_zip, "r") as zip_ref:
        zip_ref.extractall(chemin_dossier)

    log.succes("✅ ZIP extrait avec succès !")
    os.remove(chemin_zip)
    return chemin_dossier

# ============================================================
# PRÉ-REMPLISSAGE PDF
# ============================================================

def preremplir_formulaire_pdf(donnees, config, log):
    log.info("📝 Pré-remplissage du formulaire PDF...")

    chemin_modele = str(Path(config["formulaire_pdf"]))
    nom_fichier   = f"Formulaire_{donnees['numero_formate']}.pdf"
    chemin_rempli = str(Path(config["formulaire_dossier_sortie"]) / nom_fichier)

    def safe_str(v):
        return "" if v is None else str(v).strip()

    try:
        fillpdfs.write_fillable_pdf(
            chemin_modele,
            chemin_rempli,
            {
                "numéro dossier"         : safe_str(donnees.get("numero_formate")),
                "nom pétitionnaire"      : safe_str(donnees.get("nom_petitionnaire")),
                "adresse pétitionnaire"  : safe_str(donnees.get("adresse_petitionnaire")),
                "nature travaux"         : safe_str(donnees.get("nature_travaux")),
                "adresse travaux"        : safe_str(donnees.get("adresse_travaux")),
                "références cadastrales" : safe_str(donnees.get("references_cadastrales")),
            }
        )
        log.succes(f"✅ Formulaire sauvegardé : {nom_fichier}")
        return chemin_rempli
    except Exception as e:
        log.erreur(f"❌ Erreur remplissage PDF : {e}")
        return None

# ============================================================
# OUVRIR LES FICHIERS
# ============================================================

def ouvrir_fichiers_dossier(chemin_dossier, log):
    if not chemin_dossier or not chemin_dossier.exists():
        log.warning("⚠️ Dossier introuvable")
        return

    fichiers = list(chemin_dossier.iterdir())
    if not fichiers:
        log.warning("⚠️ Aucun fichier dans le dossier")
        return

    log.info(f"📂 Ouverture de {len(fichiers)} fichier(s)...")
    for fichier in fichiers:
        try:
            os.startfile(str(fichier))
            log.info(f"   ✅ {fichier.name}")
        except Exception as e:
            log.warning(f"   ⚠️ {fichier.name} : {e}")

# ============================================================
# TROUVER LE PDF D'AVIS
# ============================================================

def trouver_pdf_avis(chemin_dossier, numero_formate):
    chemin = Path(chemin_dossier)
    for fichier in chemin.iterdir():
        if (fichier.suffix.lower() == ".pdf" and
                fichier.stem.startswith(numero_formate)):
            return str(fichier)
    return None

# ============================================================
# CONFIRMATION + SAISIE AVIS
# ============================================================

def attendre_confirmation_et_avis(chemin_dossier, config, interface, donnees, page, log):
    interface.afficher_etape_avis(donnees)
    interface.relancer()

    action = interface.resultat_avis.get("action")

    if action == "ATTENTE":
        log.info(f"📦 Dossier {donnees['numero_formate']} mis en attente")
        dossier_en_attente = Path(config["dossier_en_attente"])
        dossier_en_attente.mkdir(parents=True, exist_ok=True)
        nom_dossier = Path(chemin_dossier).name
        destination = dossier_en_attente / nom_dossier
        try:
            import shutil
            shutil.move(str(chemin_dossier), str(destination))
            log.succes(f"✅ Dossier déplacé vers : {destination}")
        except Exception as e:
            log.erreur(f"❌ Erreur déplacement : {e}")

        # Retour au tableau OpenADS
        log.info("🔙 Retour au tableau OpenADS...")
        page.goto(
            "https://openads.e-mrs.fr/app/index.php?module=tab&obj=demande_avis_encours",
            wait_until="domcontentloaded"
        )
        page.wait_for_timeout(2000)
        log.info("✅ Retour au tableau effectué")
        return False, None

    if action == "PASSER":
        log.info(f"⏭️ Dossier {donnees['numero_formate']} passé")
        return False, None

    avis = interface.resultat_avis.get("avis", "")
    log.info(f"📋 Avis saisi : {avis}")
    return True, avis


# ============================================================
# SOUMETTRE L'AVIS
# ============================================================

def soumettre_avis(page, avis, chemin_dossier, numero_formate, log):
    log.separateur(f"SOUMISSION AVIS — {numero_formate}")
    log.info(f"📋 Avis à soumettre : {avis}")

    page.wait_for_selector(
        "#action-sousform-demande_avis_encours-rendre_avis > span",
        timeout=10000
    )
    page.click("#action-sousform-demande_avis_encours-rendre_avis > span")
    page.wait_for_timeout(2000)
    log.info("✅ Formulaire d'avis ouvert")

    index_avis = AVIS_OPTIONS_OPENADS[avis]
    page.wait_for_selector("select#avis_consultation", timeout=10000)
    page.select_option("select#avis_consultation", value=str(index_avis))
    page.wait_for_timeout(500)
    log.info(f"✅ Avis sélectionné : {avis} (index {index_avis})")

    # --------------------------------------------------------
    # CAS REFUS
    # --------------------------------------------------------
    if avis == "refus":
        page.wait_for_selector("#motivation", timeout=5000)
        page.fill(
            "#motivation",
            "Refus de consultation du dossier par manque de pièces"
        )
        log.info("✅ Motivation remplie")
        log.info("⏳ En attente de validation manuelle (refus)...")

        try:
            page.wait_for_selector(
                "#sousform-container > form > div.formControls.formControls-top > input",
                state="hidden",
                timeout=300000
            )
        except PlaywrightTimeoutError:
            pass

        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        log.succes("✅ Refus transmis !")
        return

    # --------------------------------------------------------
    # CAS FAVORABLE / AVEC RÉSERVES / DÉFAVORABLE / INCOMPLET
    # --------------------------------------------------------

    log.info(f"📎 Recherche du PDF d'avis pour {numero_formate}...")
    pdf_avis = trouver_pdf_avis(chemin_dossier, numero_formate)

    if not pdf_avis:
        log.warning(f"⚠️ Aucun PDF commençant par '{numero_formate}' trouvé")
        while True:
            input("\nAppuyez sur ENTRÉE une fois le PDF créé...")
            pdf_avis = trouver_pdf_avis(chemin_dossier, numero_formate)
            if pdf_avis:
                log.succes(f"✅ PDF trouvé : {Path(pdf_avis).name}")
                break
            log.warning("⚠️ Toujours pas trouvé !")
    else:
        log.succes(f"✅ PDF trouvé : {Path(pdf_avis).name}")

    page.wait_for_selector(
        "#form-content > div.field.field-type-upload2 > div.form-content > a:nth-child(3)",
        timeout=10000
    )
    page.click(
        "#form-content > div.field.field-type-upload2 > div.form-content > a:nth-child(3)"
    )
    page.wait_for_timeout(1500)
    log.info("✅ Fenêtre d'upload ouverte")

    with page.expect_file_chooser(timeout=10000) as fc_info:
        page.click("#upload-form > input.champFormulaire")

    fc = fc_info.value
    fc.set_files(pdf_avis)
    page.wait_for_timeout(1000)
    log.info(f"✅ PDF sélectionné : {Path(pdf_avis).name}")

    page.wait_for_selector(
        "#upload-form > input.om-button.ui-button.ui-widget.ui-state-default.ui-corner-all",
        timeout=10000
    )
    page.click(
        "#upload-form > input.om-button.ui-button.ui-widget.ui-state-default.ui-corner-all"
    )
    page.wait_for_timeout(2000)
    log.info("✅ PDF uploadé")

    log.info("⏳ En attente de validation manuelle...")

    try:
        page.wait_for_selector(
            "#sousform-container > form > div.formControls.formControls-top > input",
            state="hidden",
            timeout=300000
        )
    except PlaywrightTimeoutError:
        pass

    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)
    log.succes(f"✅ Avis '{avis}' transmis avec succès !")

    try:
        bouton_retour = page.locator(
            "[id^='sousform-action-demande_avis_encours-back']"
        ).first
        if bouton_retour.count() > 0:
            bouton_retour.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)
            log.info("✅ Retour au tableau")
    except Exception as e:
        log.warning(f"⚠️ Retour automatique échoué ({e}) — redirection...")
        page.goto(
            "https://openads.e-mrs.fr/app/index.php?module=tab&obj=demande_avis_encours",
            wait_until="domcontentloaded"
        )
        page.wait_for_timeout(2000)

# ============================================================
# DÉPLACER VERS "A UPLOAD"
# ============================================================

def deplacer_vers_a_upload(chemin_dossier, config, log):
    import shutil
    destination_base = Path(config["dossier_a_upload"])
    destination_base.mkdir(parents=True, exist_ok=True)
    nom_dossier = Path(chemin_dossier).name
    destination = destination_base / nom_dossier

    try:
        shutil.move(str(chemin_dossier), str(destination))
        log.succes(f"✅ Dossier déplacé vers : {destination}")
        return destination
    except Exception as e:
        log.erreur(f"❌ Erreur déplacement : {e}")
        return None

# ============================================================
# PROGRAMME PRINCIPAL
# ============================================================

def main():
    config = charger_config()

    for cle in ["dossier_telechargement", "dossier_destination",
                "formulaire_dossier_sortie", "dossier_en_attente",
                "dossier_a_upload", "dossier_logs"]:
        Path(config[cle]).mkdir(parents=True, exist_ok=True)

    log = Logger(config["dossier_logs"])
    log.separateur("AUTOMATISATION OPEN ADS — SEMM")

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            user_data_dir=config["chrome_profile"],
            channel="chrome",
            headless=False,
            accept_downloads=True,
            timeout=60000
        )
        page = context.new_page()

        try:
            # ÉTAPE 1 : Connexion
            verifier_et_connecter(page, config, log)

            # ÉTAPE 2 : CSV initial
            chemin_csv   = telecharger_csv(page, config, log)
            dossiers_csv = lire_csv(chemin_csv, log)

            if not dossiers_csv:
                log.warning("⚠️ Aucun dossier en attente !")
                return

            # Créer l'interface UNE SEULE FOIS
            interface = InterfaceOpenADS(dossiers_csv)

            # --------------------------------------------------------
            # BOUCLE PRINCIPALE
            # --------------------------------------------------------
            while True:
                interface.resultat = {"dossiers": [], "action": "QUIT"}
                interface.afficher_etape_selection()
                interface.relancer()

                action   = interface.resultat["action"]
                dossiers = interface.resultat["dossiers"]

                # REFRESH
                if action == "REFRESH":
                    log.info("🔄 Rafraîchissement...")
                    chemin_csv   = telecharger_csv(page, config, log)
                    dossiers_csv = lire_csv(chemin_csv, log)
                    interface.dossiers_csv = dossiers_csv
                    log.succes(f"✅ {len(dossiers_csv)} dossier(s) en attente")
                    continue

                # QUIT
                if action == "QUIT":
                    log.info("Travail terminé ! 🪓")
                    log.fermer()
                    interface.fermer()
                    break

                if action != "TRAITER" or not dossiers:
                    continue

                log.separateur(f"DÉBUT TRAITEMENT — {len(dossiers)} dossier(s)")

                # ============================================================
                # INITIALISATION DU PIPELINE (Préparation du tout 1er dossier)
                # ============================================================
                dossier_en_cours = dossiers[0]
                interface.afficher_attente_dossier(dossier_en_cours, "Initialisation et préparation du premier dossier...")
                trouve, url_en_cours = trouver_et_ouvrir_dossier(page, dossier_en_cours, log)
               
                if trouve:
                    chemin_zip_en_cours  = telecharger_pieces(page, config, log)
                    chemin_doss_en_cours = extraire_zip_et_creer_dossier(chemin_zip_en_cours, dossier_en_cours, config, log)
                    chemin_form_en_cours = preremplir_formulaire_pdf(dossier_en_cours, config, log)
                else:
                    log.erreur("❌ Impossible de trouver le premier dossier pour amorcer la boucle.")
                    continue

                # ============================================================
                # BOUCLE PRINCIPALE AVEC DÉCALAGE (PIPELINE)
                # ============================================================
                for index, dossier in enumerate(dossiers, 1):
                    trouve_suiv = False 
                    
                    log.separateur(f"Dossier {index}/{len(dossiers)} : {dossier_en_cours['numero_formate']}")

                    try:
                        # 1. OUVERTURE DES PDF POUR L'HUMAIN
                        if chemin_form_en_cours:
                            try:
                                os.startfile(str(chemin_form_en_cours))
                                # ---> LA FAMEUSE PAUSE DE 1 SECONDE <---
                                page.wait_for_timeout(1000) 
                            except Exception as e:
                                log.warning(f"⚠️ Impossible d'ouvrir le formulaire : {e}")
                        ouvrir_fichiers_dossier(chemin_doss_en_cours, log)

                        # 2. AFFICHAGE DE L'INTERFACE "ATTENTE" PENDANT LE TÉLÉCHARGEMENT
                        # Affiche les infos du dossier que tu es en train d'instruire pour que tu les aies sous les yeux.
                        if index < len(dossiers):
                            message = f"Préparation anticipée du dossier suivant ({dossiers[index]['numero_formate']})... Veuillez patienter."
                        else:
                            message = "Dernier dossier de la liste en cours d'instruction."
                            
                        interface.afficher_attente_dossier(dossier_en_cours, message)

                        # 3. PRÉPARATION ANTICIPÉE DU DOSSIER SUIVANT (Tâche de fond)
                        if index < len(dossiers):
                            dossier_suivant = dossiers[index]
                            log.info(f"⏳ PRÉPARATION ANTICIPÉE DU DOSSIER SUIVANT : {dossier_suivant['numero_formate']}")
                            
                            trouve_suiv, url_suiv = trouver_et_ouvrir_dossier(page, dossier_suivant, log)
                            if trouve_suiv:
                                chemin_zip_suiv  = telecharger_pieces(page, config, log)
                                chemin_doss_suiv = extraire_zip_et_creer_dossier(chemin_zip_suiv, dossier_suivant, config, log)
                                chemin_form_suiv = preremplir_formulaire_pdf(dossier_suivant, config, log)

                        # 4. LE ROBOT A FINI : ON AFFICHE LES BOUTONS D'AVIS
                        interface.afficher_etape_avis(dossier_en_cours)
                        interface.resultat_avis = {"avis": None, "action": None}
                        
                        # ---> LE SCRIPT SE MET EN PAUSE ICI EN ATTENDANT TON CLIC <---
                        pret, avis = attendre_confirmation_et_avis(
                            chemin_doss_en_cours, config, interface, dossier_en_cours, page, log
                        )

                        if not pret:
                            if index < len(dossiers) and trouve_suiv:
                                dossier_en_cours, url_en_cours = dossier_suivant, url_suiv
                                chemin_doss_en_cours, chemin_form_en_cours = chemin_doss_suiv, chemin_form_suiv
                            continue

                        reconnecter_si_besoin(page, config, log)

                        # 5. AFFICHER L'ATTENTE PENDANT L'ENVOI
                        interface.afficher_attente_dossier(dossier_en_cours, "Envoi de l'avis en cours sur OpenADS... Veuillez patienter.")

                        # 6. SOUMISSION DU DOSSIER EN COURS
                        envoi_reussi = False
                        for tentative in range(2):
                            try:
                                page.goto(url_en_cours, wait_until="domcontentloaded")
                                page.wait_for_timeout(2000)

                                soumettre_avis(page, avis, chemin_doss_en_cours, dossier_en_cours["numero_formate"], log)
                                envoi_reussi = True
                                break
                            except PlaywrightTimeoutError as e:
                                if "module=login" in page.url or "auth.e-mrs.fr" in page.url:
                                    log.warning(f"⚠️ Déconnexion (Tentative {tentative + 1}/2).")
                                    reconnecter_si_besoin(page, config, log)
                                else:
                                    raise e
                                    
                        if not envoi_reussi:
                            raise Exception("Échec critique de l'envoi.")

                        deplacer_vers_a_upload(chemin_doss_en_cours, config, log)
                        log.succes(f"✅ Dossier {dossier_en_cours['numero_formate']} entièrement traité !")

                        # 7. PASSAGE DU RELAIS
                        if index < len(dossiers) and trouve_suiv:
                            dossier_en_cours     = dossier_suivant
                            url_en_cours         = url_suiv
                            chemin_doss_en_cours = chemin_doss_suiv
                            chemin_form_en_cours = chemin_form_suiv

                    except Exception as e:
                        log.erreur(f"❌ Erreur sur {dossier_en_cours['numero_formate']} : {e}")
                        if index < len(dossiers) and trouve_suiv:
                            dossier_en_cours, url_en_cours = dossier_suivant, url_suiv
                            chemin_doss_en_cours, chemin_form_en_cours = chemin_doss_suiv, chemin_form_suiv
                        continue

                # Rafraîchir la liste après traitement du lot
                log.separateur("TOUS LES DOSSIERS DU LOT TRAITÉS ✅")
                chemin_csv   = telecharger_csv(page, config, log)
                dossiers_csv = lire_csv(chemin_csv, log)
                interface.dossiers_csv = dossiers_csv

        finally:
            log.fermer()
            context.close()


if __name__ == "__main__":
    main()
