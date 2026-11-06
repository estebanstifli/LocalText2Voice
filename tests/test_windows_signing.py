"""Signing orchestration tests; never use the maintainer's private key or trust store."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows signing tooling')


def run_ps(body):
    helper = str(ROOT / 'tools/windows_signing.ps1').replace("'", "''")
    return subprocess.run([str(PS), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
                           "$ErrorActionPreference='Stop'; . '" + helper + "'; " + body],
                          capture_output=True, text=True, timeout=30)


def test_signing_scripts_parse_in_windows_powershell():
    for script in ('windows_signing.ps1', 'sign_windows_file.ps1', 'build_windows_installer.ps1'):
        path = str(ROOT / 'tools' / script).replace("'", "''")
        result = run_ps(f"$t=$null; $e=$null; [System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e) | Out-Null; if ($e.Count) {{ throw ($e | Out-String) }}")
        assert result.returncode == 0, result.stderr


def test_callback_uses_own_modules_despite_inherited_module_path(tmp_path):
    callback = tmp_path / 'sign_windows_file.ps1'
    shutil.copyfile(ROOT / 'tools/sign_windows_file.ps1', callback)
    (tmp_path / 'windows_signing.ps1').write_text('''
function Get-LtvSigningSettings { param($ConfigPath); return @{} }
function Assert-LtvSigningReady {
    param($Settings)
    $null = Get-AuthenticodeSignature -LiteralPath "$PSHOME/powershell.exe"
    if ((Get-Command Get-AuthenticodeSignature).Module.ModuleBase -ne $PSHOME) {
        throw 'Foreign security module loaded'
    }
    Write-Host 'BUILTIN_SECURITY_OK'
}
''')
    shadow = tmp_path / 'foreign modules' / 'Microsoft.PowerShell.Security'
    shadow.mkdir(parents=True)
    (shadow / 'Microsoft.PowerShell.Security.psm1').write_text('''
function Get-AuthenticodeSignature { throw 'Foreign module must not run' }
Export-ModuleMember -Function Get-AuthenticodeSignature
''')
    log = tmp_path / 'signing.log'
    environment = os.environ | {'PSModulePath': str(shadow.parent)}
    result = subprocess.run(
        [str(PS), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(callback),
         '-ConfigPath', 'unused.json', '-Version', '3.0.0', '-CheckOnly', '-LogPath', str(log)],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert 'BUILTIN_SECURITY_OK' in result.stdout
    assert 'BUILTIN_SECURITY_OK' in log.read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('store', ['CurrentUser', 'LocalMachine'])
def test_config_and_sign_arguments(tmp_path, store):
    tool = tmp_path / 'SDK with spaces' / 'signtool.exe'
    tool.parent.mkdir()
    tool.touch()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(thumbprint='ab ' * 20, store=store,
                                     timestamp_url='http://time.certum.pl', signtool_path=str(tool))))
    result = run_ps(fr"$s=Get-LtvSigningSettings '{config}'; @(Get-LtvSignArguments $s 'C:\candidate with spaces\LocalText2Voice.exe') | ConvertTo-Json")
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert args[:3] == ['sign', '/sha1', 'AB' * 20]
    assert ('/sm' in args) == (store == 'LocalMachine')
    assert args[args.index('/fd') + 1] == args[args.index('/td') + 1] == 'SHA256'
    assert args.index('/tr') < args.index('/td')
    assert '/a' not in args and '/p' not in args
    assert args[-1] == r'C:\candidate with spaces\LocalText2Voice.exe'


def test_callback_retains_uninstaller_only_after_successful_signing(tmp_path):
    callback = tmp_path / 'sign_windows_file.ps1'
    shutil.copyfile(ROOT / 'tools/sign_windows_file.ps1', callback)
    helper = tmp_path / 'windows_signing.ps1'
    stub = '''
function Get-LtvSigningSettings { param($ConfigPath); return @{} }
function Assert-LtvSigningReady { param($Settings) }
function Invoke-LtvFileSigning { param($Settings, $Path, $Version, [switch]$Uninstaller)
    if (-not $Uninstaller) { throw 'Expected uninstaller' }
    SIGN_RESULT
}
'''
    directory = tmp_path / 'uninstaller evidence'
    directory.mkdir()
    source = directory / 'uninst.e32.tmp'
    source.write_bytes(b'uninstaller bytes, not a real signature')
    evidence = directory / 'uninst-verified.exe'
    args = [str(PS), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(callback),
            '-ConfigPath', 'unused.json', '-Version', '3.0.0', '-Path', str(source),
            '-UninstallerDirectory', str(directory)]
    helper.write_text(stub.replace('SIGN_RESULT', "throw 'Signing failed'"))
    failed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert failed.returncode != 0
    assert not evidence.exists()
    helper.write_text(stub.replace('SIGN_RESULT', 'return'))
    passed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert passed.returncode == 0, passed.stderr
    assert evidence.read_bytes() == source.read_bytes()


@pytest.mark.parametrize('extra,expected', [
    ({'password': 'not-a-real-password'}, 'Unsupported signing setting'),
    ({'thumbprint': 'invalid'}, '40-character'),
    ({'store': 'Root'}, 'Store must be'),
    ({'timestamp_url': 'file:///tmp'}, 'HTTP(S)'),
    ({'timestamp_url': 'https://user:password@example.org'}, 'without credentials'),
])
def test_bad_config_is_rejected(tmp_path, extra, expected):
    config = tmp_path / 'bad.json'
    config.write_text(json.dumps(dict(thumbprint='AB' * 20, store='CurrentUser',
                                     timestamp_url='http://time.certum.pl') | extra))
    result = run_ps(f"Get-LtvSigningSettings '{config}'")
    assert result.returncode != 0
    assert expected in result.stderr


def fake_settings():
    return "$s=[pscustomobject]@{Thumbprint=('AB'*20);Store='CurrentUser';SignTool='FakeTool';TimestampUrl='http://time.certum.pl'}; "


@pytest.mark.parametrize('problem,message', [
    ('missing', 'not available'), ('public_only', 'no associated private key'),
    ('expired', 'not currently valid'), ('wrong_eku', 'code signing'),
])
def test_preflight_fails_without_usable_private_key(problem, message):
    present = '$false' if problem == 'missing' else '$true'
    key = '$false' if problem == 'public_only' else '$true'
    expiry = '(Get-Date).AddDays(-1)' if problem == 'expired' else '(Get-Date).AddDays(1)'
    eku = '1.3.6.1.5.5.7.3.1' if problem == 'wrong_eku' else '1.3.6.1.5.5.7.3.3'
    result = run_ps(fake_settings() + f'''
function Get-AuthenticodeSignature {{ [pscustomobject]@{{Status='Valid';SignerCertificate=[pscustomobject]@{{Subject='O=Microsoft Corporation'}}}} }}
function Test-Path {{ return {present} }}
function Get-Item {{ [pscustomobject]@{{HasPrivateKey={key};NotBefore=(Get-Date).AddDays(-2);NotAfter={expiry};Extensions=@([pscustomobject]@{{Oid=[pscustomobject]@{{Value='2.5.29.37'}};EnhancedKeyUsages=@([pscustomobject]@{{Value='{eku}'}})}})}} }}
Assert-LtvSigningReady $s
''')
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize('problem', ['wrong_signer', 'missing_timestamp', 'untrusted', 'tool_warning'])
def test_verification_fails_closed(problem):
    thumb = 'CD' * 20 if problem == 'wrong_signer' else 'AB' * 20
    timestamp = '$null' if problem == 'missing_timestamp' else "[pscustomobject]@{Thumbprint='TSA'}"
    status = 'UnknownError' if problem == 'untrusted' else 'Valid'
    code = 2 if problem == 'tool_warning' else 0
    result = run_ps(fake_settings() + f'''
function FakeTool {{ $global:LASTEXITCODE={code} }}
function Get-AuthenticodeSignature {{ [pscustomobject]@{{Status='{status}';SignerCertificate=[pscustomobject]@{{Thumbprint='{thumb}'}};TimeStamperCertificate={timestamp}}} }}
Assert-LtvFileSignature $s 'unused.exe'
''')
    assert result.returncode != 0


@pytest.mark.parametrize('filename,version', [('ffmpeg.exe', '3.0.0'), ('LocalText2Voice.exe', '2.2.0')])
def test_signing_rejects_unowned_or_wrong_version(filename, version):
    result = run_ps(fake_settings() + f'''
function Get-Item {{ [pscustomobject]@{{Name='{filename}';FullName='unused.exe';VersionInfo=[pscustomobject]@{{ProductName='LocalText2Voice';ProductVersion='{version}'}}}} }}
Invoke-LtvFileSigning $s 'unused.exe' '3.0.0'
''')
    assert result.returncode != 0
    assert 'project-owned' in result.stderr or 'Unexpected product/version' in result.stderr


def test_no_legacy_test_workflow_or_trust_changes():
    assert not (ROOT / '.github/workflows/signpath-test.yml').exists()
    assert not (ROOT / 'tools/ci/verify_test_signatures.ps1').exists()
    assert '.sign/' in (ROOT / '.gitignore').read_text()
    script = (ROOT / 'tools/windows_signing.ps1').read_text()
    assert 'Import-Certificate' not in script
    assert "X509Store" not in script
    installer = (ROOT / 'installer/LocalText2Voice.iss').read_text(encoding='utf-8')
    assert '#ifdef SignedBuild\nSignTool=ltv\nSignedUninstaller=yes' in installer


def test_installer_never_normalizes_after_signing():
    build = (ROOT / 'tools/build_windows_installer.ps1').read_text()
    assert 'Invoke-LtvFileSigning' in build
    assert build.index('Assert-LtvSigningReady') < build.index('& cmd.exe /c build_windows.bat')
    callback = (ROOT / 'tools/sign_windows_file.ps1').read_text()
    assert callback.index('normalize_installer_metadata.py') < callback.index('Invoke-LtvFileSigning')
    assert "if ($Sign)" in build and 'No unsigned fallback' in (ROOT / 'tools/windows_signing.ps1').read_text()


def test_inno_reaches_uninstaller_sign_callback_and_stops_on_failure(tmp_path):
    compiler = ROOT / '.util_instalador_y_firmas/InnoSetup/ISCC.exe'
    if not compiler.exists():
        pytest.skip('Local Inno compiler unavailable')
    output = tmp_path / 'output with spaces'
    output.mkdir()
    uninstallers = tmp_path / 'uninstallers with spaces'
    uninstallers.mkdir()
    capture = tmp_path / 'callback.json'
    callback = tmp_path / 'callback.ps1'
    callback.write_text('''param([string]$Path)
$f=Get-Item -LiteralPath $Path
[pscustomobject]@{name=$f.Name; directory=$f.DirectoryName; product=$f.VersionInfo.ProductName; version=$f.VersionInfo.ProductVersion} | ConvertTo-Json | Set-Content -LiteralPath "''' + str(capture) + '''"
exit 17
''')
    script = tmp_path / 'smoke.iss'
    script.write_text(fr'''[Setup]
AppName=LocalText2Voice
AppVersion=3.0.0
DefaultDirName={{localappdata}}\LocalText2VoiceSigningTest
OutputDir={output}
VersionInfoProductName=LocalText2Voice
VersionInfoProductVersion=3.0.0
SignTool=ltv
SignedUninstaller=yes
SignedUninstallerDir={uninstallers}
''')
    command = f'$q{PS}$q -NoProfile -ExecutionPolicy Bypass -File $q{callback}$q -Path $f'
    result = subprocess.run([str(compiler), '/Qp', f'/Sltv={command}', str(script)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    captured = json.loads(capture.read_text(encoding='utf-8'))
    assert captured['name'].startswith('uninst')
    assert Path(captured['directory']) == uninstallers
    assert captured['product'].strip() == 'LocalText2Voice'
    assert captured['version'].strip() == '3.0.0'
    result = run_ps(fake_settings() + f'''
function Get-Item {{ [pscustomobject]@{{Name='{captured['name']}';FullName='unused.tmp';VersionInfo=[pscustomobject]@{{ProductName='LocalText2Voice';ProductVersion='3.0.0'}}}} }}
function Get-AuthenticodeSignature {{ [pscustomobject]@{{Status='NotSigned'}} }}
function FakeTool {{ $global:LASTEXITCODE=0 }}
function Assert-LtvFileSignature {{}}
Invoke-LtvFileSigning $s 'unused.tmp' '3.0.0' -Uninstaller
''')
    assert result.returncode == 0, result.stderr
