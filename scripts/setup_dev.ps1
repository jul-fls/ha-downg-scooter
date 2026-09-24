$ErrorActionPreference = "Stop"

py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m pip install --no-deps homeassistant==2026.2.3

Write-Host "Development environment ready. Reload VS Code to use .venv."
