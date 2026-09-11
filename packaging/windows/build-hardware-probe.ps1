[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$PackagingRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $PackagingRoot "hardware-probe\BanglaOcrHardwareProbe.cs"
$CompilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Compiler) {
    throw "The Windows .NET Framework C# compiler is unavailable."
}

$ResolvedOutput = [IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResolvedOutput) | Out-Null
& $Compiler /nologo /target:exe /optimize+ "/out:$ResolvedOutput" `
    /reference:System.dll `
    /reference:System.Core.dll `
    /reference:System.Management.dll `
    /reference:System.Web.Extensions.dll `
    $Source
if ($LASTEXITCODE -ne 0) {
    throw "The Bangla OCR hardware probe did not compile."
}

Write-Host "Built $ResolvedOutput"
