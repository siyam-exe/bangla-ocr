$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $PipelineRoot "bangla-ocr.ps1"

Write-Host "Opening Bangla OCR..." -ForegroundColor Cyan
& $Runner launcher
exit $LASTEXITCODE
