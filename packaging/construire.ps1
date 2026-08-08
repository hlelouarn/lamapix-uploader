# Construit LamapixUploader.exe puis (option) publie la release GitHub.
#
#   .\packaging\construire.ps1                  -> build seul
#   .\packaging\construire.ps1 -Publier         -> build + gh release create
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

Write-Host "-> Tests" -ForegroundColor Cyan
& $Python -m pytest
if ($LASTEXITCODE -ne 0) { throw "Les tests ne passent pas : on ne construit pas." }

$Version = (& $Python -c "import lamapix_uploader; print(lamapix_uploader.VERSION)").Trim()
Write-Host "-> Version $Version" -ForegroundColor Cyan

Write-Host "-> Icone" -ForegroundColor Cyan
& $Python packaging\generer_icone.py

Write-Host "-> PyInstaller" -ForegroundColor Cyan
& $Python -m PyInstaller packaging\lamapix.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec de PyInstaller." }

$Dossier = Join-Path $Racine "dist\LamapixUploader"
$Exe = Join-Path $Dossier "LamapixUploader.exe"
if (-not (Test-Path $Exe)) { throw "Exe introuvable : $Exe" }

# On livre un ZIP contenant le dossier LamapixUploader\ : c'est ce que l'updater
# télécharge, et ce qui se déploie à la main sur un poste.
Write-Host "-> Archive" -ForegroundColor Cyan
$Zip = Join-Path $Racine "dist\LamapixUploader-v$Version.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path $Dossier -DestinationPath $Zip -CompressionLevel Optimal

$Poids = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host "[OK] $Zip ($Poids Mo)" -ForegroundColor Green

if (-not $Publier) {
    Write-Host "`nPour publier :  .\packaging\construire.ps1 -Publier" -ForegroundColor DarkGray
    exit 0
}

Write-Host "-> Publication de la release v$Version sur $Depot" -ForegroundColor Cyan
gh release create "v$Version" $Zip --repo $Depot --title "Lamapix Uploader $Version" --notes "Version $Version"
if ($LASTEXITCODE -ne 0) { throw "Échec de la publication." }
Write-Host "[OK] Release v$Version publiée" -ForegroundColor Green
