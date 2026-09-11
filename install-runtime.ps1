[CmdletBinding()]
param(
    [ValidateSet("Auto", "All", "Cuda", "Vulkan", "Cpu")]
    [string]$Backend = "Auto",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsRoot = Join-Path $PipelineRoot "tools"
$Release = "b10107"
$Build = "10107"
$Commit = "c0bc8591e8815c63cb01dd3f051a8b0df02501c9"
$BaseUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$Release"

$Assets = @{
    Cpu = @(
        @{
            Name = "llama-b10107-bin-win-cpu-x64.zip"
            Sha256 = "52133a0a5a8f6035b1bdd2f89c3425ea8b742413d9bdb9a2dee30e3a1681b18c"
        }
    )
    Cuda = @(
        @{
            Name = "llama-b10107-bin-win-cuda-12.4-x64.zip"
            Sha256 = "1e43bbec9691cd0bc636603c366769148fa6265fd261c5f7c67050b450bbc237"
        },
        @{
            Name = "cudart-llama-bin-win-cuda-12.4-x64.zip"
            Sha256 = "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
        }
    )
    Vulkan = @(
        @{
            Name = "llama-b10107-bin-win-vulkan-x64.zip"
            Sha256 = "c5b3a5ee8319b1eccbb748a54390aa806bbf7d1aceeea452e4c57921d113e53e"
        }
    )
}

if ($Backend -eq "Auto") {
    $Backend = "All"
}
if ($Backend -eq "All") {
    foreach ($RuntimeBackend in @("Cpu", "Cuda", "Vulkan")) {
        & $PSCommandPath -Backend $RuntimeBackend -Force:$Force
    }
    return
}

$TargetName = switch ($Backend) {
    "Cuda" { "llama.cpp-cuda" }
    "Vulkan" { "llama.cpp-vulkan" }
    default { "llama.cpp-cpu" }
}
$Target = Join-Path $ToolsRoot $TargetName
$RuntimeLicense = Join-Path $PipelineRoot "packaging\licenses\llama.cpp-LICENSE.txt"
function Write-RuntimeMetadata([string]$RuntimePath, [string]$InstalledUtc) {
    Copy-Item -LiteralPath $RuntimeLicense -Destination (Join-Path $RuntimePath "LICENSE.llama.cpp.txt") -Force
    [ordered]@{
        release = $Release
        build = $Build
        commit = $Commit
        backend = $Backend.ToLowerInvariant()
        source = "ggml-org/llama.cpp"
        assets = @($Assets[$Backend] | ForEach-Object { [ordered]@{ name = $_.Name; sha256 = $_.Sha256 } })
        installed_utc = $InstalledUtc
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $RuntimePath "runtime-version.json") -Encoding UTF8
}
$ExistingServer = Join-Path $Target "llama-server.exe"
if ((Test-Path -LiteralPath $ExistingServer) -and -not $Force) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $VersionText = (& $ExistingServer --version 2>&1 | Out-String)
        $VersionExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($VersionExitCode -eq 0 -and $VersionText -match "version:\s+$Build\b") {
        $InstalledUtc = [DateTime]::UtcNow.ToString("o")
        $ExistingManifest = Join-Path $Target "runtime-version.json"
        if (Test-Path -LiteralPath $ExistingManifest) {
            try {
                $ExistingValue = Get-Content -LiteralPath $ExistingManifest -Raw | ConvertFrom-Json
                if ($ExistingValue.installed_utc) {
                    $InstalledUtc = [string]$ExistingValue.installed_utc
                }
            }
            catch {}
        }
        Write-RuntimeMetadata $Target $InstalledUtc
        Write-Host "llama.cpp $Release ($Backend) is already installed."
        return
    }
}

New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
$StageRoot = Join-Path $ToolsRoot (".runtime-install-" + [Guid]::NewGuid().ToString("N"))
$Downloads = Join-Path $StageRoot "downloads"
$Extracted = Join-Path $StageRoot "extracted"
$Prepared = Join-Path $StageRoot $TargetName
New-Item -ItemType Directory -Path $Downloads, $Extracted, $Prepared -Force | Out-Null

try {
    foreach ($Asset in $Assets[$Backend]) {
        $Archive = Join-Path $Downloads $Asset.Name
        Write-Host "Downloading $($Asset.Name)..."
        Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$($Asset.Name)" -OutFile $Archive
        $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne $Asset.Sha256) {
            throw "Checksum mismatch for $($Asset.Name). Expected $($Asset.Sha256), received $Actual."
        }
        $Component = Join-Path $Extracted ([IO.Path]::GetFileNameWithoutExtension($Asset.Name))
        Expand-Archive -LiteralPath $Archive -DestinationPath $Component -Force
        $Server = Get-ChildItem -LiteralPath $Component -Recurse -Filter "llama-server.exe" -File |
            Select-Object -First 1
        $ContentRoot = $(if ($Server) { $Server.Directory.FullName } else { $Component })
        Copy-Item -Path (Join-Path $ContentRoot "*") -Destination $Prepared -Recurse -Force
    }

    $PreparedServer = Join-Path $Prepared "llama-server.exe"
    if (-not (Test-Path -LiteralPath $PreparedServer)) {
        throw "The verified archives did not contain llama-server.exe."
    }

    $ToolsFull = [IO.Path]::GetFullPath($ToolsRoot).TrimEnd('\') + '\'
    $TargetFull = [IO.Path]::GetFullPath($Target)
    if (-not $TargetFull.StartsWith($ToolsFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace a runtime outside the project tools directory."
    }
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    Move-Item -LiteralPath $Prepared -Destination $Target
    Write-RuntimeMetadata $Target ([DateTime]::UtcNow.ToString("o"))

    Write-Host "Installed llama.cpp $Release ($Backend) in $Target"
}
finally {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
}
