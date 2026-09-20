"""Run only B's first passage; retain the plain reply without parsing it."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import requests

from prepare_appearance_comparison import call


def main():
    source = Path("tmp/appearance-comparison/20260910-222219-724702")
    text_file = source / "texto1.txt"
    before = hashlib.sha256(text_file.read_bytes()).hexdigest()
    prompt = (source / "B-combined-prompt.txt").read_text(encoding="utf-8")
    passage = text_file.read_text(encoding="utf-8")
    target = source / ("B-first-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    target.mkdir()
    print(f"OUTPUT: {target.resolve()}", flush=True)
    with requests.Session() as session:
        _, stats = call(session, target, "block1-combined", [
            {"role": "user", "content": prompt + "\n\n" + passage}])
    stats["source_unchanged"] = hashlib.sha256(text_file.read_bytes()).hexdigest() == before
    stats["source_sha256"] = before
    (target / "metrics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    assert stats["source_unchanged"]
    print("COMPLETE: one request only; source unchanged.", flush=True)


if __name__ == "__main__":
    main()
