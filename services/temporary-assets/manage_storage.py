"""Local beta administration. Secrets are kept in ignored files using Windows DPAPI.

Run with the repository's .venv Python. No admin secret belongs in the desktop app.
"""
import argparse
import json
import secrets
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.core.storyboard_credentials import protect, reveal

PRIVATE = Path(__file__).resolve().parent / ".secrets"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def call(config, path, method="GET", data=None):
    request = urllib.request.Request(config["url"] + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + reveal(config["admin_encrypted"]), "Content-Type": "application/json", "User-Agent": "LocalText2Voice/StorageAdministration"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "set-url", "create-client", "export-code", "status", "list", "revoke", "pause", "resume", "cleanup", "configure-app"])
    parser.add_argument("--url")
    parser.add_argument("--label", default="Local beta")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--id")
    parser.add_argument("--client-file", default="local-beta.json")
    args = parser.parse_args()
    config_file = PRIVATE / "operator.json"
    if args.action == "init":
        if config_file.exists():
            parser.error("Operator credentials already exist. They were not changed.")
        admin, signing = secrets.token_hex(32), secrets.token_hex(32)
        save(config_file, {"url": "", "admin_encrypted": protect(admin), "signing_encrypted": protect(signing)})
        save(PRIVATE / "worker-secrets.json", {"ADMIN_TOKEN": admin, "URL_SIGNING_KEY": signing})
        print("Created protected operator credentials and one-time Wrangler import file.")
        return
    config = json.loads(config_file.read_text(encoding="utf-8"))
    if args.action == "set-url":
        if not args.url or not args.url.startswith("https://"):
            parser.error("An HTTPS deployment URL is required.")
        config["url"] = args.url.rstrip("/")
        save(config_file, config)
        return
    client_path = PRIVATE / Path(args.client_file).name
    if args.action == "create-client":
        if client_path.exists():
            parser.error("Choose a new --client-file; existing access was preserved.")
        result = call(config, "/admin/clients", "POST", {"label": args.label, "days": args.days})
        result["token_encrypted"] = protect(result.pop("token"))
        result["service_url"] = config["url"]
        save(client_path, result)
        print(f"Created individual beta access; saved encrypted in {client_path.name}.")
    elif args.action == "export-code":
        client = json.loads(client_path.read_text(encoding="utf-8"))
        destination = client_path.with_suffix(".txt")
        destination.write_text(reveal(client["token_encrypted"]), encoding="utf-8")
        print(f"Exported this individual access code to {destination.name}. Share privately, then delete the text file.")
    elif args.action == "configure-app":
        from copy import deepcopy
        from app.core.settings_manager import DEFAULT_SETTINGS
        from app.core.storyboard_profiles import visual_snapshot
        client = json.loads(client_path.read_text(encoding="utf-8"))
        path = ROOT / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        storyboard = data.setdefault("video_storyboard", {})
        value = {"reference_storage": "managed", "temporary_storage_url": client["service_url"], "temporary_storage_token_encrypted": client["token_encrypted"]}
        storyboard.setdefault("runpod", {}).update(value)
        profiles = storyboard.setdefault("profiles", {})
        if "runpod" not in profiles:
            profiles["runpod"] = visual_snapshot(DEFAULT_SETTINGS["video_storyboard"])
            profiles["runpod"].update(image_provider="runpod", image_edit_provider="runpod", video_provider="runpod",
                                     runpod=deepcopy(storyboard["runpod"]))
        profiles["runpod"].setdefault("runpod", {}).update(value)
        temporary = path.with_suffix(".json.tmp")
        save(temporary, data)
        temporary.replace(path)
        print("Configured managed temporary storage in local app settings. Runpod API key and active profile preserved.")
    elif args.action in {"pause", "resume"}:
        print(json.dumps(call(config, "/admin/config", "PATCH", {"paused": args.action == "pause"})))
    elif args.action == "revoke":
        if not args.id:
            parser.error("--id is required")
        print(json.dumps(call(config, "/admin/clients/" + args.id, "DELETE")))
    else:
        path = {"status": "/admin/status", "list": "/admin/clients", "cleanup": "/admin/cleanup"}[args.action]
        print(json.dumps(call(config, path, "POST" if args.action == "cleanup" else "GET"), indent=2))


if __name__ == "__main__":
    main()
