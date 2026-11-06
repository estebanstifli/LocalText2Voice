[CmdletBinding()]
param(
    [switch]$SkipAppBuild,
    [string]$DistRoot = "dist",
    [switch]$Sign,
    [string]$SigningConfig = ".sign/signing.json",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $RepositoryRoot "app\__init__.py"
$InstallerScript = Join-Path $RepositoryRoot "installer\LocalText2Voice.iss"
$Compiler = Join-Path $RepositoryRoot ".util_instalador_y_firmas\InnoSetup\ISCC.exe"
if (-not $OutputDirectory) {
    $relative = if ($Sign) { ".util_instalador_y_firmas\output-signed\$(Get-Date -Format 'yyyyMMdd-HHmmss')" } else { '.util_instalador_y_firmas\output' }
    $OutputDirectory = Join-Path $RepositoryRoot $relative
}
elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $RepositoryRoot $OutputDirectory
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if ($Sign -and (Test-Path -LiteralPath $OutputDirectory)) { throw 'Use a new output directory for signed builds; old release artifacts must not be overwritten.' }
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
if ($Sign) {
    if (-not [IO.Path]::IsPathRooted($SigningConfig)) { $SigningConfig = Join-Path $RepositoryRoot $SigningConfig }
    $SigningConfig = (Get-Item -LiteralPath $SigningConfig).FullName
    . "$PSScriptRoot/windows_signing.ps1"
    $SigningSettings = Get-LtvSigningSettings $SigningConfig
    # Fail before rebuilding or changing binaries when the provider is not ready.
    Assert-LtvSigningReady $SigningSettings
}

Push-Location $RepositoryRoot
try {
    if (-not $SkipAppBuild) {
        $PreviousDistRoot = $env:LTV_DIST_ROOT
        $PreviousPreserveRuntime = $env:LTV_PRESERVE_RUNTIME
        $PreviousPreserveConfig = $env:LTV_PRESERVE_CONFIG
        try {
            $env:LTV_DIST_ROOT = $ResolvedDistRoot
            $env:LTV_PRESERVE_RUNTIME = "0"
            $env:LTV_PRESERVE_CONFIG = "0"
            & cmd.exe /c build_windows.bat
            if ($LASTEXITCODE -ne 0) {
                throw "The Windows application build failed with exit code $LASTEXITCODE."
            }
        }
        finally {
            $env:LTV_DIST_ROOT = $PreviousDistRoot
            $env:LTV_PRESERVE_RUNTIME = $PreviousPreserveRuntime
            $env:LTV_PRESERVE_CONFIG = $PreviousPreserveConfig
        }
    }

    if (-not (Test-Path -LiteralPath $InstallerSource -PathType Container)) {
        throw "Portable application folder not found: $InstallerSource"
    }

    & "$RepositoryRoot\.venv\Scripts\python.exe" "$RepositoryRoot\tools\check_windows_qt_bundle.py" $InstallerSource
    if ($LASTEXITCODE -ne 0) {
        throw "The portable application failed the Qt DLL packaging check."
    }

    & "$RepositoryRoot\.venv\Scripts\python.exe" "$RepositoryRoot\tools\check_windows_engine_host_bundle.py" $InstallerSource
    if ($LASTEXITCODE -ne 0) {
        throw "The portable application failed the EngineHost resource check."
    }

    $CompilerArguments = @('/Qp', "/DMyAppVersion=$Version", "/DSourceDir=$InstallerSource", "/DOutputDir=$OutputDirectory")
    if ($Sign) {
        $OwnedFiles = @('LocalText2Voice.exe', 'LocalText2VoiceMCP.exe', 'LocalText2VoiceEngineHost.exe')
        foreach ($Name in $OwnedFiles) {
            Invoke-LtvFileSigning $SigningSettings (Join-Path $InstallerSource $Name) $Version
        }
        $UninstallerDirectory = Join-Path $OutputDirectory 'signed-uninstaller'
        New-Item -ItemType Directory -Path $UninstallerDirectory -Force | Out-Null
        $PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $Callback = Join-Path $PSScriptRoot 'sign_windows_file.ps1'
        $SigningLog = Join-Path $OutputDirectory 'signing.log'
        $Command = '$q{0}$q -NoProfile -ExecutionPolicy Bypass -File $q{1}$q -ConfigPath $q{2}$q -Version {3} -UninstallerDirectory $q{4}$q -LogPath $q{5}$q -Path $f' -f $PowerShell, $Callback, $SigningConfig, $Version, $UninstallerDirectory, $SigningLog
        $CompilerArguments += @('/DSignedBuild=1', "/DSignedUninstallerDir=$UninstallerDirectory", "/Sltv=$Command")
    }
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    & $Compiler @CompilerArguments $InstallerScript
    if ($LASTEXITCODE -ne 0) {
        throw "The Inno Setup build failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "The expected installer was not generated: $Installer"
    }
    if ($Sign) {
        # Setup was normalized and signed by the Inno callback. Never patch it now.
        Assert-LtvFileSignature $SigningSettings $Installer
        $Uninstallers = @(Get-ChildItem -LiteralPath $UninstallerDirectory -File | Where-Object Name -Match '^uninst.*\.(exe|e32|e64)$')
        if (-not $Uninstallers.Count) { throw 'Signed Inno uninstaller is missing.' }
        $SignedFiles = @($Installer) + @($OwnedFiles | ForEach-Object { Join-Path $InstallerSource $_ }) + @($Uninstallers.FullName)
        $Evidence = foreach ($File in $SignedFiles) {
            Assert-LtvFileSignature $SigningSettings $File
            $Signature = Get-AuthenticodeSignature -LiteralPath $File
            [pscustomobject]@{ file=$File; sha256=(Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant(); signer=$Signature.SignerCertificate.Thumbprint; timestamp_signer=$Signature.TimeStamperCertificate.Thumbprint; status=$Signature.Status.ToString() }
        }
        $Evidence | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'signatures.json') -Encoding UTF8
    }
    else {
        & "$RepositoryRoot\.venv\Scripts\python.exe" "$RepositoryRoot\tools\normalize_installer_metadata.py" $Installer --version $Version
        if ($LASTEXITCODE -ne 0) {
            throw "Installer metadata normalization failed. Do not sign or publish this build."
        }
    }
    $Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  LocalText2Voice-Setup.exe" | Set-Content -LiteralPath $Checksum -Encoding Ascii

    Write-Host "Release assets ready for v$Version"
    Get-Item -LiteralPath $Installer, $Checksum | Select-Object Name, Length, FullName
}
finally {
    Pop-Location
}
