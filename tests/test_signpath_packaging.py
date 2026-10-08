from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

from tools.ci.prepare_windows_assets import download, extract_checked
from tools.write_windows_version_info import EXECUTABLES, ROOT, app_version, version_resource


@pytest.mark.parametrize("name", EXECUTABLES)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows PE resource round trip")
def test_windows_metadata_has_product_and_version(name, tmp_path):
    from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
    file = tmp_path / "version.txt"
    file.write_text(version_resource(name, app_version()), encoding="utf-8")
    info = load_version_info_from_text_file(str(file))
    values = {item.name: item.val for item in info.kids[0].kids[0].kids}
    assert values["ProductName"] == "LocalText2Voice"
    assert values["ProductVersion"] == app_version()
    assert values["OriginalFilename"] == name + ".exe"
    assert info.toRaw()


@pytest.mark.parametrize("version", ["2.2.0rc1", "65536.0.0", "2.2", "2.2.0\nmalicious"])
def test_invalid_windows_version_is_rejected(version, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text(f"__version__ = {version!r}", encoding="utf-8")
    with pytest.raises(ValueError):
        app_version(tmp_path)


@pytest.mark.parametrize("filename,names", [
    ("application-zip-v1.xml", {name + ".exe" for name in EXECUTABLES}),
    ("installer-zip-v1.xml", {"LocalText2Voice-Setup.exe"}),
])
def test_artifact_configuration_only_signs_owned_files(filename, names):
    ns = {"s": "http://signpath.io/artifact-configuration/v1"}
    root = ET.parse(ROOT / "installer/signpath" / filename).getroot()
    assert root.find("s:parameters/s:parameter", ns).attrib == {"name": "version", "required": "true"}
    files = root.findall("s:zip-file/s:pe-file", ns)
    assert {file.attrib["path"] for file in files} == names
    for file in files:
        assert file.attrib["product-name"] == "LocalText2Voice"
        assert file.attrib["product-version"] == "${version}"
        assert file.find("s:authenticode-sign", ns) is not None


def test_workflow_is_manual_test_only_and_does_not_publish():
    # BaseLoader keeps the YAML 1.2 GitHub key "on" as a string.
    workflow = yaml.load((ROOT / ".github/workflows/signpath-test.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    job = workflow["jobs"]["windows-test"]
    assert job["runs-on"] == "windows-2022"
    signed_steps = [step for step in job["steps"] if step.get("uses", "").startswith("signpath/")]
    assert len(signed_steps) == 2
    for step in signed_steps:
        assert step["if"] == "inputs.sign_test"
        assert step["with"]["signing-policy-slug"] == "test-signing"
        assert step["with"]["organization-id"] == "f18d1304-1b63-4ca0-afd7-847be5f5d3be"
        assert (ROOT / "installer/signpath" / (step["with"]["artifact-configuration-slug"] + ".xml")).exists()
    for step in job["steps"]:
        if "uses" in step:
            assert len(step["uses"].split("@")[1]) == 40
        assert "gh release" not in step.get("run", "")
        if "verify_test_signatures.ps1" in step.get("run", ""):
            assert step["timeout-minutes"] == "5"


def test_certificate_trust_is_pinned_and_restricted_to_disposable_ci():
    script = (ROOT / "tools/ci/verify_test_signatures.ps1").read_text()
    assert "$env:GITHUB_ACTIONS -ne 'true'" in script
    assert "$env:RUNNER_ENVIRONMENT -ne 'github-hosted'" in script
    assert "15C8F90D4432F333EF31C8C5C1DB02E4A91F1FE6" in script
    assert "::new('Root', 'LocalMachine')" in script
    assert "::new('Root', 'CurrentUser')" not in script
    assert "$Certificate.Thumbprint -ne $Thumbprint" in script
    assert "$Verified.Status -ne 'Valid'" in script
    assert "$Verified.TimeStamperCertificate" in script
    assert "finally {" in script
    assert "$Store.Remove($Certificate)" in script


def test_download_requires_matching_hash_and_cleans_temporary_file(tmp_path):
    target = tmp_path / "tool.zip"
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"valid")):
        download("https://example.org/tool", hashlib.sha256(b"valid").hexdigest(), target)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"tampered")):
        with pytest.raises(ValueError, match="SHA-256"):
            download("https://example.org/tool", hashlib.sha256(b"valid").hexdigest(), target)
    assert target.read_bytes() == b"valid"
    assert not target.with_suffix(".zip.tmp").exists()


@pytest.mark.parametrize("name", ["../outside.exe", "/outside.exe", "C:/outside.exe", "..\\outside.exe"])
def test_zip_rejects_paths_outside_destination(name, tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr(name, b"bad")
    with pytest.raises(ValueError):
        extract_checked(archive, tmp_path / "extracted")
    assert not (tmp_path / "outside.exe").exists()


def test_zip_accepts_valid_runtime(tmp_path):
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("piper/piper.exe", b"runtime")
    extract_checked(archive, tmp_path / "extracted")
    assert (tmp_path / "extracted/piper/piper.exe").read_bytes() == b"runtime"
