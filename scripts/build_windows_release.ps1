[CmdletBinding()]
param(
    [ValidateSet("Universal", "Cpu", "Cuda", "Vulkan")]
    [string]$Runtime = "Universal",
    [switch]$Resume,
    [switch]$SkipArchive,
    [switch]$SkipInstaller,
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"
$PipelineRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BuildRoot = Join-Path $PipelineRoot "build\windows"
$DistRoot = Join-Path $PipelineRoot "dist"
$CacheRoot = Join-Path $PipelineRoot ".cache\windows-release"
$StageParent = Join-Path $BuildRoot $Runtime.ToLowerInvariant()
$StageRoot = Join-Path $StageParent "Bangla OCR"
$PythonRoot = Join-Path $StageRoot "runtime\python"
$SitePackages = Join-Path $PythonRoot "Lib\site-packages"
$DependencyLayerMarker = Join-Path $StageRoot "runtime\dependency-layer.txt"
$HardwareProbePath = Join-Path $StageParent "Bangla OCR Hardware Probe.exe"
$AppIconPath = Join-Path $PipelineRoot "packaging\windows\assets\app-icon.ico"
$DependencyLayerId = "2026-09-10-pypdf-6.16.1"
$PythonVersion = "3.12.10"
$PythonArchiveName = "python-$PythonVersion-embed-amd64.zip"
$PythonArchive = Join-Path $CacheRoot $PythonArchiveName
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchiveName"
$PythonSha256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
$VersionMatch = Select-String -LiteralPath (Join-Path $PipelineRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $VersionMatch) {
    throw "Cannot read the application version from pyproject.toml."
}
$AppVersion = $VersionMatch.Matches[0].Groups[1].Value

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    $FullParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $FullPath.StartsWith($FullParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside $FullParent"
    }
}

function Invoke-PythonPip([string[]]$Arguments) {
    & py -3.12 -m pip @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "pip failed while preparing the portable runtime."
    }
}

function Remove-PackagingWaste([string]$Root) {
    Write-Host "Removing dependency tests, bytecode caches, and link-only files..."
    Get-ChildItem -LiteralPath $Root -Directory -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("test", "tests", "__pycache__") } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

    Get-ChildItem -LiteralPath $Root -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".pyc", ".pyo", ".a", ".lib") } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

foreach ($Path in @($BuildRoot, $DistRoot, $CacheRoot, $StageParent)) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}
Assert-ChildPath $StageRoot $BuildRoot
$RuntimeLabel = switch ($Runtime) {
    "Cuda" { "nvidia" }
    "Vulkan" { "vulkan" }
    "Universal" { "universal" }
    default { "cpu" }
}

function Format-DirectorySize([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return "not included"
    }
    $Bytes = (Get-ChildItem -LiteralPath $Path -File -Recurse -ErrorAction Stop |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $Bytes) {
        $Bytes = 0
    }
    if ($Bytes -ge 1GB) {
        return ("about {0:N1} GB" -f ($Bytes / 1GB))
    }
    return ("about {0:N0} MB" -f ($Bytes / 1MB))
}
$CurrentArtifactNames = @(
    "Bangla-OCR-$AppVersion-windows-x64-$RuntimeLabel-portable.zip",
    "Bangla-OCR-$AppVersion-windows-x64-$RuntimeLabel-setup.exe",
    "Bangla-OCR-$AppVersion-windows-x64-$RuntimeLabel-SHA256SUMS.txt"
)
Get-ChildItem -LiteralPath $DistRoot -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -like "Bangla-OCR-*-windows-x64-$RuntimeLabel-portable.zip" -or
        $_.Name -like "Bangla-OCR-*-windows-x64-$RuntimeLabel-setup.exe" -or
        $_.Name -like "Bangla-OCR-*-windows-x64-$RuntimeLabel-SHA256SUMS.txt"
    } |
    Where-Object { $_.Name -notin $CurrentArtifactNames } |
    ForEach-Object {
        Assert-ChildPath $_.FullName $DistRoot
        Remove-Item -LiteralPath $_.FullName -Force
    }
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:TEMP = Join-Path $BuildRoot "temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

if ($Resume) {
    $RequiredPreparedPaths = @(
        (Join-Path $PythonRoot "python.exe"),
        (Join-Path $SitePackages "surya"),
        (Join-Path $SitePackages "easyocr"),
        (Join-Path $SitePackages "PIL"),
        (Join-Path $SitePackages "Pillow-12.3.0.dist-info")
    )
    $MissingPreparedPath = $RequiredPreparedPaths | Where-Object { -not (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if ($MissingPreparedPath) {
        throw "Cannot resume because the prepared runtime is incomplete: $MissingPreparedPath"
    }
    Write-Host "Reusing the prepared Python and OCR dependency layers."
}
else {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $PythonRoot, $SitePackages | Out-Null

    if (-not (Test-Path -LiteralPath $PythonArchive)) {
        Write-Host "Downloading Python $PythonVersion embedded runtime..."
        Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $PythonArchive
    }
    $PythonHash = (Get-FileHash -LiteralPath $PythonArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($PythonHash -ne $PythonSha256) {
        throw "Python archive checksum mismatch."
    }
    Expand-Archive -LiteralPath $PythonArchive -DestinationPath $PythonRoot -Force
    @(
        "python312.zip",
        ".",
        "Lib\site-packages",
        "import site"
    ) | Set-Content -LiteralPath (Join-Path $PythonRoot "python312._pth") -Encoding ASCII

    Write-Host "Installing Surya and its runtime dependencies..."
    Invoke-PythonPip @(
        "install", "--only-binary=:all:", "--target", $SitePackages,
        "surya-ocr==0.22.1"
    )

    Get-ChildItem -LiteralPath $SitePackages -Filter "Pillow-*.dist-info" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $SitePackages "PIL") -Recurse -Force -ErrorAction SilentlyContinue

}
$CurrentDependencyLayer = $(
    if (Test-Path -LiteralPath $DependencyLayerMarker) {
        (Get-Content -LiteralPath $DependencyLayerMarker -Raw).Trim()
    }
    else {
        ""
    }
)
if ($CurrentDependencyLayer -ne $DependencyLayerId) {
    Write-Host "Installing Bangla OCR and the explicit EasyOCR alternative..."
    Remove-Item -LiteralPath (Join-Path $SitePackages "pypdf") -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $SitePackages -Filter "pypdf-*.dist-info" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    Invoke-PythonPip @(
        "install", "--only-binary=:all:", "--upgrade", "--target", $SitePackages,
        "Flask==3.1.3",
        "numpy==2.5.1",
        "opencv-python-headless==4.11.0.86",
        "Pillow==12.3.0",
        "pypdf==6.16.1",
        "pypdfium2==5.12.1",
        "rapidfuzz==3.14.5",
        "waitress==3.0.2",
        "easyocr==1.7.2"
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DependencyLayerMarker) | Out-Null
    Set-Content -LiteralPath $DependencyLayerMarker -Value $DependencyLayerId -Encoding ASCII
}
else {
    Write-Host "Reusing the pinned application dependency layer."
}
Invoke-PythonPip @(
    "install", "--no-deps", "--upgrade",
    "--target", $SitePackages, $PipelineRoot
)
Remove-PackagingWaste $SitePackages
$LicenseArchive = Join-Path $StageRoot "runtime\third-party-licenses.zip"
if (-not $Resume -or -not (Test-Path -LiteralPath $LicenseArchive)) {
    & py -3.12 (Join-Path $PipelineRoot "scripts\archive_package_licenses.py") $SitePackages $LicenseArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Package license files could not be archived."
    }
}
else {
    Write-Host "Reusing the prepared third-party license archive."
}

Remove-Item -LiteralPath (Join-Path $StageRoot "config") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $PipelineRoot "config") -Destination (Join-Path $StageRoot "config") -Recurse
foreach ($Name in @("LICENSE", "README.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md")) {
    Copy-Item -LiteralPath (Join-Path $PipelineRoot $Name) -Destination $StageRoot
}
foreach ($LegacyPath in @("runtime\logs", "runtime\surya", "runtime\temp")) {
    Remove-Item -LiteralPath (Join-Path $StageRoot $LegacyPath) -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($Path in @(
    "documents\imports",
    "models",
    "workspace\logs",
    "workspace\surya",
    "workspace\temp",
    "tools"
)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $StageRoot $Path) | Out-Null
}

$RuntimeFolders = switch ($Runtime) {
    "Cuda" { @("llama.cpp-cuda") }
    "Vulkan" { @("llama.cpp-vulkan") }
    "Universal" { @("llama.cpp-cpu", "llama.cpp-cuda", "llama.cpp-vulkan") }
    default { @("llama.cpp-cpu") }
}
$RuntimeBackends = @{
    "llama.cpp-cpu" = "Cpu"
    "llama.cpp-cuda" = "Cuda"
    "llama.cpp-vulkan" = "Vulkan"
}
foreach ($RuntimeFolder in $RuntimeFolders) {
    $LocalRuntime = Join-Path $PipelineRoot "tools\$RuntimeFolder"
    if (-not (Test-Path -LiteralPath (Join-Path $LocalRuntime "llama-server.exe"))) {
        & (Join-Path $PipelineRoot "install-runtime.ps1") -Backend $RuntimeBackends[$RuntimeFolder]
    }
    $PackagedRuntime = Join-Path $StageRoot "tools\$RuntimeFolder"
    Remove-Item -LiteralPath $PackagedRuntime -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $LocalRuntime -Destination $PackagedRuntime -Recurse -Force
}

& (Join-Path $PipelineRoot "packaging\windows\build-launcher.ps1") `
    -OutputPath (Join-Path $StageRoot "Bangla OCR.exe") `
    -IconPath $AppIconPath

$Manifest = [ordered]@{
    application = "Bangla OCR"
    version = $AppVersion
    architecture = "windows-x64"
    runtime = $Runtime.ToLowerInvariant()
    bundled_runtimes = @($RuntimeFolders | ForEach-Object { ($RuntimeBackends[$_]).ToLowerInvariant() })
    package = $RuntimeLabel
    python = $PythonVersion
    surya = "0.22.1"
    built_utc = [DateTime]::UtcNow.ToString("o")
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StageRoot "release-manifest.json") -Encoding UTF8

$MaximumRelativePathLength = 180
$LongPackagedPath = Get-ChildItem -LiteralPath $StageRoot -File -Recurse -ErrorAction Stop |
    Where-Object { $_.FullName.Substring($StageRoot.Length + 1).Length -gt $MaximumRelativePathLength } |
    Select-Object -First 1
if ($LongPackagedPath) {
    $RelativePath = $LongPackagedPath.FullName.Substring($StageRoot.Length + 1)
    throw "Packaged path exceeds $MaximumRelativePathLength characters: $RelativePath"
}

$PreviousHome = $env:BANGLA_OCR_HOME
$PackageDoctorPath = Join-Path $StageParent "package-doctor.json"
try {
    $env:BANGLA_OCR_HOME = $StageRoot
    & (Join-Path $PythonRoot "python.exe") (Join-Path $PipelineRoot "scripts\check_dependencies.py") --allow-surya-pillow-override
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged dependency check failed."
    }
    & (Join-Path $PythonRoot "python.exe") -m bangla_ocr doctor | Out-File -LiteralPath $PackageDoctorPath -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged application health check failed."
    }
    try {
        $PackageDoctor = Get-Content -LiteralPath $PackageDoctorPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "The packaged application health check returned invalid JSON."
    }
    if (-not $PackageDoctor.engines.surya.available) {
        throw "The packaged application health check did not find a usable Surya runtime."
    }
}
finally {
    $env:BANGLA_OCR_HOME = $PreviousHome
}
Remove-PackagingWaste $SitePackages

$UnexpectedMutableFile = @(
    foreach ($MutableRoot in @("documents", "models", "workspace")) {
        $Root = Join-Path $StageRoot $MutableRoot
        if (Test-Path -LiteralPath $Root) {
            Get-ChildItem -LiteralPath $Root -File -Recurse -ErrorAction Stop
        }
    }
) | Select-Object -First 1
if ($UnexpectedMutableFile) {
    $RelativePath = $UnexpectedMutableFile.FullName.Substring($StageRoot.Length + 1)
    throw "The release contains mutable application data: $RelativePath"
}

if (-not $SkipArchive) {
    $Archive = Join-Path $DistRoot "Bangla-OCR-$AppVersion-windows-x64-$RuntimeLabel-portable.zip"
    & py -3.12 (Join-Path $PipelineRoot "scripts\create_portable_archive.py") $StageRoot $Archive
    if ($LASTEXITCODE -ne 0) {
        throw "The portable archive could not be created."
    }
}

if (-not $SkipInstaller) {
    & (Join-Path $PipelineRoot "packaging\windows\build-hardware-probe.ps1") -OutputPath $HardwareProbePath
    $CompilerCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
    $CompilerPath = $(if ($CompilerCommand) { $CompilerCommand.Source } else { $null })
    if (-not $CompilerPath) {
        $CompilerPath = @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if (-not $CompilerPath) {
        throw "Inno Setup 6 is required to build Setup.exe. Use -SkipInstaller for a portable-only build."
    }
    $CpuRuntimeSize = Format-DirectorySize (Join-Path $StageRoot "tools\llama.cpp-cpu")
    $CudaRuntimeSize = Format-DirectorySize (Join-Path $StageRoot "tools\llama.cpp-cuda")
    $VulkanRuntimeSize = Format-DirectorySize (Join-Path $StageRoot "tools\llama.cpp-vulkan")
    $CompilerArguments = @(
        "/Qp"
        "/DStageDir=$StageRoot"
        "/DOutputDir=$DistRoot"
        "/DAppVersion=$AppVersion"
        "/DRuntimeName=$RuntimeLabel"
        "/DHardwareProbePath=$HardwareProbePath"
        "/DAppIconPath=$AppIconPath"
        "/DCpuRuntimeSize=$CpuRuntimeSize"
        "/DCudaRuntimeSize=$CudaRuntimeSize"
        "/DVulkanRuntimeSize=$VulkanRuntimeSize"
        (Join-Path $PipelineRoot "packaging\windows\BanglaOCR.iss")
    )
    & $CompilerPath @CompilerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup did not build the installer."
    }
}

$ChecksumArtifacts = $CurrentArtifactNames[0..1] |
    ForEach-Object { Join-Path $DistRoot $_ } |
    Where-Object { Test-Path -LiteralPath $_ }
if ($ChecksumArtifacts.Count -gt 0) {
    $ChecksumLines = $ChecksumArtifacts | ForEach-Object {
        $Hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $([IO.Path]::GetFileName($_))"
    }
    $ChecksumLines | Set-Content -LiteralPath (Join-Path $DistRoot $CurrentArtifactNames[2]) -Encoding ASCII
}

Write-Host "Windows $Runtime release prepared in $DistRoot"
if ($KeepStage) {
    Write-Host "Verified staging directory: $StageRoot"
}
else {
    Assert-ChildPath $StageRoot $BuildRoot
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
