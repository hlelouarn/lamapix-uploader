"""Écran de réglages : tout ce qui était en variables en tête de l'ancien script.

Le mot de passe n'est jamais réaffiché — on montre seulement s'il est mémorisé,
et on propose de le remplacer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import paths, secrets_win, updater
from ..config import Config
from ..ftp import ClientFtps, ErreurFtp, ErreurIdentifiants


class DialogueReglages(QDialog):
    """Modifie la config en place. `accept()` la sauvegarde sur disque."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Réglages — Lamapix Uploader")
        self.setMinimumWidth(560)
        self._mot_de_passe_saisi: str | None = None

        disposition = QVBoxLayout(self)
        disposition.addWidget(self._groupe_source())
        disposition.addWidget(self._groupe_lamapix())
        disposition.addWidget(self._groupe_rythme())
        disposition.addWidget(self._groupe_divers())

        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        boutons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        disposition.addWidget(boutons)

    # ------------------------------------------------------------------ groupes

    def _groupe_source(self) -> QGroupBox:
        groupe = QGroupBox("Source des photos")
        formulaire = QFormLayout(groupe)
        self.champ_base = QLineEdit(self.config.base_redim)
        self.champ_base.setPlaceholderText(r"\\serveur\Kadra\redim")
        formulaire.addRow("Dossier des événements Kadra :", self.champ_base)
        formulaire.addRow(
            "",
            self._aide("Le dossier qui contient un sous-dossier par événement."),
        )
        return groupe

    def _groupe_lamapix(self) -> QGroupBox:
        groupe = QGroupBox("Compte Lamapix (FTPS)")
        formulaire = QFormLayout(groupe)

        self.champ_hote = QLineEdit(self.config.ftp_hote)
        formulaire.addRow("Hôte :", self.champ_hote)

        self.champ_port = QSpinBox()
        self.champ_port.setRange(1, 65535)
        self.champ_port.setValue(self.config.ftp_port)
        formulaire.addRow("Port :", self.champ_port)

        self.champ_utilisateur = QLineEdit(self.config.ftp_utilisateur)
        self.champ_utilisateur.setPlaceholderText("identifiant du compte Lamapix")
        formulaire.addRow("Identifiant :", self.champ_utilisateur)

        ligne = QHBoxLayout()
        self.champ_mot_de_passe = QLineEdit()
        self.champ_mot_de_passe.setEchoMode(QLineEdit.EchoMode.Password)
        memorise = secrets_win.lire(paths.fichier_secret()) is not None
        self.champ_mot_de_passe.setPlaceholderText(
            "déjà mémorisé — laisser vide pour le conserver" if memorise
            else "à saisir (mémorisé chiffré sur ce PC)"
        )
        bouton_test = QPushButton("Tester la connexion")
        bouton_test.clicked.connect(self._tester)
        ligne.addWidget(self.champ_mot_de_passe, 1)
        ligne.addWidget(bouton_test)
        formulaire.addRow("Mot de passe :", ligne)

        protege = secrets_win.disponible()
        formulaire.addRow(
            "",
            self._aide(
                "Chiffré par Windows (DPAPI) : illisible depuis un autre compte ou un autre PC."
                if protege
                else "⚠ Hors Windows : le mot de passe n'est PAS réellement chiffré."
            ),
        )

        self.case_certificat = QCheckBox(
            "Ignorer les erreurs de certificat TLS (à n'activer qu'en cas de blocage)"
        )
        self.case_certificat.setChecked(self.config.ignorer_certificat)
        formulaire.addRow("", self.case_certificat)
        return groupe

    def _groupe_rythme(self) -> QGroupBox:
        groupe = QGroupBox("Rythme et robustesse")
        formulaire = QFormLayout(groupe)

        self.champ_intervalle = self._compteur(5, 3600, self.config.intervalle_scan, " s")
        formulaire.addRow("Pause entre deux scans :", self.champ_intervalle)

        self.champ_stabilite = self._compteur(0, 300, self.config.delai_stabilite, " s")
        formulaire.addRow("Attendre qu'un fichier soit stable :", self.champ_stabilite)

        self.champ_essais = self._compteur(1, 10, self.config.essais_max, "")
        formulaire.addRow("Tentatives par photo :", self.champ_essais)

        self.champ_paralleles = self._compteur(1, 3, self.config.connexions_paralleles, "")
        formulaire.addRow("Envois simultanés :", self.champ_paralleles)
        formulaire.addRow(
            "",
            self._aide("2 ou 3 accélèrent les gros rattrapages ; 1 = envoi séquentiel."),
        )

        self.champ_purge = self._compteur(0, 720, self.config.purge_apres_heures, " h")
        formulaire.addRow("Purger le tampon après :", self.champ_purge)
        formulaire.addRow(
            "",
            self._aide("0 = ne jamais purger. La mémoire, elle, n'est jamais effacée."),
        )
        return groupe

    def _groupe_divers(self) -> QGroupBox:
        groupe = QGroupBox("Démarrage")
        disposition = QVBoxLayout(groupe)

        self.case_demarrage = QCheckBox("Lancer automatiquement avec la session Windows")
        self.case_demarrage.setChecked(updater.demarrage_actif())
        disposition.addWidget(self.case_demarrage)

        self.case_zone = QCheckBox(
            "Continuer en arrière-plan quand on ferme la fenêtre (icône près de l'horloge)"
        )
        self.case_zone.setChecked(self.config.reduire_dans_zone_notification)
        disposition.addWidget(self.case_zone)

        self.case_maj = QCheckBox("Vérifier les mises à jour au démarrage")
        self.case_maj.setChecked(self.config.verifier_mises_a_jour)
        disposition.addWidget(self.case_maj)
        return groupe

    # ------------------------------------------------------------------ actions

    def _tester(self) -> None:
        """Un vrai aller-retour FTPS : c'est le seul test qui prouve quelque chose."""
        mot_de_passe = self.champ_mot_de_passe.text() or secrets_win.lire(
            paths.fichier_secret()
        )
        if not mot_de_passe:
            QMessageBox.warning(self, "Test", "Saisissez d'abord le mot de passe.")
            return

        client = ClientFtps(
            hote=self.champ_hote.text().strip(),
            port=self.champ_port.value(),
            utilisateur=self.champ_utilisateur.text().strip(),
            mot_de_passe=mot_de_passe,
            racine="",
            ignorer_certificat=self.case_certificat.isChecked(),
        )
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            client.tester()
        except ErreurIdentifiants as exc:
            QMessageBox.critical(
                self, "Test", f"Identifiants refusés par le serveur.\n\n{exc}"
            )
            return
        except ErreurFtp as exc:
            QMessageBox.critical(self, "Test", f"Connexion impossible.\n\n{exc}")
            return
        finally:
            client.fermer()
            self.unsetCursor()
        QMessageBox.information(self, "Test", "Connexion réussie (FTPS chiffré).")

    def accept(self) -> None:
        self.config.base_redim = self.champ_base.text().strip() or self.config.base_redim
        self.config.ftp_hote = self.champ_hote.text().strip()
        self.config.ftp_port = self.champ_port.value()
        self.config.ftp_utilisateur = self.champ_utilisateur.text().strip()
        self.config.ignorer_certificat = self.case_certificat.isChecked()
        self.config.intervalle_scan = self.champ_intervalle.value()
        self.config.delai_stabilite = self.champ_stabilite.value()
        self.config.essais_max = self.champ_essais.value()
        self.config.connexions_paralleles = self.champ_paralleles.value()
        self.config.purge_apres_heures = self.champ_purge.value()
        self.config.reduire_dans_zone_notification = self.case_zone.isChecked()
        self.config.verifier_mises_a_jour = self.case_maj.isChecked()
        self.config.sauver()

        saisi = self.champ_mot_de_passe.text()
        if saisi:
            secrets_win.enregistrer(paths.fichier_secret(), saisi)

        try:
            updater.raccourci_demarrage(self.case_demarrage.isChecked())
        except OSError as exc:
            QMessageBox.warning(
                self, "Démarrage automatique", f"Réglage impossible : {exc}"
            )
        super().accept()

    # -------------------------------------------------------------------- outils

    @staticmethod
    def _compteur(mini: int, maxi: int, valeur: int, suffixe: str) -> QSpinBox:
        champ = QSpinBox()
        champ.setRange(mini, maxi)
        champ.setValue(valeur)
        if suffixe:
            champ.setSuffix(suffixe)
        return champ

    @staticmethod
    def _aide(texte: str) -> QLabel:
        etiquette = QLabel(texte)
        etiquette.setObjectName("note")
        etiquette.setWordWrap(True)
        return etiquette
