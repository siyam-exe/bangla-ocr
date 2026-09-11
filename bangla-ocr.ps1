$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $PipelineRoot
$Python = Join-Path $PipelineRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Pipeline environment is missing. Run setup.ps1 first."
}

$env:PYTHONUTF8 = "1"
$RuntimeRoot = Join-Path $ProjectRoot "runtime"
$RuntimeTemp = Join-Path $RuntimeRoot "temp"
$SuryaRuntime = Join-Path $RuntimeRoot "surya"
$RuntimeLogs = Join-Path $RuntimeRoot "logs"
foreach ($RuntimePath in @($RuntimeRoot, $RuntimeTemp, $SuryaRuntime, $RuntimeLogs)) {
    New-Item -ItemType Directory -Path $RuntimePath -Force | Out-Null
}
$env:TEMP = $RuntimeTemp
$env:TMP = $RuntimeTemp
$env:TMPDIR = $RuntimeTemp
if (-not $env:SURYA_RUNTIME_DIR) { $env:SURYA_RUNTIME_DIR = $SuryaRuntime }
$env:SURYA_LOG_MAX_MIB = "8"
$env:SURYA_LOG_BACKUPS = "3"
$env:HF_HOME = Join-Path $PipelineRoot "models\huggingface"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:EASYOCR_MODULE_PATH = Join-Path $PipelineRoot "models\easyocr"
$env:MODEL_CACHE_DIR = Join-Path $PipelineRoot "models\surya"
$LegacySuryaRuntime = Join-Path $env:USERPROFILE ".cache\datalab\surya"
if (($env:SURYA_RUNTIME_DIR -eq $SuryaRuntime) -and -not (Test-Path -LiteralPath $LegacySuryaRuntime)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $LegacySuryaRuntime) -Force | Out-Null
    New-Item -ItemType Junction -Path $LegacySuryaRuntime -Target $SuryaRuntime | Out-Null
}
elseif ($env:SURYA_RUNTIME_DIR -eq $SuryaRuntime) {
    $LegacySuryaItem = Get-Item -LiteralPath $LegacySuryaRuntime -Force
    if (-not ($LegacySuryaItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Write-Warning "Surya still has a normal runtime directory on C:. Run storage migration before the next OCR job."
        $env:SURYA_RUNTIME_DIR = $LegacySuryaRuntime
    }
}
$ExistingLlamaPids = @(
    Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Id }
)
$PipelineExitCode = 1
try {
    & $Python -m bangla_ocr @args
    $PipelineExitCode = $LASTEXITCODE
}
finally {
    $NewLlamaProcesses = @(
        Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $ExistingLlamaPids }
    )
    foreach ($LlamaProcess in $NewLlamaProcesses) {
        Stop-Process -Id $LlamaProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($NewLlamaProcesses.Count -gt 0) {
        $SuryaSentinel = Join-Path $env:SURYA_RUNTIME_DIR "llamacpp_server.json"
        Remove-Item -LiteralPath $SuryaSentinel -Force -ErrorAction SilentlyContinue
    }
}
exit $PipelineExitCode
