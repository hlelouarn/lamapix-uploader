"""Fenêtre principale : un tableau de bord qu'on regarde de loin.

Elle ne fait rien elle-même — elle pousse des commandes au moteur et relit son
état une fois par seconde. Fermer la fenêtre ne suspend donc aucun envoi.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
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
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import NOM_APPLICATION, VERSION, paths
from ..config import Config
from ..engine import Etat, Moteur
from . import theme
from .settings_dialog import DialogueReglages

RAFRAICHISSEMENT_MS = 1000


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

        bouton_reglages = QPushButton("Réglages")
        bouton_reglages.clicked.connect(self._ouvrir_reglages)

        ligne.addWidget(titre)
        ligne.addSpacing(12)
        ligne.addWidget(self.etiquette_evenement, 1)
        ligne.addWidget(self.badge_pause)
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
            "Marque toutes les photos présentes comme déjà envoyées, sans rien envoyer."
        )
        bouton_init.clicked.connect(self._initialiser)

        bouton_reset = QPushButton("Réinitialiser (tout renvoyer)")
        bouton_reset.setObjectName("danger")
        bouton_reset.clicked.connect(self._reinitialiser)

        bouton_dossier = QPushButton("Ouvrir le dossier de l'outil")
        bouton_dossier.clicked.connect(self._ouvrir_dossier)

        for bouton in (self.bouton_pause, bouton_init, bouton_reset, bouton_dossier):
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
        reponse = QMessageBox.question(
            self,
            "Initialiser la mémoire",
            "Marquer TOUTES les photos actuellement présentes comme déjà envoyées ?\n\n"
            "Rien ne sera envoyé. Seules les photos qui arriveront ENSUITE partiront "
            "sur Lamapix.\n\nÀ utiliser quand Kadra a déjà uploadé une partie de "
            "l'événement.",
        )
        if reponse != QMessageBox.StandardButton.Yes:
            return
        nombre = self.moteur.initialiser_memoire()
        QMessageBox.information(
            self,
            "Initialisation",
            f"{nombre} photo(s) marquée(s) comme déjà envoyée(s).\nRien n'a été envoyé.",
        )

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
        self._quitter_pour_de_bon = True
        self.close()

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
