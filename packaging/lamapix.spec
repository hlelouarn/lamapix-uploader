# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller : un seul LamapixUploader.exe, sans console.

    cd C:\\dev\\lamapix-uploader
    .\\.venv\\Scripts\\python.exe -m PyInstaller packaging\\lamapix.spec --noconfirm

L'exe produit est PORTABLE : il crée son dossier `donnees\\` à côté de lui au
premier lancement (config, mémoire, tampon, journaux). Copier l'exe seul sur un
autre PC suffit — il redemandera juste le mot de passe Lamapix.
"""

from pathlib import Path

RACINE = Path(SPECPATH).parent
ICONE = RACINE / "assets" / "lamapix.ico"

a = Analysis(
    [str(RACINE / "app.py")],
    pathex=[str(RACINE)],
    binaries=[],
    datas=[(str(ICONE), "assets")],
    hiddenimports=["PySide6.QtNetwork"],
    hookspath=[],
    runtime_hooks=[],
    # Modules Qt lourds et inutiles ici : on garde l'exe raisonnable.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "tkinter",
        "PIL",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LamapixUploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # pas de fenêtre noire : c'est un utilitaire, pas un script
    disable_windowed_traceback=False,
    icon=str(ICONE),
)
