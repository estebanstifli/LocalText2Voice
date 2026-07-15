[CmdletBinding()]
param(
    [switch]$SkipAppBuild,
    [string]$DistRoot = "dist"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $RepositoryRoot "app\__init__.py"
$InstallerScript = Join-Path $RepositoryRoot "installer\LocalText2Voice.iss"
$Compiler = Join-Path $RepositoryRoot ".util_instalador_y_firmas\InnoSetup\ISCC.exe"
$OutputDirectory = Join-Path $RepositoryRoot ".util_instalador_y_firmas\output"
$Installer = Join-Path $OutputDirectory "LocalText2Voice-Setup.exe"
$Checksum = "$Installer.sha256"
$ResolvedDistRoot = if ([System.IO.Path]::IsPathRooted($DistRoot)) {
    [System.IO.Path]::GetFullPath($DistRoot)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $DistRoot))
}
$InstallerSource = Join-Path $ResolvedDistRoot "LocalText2Voice"

if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw "Inno Setup compiler not found: $Compiler"
}
if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
    throw "Installer script not found: $InstallerScript"
}

$VersionSource = Get-Content -LiteralPath $VersionFile -Raw
$VersionMatch = [regex]::Match(
    $VersionSource,
    '__version__\s*=\s*"(?<version>[^"\r\n]+)"'
)
if (-not $VersionMatch.Success) {
    throw "Could not read __version__ from $VersionFile"
}
$Version = $VersionMatch.Groups["version"].Value

Push-Location $RepositoryRoot
try {
    if (-not $SkipAppBuild) {
        $PreviousDistRoot = $env:LTV_DIST_ROOT
        $PreviousPreserveRuntime = $env:LTV_PRESERVE_RUNTIME
        try {
            $env:LTV_DIST_ROOT = $ResolvedDistRoot
            $env:LTV_PRESERVE_RUNTIME = "0"
            & cmd.exe /c build_windows.bat
            if ($LASTEXITCODE -ne 0) {
                throw "The Windows application build failed with exit code $LASTEXITCODE."
            }
        }
        finally {
            $env:LTV_DIST_ROOT = $PreviousDistRoot
            $env:LTV_PRESERVE_RUNTIME = $PreviousPreserveRuntime
        }
    }

    if (-not (Test-Path -LiteralPath $InstallerSource -PathType Container)) {
        throw "Portable application folder not found: $InstallerSource"
    }

    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    & $Compiler /Qp "/DMyAppVersion=$Version" "/DSourceDir=$InstallerSource" $InstallerScript
    if ($LASTEXITCODE -ne 0) {
        throw "The Inno Setup build failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "The expected installer was not generated: $Installer"
    }
    $Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  LocalText2Voice-Setup.exe" | Set-Content -LiteralPath $Checksum -Encoding Ascii

    Write-Host "Release assets ready for v$Version"
    Get-Item -LiteralPath $Installer, $Checksum | Select-Object Name, Length, FullName
}
finally {
    Pop-Location
}
