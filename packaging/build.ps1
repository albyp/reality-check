# Build the release: dist\RealityCheck\RealityCheck.exe and dist\RealityCheck-<version>-win64.zip
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$version = (& .venv\Scripts\python -c "import reality_check; print(reality_check.__version__)").Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read the version" }

& .venv\Scripts\python -m PyInstaller packaging\reality-check.spec --noconfirm --distpath dist --workpath build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
# The work folder holds an intermediate exe that cannot run on its own. Remove it so it is never launched by mistake.
Remove-Item -Recurse -Force build\work

# The GPL requires the license to travel with the binary.
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md, README.md, CHANGELOG.md dist\RealityCheck\

$zip = "dist\RealityCheck-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\RealityCheck -DestinationPath $zip -CompressionLevel Optimal
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Built: $root\dist\RealityCheck\RealityCheck.exe (keep the _internal folder next to it)"
Write-Host "Release zip: $root\$zip ($mb MB)"
