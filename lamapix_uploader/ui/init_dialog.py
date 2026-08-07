"""Dialogue « Initialiser la mémoire ».

Ce geste est le seul de l'outil qui repose sur une croyance et non sur un fait :
Lamapix aspirant ce qu'on y dépose, rien ne permet de savoir ce qu'il a reçu.
L'écran doit donc dire franchement ce qu'il fait, montrer combien de photos il
va avaler, et laisser choisir la frontière plutôt que de l'imposer.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..engine import Moteur


class DialogueInitialisation(QDialog):
    """Retourne, via `frontiere()`, le timestamp choisi (ou None = tout)."""

    def __init__(self, moteur: Moteur, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.moteur = moteur
        self.setWindowTitle("Initialiser la mémoire")
        self.setMinimumWidth(560)

        disposition = QVBoxLayout(self)
        disposition.addWidget(self._explication())
        disposition.addSpacing(6)

        self.choix_tout = QRadioButton("Toutes les photos présentes")
        self.choix_tout.setChecked(True)
        self.choix_tout.toggled.connect(self._rafraichir)
        disposition.addWidget(self.choix_tout)

        ligne = QHBoxLayout()
        self.choix_avant = QRadioButton("Seulement celles antérieures à :")
        self.champ_date = QDateTimeEdit(QDateTime.currentDateTime())
        self.champ_date.setDisplayFormat("dd/MM/yyyy  HH:mm")
        self.champ_date.setCalendarPopup(True)
        self.champ_date.setEnabled(False)
        self.champ_date.dateTimeChanged.connect(self._rafraichir)
        self.choix_avant.toggled.connect(self.champ_date.setEnabled)
        ligne.addWidget(self.choix_avant)
        ligne.addWidget(self.champ_date)
        ligne.addStretch(1)
        disposition.addLayout(ligne)

        self.apercu = QLabel()
        self.apercu.setObjectName("evenement")
        self.apercu.setWordWrap(True)
        disposition.addSpacing(8)
        disposition.addWidget(self.apercu)

        self.avertissement = QLabel()
        self.avertissement.setObjectName("erreur")
        self.avertissement.setWordWrap(True)
        disposition.addWidget(self.avertissement)

        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        boutons.button(QDialogButtonBox.StandardButton.Ok).setText("Poser la frontière")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        disposition.addWidget(boutons)

        self._rafraichir()

    # ------------------------------------------------------------------ contenu

    @staticmethod
    def _explication() -> QLabel:
        texte = QLabel(
            "Les photos retenues seront <b>déclarées déjà envoyées</b>, sans rien "
            "envoyer. Seules celles qui arriveront ensuite partiront sur Lamapix."
            "<br><br>"
            "L'outil <b>ne peut pas vérifier</b> ce que Lamapix a réellement reçu : "
            "il aspire les fichiers déposés, le dossier distant est vide la plupart "
            "du temps. C'est donc votre déclaration, pas un constat. Si Kadra s'est "
            "arrêté en cours d'événement, posez la frontière à l'heure de son arrêt "
            "plutôt que d'avaler tout le dossier."
            "<br><br>"
            "Le geste reste <b>annulable</b> : le bouton « Annuler l'initialisation » "
            "remet ces photos en file d'attente."
        )
        texte.setWordWrap(True)
        texte.setTextFormat(Qt.TextFormat.RichText)
        return texte

    def frontiere(self) -> float | None:
        if self.choix_tout.isChecked():
            return None
        return float(self.champ_date.dateTime().toSecsSinceEpoch())

    def _rafraichir(self) -> None:
        concernees, total = self.moteur.apercu_initialisation(self.frontiere())
        restantes = total - concernees
        self.apercu.setText(
            f"{concernees} photo(s) sur {total} seraient déclarées déjà envoyées."
            + (f"  Les {restantes} autres partiront normalement." if restantes else "")
        )
        self.avertissement.setText(
            "Aucune photo ne serait concernée : vérifiez la date choisie."
            if not concernees
            else ""
        )
