$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python 3 est introuvable. Installe-le depuis https://www.python.org/downloads/ et coche 'Add python.exe to PATH'."
    exit 1
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg est introuvable. Installe-le et ajoute-le au PATH : https://ffmpeg.org/download.html"
    exit 1
}
$runtime = & dotnet --list-runtimes 2>$null | Select-String "Microsoft.NETCore.App 8"
if (-not $runtime) {
    Write-Host ".NET 8 est introuvable. Installe-le : https://dotnet.microsoft.com/download/dotnet/8.0"
    exit 1
}

Write-Host "Ferme le jeu avant de continuer."
& python ".\apply_vf.py" @args all
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "Termine. Dans le jeu, choisis la langue Francais pour que les sous-titres suivent les voix."
