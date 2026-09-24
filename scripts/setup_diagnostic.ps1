param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $RepoRoot ".diagnostic-venv"

& $Python -m venv $Venv
& (Join-Path $Venv "Scripts\python.exe") -m pip install `
    bleak==2.0.0 `
    bleak-retry-connector==4.4.3 `
    cryptography==46.0.5

Write-Host "Diagnostic environment ready: $Venv"
