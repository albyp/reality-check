# Build the distributable folder: dist\RealityCheck\RealityCheck.exe
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
& .venv\Scripts\python -m PyInstaller packaging\reality-check.spec --noconfirm --distpath dist --workpath build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
# The work folder holds an intermediate exe that cannot run on its own. Remove it so it is never launched by mistake.
Remove-Item -Recurse -Force build\work
Write-Host "Built: $root\dist\RealityCheck\RealityCheck.exe (keep the _internal folder next to it)"
