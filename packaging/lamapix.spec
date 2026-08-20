# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller : un dossier LamapixUploader\\ contenant l'exe, sans console.

    cd C:\\dev\\lamapix-uploader
    .\\.venv\\Scripts\\python.exe -m PyInstaller packaging\\lamapix.spec --noconfirm

Build « onedir » et non « onefile », délibérément : un exe onefile se décompresse
dans %TEMP% à chaque lancement puis exécute le résultat — comportement d'un
dropper, que les antivirus suppriment régulièrement sans qu'aucune signature
précise soit en cause. Le dossier démarre aussi plus vite.

L'ensemble reste PORTABLE : le dossier crée son sous-dossier `donnees\\` au
premier lancement (config, mémoire, tampon, journaux). Copier le dossier sur un
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
    # certifi est importé paresseusement (magasin de certificats de secours
    # pour la mise à jour) : l'analyse statique ne le verrait pas.
    hiddenimports=["PySide6.QtNetwork", "certifi"],
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
    [],
    exclude_binaries=True,   # les DLL vivent à côté, pas dans l'exe
    name="LamapixUploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # pas de fenêtre noire : c'est un utilitaire, pas un script
    disable_windowed_traceback=False,
    icon=str(ICONE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LamapixUploader",
)
