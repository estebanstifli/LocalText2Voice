"""Send the complete experimental audiobook once, without modifying its source."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import requests

from prepare_appearance_comparison import call


def main():
    source = Path("tmp/appearance-comparison/20260910-222219-724702")
    inputs = [source / f"texto{i}.txt" for i in range(1, 6)]
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    text = "".join(p.read_text(encoding="utf-8") for p in inputs)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(text.encode()).hexdigest() == manifest["source_sha256"]
    prompt = (source / "B-combined-prompt.txt").read_text(encoding="utf-8")
    target = source / ("B-whole-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    target.mkdir()
    print(f"OUTPUT: {target.resolve()}\nSOURCE: {len(text)} characters; context 32768", flush=True)
    (target / "input.json").write_text(json.dumps({"characters": len(text), "source_sha256": manifest["source_sha256"],
        "context": 32768, "output_limit": 4096}, indent=2), encoding="utf-8")
    try:
        with requests.Session() as session:
            _, stats = call(session, target, "whole-book", [{"role": "user", "content": prompt + "\n\n" + text}],
                            context_length=32768)
        (target / "metrics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print("COMPLETE", flush=True)
    except Exception as exc:
        (target / "error.txt").write_text(str(exc), encoding="utf-8")
        raise
    finally:
        unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
        (target / "originals-check.json").write_text(json.dumps({"unchanged": unchanged, "sha256": hashes}, indent=2), encoding="utf-8")
        assert unchanged


if __name__ == "__main__":
    main()
