"""Compléter une chaîne de certificats incomplète, sans affaiblir la vérification.

Le serveur FTP de Lamapix n'envoie que SON certificat, sans l'intermédiaire qui
le relie à une racine connue (son site web, lui, envoie la chaîne entière).
Tant que Windows avait l'ancien intermédiaire en cache, ça passait par chance ;
le 18/09/2026 le certificat a été renouvelé sous un intermédiaire tout neuf
(« YE1 » de Let's Encrypt) que personne n'a en cache : plus aucun PC ne peut
vérifier, et le seul recours était de désactiver toute vérification.

On fait donc ce que fait un navigateur : le certificat indique lui-même où
télécharger celui de son émetteur (extension AIA, « caIssuers »). On remonte la
chaîne de proche en proche et on fournit ces intermédiaires au vérificateur.

Ce que ça ne change PAS, et c'est tout l'enjeu : un intermédiaire téléchargé
n'est jamais une ancre de confiance. La chaîne doit toujours aboutir à une
racine déjà présente dans le magasin (Windows ou certifi). Deux verrous :

- un certificat téléchargé autosigné est écarté — une « racine » ne se
  télécharge pas, elle se possède déjà ;
- le contexte qui reçoit ces intermédiaires perd VERIFY_X509_PARTIAL_CHAIN
  (voir ftp.py) : sinon OpenSSL accepterait n'importe quel certificat du
  magasin comme point d'arrivée, y compris ceux qu'on vient de télécharger en
  HTTP clair.
"""

from __future__ import annotations

import os
import re
import ssl
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

PROFONDEUR_MAX = 4            # feuille → intermédiaire → … : jamais plus en pratique
TAILLE_MAX = 64 * 1024        # un certificat pèse 1 à 2 ko
DELAI_TELECHARGEMENT = 10

_verrou = threading.Lock()
_memoire: dict[str, list[bytes]] = {}


def decoder(der: bytes) -> dict:
    """Champs lisibles d'un certificat DER (sujet, émetteur, caIssuers…).

    `ssl` ne décode publiquement que le certificat d'un pair DÉJÀ vérifié —
    précisément ce qu'on n'a pas. Son décodeur interne fait l'affaire ; un test
    garantit qu'il existe toujours avant chaque construction de l'exe.
    """
    pem = ssl.DER_cert_to_PEM_cert(der)
    descripteur, chemin = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(descripteur, "w", encoding="ascii") as flux:
            flux.write(pem)
        return ssl._ssl._test_decode_cert(chemin)  # type: ignore[attr-defined]
    finally:
        try:
            os.unlink(chemin)
        except OSError:
            pass


def est_autosigne(infos: dict) -> bool:
    return bool(infos.get("subject")) and infos.get("subject") == infos.get("issuer")


def _telecharger(url: str) -> bytes | None:
    """Un certificat depuis son URL AIA, en DER. None si quoi que ce soit cloche."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    requete = urllib.request.Request(url, headers={"User-Agent": "LamapixUploader"})
    try:
        with urllib.request.urlopen(requete, timeout=DELAI_TELECHARGEMENT) as reponse:
            brut = reponse.read(TAILLE_MAX + 1)
    except (OSError, ValueError):
        return None
    if not brut or len(brut) > TAILLE_MAX:
        return None
    if brut.lstrip().startswith(b"-----BEGIN"):
        try:
            return ssl.PEM_cert_to_DER_cert(brut.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None
    return brut


def remonter(
    feuille: bytes, telecharger: Callable[[str], bytes | None] = _telecharger
) -> list[bytes]:
    """Les intermédiaires manquants, de l'émetteur de la feuille vers la racine.

    S'arrête à la première racine (autosignée) rencontrée, SANS la retenir.
    """
    trouves: list[bytes] = []
    courant = feuille
    for _ in range(PROFONDEUR_MAX):
        try:
            infos = decoder(courant)
        except Exception:
            break
        if est_autosigne(infos):
            break

        suivant: bytes | None = None
        infos_suivant: dict = {}
        for url in infos.get("caIssuers", ()):
            candidat = telecharger(url)
            if candidat is None:
                continue
            try:
                infos_candidat = decoder(candidat)
            except Exception:
                continue
            # Ce qu'on a reçu doit être l'émetteur annoncé, pas autre chose.
            if infos_candidat.get("subject") != infos.get("issuer"):
                continue
            suivant, infos_suivant = candidat, infos_candidat
            break

        if suivant is None or est_autosigne(infos_suivant):
            break  # racine atteinte (jamais ajoutée) ou piste perdue
        trouves.append(suivant)
        courant = suivant
    return trouves


# ------------------------------------------------------------------ mémoire

def _cle(hote: str, port: int) -> str:
    return f"{hote.lower()}:{port}"


def _fichier(hote: str, port: int) -> Path:
    nom = re.sub(r"[^A-Za-z0-9.-]", "_", hote.lower())
    return paths.racine_donnees() / "certificats" / f"{nom}_{port}.pem"


def _sans_racines(ders: list[bytes]) -> list[bytes]:
    """Défense en profondeur : même relu du disque, rien d'autosigné ne passe."""
    gardes = []
    for der in ders:
        try:
            if not est_autosigne(decoder(der)):
                gardes.append(der)
        except Exception:
            continue
    return gardes


def connus(hote: str, port: int) -> list[bytes]:
    """Intermédiaires déjà récupérés pour ce serveur (mémoire, sinon disque).

    Le cache disque évite un téléchargement à chaque reconnexion — et sur une
    liaison Starlink, on se reconnecte souvent.
    """
    cle = _cle(hote, port)
    with _verrou:
        if cle in _memoire:
            return list(_memoire[cle])
    ders: list[bytes] = []
    try:
        texte = _fichier(hote, port).read_text(encoding="ascii")
        for bloc in re.findall(
            r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", texte, re.S
        ):
            ders.append(ssl.PEM_cert_to_DER_cert(bloc))
    except (OSError, ValueError):
        ders = []
    ders = _sans_racines(ders)
    with _verrou:
        _memoire[cle] = list(ders)
    return ders


def memoriser(hote: str, port: int, ders: list[bytes]) -> None:
    with _verrou:
        _memoire[_cle(hote, port)] = list(ders)
    try:
        fichier = _fichier(hote, port)
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(
            "".join(ssl.DER_cert_to_PEM_cert(der) for der in ders), encoding="ascii"
        )
    except OSError:
        pass  # le cache disque est un confort


def oublier(hote: str, port: int) -> None:
    """Tests, et cas d'un cache devenu faux."""
    with _verrou:
        _memoire.pop(_cle(hote, port), None)


def completer(
    hote: str,
    port: int,
    obtenir_feuille: Callable[[], bytes],
    telecharger: Callable[[str], bytes | None] = _telecharger,
) -> bool:
    """Tente de récupérer les intermédiaires manquants. True s'il y a du NOUVEAU
    — sinon réessayer la connexion ne servirait à rien."""
    try:
        feuille = obtenir_feuille()
    except Exception:
        return False
    nouveaux = remonter(feuille, telecharger)
    if not nouveaux or nouveaux == connus(hote, port):
        return False
    memoriser(hote, port, nouveaux)
    return True
