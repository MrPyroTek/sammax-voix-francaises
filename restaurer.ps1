$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Ferme le jeu avant de continuer."
& python ".\apply_vf.py" @args restore
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
