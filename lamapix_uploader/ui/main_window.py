"""Fenêtre principale : un tableau de bord qu'on regarde de loin.

Elle ne fait rien elle-même — elle pousse des commandes au moteur et relit son
état une fois par seconde. Fermer la fenêtre ne suspend donc aucun envoi.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import NOM_APPLICATION, VERSION, paths, updater
from ..config import Config
from ..engine import Etat, Moteur
from . import theme
from .init_dialog import DialogueInitialisation
from .settings_dialog import DialogueReglages
from .update import ChercheurMiseAJour, TelechargeurMiseAJour

RAFRAICHISSEMENT_MS = 1000
DELAI_VERIF_MAJ_MS = 3000


class Carte(QFrame):
    """Un compteur : un grand chiffre, un libellé."""

    def __init__(self, libelle: str, couleur: str) -> None:
        super().__init__()
        self.setProperty("class", "carte")
        self.setFrameShape(QFrame.Shape.NoFrame)
        disposition = QVBoxLayout(self)
        disposition.setContentsMargins(14, 12, 14, 12)

        self.chiffre = QLabel("—")
        self.chiffre.setProperty("class", "chiffre")
        self.chiffre.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chiffre.setStyleSheet(f"color: {couleur};")

        titre = QLabel(libelle.upper())
        titre.setProperty("class", "libelle")
        titre.setAlignment(Qt.AlignmentFlag.AlignCenter)

        disposition.addWidget(self.chiffre)
        disposition.addWidget(titre)

    def afficher(self, valeur: int) -> None:
        self.chiffre.setText(str(valeur))


class FenetrePrincipale(QMainWindow):
    def __init__(self, config: Config, moteur: Moteur, icone: QIcon | None = None) -> None:
        super().__init__()
        self.config = config
        self.moteur = moteur
        self._icone = icone
        self._quitter_pour_de_bon = False
        self._evenements_affiches: list[str] = []
        self._chercheur: ChercheurMiseAJour | None = None
        self._telechargeur: TelechargeurMiseAJour | None = None
        self._progres: QProgressDialog | None = None

        self.setWindowTitle(f"{NOM_APPLICATION} {VERSION}")
        self.setMinimumSize(760, 640)
        if icone is not None:
            self.setWindowIcon(icone)

        self._construire()
        self._construire_zone_notification()

        self._minuteur = QTimer(self)
        self._minuteur.timeout.connect(self._rafraichir)
        self._minuteur.start(RAFRAICHISSEMENT_MS)
        self._rafraichir()

        # Après l'affichage : la fenêtre doit apparaître tout de suite, même si
        # GitHub met dix secondes à répondre.
        if config.verifier_mises_a_jour:
            QTimer.singleShot(
                DELAI_VERIF_MAJ_MS, lambda: self._verifier_maj(silencieux=True)
            )

    # ---------------------------------------------------------------- montage

    def _construire(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        racine = QVBoxLayout(central)
        racine.setContentsMargins(20, 16, 20, 16)
        racine.setSpacing(12)

        racine.addLayout(self._bandeau())
        racine.addLayout(self._selecteur())
        racine.addLayout(self._compteurs())

        self.barre = QProgressBar()
        self.barre.setRange(0, 100)
        self.barre.setTextVisible(False)
        racine.addWidget(self.barre)

        self.sous_barre = QLabel("—")
        self.sous_barre.setObjectName("note")
        self.sous_barre.setAlignment(Qt.AlignmentFlag.AlignCenter)
        racine.addWidget(self.sous_barre)

        self.ligne_debit = QLabel()
        self.ligne_debit.setObjectName("note")
        racine.addWidget(self.ligne_debit)

        self.etiquette_note = QLabel()
        self.etiquette_note.setObjectName("note")
        self.etiquette_note.setWordWrap(True)
        racine.addWidget(self.etiquette_note)

        self.etiquette_initialisees = QLabel()
        self.etiquette_initialisees.setObjectName("initialisees")
        self.etiquette_initialisees.setWordWrap(True)
        self.etiquette_initialisees.hide()
        racine.addWidget(self.etiquette_initialisees)

        self.etiquette_erreur = QLabel()
        self.etiquette_erreur.setObjectName("erreur")
        self.etiquette_erreur.setWordWrap(True)
        racine.addWidget(self.etiquette_erreur)

        racine.addLayout(self._boutons())

        self.liste_journal = QListWidget()
        self.liste_journal.setMinimumHeight(150)
        racine.addWidget(self.liste_journal, 1)

    def _bandeau(self) -> QHBoxLayout:
        ligne = QHBoxLayout()
        titre = QLabel(NOM_APPLICATION.upper())
        titre.setObjectName("titre")
        self.etiquette_evenement = QLabel("aucun événement choisi")
        self.etiquette_evenement.setObjectName("evenement")
        self.badge_pause = QLabel("EN PAUSE")
        self.badge_pause.setObjectName("badgePause")
        self.badge_pause.hide()

        self.bouton_maj = QPushButton("Mise à jour")
        self.bouton_maj.setToolTip(
            "Chercher tout de suite une nouvelle version et l'installer."
        )
        self.bouton_maj.clicked.connect(lambda: self._verifier_maj(silencieux=False))

        bouton_reglages = QPushButton("Réglages")
        bouton_reglages.clicked.connect(self._ouvrir_reglages)

        ligne.addWidget(titre)
        ligne.addSpacing(12)
        ligne.addWidget(self.etiquette_evenement, 1)
        ligne.addWidget(self.badge_pause)
        ligne.addWidget(self.bouton_maj)
        ligne.addWidget(bouton_reglages)
        return ligne

    def _selecteur(self) -> QVBoxLayout:
        bloc = QVBoxLayout()
        ligne = QHBoxLayout()

        self.liste_evenements = QComboBox()
        self.liste_evenements.setEditable(True)
        self.liste_evenements.lineEdit().setPlaceholderText(
            r"Choisissez un événement, ou collez un chemin (\\serveur\Kadra\redim\MON_EVENEMENT)"
        )
        bouton_parcourir = QPushButton("Parcourir…")
        bouton_parcourir.clicked.connect(self._parcourir)
        bouton_surveiller = QPushButton("Surveiller ce dossier")
        bouton_surveiller.clicked.connect(self._surveiller)

        ligne.addWidget(self.liste_evenements, 1)
        ligne.addWidget(bouton_parcourir)
        ligne.addWidget(bouton_surveiller)
        bloc.addLayout(ligne)

        self.etiquette_source = QLabel()
        self.etiquette_source.setObjectName("source")
        bloc.addWidget(self.etiquette_source)
        return bloc

    def _compteurs(self) -> QHBoxLayout:
        ligne = QHBoxLayout()
        self.carte_detectees = Carte("Détectées", theme.BLEU)
        self.carte_passees = Carte("Passées", theme.VERT)
        self.carte_avenir = Carte("À venir", theme.JAUNE)
        self.carte_erreurs = Carte("Erreurs", theme.ROUGE)
        for carte in (
            self.carte_detectees,
            self.carte_passees,
            self.carte_avenir,
            self.carte_erreurs,
        ):
            ligne.addWidget(carte)
        return ligne

    def _boutons(self) -> QHBoxLayout:
        ligne = QHBoxLayout()
        self.bouton_pause = QPushButton("Pause")
        self.bouton_pause.setObjectName("pause")
        self.bouton_pause.clicked.connect(self._basculer_pause)

        bouton_init = QPushButton("Initialiser la mémoire")
        bouton_init.setToolTip(
            "Déclare les photos présentes comme déjà envoyées, sans rien envoyer."
        )
        bouton_init.clicked.connect(self._initialiser)

        # N'apparaît que s'il y a quelque chose à annuler : sinon c'est du bruit.
        self.bouton_annuler_init = QPushButton("Annuler l'initialisation")
        self.bouton_annuler_init.setToolTip(
            "Remet en file d'attente les photos déclarées envoyées sans l'avoir été."
        )
        self.bouton_annuler_init.clicked.connect(self._annuler_initialisation)
        self.bouton_annuler_init.hide()

        bouton_reset = QPushButton("Réinitialiser (tout renvoyer)")
        bouton_reset.setObjectName("danger")
        bouton_reset.clicked.connect(self._reinitialiser)

        bouton_dossier = QPushButton("Ouvrir le dossier de l'outil")
        bouton_dossier.clicked.connect(self._ouvrir_dossier)

        for bouton in (
            self.bouton_pause,
            bouton_init,
            self.bouton_annuler_init,
            bouton_reset,
            bouton_dossier,
        ):
            ligne.addWidget(bouton)
        ligne.addStretch(1)
        return ligne

    def _construire_zone_notification(self) -> None:
        """Icône près de l'horloge : l'outil vit sa vie fenêtre fermée."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.icone_zone = None
            return
        self.icone_zone = QSystemTrayIcon(self._icone or QIcon(), self)
        menu = QMenu()

        action_ouvrir = QAction("Ouvrir la fenêtre", self)
        action_ouvrir.triggered.connect(self._revenir_au_premier_plan)
        self.action_pause = QAction("Pause", self)
        self.action_pause.triggered.connect(self._basculer_pause)
        action_quitter = QAction("Quitter (arrête les envois)", self)
        action_quitter.triggered.connect(self._quitter)

        menu.addAction(action_ouvrir)
        menu.addAction(self.action_pause)
        menu.addSeparator()
        menu.addAction(action_quitter)

        self.icone_zone.setContextMenu(menu)
        self.icone_zone.activated.connect(self._clic_zone)
        self.icone_zone.show()

    # ---------------------------------------------------------------- actions

    def _parcourir(self) -> None:
        depart = self.config.dossier_source or self.config.base_redim
        dossier = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de l'événement à surveiller", depart
        )
        if dossier:
            self.liste_evenements.setEditText(dossier)
            self._surveiller()

    def _surveiller(self) -> None:
        saisie = self.liste_evenements.currentText().strip()
        if not saisie:
            QMessageBox.information(
                self, "Événement", "Choisissez un événement ou collez un chemin."
            )
            return
        if saisie == self.config.evenement:
            return
        reponse = QMessageBox.question(
            self,
            "Surveiller ce dossier ?",
            f"Surveiller :\n{saisie}\n\nLes nouvelles photos partiront sur Lamapix.",
        )
        if reponse == QMessageBox.StandardButton.Yes:
            self.moteur.choisir_evenement(saisie)

    def _basculer_pause(self) -> None:
        self.moteur.basculer_pause()
        self._rafraichir()

    def _initialiser(self) -> None:
        if not self.config.evenement:
            return
        dialogue = DialogueInitialisation(self.moteur, self)
        if not dialogue.exec():
            return
        nombre = self.moteur.initialiser_memoire(dialogue.frontiere())
        QMessageBox.information(
            self,
            "Initialisation",
            f"{nombre} photo(s) déclarée(s) déjà envoyée(s). Rien n'a été envoyé.\n\n"
            "Si Kadra n'avait pas fini d'uploader, ces photos ne partiront jamais : "
            "le bouton « Annuler l'initialisation » les remet en file d'attente.",
        )

    def _annuler_initialisation(self) -> None:
        initialisees = self.moteur.etat().initialisees
        if not initialisees:
            return
        reponse = QMessageBox.question(
            self,
            "Annuler l'initialisation",
            f"Remettre en file d'attente les {initialisees} photo(s) déclarées "
            "envoyées sans l'avoir été ?\n\n"
            "Elles partiront sur Lamapix. Si elles y sont déjà, elles y "
            "apparaîtront en double.\n\n"
            "Les photos réellement envoyées par l'outil ne sont pas concernées.",
        )
        if reponse == QMessageBox.StandardButton.Yes:
            self.moteur.annuler_initialisation()

    def _reinitialiser(self) -> None:
        if not self.config.evenement:
            return
        reponse = QMessageBox.warning(
            self,
            "Tout renvoyer ?",
            f"Effacer la mémoire de « {self.config.evenement} » ?\n\n"
            "TOUTES les photos présentes seront (re)envoyées sur Lamapix.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reponse == QMessageBox.StandardButton.Yes:
            self.moteur.reinitialiser_memoire()

    def _ouvrir_reglages(self) -> None:
        dialogue = DialogueReglages(self.config, self)
        if dialogue.exec():
            self.moteur.recharger_config()

    # -------------------------------------------------------------- mise à jour

    def _verifier_maj(self, silencieux: bool) -> None:
        """`silencieux` : vérification au démarrage, qui ne dit rien s'il n'y a
        rien. À la demande, au contraire, on répond toujours quelque chose."""
        if self._chercheur is not None:
            return
        if not silencieux:
            self.bouton_maj.setEnabled(False)
            self.bouton_maj.setText("Vérification…")
        self._chercheur = ChercheurMiseAJour(self.config.depot_mises_a_jour, self)
        self._chercheur.fini.connect(
            lambda trouvee: self._reponse_maj(trouvee, silencieux)
        )
        self._chercheur.demarrer()

    def _reponse_maj(self, trouvee, silencieux: bool) -> None:
        self._chercheur = None
        self.bouton_maj.setEnabled(True)
        self.bouton_maj.setText("Mise à jour")

        if trouvee is None:
            self.moteur.journal.ecrire("Vérification des mises à jour : sans réponse")
            if not silencieux:
                QMessageBox.warning(
                    self,
                    "Mise à jour",
                    "Impossible de joindre GitHub.\n\n"
                    "Sans importance : l'outil continue d'envoyer normalement.",
                )
            return

        if not trouvee.plus_recente:
            if not silencieux:
                QMessageBox.information(
                    self, "Mise à jour", f"Vous êtes à jour (version {VERSION})."
                )
            return

        self.moteur.journal.ecrire(f"Mise à jour disponible : version {trouvee.version}")

        if not paths.est_gele():
            QMessageBox.information(
                self,
                "Mise à jour",
                f"Version {trouvee.version} disponible (vous avez {VERSION}).\n\n"
                "Vous tournez depuis les sources : faites un « git pull ».",
            )
            return

        notes = (trouvee.notes or "").strip()
        if len(notes) > 400:
            notes = notes[:400] + "…"
        reponse = QMessageBox.question(
            self,
            "Mise à jour disponible",
            f"Version {trouvee.version} disponible (vous avez {VERSION}).\n\n"
            f"{notes}\n\n"
            "Installer maintenant ? L'outil se ferme et se relance seul. "
            "La mémoire est conservée : les envois reprennent où ils en étaient.",
        )
        if reponse == QMessageBox.StandardButton.Yes:
            self._telecharger_maj(trouvee)

    def _telecharger_maj(self, trouvee) -> None:
        self._progres = QProgressDialog(
            f"Téléchargement de la version {trouvee.version}…", "", 0, 0, self
        )
        self._progres.setWindowTitle("Mise à jour")
        self._progres.setCancelButton(None)   # un exe à moitié écrit ne sert à rien
        self._progres.setWindowModality(Qt.WindowModality.WindowModal)
        self._progres.show()

        self._telechargeur = TelechargeurMiseAJour(trouvee, self)
        self._telechargeur.fini.connect(self._installer_maj)
        self._telechargeur.echec.connect(self._echec_maj)
        self._telechargeur.demarrer()

    def _installer_maj(self, chemin) -> None:
        self._fermer_progres()
        try:
            updater.appliquer(chemin)
        except Exception as exc:
            QMessageBox.critical(
                self, "Mise à jour", f"Installation impossible.\n\n{exc}"
            )
            return
        self.moteur.journal.ecrire("Mise à jour : redémarrage en cours")
        self._quitter()

    def _echec_maj(self, message: str) -> None:
        self._fermer_progres()
        QMessageBox.critical(
            self,
            "Mise à jour",
            f"Téléchargement impossible.\n\n{message}\n\n"
            "L'outil continue de fonctionner normalement.",
        )

    def _fermer_progres(self) -> None:
        if self._progres is not None:
            self._progres.close()
            self._progres = None

    def _ouvrir_dossier(self) -> None:
        import os

        try:
            os.startfile(paths.racine_donnees())  # noqa: S606  (Windows uniquement)
        except (AttributeError, OSError) as exc:
            QMessageBox.information(
                self, "Dossier", f"{paths.racine_donnees()}\n\n({exc})"
            )

    # ------------------------------------------------------------ rafraîchissement

    def _rafraichir(self) -> None:
        etat = self.moteur.etat()
        self._maj_bandeau(etat)
        self._maj_compteurs(etat)
        self._maj_journal(etat)
        self._maj_zone_notification(etat)

    def _maj_bandeau(self, etat: Etat) -> None:
        self.etiquette_evenement.setText(
            f"Événement : {etat.evenement}" if etat.evenement else "aucun événement choisi"
        )
        self.etiquette_source.setText(
            f"Dossier surveillé : {etat.source}" if etat.source else ""
        )
        self.badge_pause.setVisible(etat.en_pause)
        self.bouton_pause.setText("Reprendre les envois" if etat.en_pause else "Pause")

        # On ne réécrit la liste que si elle a changé : sinon on écraserait la
        # saisie en cours de l'utilisateur à chaque seconde.
        if etat.evenements_disponibles != self._evenements_affiches:
            self._evenements_affiches = list(etat.evenements_disponibles)
            saisie = self.liste_evenements.currentText()
            self.liste_evenements.blockSignals(True)
            self.liste_evenements.clear()
            self.liste_evenements.addItems(self._evenements_affiches)
            self.liste_evenements.setEditText(saisie or (etat.evenement or ""))
            self.liste_evenements.blockSignals(False)

    def _maj_compteurs(self, etat: Etat) -> None:
        self.carte_detectees.afficher(etat.detectees)
        self.carte_passees.afficher(etat.envoyees)
        self.carte_avenir.afficher(etat.en_attente)
        self.carte_erreurs.afficher(etat.erreurs)

        self.barre.setValue(etat.pourcentage)
        self.sous_barre.setText(
            f"{etat.envoyees} / {etat.total_connu} photos envoyées ({etat.pourcentage} %)"
        )
        self.ligne_debit.setText(
            f"Débit : {etat.debit_par_minute} photo(s)/min     "
            f"Dernier envoi : {etat.dernier_envoi or '—'}"
            + (f"     En cours : {etat.en_cours}" if etat.en_cours else "")
        )
        self.etiquette_note.setText(etat.note)

        # Le compteur « passées » mélange envois réels et déclarations : on dit
        # lesquelles ne reposent que sur la parole de l'opérateur.
        self.bouton_annuler_init.setVisible(bool(etat.initialisees))
        self.etiquette_initialisees.setVisible(bool(etat.initialisees))
        self.etiquette_initialisees.setText(
            f"Dont {etat.initialisees} photo(s) déclarées envoyées sans vérification "
            "(initialisation) — non vérifiable côté Lamapix."
            if etat.initialisees
            else ""
        )

        self.etiquette_erreur.setText(
            f"Dernière erreur : {etat.derniere_erreur}" if etat.derniere_erreur else ""
        )

    def _maj_journal(self, etat: Etat) -> None:
        lignes = etat.journal
        if [self.liste_journal.item(i).text() for i in range(self.liste_journal.count())] == lignes:
            return
        self.liste_journal.clear()
        for ligne in lignes:
            self.liste_journal.addItem(ligne)
            element = self.liste_journal.item(self.liste_journal.count() - 1)
            if ligne.startswith("OK "):
                element.setForeground(Qt.GlobalColor.green)
            elif ligne.startswith("ERREUR "):
                element.setForeground(Qt.GlobalColor.red)

    def _maj_zone_notification(self, etat: Etat) -> None:
        if getattr(self, "icone_zone", None) is None:
            return
        self.action_pause.setText("Reprendre" if etat.en_pause else "Pause")
        resume = etat.evenement or "aucun événement"
        if etat.en_pause:
            resume += " — EN PAUSE"
        elif etat.en_attente:
            resume += f" — {etat.en_attente} en attente"
        else:
            resume += " — à jour"
        self.icone_zone.setToolTip(f"{NOM_APPLICATION}\n{resume}")

    # -------------------------------------------------------------- fermeture

    def _clic_zone(self, raison: QSystemTrayIcon.ActivationReason) -> None:
        if raison == QSystemTrayIcon.ActivationReason.Trigger:
            self._revenir_au_premier_plan()

    def _revenir_au_premier_plan(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quitter(self) -> None:
        """Arrêt réel du process.

        `setQuitOnLastWindowClosed(False)` — nécessaire pour survivre à la
        fermeture de la fenêtre — signifie aussi que fermer ne suffit PAS à
        quitter : sans `quit()` explicite, l'outil restait vivant, invisible,
        avec son moteur qui tournait toujours.
        """
        self._quitter_pour_de_bon = True
        self.close()
        if getattr(self, "icone_zone", None) is not None:
            self.icone_zone.hide()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Fermer la fenêtre ne doit pas couper les envois en plein concours."""
        garder = (
            self.config.reduire_dans_zone_notification
            and getattr(self, "icone_zone", None) is not None
            and not self._quitter_pour_de_bon
        )
        if garder:
            event.ignore()
            self.hide()
            self.icone_zone.showMessage(
                NOM_APPLICATION,
                "Les envois continuent en arrière-plan. Clic sur l'icône pour revenir.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            return
        self.moteur.arreter()
        event.accept()


def dossier_par_defaut(config: Config) -> Path:
    return Path(config.dossier_source or config.base_redim)
