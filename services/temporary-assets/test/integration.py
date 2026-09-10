"""Run against wrangler dev with test-only credentials and a 10 second asset TTL."""
import concurrent.futures
import io
import json
import secrets
import time
import urllib.error
import urllib.request

from PIL import Image

BASE = "http://127.0.0.1:8791"
ADMIN = "1" * 64


def call(path, token=None, method="GET", data=None, mime="application/json"):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    headers = {"Content-Type": mime}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(BASE + path if path.startswith("/") else path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def client(label):
    status, data, _ = call("/admin/clients", ADMIN, "POST", {"label": label})
    assert status == 201, data
    return json.loads(data)


def upload(token):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, "PNG")
    return call("/v1/assets", token, "POST", stream.getvalue(), "image/png")


assert call("/admin/status")[0] == 401
assert call("/v1/status")[0] == 401
assert call("/internal/cleanup", method="POST")[0] == 404
assert call("/admin/config", ADMIN, "PATCH", {"paused": False})[0] == 200
automatic = secrets.token_hex(32)
assert call("/v1/register", automatic, "POST", {"consent_version": 0})[0] == 400
assert call("/v1/register", automatic, "POST", {"consent_version": 1})[0] == 201
assert call("/v1/register", automatic, "POST", {"consent_version": 1})[0] == 200
assert call("/v1/status", automatic)[0] == 200
clients = json.loads(call("/admin/clients", ADMIN)[1])["clients"]
auto_id = next(c["id"] for c in clients if c["label"] == "Automatic installation")
assert call("/admin/clients/" + auto_id, ADMIN, "DELETE")[0] == 200
assert call("/v1/register", automatic, "POST", {"consent_version": 1})[0] == 401
for _ in range(9):
    assert call("/v1/register", secrets.token_hex(32), "POST", {"consent_version": 1})[0] == 201
assert call("/v1/register", secrets.token_hex(32), "POST", {"consent_version": 1})[0] == 429
first, second = client("Integration A"), client("Integration B")
assert call("/v1/status", first["token"])[0] == 200
status, data, _ = upload(first["token"])
assert status == 201, data
asset = json.loads(data)
status, image, headers = call(asset["url"])
assert status == 200 and image.startswith(b"\x89PNG")
assert headers["Cache-Control"] == "no-store"
assert call(asset["url"], method="HEAD")[0] == 200
assert call(asset["url"] + "0")[0] == 403
assert call("/media/" + asset["id"])[0] == 403
assert call("/v1/assets/" + asset["id"], second["token"], "DELETE")[0] == 200
assert call(asset["url"])[0] == 200  # Another user's delete did not remove it.
assert call("/admin/config", ADMIN, "PATCH", {"paused": True})[0] == 200
assert upload(second["token"])[0] == 503
assert call(asset["url"])[0] == 200  # Pausing uploads preserves active downloads.
assert call("/admin/config", ADMIN, "PATCH", {"paused": False})[0] == 200
assert call("/v1/assets/" + asset["id"], first["token"], "DELETE")[0] == 200
assert call(asset["url"])[0] == 404
assert call("/v1/assets", second["token"], "POST", b"<html>invalid</html>", "image/png")[0] == 415
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(lambda _: upload(first["token"]), range(5)))
assert sum(result[0] == 201 for result in results) <= 2, results
assert all(result[0] in {201, 429} for result in results)
usage = json.loads(call("/v1/status", first["token"])[1])["usage"]
assert usage["uploads"] <= 3, usage
assert call("/admin/clients/" + first["id"], ADMIN, "DELETE")[0] == 200
assert call("/v1/status", first["token"])[0] == 401
for status, data, _ in results:
    if status == 201:
        assert call(json.loads(data)["url"])[0] == 404
status, data, _ = upload(second["token"])
assert status == 201, data
expiring = json.loads(data)
time.sleep(11)
assert call(expiring["url"])[0] == 403
assert call("/admin/cleanup", ADMIN, "POST")[0] == 200
stats = json.loads(call("/admin/status", ADMIN)[1])
assert stats["assets"]["count"] == 0, stats
print("Local Worker integration passed: auth, ownership, quotas, malformed input, pause, revocation, expiry and cleanup.")
