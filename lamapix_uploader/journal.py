"""Journal horodaté : fichier tournant + les 15 dernières lignes pour l'écran.

Le fichier est la trace d'exploitation (on doit pouvoir répondre à « cette photo
est-elle partie, et quand ? » deux semaines plus tard) ; l'anneau mémoire n'est
qu'un confort d'affichage.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from pathlib import Path

LIGNES_AFFICHEES = 15
TAILLE_MAX_OCTETS = 2_000_000
ARCHIVES_CONSERVEES = 5


class Journal:
    """Écriture thread-safe : plusieurs uploads en parallèle y écrivent."""

    def __init__(self, fichier: Path | None = None) -> None:
        self._verrou = threading.Lock()
        self._fichier = fichier
        self._recentes: deque[str] = deque(maxlen=LIGNES_AFFICHEES)

    def rediriger(self, fichier: Path) -> None:
        with self._verrou:
            self._fichier = fichier

    def ecrire(self, message: str, marque: str = "") -> None:
        """Une ligne dans le fichier ET dans l'anneau affiché."""
        maintenant = datetime.now()
        ligne_fichier = f"{maintenant:%Y-%m-%d %H:%M:%S}  {message}"
        ligne_ecran = f"{maintenant:%H:%M:%S}  {message}"
        with self._verrou:
            self._recentes.appendleft(f"{marque}{ligne_ecran}" if marque else ligne_ecran)
            fichier = self._fichier
        if fichier is None:
            return
        try:
            self._tourner_si_gros(fichier)
            with fichier.open("a", encoding="utf-8") as flux:
                flux.write(ligne_fichier + "\n")
        except OSError:
            pass  # un journal injoignable ne doit jamais arrêter les envois

    def succes(self, message: str) -> None:
        self.ecrire(message, marque="OK ")

    def erreur(self, message: str) -> None:
        self.ecrire(message, marque="ERREUR ")

    def dernieres(self) -> list[str]:
        with self._verrou:
            return list(self._recentes)

    @staticmethod
    def _tourner_si_gros(fichier: Path) -> None:
        """Rotation simple : journal.txt → journal.1.txt → … → journal.5.txt."""
        try:
            if not fichier.exists() or fichier.stat().st_size < TAILLE_MAX_OCTETS:
                return
        except OSError:
            return
        for index in range(ARCHIVES_CONSERVEES, 0, -1):
            ancien = fichier.with_suffix(f".{index}{fichier.suffix}")
            if index == ARCHIVES_CONSERVEES:
                ancien.unlink(missing_ok=True)
                continue
            suivant = fichier.with_suffix(f".{index + 1}{fichier.suffix}")
            if ancien.exists():
                ancien.replace(suivant)
        fichier.replace(fichier.with_suffix(f".1{fichier.suffix}"))


class Diagnostic:
    """Trace technique horodatée à la milliseconde, pour analyser les lenteurs.

    Le journal lisible dit CE QUI s'est passé ; ce fichier dit COMBIEN DE TEMPS
    chaque étape a pris (scan, copie, chaque envoi, coupures, sondes), en
    « clé=valeur » facile à dépouiller. C'est lui qu'on envoie quand « c'est
    lent » : il désigne le coupable au lieu de le faire deviner.
    """

    def __init__(self, fichier: Path | None = None) -> None:
        self._verrou = threading.Lock()
        self._fichier = fichier

    def rediriger(self, fichier: Path) -> None:
        with self._verrou:
            self._fichier = fichier

    def tracer(self, etape: str, **valeurs) -> None:
        with self._verrou:
            fichier = self._fichier
        if fichier is None:
            return
        maintenant = datetime.now()
        champs = " ".join(f"{cle}={valeur}" for cle, valeur in valeurs.items())
        ligne = f"{maintenant:%Y-%m-%d %H:%M:%S}.{maintenant.microsecond // 1000:03d} {etape} {champs}"
        try:
            Journal._tourner_si_gros(fichier)
            with fichier.open("a", encoding="utf-8") as flux:
                flux.write(ligne.rstrip() + "\n")
        except OSError:
            pass  # le diagnostic ne doit jamais gêner les envois
