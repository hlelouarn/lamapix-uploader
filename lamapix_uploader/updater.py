"""Mise à jour depuis les *releases* GitHub publiques.

Rien de sensible : dépôt public, pas de token. On télécharge l'exe de la release,
on le pose à côté, et un petit .bat fait l'échange une fois l'outil fermé (on ne
peut pas remplacer un exe en cours d'exécution sous Windows).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import VERSION, paths

DELAI_RESEAU = 10
NOM_EXE = "LamapixUploader.exe"
ESSAIS_REMPLACEMENT = 30  # ~30 s d'attente que Windows relâche l'exe


@dataclass
class MiseAJour:
    version: str
    url_exe: str
    notes: str

    @property
    def plus_recente(self) -> bool:
        return _numeros(self.version) > _numeros(VERSION)


def _numeros(version: str) -> tuple[int, ...]:
    nombres = [int(n) for n in re.findall(r"\d+", version)]
    while len(nombres) < 3:
        nombres.append(0)
    return tuple(nombres[:3])


def chercher(depot: str) -> MiseAJour | None:
    """Dernière release publiée, ou None (hors ligne, pas de release, dépôt privé…).

    Ne lève jamais : une mise à jour indisponible ne doit pas empêcher de bosser.
    """
    url = f"https://api.github.com/repos/{depot}/releases/latest"
    requete = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "LamapixUploader"}
    )
    try:
        with urllib.request.urlopen(requete, timeout=DELAI_RESEAU) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    version = str(donnees.get("tag_name") or "").lstrip("vV")
    if not version:
        return None
    url_exe = ""
    for actif in donnees.get("assets") or []:
        if str(actif.get("name", "")).lower().endswith(".exe"):
            url_exe = actif.get("browser_download_url", "")
            break
    if not url_exe:
        return None
    return MiseAJour(version=version, url_exe=url_exe, notes=str(donnees.get("body") or ""))


def telecharger(mise_a_jour: MiseAJour) -> Path:
    """Télécharge le nouvel exe dans un dossier temporaire."""
    destination = Path(tempfile.mkdtemp(prefix="lamapix_maj_")) / NOM_EXE
    requete = urllib.request.Request(
        mise_a_jour.url_exe, headers={"User-Agent": "LamapixUploader"}
    )
    with urllib.request.urlopen(requete, timeout=60) as reponse:
        destination.write_bytes(reponse.read())
    return destination


def appliquer(nouvel_exe: Path) -> None:
    """Écrit et lance le .bat de remplacement, puis rend la main (l'appelant quitte)."""
    if not paths.est_gele():
        raise RuntimeError("La mise à jour automatique n'a de sens que sur l'exe.")

    exe_actuel = Path(sys.executable)
    script = exe_actuel.parent / "_mise_a_jour.bat"
    # Windows garde l'exe verrouillé quelques instants après la fermeture : on
    # réessaie, mais un nombre borné de fois. Une boucle infinie laisserait un
    # cmd.exe fantôme tourner pour toujours si la copie ne passait jamais
    # (droits, antivirus, disque plein).
    script.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "echo Mise a jour de Lamapix Uploader en cours...\r\n"
        "set /a essais=0\r\n"
        ":attendre\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f'copy /Y "{nouvel_exe}" "{exe_actuel}" >nul 2>&1 && goto lancer\r\n'
        "set /a essais+=1\r\n"
        f"if %essais% lss {ESSAIS_REMPLACEMENT} goto attendre\r\n"
        "echo Echec : impossible de remplacer l'executable.\r\n"
        f'echo Le nouveau fichier reste disponible ici : {nouvel_exe}\r\n'
        "pause\r\n"
        f'start "" "{exe_actuel}"\r\n'
        'del "%~f0"\r\n'
        "exit /b 1\r\n"
        ":lancer\r\n"
        f'start "" "{exe_actuel}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def raccourci_demarrage(activer: bool) -> None:
    """(Dés)active le lancement automatique avec la session Windows.

    On passe par la clé Run de l'utilisateur courant : pas de droits admin,
    pas de tâche planifiée à nettoyer.
    """
    if sys.platform != "win32":
        return
    import winreg

    cle = r"Software\Microsoft\Windows\CurrentVersion\Run"
    nom = "LamapixUploader"
    cible = (
        f'"{sys.executable}"'
        if paths.est_gele()
        else f'"{sys.executable}" "{Path(__file__).resolve().parent.parent / "app.py"}"'
    )
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cle, 0, winreg.KEY_SET_VALUE) as poignee:
        if activer:
            winreg.SetValueEx(poignee, nom, 0, winreg.REG_SZ, cible)
        else:
            try:
                winreg.DeleteValue(poignee, nom)
            except FileNotFoundError:
                pass


def demarrage_actif() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as poignee:
            winreg.QueryValueEx(poignee, "LamapixUploader")
            return True
    except OSError:
        return False


__all__ = [
    "MiseAJour",
    "chercher",
    "telecharger",
    "appliquer",
    "raccourci_demarrage",
    "demarrage_actif",
]
