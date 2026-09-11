[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [Parameter(Mandatory = $true)]
    [string]$IconPath
)

$ErrorActionPreference = "Stop"
$PackagingRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $PackagingRoot "launcher\BanglaOcrLauncher.cs"
$CompilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Compiler) {
    throw "The Windows .NET Framework C# compiler is unavailable."
}

$ResolvedOutput = [IO.Path]::GetFullPath($OutputPath)
$ResolvedIcon = [IO.Path]::GetFullPath($IconPath)
if (-not (Test-Path -LiteralPath $ResolvedIcon -PathType Leaf)) {
    throw "The Bangla OCR icon is unavailable: $ResolvedIcon"
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResolvedOutput) | Out-Null
& $Compiler /nologo /target:winexe /optimize+ "/out:$ResolvedOutput" `
    "/win32icon:$ResolvedIcon" `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll `
    /reference:System.Web.Extensions.dll `
    $Source
if ($LASTEXITCODE -ne 0) {
    throw "The Bangla OCR launcher did not compile."
}

Write-Host "Built $ResolvedOutput"
