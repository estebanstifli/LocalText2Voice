# Shared local Authenticode helpers. Credentials stay in the provider, never here.
Set-StrictMode -Version Latest

function Get-LtvSigningSettings {
    param([Parameter(Mandatory)][string]$ConfigPath)
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $allowed = @('thumbprint', 'store', 'timestamp_url', 'signtool_path')
    foreach ($property in $config.PSObject.Properties.Name) {
        if ($property -notin $allowed) { throw "Unsupported signing setting: $property. Never store PINs/passwords here." }
    }
    foreach ($required in @('thumbprint', 'store', 'timestamp_url')) {
        if ($required -notin $config.PSObject.Properties.Name) { throw "Missing signing setting: $required" }
    }
    $thumbprint = ([string]$config.thumbprint -replace '\s', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[A-F0-9]{40}$') { throw 'Expected a 40-character certificate SHA-1 thumbprint (identification only; signatures use SHA-256).' }
    if ($config.store -notin @('CurrentUser', 'LocalMachine')) { throw 'Store must be CurrentUser or LocalMachine.' }
    $timestamp = [uri]$config.timestamp_url
    if (-not $timestamp.IsAbsoluteUri -or $timestamp.Scheme -notin @('http', 'https') -or $timestamp.UserInfo) {
        throw 'Expected an HTTP(S) RFC 3161 timestamp URL without credentials.'
    }
    $tool = ''
    if ('signtool_path' -in $config.PSObject.Properties.Name) { $tool = [string]$config.signtool_path }
    if (-not $tool) {
        $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
        $candidates = @(Get-ChildItem -LiteralPath $kits -Directory -ErrorAction SilentlyContinue |
            Where-Object Name -Match '^\d+\.\d+\.\d+\.\d+$' |
            Sort-Object { [version]$_.Name } -Descending)
        foreach ($candidate in $candidates) {
            $path = Join-Path $candidate.FullName 'x64\signtool.exe'
            if (Test-Path -LiteralPath $path -PathType Leaf) { $tool = $path; break }
        }
    }
    if (-not $tool -or -not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw 'SignTool not found. Install Windows SDK Signing Tools or set signtool_path.'
    }
    [pscustomobject]@{ Thumbprint=$thumbprint; Store=[string]$config.store; TimestampUrl=$timestamp.AbsoluteUri; SignTool=(Get-Item -LiteralPath $tool).FullName }
}

function Assert-LtvSigningReady {
    param([Parameter(Mandatory)]$Settings)
    $toolSignature = Get-AuthenticodeSignature -LiteralPath $Settings.SignTool
    if ($toolSignature.Status -ne 'Valid' -or $toolSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw 'SignTool must have a valid Microsoft signature. No certificate trust changes will be made.'
    }
    $location = "Cert:\$($Settings.Store)\My\$($Settings.Thumbprint)"
    if (-not (Test-Path -LiteralPath $location)) {
        throw "Signing certificate is not available at $location. Connect SimplySign Desktop, or connect/register the Certum token. A .cer/.pem containing only CERTIFICATE cannot sign."
    }
    $certificate = Get-Item -LiteralPath $location
    if (-not $certificate.HasPrivateKey) {
        throw 'Certificate has no associated private key. Connect the provider; importing the public .cer alone is insufficient.'
    }
    $now = Get-Date
    if ($certificate.NotBefore -gt $now -or $certificate.NotAfter -le $now) { throw 'Signing certificate is not currently valid.' }
    $eku = @($certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.37' })
    if ($eku.Count -ne 1 -or '1.3.6.1.5.5.7.3.3' -notin @($eku[0].EnhancedKeyUsages | ForEach-Object Value)) {
        throw 'The selected certificate does not explicitly permit code signing.'
    }
    Write-Host "Signing provider ready: $($certificate.Subject); expires $($certificate.NotAfter.ToString('yyyy-MM-dd')). Provider PIN/session may still be required."
}

function Get-LtvSignArguments {
    param($Settings, [string]$Path)
    $arguments = @('sign', '/sha1', $Settings.Thumbprint, '/s', 'My')
    if ($Settings.Store -eq 'LocalMachine') { $arguments += '/sm' }
    $arguments + @('/fd', 'SHA256', '/tr', $Settings.TimestampUrl, '/td', 'SHA256', '/d', 'LocalText2Voice', $Path)
}

function Assert-LtvFileSignature {
    param($Settings, [string]$Path)
    & $Settings.SignTool verify /pa /all /tw $Path | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed (including timestamp): $Path" }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $Settings.Thumbprint -or -not $signature.TimeStamperCertificate) {
        throw "Invalid signature, wrong signer, or missing timestamp: $Path"
    }
}

function Invoke-LtvFileSigning {
    param($Settings, [string]$Path, [string]$Version, [switch]$Uninstaller)
    $file = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($Uninstaller) {
        if ($file.Name -notmatch '^uninst.*\.(exe|e32|e64)(\.tmp)?$') { throw 'Unexpected Inno uninstaller filename.' }
    }
    elseif ($file.Name -notin @('LocalText2Voice.exe', 'LocalText2VoiceMCP.exe', 'LocalText2VoiceEngineHost.exe', 'LocalText2Voice-Setup.exe')) {
        throw 'Only project-owned executables may be signed.'
    }
    if ($file.VersionInfo.ProductName.Trim() -ne 'LocalText2Voice' -or $file.VersionInfo.ProductVersion.Trim() -ne $Version) {
        throw "Unexpected product/version in $Path"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -ne 'NotSigned') {
        Assert-LtvFileSignature $Settings $file.FullName
        return
    }
    $arguments = Get-LtvSignArguments $Settings $file.FullName
    & $Settings.SignTool @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Signing failed: $Path. No unsigned fallback. Check the provider session/PIN and timestamp service." }
    Assert-LtvFileSignature $Settings $file.FullName
}
