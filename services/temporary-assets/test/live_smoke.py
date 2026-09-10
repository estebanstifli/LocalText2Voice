"""Exercise the deployed service with synthetic images; no Runpod generation."""
import importlib.util
import io
import json
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.core import storyboard_temporary_storage as storage
from app.core.storyboard_credentials import protect
from app.core import installation_identity

spec = importlib.util.spec_from_file_location("storage_administration", Path(__file__).resolve().parents[1] / "manage_storage.py")
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)
operator = json.loads((admin.PRIVATE / "operator.json").read_text(encoding="utf-8"))
clients = []


def download(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LocalText2Voice"}), timeout=45) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


try:
    for label in ("Smoke test A", "Smoke test B"):
        client = admin.call(operator, "/admin/clients", "POST", {"label": label, "days": 1})
        clients.append(client)
    configs = [{"temporary_storage_url": operator["url"], "temporary_storage_token_encrypted": protect(client["token"])} for client in clients]
    assert storage.request(configs[0], "/v1/status")["available"]
    with tempfile.TemporaryDirectory() as folder:
        identity_patch = patch.object(installation_identity, "identity_path", lambda: Path(folder) / "identity.json")
        identity_patch.start()
        installation_identity.accept_storage(operator["url"])
        path = Path(folder) / "synthetic.png"
        # Exercise a realistic upload size, not just a tiny health-check fixture.
        Image.frombytes("RGB", (1280, 720), secrets.token_bytes(1280 * 720 * 3)).save(path)
        expected = storage.normalized_png(path)
        asset = storage.upload(path, configs[0])
        status, data, headers = download(asset["url"])
        assert status == 200 and data == expected
        assert headers["Cache-Control"] == "no-store"
        assert download(asset["url"] + "0")[0] == 403
        storage.request(configs[1], "/v1/assets/" + asset["id"], method="DELETE")
        assert download(asset["url"])[0] == 200
        assert storage.delete(asset, configs[0])
        assert download(asset["url"])[0] == 404
        print(f"Live Cloudflare smoke passed: {len(expected):,} byte PNG upload/download, signed access, tenant isolation and deletion.")
finally:
    if 'identity_patch' in globals():
        identity_patch.stop()
    for client in clients:
        admin.call(operator, "/admin/clients/" + client["id"], "DELETE")
    print("Smoke access codes revoked; synthetic references removed.")
