[CmdletBinding()]
param(
    [switch]$Pull,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $PipelineRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Bangla OCR is not installed. Run setup.ps1 first."
}

if ($Pull) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git is required for -Pull. Update the source manually or install Git."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PipelineRoot ".git"))) {
        throw "This folder is not a Git repository. Update the source manually."
    }
    $Dirty = & git -C $PipelineRoot status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Git could not inspect the repository."
    }
    if ($Dirty) {
        throw "The repository has local changes. Commit or preserve them before pulling."
    }
    & git -C $PipelineRoot pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "The source update failed; no dependency update was attempted."
    }
}

& $Python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('surya') else 1)"
$WithSurya = $LASTEXITCODE -eq 0

$SetupArguments = @{Update = $true; SkipTests = $SkipTests}
if ($WithSurya) {
    $SetupArguments["WithSurya"] = $true
}
& (Join-Path $PipelineRoot "setup.ps1") @SetupArguments

Write-Host ""
Write-Host "Checking OCR engines..."
& (Join-Path $PipelineRoot "bangla-ocr.ps1") doctor
if ($LASTEXITCODE -ne 0) {
    throw "Update completed, but the OCR health check reported a failure."
}
