[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Directory,
    [Parameter(Mandatory)][string]$Version,
    [ValidateSet('application', 'installer')][string]$Kind = 'application'
)

$ErrorActionPreference = 'Stop'
# Public certificate fingerprint supplied by the maintainer. Never trust arbitrary
# certificates returned by a remote service. This script is for disposable CI only.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Test-certificate verification is restricted to GitHub-hosted runners.'
}
$Thumbprint = '15C8F90D4432F333EF31C8C5C1DB02E4A91F1FE6'
$Names = if ($Kind -eq 'application') {
    @('LocalText2Voice.exe', 'LocalText2VoiceEngineHost.exe', 'LocalText2VoiceMCP.exe')
} else { @('LocalText2Voice-Setup.exe') }
$Files = @(Get-ChildItem -LiteralPath $Directory -File -Recurse)
if ($Files.Count -ne $Names.Count) { throw 'Unexpected signed artifact contents.' }
foreach ($Name in $Names) {
    $File = Get-Item -LiteralPath (Join-Path $Directory $Name)
    if ($File.VersionInfo.ProductName -ne 'LocalText2Voice' -or
        $File.VersionInfo.ProductVersion.Trim() -ne $Version) {
        throw "Wrong product/version in $Name"
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if (-not $Signature.SignerCertificate -or $Signature.SignerCertificate.Thumbprint -ne $Thumbprint) {
        throw "Unexpected or absent signing certificate in $Name"
    }
    $Certificate = $Signature.SignerCertificate
    $Store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', 'CurrentUser')
    $Added = $false
    try {
        $Store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        if (-not @($Store.Certificates | Where-Object Thumbprint -eq $Thumbprint).Count) {
            $Store.Add($Certificate)
            $Added = $true
        }
        # Validate integrity, EKU and validity using Windows, not just presence of a certificate.
        $Verified = Get-AuthenticodeSignature -LiteralPath $File.FullName
        if ($Verified.Status -ne 'Valid') { throw "Invalid signature in ${Name}: $($Verified.StatusMessage)" }
        if (-not $Verified.TimeStamperCertificate) { throw "Missing signing timestamp in $Name" }
        Write-Output "Verified TEST signature: $Name ($Thumbprint)"
    } finally {
        if ($Added) { $Store.Remove($Certificate) }
        $Store.Close()
    }
}
