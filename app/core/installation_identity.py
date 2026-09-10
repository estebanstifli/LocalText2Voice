"""Random, local installation identity and versioned storage consent."""
import json
import secrets
import threading
import time

from app.core.storyboard_credentials import protect, reveal
from app.utils.paths import application_root

_lock = threading.RLock()


def identity_path():
    return application_root() / ".installation.json"


def _read():
    path = identity_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = {"token_encrypted": protect(secrets.token_hex(32)), "consents": {}}
    _write(value)
    return value


def _write(value):
    path = identity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path)


def installation_token():
    with _lock:
        return reveal(_read()["token_encrypted"])


def storage_consent(origin, version=1):
    with _lock:
        return _read().get("consents", {}).get(origin, {}).get("version") == version


def accept_storage(origin, version=1):
    with _lock:
        value = _read()
        value.setdefault("consents", {})[origin] = {"version": version, "accepted_at": int(time.time())}
        _write(value)
