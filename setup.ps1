[CmdletBinding()]
param(
    [switch]$WithSurya,
    [ValidateSet("Auto", "Cuda", "Cpu", "None")]
    [string]$Runtime = "Auto",
    [switch]$Update,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $PipelineRoot
$VenvPath = Join-Path $PipelineRoot ".venv"
$CachePath = Join-Path $PipelineRoot ".cache\pip"
$RuntimeTemp = Join-Path $ProjectRoot "runtime\temp"
New-Item -ItemType Directory -Path $RuntimeTemp -Force | Out-Null
$env:TEMP = $RuntimeTemp
$env:TMP = $RuntimeTemp
$env:TMPDIR = $RuntimeTemp

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "The Windows Python launcher ('py') is required."
}

if (-not (Test-Path -LiteralPath $VenvPath)) {
    py -3.12 -m venv $VenvPath
}

$Python = Join-Path $VenvPath "Scripts\python.exe"
$env:PIP_CACHE_DIR = $CachePath
& $Python -m pip install --upgrade pip

if ($WithSurya) {
    # Install Surya before applying the tested Pillow compatibility override.
    & $Python -c "import importlib.metadata as m; raise SystemExit(0 if m.version('surya-ocr') == '0.22.1' else 1)" 2>$null
    $SuryaReady = $LASTEXITCODE -eq 0
    if (-not $SuryaReady) {
        & $Python -m pip install "surya-ocr==0.22.1"
        if ($LASTEXITCODE -ne 0) {
            throw "Surya dependencies could not be installed."
        }
    }
    else {
        Write-Host "Surya OCR 0.22.1 is already installed."
    }
}

$InstallAction = @("-m", "pip", "install")
if ($Update) {
    $InstallAction += "--upgrade"
}
$InstallAction += @("-e", "$PipelineRoot[easyocr,dev]")
& $Python @InstallAction
if ($LASTEXITCODE -ne 0) {
    throw "Bangla OCR dependencies could not be installed."
}

if ($WithSurya) {
    if ($Runtime -ne "None") {
        & (Join-Path $PipelineRoot "install-runtime.ps1") -Backend $Runtime
        if ($LASTEXITCODE -ne 0) {
            throw "The Surya llama.cpp runtime could not be installed."
        }
    }
}

$DependencyCheck = @((Join-Path $PipelineRoot "scripts\check_dependencies.py"))
if ($WithSurya) {
    $DependencyCheck += "--allow-surya-pillow-override"
}
& $Python @DependencyCheck
if ($LASTEXITCODE -ne 0) {
    throw "Installed Python packages have incompatible dependencies."
}

if (-not $SkipTests) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "Installation finished, but the self-test failed."
    }
}

Write-Host ""
Write-Host $(if ($Update) { "Update complete." } else { "Installation complete." })
Write-Host "Run: $PipelineRoot\bangla-ocr.ps1 doctor"
