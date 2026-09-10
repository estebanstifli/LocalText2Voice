"""Check the saved Runpod credential without logging it or submitting generations."""
import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.settings_manager import SettingsManager
from app.core.video_storyboard_runpod import RunpodError, api_key, check_connection, configuration, request
from app.utils.logging_utils import configure_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=ROOT / "config.json")
    parser.add_argument("--log", type=Path, default=ROOT / "logs" / "runpod_diagnostic.log")
    parser.add_argument("--include-health", action="store_true", help="Reproduce the former public /health check (it can reject valid keys).")
    args = parser.parse_args()
    configure_logging(args.log)
    settings = SettingsManager(args.settings).get("video_storyboard")
    config = configuration(settings)
    api_key(config)
    logging.info("Runpod diagnostic: saved credential loaded; Authorization uses Bearer; no generation will be submitted.")
    if args.include_health:
        for role in ("image", "edit", "video"):
            try:
                request(config, config[f"{role}_endpoint"], "health")
            except RunpodError:
                pass  # The HTTP layer already recorded the sanitized error.
    try:
        result = check_connection(settings)
    except RunpodError as error:
        logging.error("Runpod authentication check failed: %s", error)
        return 1
    logging.info("Runpod check complete: %s. Generation access and billing were not exercised.", ", ".join(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
