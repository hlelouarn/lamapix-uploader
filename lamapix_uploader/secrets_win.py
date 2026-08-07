"""Chiffrement du mot de passe FTP par DPAPI Windows (§6 du brief).

DPAPI chiffre avec le compte Windows courant : le fichier produit est illisible
sur un autre PC ou par un autre utilisateur. C'est exactement ce qu'on veut —
le mot de passe Lamapix n'est jamais en clair sur disque, jamais dans le code,
jamais dans Git.

Hors Windows (tests sur une autre plateforme), on retombe sur un encodage
réversible clairement identifié comme NON sécurisé : l'outil reste testable,
sans laisser croire que le secret est protégé.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

_DESCRIPTION = "Lamapix Uploader — mot de passe FTP"
_PREFIXE_NON_CHIFFRE = b"CLAIR:"


class _BlobDonnees(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(donnees: bytes) -> _BlobDonnees:
    tampon = ctypes.create_string_buffer(donnees, len(donnees))
    return _BlobDonnees(len(donnees), ctypes.cast(tampon, ctypes.POINTER(ctypes.c_char)))


def _extraire(blob: _BlobDonnees) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _liberer(blob: _BlobDonnees) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]


def disponible() -> bool:
    """True si DPAPI est utilisable (donc si le secret sera réellement chiffré)."""
    return sys.platform == "win32"


def chiffrer(secret: str) -> bytes:
    if not disponible():
        return _PREFIXE_NON_CHIFFRE + base64.b64encode(secret.encode("utf-8"))

    entree = _blob(secret.encode("utf-8"))
    sortie = _BlobDonnees()
    succes = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(entree), _DESCRIPTION, None, None, None, 0, ctypes.byref(sortie)
    )
    if not succes:
        raise OSError("CryptProtectData a échoué (chiffrement du mot de passe)")
    try:
        return _extraire(sortie)
    finally:
        _liberer(sortie)


def dechiffrer(donnees: bytes) -> str:
    if donnees.startswith(_PREFIXE_NON_CHIFFRE):
        return base64.b64decode(donnees[len(_PREFIXE_NON_CHIFFRE):]).decode("utf-8")
    if not disponible():
        raise OSError("Secret DPAPI illisible hors Windows")

    entree = _blob(donnees)
    sortie = _BlobDonnees()
    succes = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(entree), None, None, None, None, 0, ctypes.byref(sortie)
    )
    if not succes:
        raise OSError("CryptUnprotectData a échoué (mot de passe illisible)")
    try:
        return _extraire(sortie).decode("utf-8")
    finally:
        _liberer(sortie)


def enregistrer(fichier: Path, secret: str) -> None:
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(chiffrer(secret))


def lire(fichier: Path) -> str | None:
    """Mot de passe mémorisé, ou None s'il n'y en a pas / s'il est illisible."""
    if not fichier.exists():
        return None
    try:
        return dechiffrer(fichier.read_bytes())
    except (OSError, ValueError):
        return None


def oublier(fichier: Path) -> None:
    try:
        fichier.unlink(missing_ok=True)
    except OSError:
        pass
