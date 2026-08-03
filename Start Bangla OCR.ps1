$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $PipelineRoot "bangla-ocr.ps1"

Write-Host "Starting the Bangla OCR application..." -ForegroundColor Cyan
Write-Host "Keep this window open while the application is running." -ForegroundColor DarkGray
& $Runner app
exit $LASTEXITCODE
