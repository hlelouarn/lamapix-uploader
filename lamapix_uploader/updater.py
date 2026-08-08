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
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import VERSION, paths

DELAI_RESEAU = 10
NOM_EXE = "LamapixUploader.exe"
ESSAIS_REMPLACEMENT = 30  # ~30 s d'attente que Windows relâche l'exe


@dataclass
class MiseAJour:
    version: str
    url_paquet: str
    nom_paquet: str
    notes: str

    @property
    def plus_recente(self) -> bool:
        return _numeros(self.version) > _numeros(VERSION)

    @property
    def est_archive(self) -> bool:
        """Depuis la 1.3.0 on publie un ZIP (build « onedir »). Les .exe des
        versions antérieures restent gérés : un poste peut être en retard."""
        return self.nom_paquet.lower().endswith(".zip")


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

    # Le ZIP prime : c'est le format courant. Le .exe reste accepté en repli.
    paquet: dict = {}
    for actif in donnees.get("assets") or []:
        nom = str(actif.get("name", "")).lower()
        if nom.endswith(".zip"):
            paquet = actif
            break
        if nom.endswith(".exe") and not paquet:
            paquet = actif
    if not paquet.get("browser_download_url"):
        return None

    return MiseAJour(
        version=version,
        url_paquet=paquet["browser_download_url"],
        nom_paquet=str(paquet.get("name", "")),
        notes=str(donnees.get("body") or ""),
    )


def _trouver_exe(racine: Path) -> Path | None:
    """L'exe dans une archive extraite, que le ZIP porte un dossier racine ou non."""
    direct = racine / NOM_EXE
    if direct.exists():
        return direct
    for trouve in racine.rglob(NOM_EXE):
        return trouve
    return None


def telecharger(mise_a_jour: MiseAJour) -> Path:
    """Récupère la nouvelle version. Retourne le DOSSIER applicatif extrait
    (ou l'exe seul, pour une release ancienne)."""
    travail = Path(tempfile.mkdtemp(prefix="lamapix_maj_"))
    archive = travail / (mise_a_jour.nom_paquet or NOM_EXE)
    requete = urllib.request.Request(
        mise_a_jour.url_paquet, headers={"User-Agent": "LamapixUploader"}
    )
    with urllib.request.urlopen(requete, timeout=120) as reponse:
        archive.write_bytes(reponse.read())

    if not mise_a_jour.est_archive:
        return archive

    extrait = travail / "extrait"
    with zipfile.ZipFile(archive) as zip_:
        zip_.extractall(extrait)
    exe = _trouver_exe(extrait)
    if exe is None:
        raise RuntimeError(f"{NOM_EXE} est introuvable dans l'archive téléchargée.")
    return exe.parent


def appliquer(source: Path) -> None:
    """Écrit et lance le .bat de remplacement, puis rend la main (l'appelant quitte).

    `source` est le dossier applicatif extrait (cas normal) ou un exe seul
    (release antérieure à la 1.3.0).
    """
    if not paths.est_gele():
        raise RuntimeError("La mise à jour automatique n'a de sens que sur l'exe.")

    exe_actuel = Path(sys.executable)
    dossier_app = exe_actuel.parent
    script = dossier_app / "_mise_a_jour.bat"

    if source.is_dir():
        # robocopy SANS /MIR : on écrase les fichiers de l'application et on ne
        # supprime rien. `donnees\` — config, et surtout la mémoire des envois —
        # doit survivre : la perdre ferait tout renvoyer sur Lamapix.
        # Codes de retour robocopy : < 8 = succès.
        remplacement = (
            f'robocopy "{source}" "{dossier_app}" /E /R:1 /W:1 >nul\r\n'
            "if %ERRORLEVEL% lss 8 goto lancer\r\n"
        )
    else:
        remplacement = (
            f'copy /Y "{source}" "{exe_actuel}" >nul 2>&1 && goto lancer\r\n'
        )
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
        + remplacement
        + "set /a essais+=1\r\n"
        f"if %essais% lss {ESSAIS_REMPLACEMENT} goto attendre\r\n"
        "echo Echec : impossible de remplacer l'application.\r\n"
        f"echo La nouvelle version reste disponible ici : {source}\r\n"
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
