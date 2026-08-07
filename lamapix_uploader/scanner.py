"""Scan du dossier événement (§4.1 et §4.2 du brief).

Deux protections :
- on ignore les fichiers modifiés il y a moins de ~15 s (Kadra ou une synchro est
  peut-être encore en train de les écrire — on enverrait un JPEG tronqué) ;
- une source réseau qui tombe (`\\\\serveur\\...`) ne fait pas planter l'outil :
  le scan renvoie ce qu'il a pu lire et reprendra au tour suivant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path, PurePath

from .mapping import EXTENSIONS_PAR_DEFAUT, est_photo_eligible


@dataclass(frozen=True)
class PhotoTrouvee:
    """Une photo source retenue par le scan."""

    chemin: Path        # chemin absolu
    relatif: str        # relatif au dossier événement (style Windows d'origine)
    taille: int
    modifie_le: float   # timestamp


def scanner(
    dossier_source: Path,
    extensions: tuple[str, ...] = EXTENSIONS_PAR_DEFAUT,
    delai_stabilite: float = 15.0,
    maintenant: float | None = None,
) -> list[PhotoTrouvee]:
    """Liste les photos éligibles et stables du dossier événement."""
    instant = time.time() if maintenant is None else maintenant
    limite = instant - delai_stabilite
    trouvees: list[PhotoTrouvee] = []

    try:
        candidats = dossier_source.rglob("*")
    except OSError:
        return []

    while True:
        try:
            chemin = next(candidats)
        except StopIteration:
            break
        except OSError:
            # Un sous-dossier illisible (réseau coupé, droits) ne doit pas
            # interrompre le scan : on abandonne juste cette branche.
            break

        try:
            if not chemin.is_file():
                continue
            relatif = chemin.relative_to(dossier_source)
            if not est_photo_eligible(relatif, extensions):
                continue
            infos = chemin.stat()
        except (OSError, ValueError):
            continue

        if infos.st_mtime > limite:
            continue  # encore en cours d'écriture, on le reverra au prochain scan

        trouvees.append(
            PhotoTrouvee(
                chemin=chemin,
                relatif=str(PurePath(relatif)),
                taille=infos.st_size,
                modifie_le=infos.st_mtime,
            )
        )

    return trouvees


def lister_evenements(base: Path, limite: int = 30) -> list[str]:
    """Sous-dossiers du répertoire de base, les plus récents en premier."""
    try:
        dossiers = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return []
    try:
        dossiers.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        dossiers.sort(key=lambda d: d.name, reverse=True)
    return [d.name for d in dossiers[:limite]]
