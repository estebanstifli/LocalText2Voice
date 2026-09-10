"""Test public installation registration with synthetic data, then revoke it."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.core import installation_identity as identity
from app.core import storyboard_temporary_storage as storage

spec = importlib.util.spec_from_file_location("admin", Path(__file__).resolve().parents[1] / "manage_storage.py")
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)
operator = json.loads((admin.PRIVATE / "operator.json").read_text(encoding="utf-8"))
before = {c["id"] for c in admin.call(operator, "/admin/clients")["clients"]}
client_id = None
asset = None
with tempfile.TemporaryDirectory() as folder, patch.object(identity, "identity_path", lambda: Path(folder) / "identity.json"):
    config = {"temporary_storage_url": operator["url"]}
    try:
        try:
            storage.register(config)
            raise AssertionError("Registration must require consent")
        except storage.TemporaryStorageError as exc:
            assert "Accept temporary" in str(exc)
        identity.accept_storage(operator["url"])
        assert storage.register(config)["registered"]
        assert storage.register(config)["registered"]
        created = [c for c in admin.call(operator, "/admin/clients")["clients"] if c["id"] not in before]
        assert len(created) == 1
        client_id = created[0]["id"]
        image = Path(folder) / "synthetic.png"
        Image.new("RGB", (1280, 720), "blue").save(image)
        asset = storage.upload(image, config)
        with urllib.request.urlopen(urllib.request.Request(asset["url"], headers={"User-Agent": "LocalText2Voice"})) as response:
            assert response.read() == storage.normalized_png(image)
        assert storage.delete(asset, config)
        try:
            urllib.request.urlopen(urllib.request.Request(asset["url"], headers={"User-Agent": "LocalText2Voice"}))
            raise AssertionError("Deleted reference was accessible")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        print("Live automatic setup passed: consent gate, random installation identity, idempotent registration, upload, signed read and deletion.")
    finally:
        if asset:
            storage.delete(asset, config)
        if client_id:
            admin.call(operator, "/admin/clients/" + client_id, "DELETE")
            try:
                storage.register(config)
                raise AssertionError("Revoked installation registered again")
            except storage.TemporaryStorageError:
                pass
            print("Synthetic test installation revoked; re-registration denied.")
