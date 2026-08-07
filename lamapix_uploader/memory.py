"""Mémoire persistante des photos déjà envoyées (§5 du brief) — le cœur de l'outil.

Lamapix « aspire » les fichiers déposés : ce qui est sur le FTP disparaît après
ingestion. Le serveur distant ne peut donc JAMAIS servir de référence pour savoir
ce qui a été envoyé. Cette mémoire locale est la seule source de vérité.

Un fichier JSON par événement, clé = chemin source complet. Écriture atomique
(fichier temporaire + remplacement) pour qu'une coupure de courant ne laisse
jamais un JSON tronqué — sinon on renverrait tout l'événement.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VERSION_FORMAT = 3

# Au-delà de N envois sans sauvegarde, on écrit : un crash ne coûte que N doublons.
ENVOIS_ENTRE_SAUVEGARDES = 20


@dataclass
class Entree:
    """Ce qu'on retient d'une photo source."""

    rel: str                      # chemin distant relatif, style POSIX
    taille: int                   # sert à détecter une photo retouchée
    envoyee: bool = False
    envoyee_le: str | None = None  # ISO 8601


@dataclass
class MemoireEvenement:
    """Mémoire d'un événement. Liée au chemin source exact ET à la machine."""

    fichier: Path
    entrees: dict[str, Entree] = field(default_factory=dict)
    rels_utilises: dict[str, str] = field(default_factory=dict)
    _depuis_sauvegarde: int = 0

    # ---------------------------------------------------------------- lecture

    @classmethod
    def charger(cls, fichier: Path) -> "MemoireEvenement":
        """Charge la mémoire. Un JSON illisible ne fait pas planter l'outil : on
        repart de zéro (au pire on renvoie des photos, jamais on n'en perd)."""
        memoire = cls(fichier=fichier)
        if not fichier.exists():
            return memoire
        try:
            donnees = json.loads(fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return memoire

        for source, brut in (donnees.get("files") or {}).items():
            try:
                entree = Entree(
                    rel=str(brut["rel"]),
                    taille=int(brut.get("size", 0)),
                    envoyee=bool(brut.get("sent", False)),
                    envoyee_le=brut.get("sentAt"),
                )
            except (KeyError, TypeError, ValueError):
                continue
            memoire.entrees[source] = entree
            memoire.rels_utilises[entree.rel] = source
        return memoire

    # -------------------------------------------------------------- écriture

    def sauver(self) -> None:
        """Écriture atomique : on écrit à côté puis on remplace d'un seul geste."""
        charge = {
            "version": VERSION_FORMAT,
            "files": {
                source: {
                    "rel": e.rel,
                    "size": e.taille,
                    "sent": e.envoyee,
                    "sentAt": e.envoyee_le,
                }
                for source, e in self.entrees.items()
            },
        }
        temporaire = self.fichier.with_suffix(self.fichier.suffix + ".tmp")
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        temporaire.write_text(
            json.dumps(charge, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporaire, self.fichier)
        self._depuis_sauvegarde = 0

    def sauver_si_necessaire(self, seuil: int = ENVOIS_ENTRE_SAUVEGARDES) -> bool:
        """Sauvegarde périodique pendant un gros rattrapage. True si on a écrit."""
        if self._depuis_sauvegarde >= seuil:
            self.sauver()
            return True
        return False

    # ------------------------------------------------------------ mutations

    def enregistrer(self, source: str, rel: str, taille: int) -> Entree:
        """Déclare (ou redéclare) une photo à envoyer."""
        entree = Entree(rel=rel, taille=taille, envoyee=False, envoyee_le=None)
        self.entrees[source] = entree
        self.rels_utilises[rel] = source
        return entree

    def marquer_envoyee(self, source: str, horodatage: datetime | None = None) -> None:
        entree = self.entrees.get(source)
        if entree is None:
            return
        moment = horodatage or datetime.now(timezone.utc)
        entree.envoyee = True
        entree.envoyee_le = moment.isoformat()
        self._depuis_sauvegarde += 1

    def est_nouvelle(self, source: str, taille: int) -> bool:
        """Une photo est « nouvelle » si inconnue, ou si sa taille a changé
        (photo retouchée → on la renvoie sous le même nom distant, qui écrase)."""
        entree = self.entrees.get(source)
        return entree is None or entree.taille != taille

    def marquer_tout_envoye(self, photos: list[tuple[str, str, int]]) -> int:
        """« Initialiser » : poser la frontière sans rien envoyer.

        `photos` = liste de (source, rel, taille). Les entrées déjà connues ne sont
        pas touchées — on ne réécrit pas l'histoire d'un envoi réel.
        """
        maintenant = datetime.now(timezone.utc).isoformat()
        nombre = 0
        for source, rel, taille in photos:
            if source in self.entrees:
                continue
            self.entrees[source] = Entree(
                rel=rel, taille=taille, envoyee=True, envoyee_le=maintenant
            )
            self.rels_utilises[rel] = source
            nombre += 1
        self.sauver()
        return nombre

    def effacer(self) -> None:
        """« Réinitialiser » : tout sera renvoyé."""
        self.entrees.clear()
        self.rels_utilises.clear()
        self._depuis_sauvegarde = 0
        try:
            self.fichier.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------- lectures

    @property
    def nombre_envoyees(self) -> int:
        return sum(1 for e in self.entrees.values() if e.envoyee)

    @property
    def nombre_en_attente(self) -> int:
        return sum(1 for e in self.entrees.values() if not e.envoyee)

    def en_attente(self) -> list[str]:
        """Sources restant à envoyer, triées par chemin distant (envoi dossier
        par dossier : plus lisible dans le journal, et meilleur pour le cache FTP)."""
        sources = [s for s, e in self.entrees.items() if not e.envoyee]
        return sorted(sources, key=lambda s: self.entrees[s].rel)
