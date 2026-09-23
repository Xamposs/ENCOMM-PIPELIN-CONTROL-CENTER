# Build the ENCOMM Pipeline Control Center Windows release candidate.
#
# Usage (from the repository root, in the project environment):
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# Guarantees:
# - fails on the first error
# - cleans ONLY the known build outputs (build\, dist\)
# - never touches source files, user configuration or databases
# - produces dist\ENCOMM-PCC\ENCOMM-PCC.exe (one-folder build)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== ENCOMM PCC Windows build ==" -ForegroundColor Cyan

# 1. Environment checks ------------------------------------------------------
python --version
if ($LASTEXITCODE -ne 0) { throw "python is not available in this environment." }
python -m PyInstaller --version
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is missing. Install with: python -m pip install pyinstaller" }

# 2. Clean ONLY known build outputs -----------------------------------------
foreach ($dir in @("build", "dist")) {
    if (Test-Path $dir) {
        Write-Host "Removing $dir\"
        Remove-Item -Recurse -Force $dir
    }
}

# 3. Build -------------------------------------------------------------------
python -m PyInstaller --noconfirm --clean ENCOMM-PCC.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

# 4. Verify the artifact -----------------------------------------------------
$exe = "dist\ENCOMM-PCC\ENCOMM-PCC.exe"
if (-not (Test-Path $exe)) { throw "Expected executable not found: $exe" }

Write-Host "== BUILD OK: $exe ==" -ForegroundColor Green
Write-Host "Smoke-test it with:"
Write-Host '  $env:ENCOMM_PCC_DATA_DIR = "$env:TEMP\pcc-smoke"; & dist\ENCOMM-PCC\ENCOMM-PCC.exe --smoke-test; echo $LASTEXITCODE'
