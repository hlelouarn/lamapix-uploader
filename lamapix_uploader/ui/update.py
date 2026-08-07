"""Mise à jour côté interface : recherche et téléchargement hors du thread Qt.

Le réseau ne doit jamais figer la fenêtre — un GitHub lent ou injoignable est
la norme sur un site de concours. Les workers travaillent dans un thread Python
et remontent leur résultat par signal : Qt le livre automatiquement au thread de
l'interface, seul autorisé à ouvrir des boîtes de dialogue.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import updater


class ChercheurMiseAJour(QObject):
    """Interroge les releases GitHub. Ne lève jamais : émet None en cas d'échec."""

    fini = Signal(object)  # updater.MiseAJour | None

    def __init__(self, depot: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._depot = depot

    def demarrer(self) -> None:
        threading.Thread(target=self._travail, name="maj-recherche", daemon=True).start()

    def _travail(self) -> None:
        try:
            resultat = updater.chercher(self._depot)
        except Exception:
            resultat = None
        self.fini.emit(resultat)


class TelechargeurMiseAJour(QObject):
    """Récupère le nouvel exe dans un dossier temporaire."""

    fini = Signal(object)   # Path
    echec = Signal(str)

    def __init__(self, mise_a_jour: "updater.MiseAJour", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mise_a_jour = mise_a_jour

    def demarrer(self) -> None:
        threading.Thread(target=self._travail, name="maj-telechargement", daemon=True).start()

    def _travail(self) -> None:
        try:
            chemin: Path = updater.telecharger(self._mise_a_jour)
        except Exception as exc:
            self.echec.emit(str(exc))
            return
        self.fini.emit(chemin)
