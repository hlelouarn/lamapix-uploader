"""Règles de transformation « arborescence Kadra → arborescence Lamapix » (§3 du brief).

    <EPREUVE>\\<NUM>_<NOM>_<CHEVAL>\\x.jpg   ->  <NOM>_<CHEVAL>/x.jpg
    0_AMBIANCE\\**\\x.jpg                     ->  AMBIANCE/x.jpg   (tout à plat)
    x.jpg (racine du dossier surveillé)      ->  x.jpg

L'épreuve et le numéro de dossard disparaissent : un cavalier engagé sur plusieurs
épreuves fusionne donc dans un seul dossier distant.

Les chemins distants sont manipulés en style POSIX (`/`) : c'est ce qu'attend le FTP,
et ça évite toute ambiguïté avec les chemins Windows de la source.
"""

from __future__ import annotations

import re
from pathlib import PurePath, PurePosixPath

# Seuls les JPEG partent en vente.
EXTENSIONS_PAR_DEFAUT: tuple[str, ...] = (".jpg", ".jpeg")

# Kadra dépose parfois des dérivés dans un sous-dossier `webp` : à exclure totalement.
DOSSIER_EXCLU = "webp"

# Le dossier d'ambiance s'appelle `0_AMBIANCE`, parfois juste `AMBIANCE`.
_MOTIF_AMBIANCE = re.compile(r"^\d*_?AMBIANCE$", re.IGNORECASE)

# Préfixe « numéro de dossard » d'un dossier cavalier : `1001_DUPONT MARIE_ECLAIR`.
_MOTIF_DOSSARD = re.compile(r"^\d+_")

DOSSIER_AMBIANCE_DISTANT = "AMBIANCE"


def est_photo_eligible(
    chemin_relatif: PurePath | str,
    extensions: tuple[str, ...] = EXTENSIONS_PAR_DEFAUT,
) -> bool:
    """True si le fichier doit être envoyé : bonne extension et hors dossier `webp`."""
    chemin = PurePath(str(chemin_relatif))
    if chemin.suffix.lower() not in extensions:
        return False
    # `webp` peut apparaître à n'importe quelle profondeur.
    return not any(part.lower() == DOSSIER_EXCLU for part in chemin.parts[:-1])


def chemin_distant(chemin_relatif: PurePath | str) -> str | None:
    """Chemin distant (relatif à la racine de l'événement) pour une photo source.

    `chemin_relatif` est relatif au dossier événement surveillé.
    Retourne None si la photo est dans une épreuve mais sans dossier cavalier :
    on ne sait pas à qui l'attribuer, on préfère l'ignorer que la mal ranger.
    """
    parties = PurePath(str(chemin_relatif)).parts
    if not parties:
        return None

    # Photo posée directement à la racine du dossier surveillé.
    if len(parties) < 2:
        return parties[0]

    epreuve = parties[0]

    # Ambiance : tout à plat, quelle que soit la profondeur.
    # Les noms de fichiers Kadra portent un hash, ils restent uniques.
    if _MOTIF_AMBIANCE.match(epreuve):
        return f"{DOSSIER_AMBIANCE_DISTANT}/{parties[-1]}"

    # Une épreuve contient toujours des dossiers cavaliers ; sinon on ne sait pas ranger.
    if len(parties) < 3:
        return None

    cavalier = _MOTIF_DOSSARD.sub("", parties[1])
    if not cavalier:
        return None

    return str(PurePosixPath(cavalier, *parties[2:]))


def rendre_unique(rel: str, source: str, rels_utilises: dict[str, str]) -> str:
    """Évite que deux photos sources différentes visent le même chemin distant.

    Le suffixe (`_2`, `_3`…) est stable dans le temps : `rels_utilises` vient de la
    mémoire persistante, donc une photo garde le même nom distant d'un lancement à
    l'autre. Si le chemin est déjà réservé par CETTE source, on le rend tel quel.
    """
    occupant = rels_utilises.get(rel)
    if occupant is None or occupant == source:
        return rel

    chemin = PurePosixPath(rel)
    parent = chemin.parent
    tronc = chemin.stem
    extension = chemin.suffix

    index = 2
    while True:
        essai = str(parent / f"{tronc}_{index}{extension}")
        occupant = rels_utilises.get(essai)
        if occupant is None or occupant == source:
            return essai
        index += 1
