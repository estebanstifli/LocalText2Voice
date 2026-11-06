[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [Parameter(Mandatory)][string]$Version,
    [string]$Path,
    [string]$UninstallerDirectory,
    [string]$LogPath,
    [switch]$CheckOnly,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$previousModulePath = $env:PSModulePath
# Inno can inherit PowerShell 7 module paths when it launches Windows PowerShell.
# This standalone callback only needs the modules shipped with its own runtime.
$env:PSModulePath = "$PSHOME\Modules"
$transcribing = $false
try {
    if ($LogPath) {
        Start-Transcript -LiteralPath $LogPath -Append | Out-Null
        $transcribing = $true
    }
    . "$PSScriptRoot/windows_signing.ps1"
    $settings = Get-LtvSigningSettings $ConfigPath
    if ($CheckOnly) {
        Assert-LtvSigningReady $settings
        return
    }
    if (-not $Path) { throw 'Path is required unless CheckOnly is used.' }
    if ($VerifyOnly) {
        Assert-LtvFileSignature $settings (Get-Item -LiteralPath $Path).FullName
        return
    }
    Assert-LtvSigningReady $settings
    $file = Get-Item -LiteralPath $Path
    $uninstaller = $false
    if ($UninstallerDirectory -and $file.DirectoryName -eq [IO.Path]::GetFullPath($UninstallerDirectory)) {
        $uninstaller = $true
    }
    if ($file.Name -eq 'LocalText2Voice-Setup.exe' -and (Get-AuthenticodeSignature -LiteralPath $file.FullName).Status -eq 'NotSigned') {
        # Inno calls us after generating Setup. Normalize BEFORE signing, never after.
        $root = Split-Path -Parent $PSScriptRoot
        & "$root/.venv/Scripts/python.exe" "$PSScriptRoot/normalize_installer_metadata.py" $file.FullName --version $Version | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Installer metadata normalization failed before signing.' }
    }
    Invoke-LtvFileSigning $settings $file.FullName $Version -Uninstaller:$uninstaller
    if ($uninstaller) {
        # Inno deletes on-the-fly signed uninstallers. Retain the verified bytes.
        $evidence = Join-Path $UninstallerDirectory 'uninst-verified.exe'
        [IO.File]::Copy($file.FullName, $evidence, $false)
    }
}
catch {
    Write-Host ($_ | Out-String)
    throw
}
finally {
    if ($transcribing) { Stop-Transcript | Out-Null }
    $env:PSModulePath = $previousModulePath
}
