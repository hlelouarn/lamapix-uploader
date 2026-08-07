"""Emplacements de l'outil — tout vit À CÔTÉ de l'exécutable (portable, §8 du brief).

Rien n'est écrit dans %APPDATA% ni ailleurs : on copie le dossier sur un autre PC
et l'outil repart avec sa config, sa mémoire, son tampon et ses journaux.
"""

from __future__ import annotations

import sys
from pathlib import Path


def est_gele() -> bool:
    """True si on tourne depuis un .exe PyInstaller."""
    return getattr(sys, "frozen", False)


def racine_application() -> Path:
    """Dossier de référence : celui de l'exe, ou la racine du dépôt en mode source."""
    if est_gele():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def racine_donnees() -> Path:
    """Dossier qui porte config, mémoire, tampon et journaux."""
    dossier = racine_application() / "donnees"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def fichier_config() -> Path:
    return racine_donnees() / "config.json"


def fichier_secret() -> Path:
    """Mot de passe FTP chiffré (DPAPI) — jamais en clair, jamais committé."""
    return racine_donnees() / "motdepasse.bin"


def racine_tampon() -> Path:
    dossier = racine_donnees() / "tampon"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def racine_journaux() -> Path:
    dossier = racine_donnees() / "journaux"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def dossier_ressources() -> Path:
    """Ressources embarquées (icônes) — résout sys._MEIPASS une fois gelé."""
    if est_gele():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "assets"
    return racine_application() / "assets"
