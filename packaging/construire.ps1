# Construit LamapixUploader.exe puis (option) publie la release GitHub.
#
#   .\packaging\construire.ps1                  → build seul
#   .\packaging\construire.ps1 -Publier         → build + gh release create
#
# La version publiée est celle de lamapix_uploader/__init__.py : c'est la seule
# source de vérité, l'updater compare le tag à cette valeur.

param(
    [switch]$Publier,
    [string]$Depot = "hlelouarn/lamapix-uploader"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = 1

$Racine = Split-Path -Parent $PSScriptRoot
Set-Location $Racine
$Python = Join-Path $Racine ".venv\Scripts\python.exe"

Write-Host "→ Tests" -ForegroundColor Cyan
& $Python -m pytest
if ($LASTEXITCODE -ne 0) { throw "Les tests ne passent pas : on ne construit pas." }

$Version = (& $Python -c "import lamapix_uploader; print(lamapix_uploader.VERSION)").Trim()
Write-Host "→ Version $Version" -ForegroundColor Cyan

Write-Host "→ Icône" -ForegroundColor Cyan
& $Python packaging\generer_icone.py

Write-Host "→ PyInstaller" -ForegroundColor Cyan
& $Python -m PyInstaller packaging\lamapix.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec de PyInstaller." }

$Exe = Join-Path $Racine "dist\LamapixUploader.exe"
if (-not (Test-Path $Exe)) { throw "Exe introuvable : $Exe" }
$Poids = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host "✓ $Exe ($Poids Mo)" -ForegroundColor Green

if (-not $Publier) {
    Write-Host "`nPour publier :  .\packaging\construire.ps1 -Publier" -ForegroundColor DarkGray
    exit 0
}

Write-Host "→ Publication de la release v$Version sur $Depot" -ForegroundColor Cyan
gh release create "v$Version" $Exe --repo $Depot --title "Lamapix Uploader $Version" --notes "Version $Version"
if ($LASTEXITCODE -ne 0) { throw "Échec de la publication." }
Write-Host "✓ Release v$Version publiée" -ForegroundColor Green
