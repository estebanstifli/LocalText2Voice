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
    Write-Output "Checking TEST artifact: $Name"
    $File = Get-Item -LiteralPath (Join-Path $Directory $Name)
    # Inno Setup reserves fixed-width version strings padded with spaces.
    if ($File.VersionInfo.ProductName.Trim() -ne 'LocalText2Voice' -or
        $File.VersionInfo.ProductVersion.Trim() -ne $Version) {
        throw "Wrong product/version in $Name"
    }
    # Extract the public certificate without doing a trust lookup first.
    $Certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        [System.Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile($File.FullName)
    )
    if ($Certificate.Thumbprint -ne $Thumbprint) {
        throw "Unexpected or absent signing certificate in $Name"
    }
    # CurrentUser Root can show a consent dialog and hang unattended CI. The
    # machine store on this disposable, administrator-run hosted VM has no UI.
    $Store = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', 'LocalMachine')
    $Added = $false
    try {
        $Store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        if (-not @($Store.Certificates | Where-Object Thumbprint -eq $Thumbprint).Count) {
            Write-Output "Temporarily trusting pinned TEST certificate on the disposable runner: $Thumbprint"
            $Store.Add($Certificate)
            $Added = $true
        }
        # Validate integrity, EKU and validity using Windows, not just presence of a certificate.
        Write-Output "Verifying Authenticode integrity and timestamp: $Name"
        $Verified = Get-AuthenticodeSignature -LiteralPath $File.FullName
        if ($Verified.Status -ne 'Valid') { throw "Invalid signature in ${Name}: $($Verified.StatusMessage)" }
        if ($Verified.SignerCertificate.Thumbprint -ne $Thumbprint) { throw "Unexpected verified signer in $Name" }
        if (-not $Verified.TimeStamperCertificate) { throw "Missing signing timestamp in $Name" }
        Write-Output "Verified TEST signature: $Name ($Thumbprint)"
    } finally {
        if ($Added) {
            Write-Output 'Removing temporary TEST certificate trust.'
            $Store.Remove($Certificate)
        }
        $Store.Close()
        $Certificate.Dispose()
    }
}
