"""Short-prompt experiment: append answers, never rewrite existing definitions."""
from datetime import datetime
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import requests


PROMPT = "Which characters appear in the second text but not in the first? List only those characters."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptions", action="store_true")
    args = parser.parse_args()
    instruction = ("Which characters appear in the second text but not in the first? List only those characters and their descriptions."
                   if args.descriptions else PROMPT)
    source = Path("projects/Project5/storyboard/analysis/20260910-202808-507969a7")
    originals = [source / f"resumen{i}.txt" for i in range(1, 6)]
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    texts = [p.read_text(encoding="utf-8") for p in originals]
    root = Path("tmp/minimal-character-descriptions" if args.descriptions else "tmp/minimal-character-additions") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    (root / "prompt.txt").write_text(instruction, encoding="utf-8")
    print(f"OUTPUT: {root.resolve()}", flush=True)
    # These headings belong to the saved experimental inputs, not a production parser.
    stories = []
    for text in texts:
        match = re.search(r"(?m)^\*\*Summary:\*\*[^\S\n]*\n", text)
        if not match:
            raise ValueError("Missing Summary heading in original input")
        stories.append(text[match.end():])
    (root / "resumen_historia_concatenado.txt").write_text("\n\n".join(stories), encoding="utf-8")
    initial_characters = texts[0].split("**Changes in appearance, clothing, or age:**", 1)[0]
    accumulated = texts[0]
    characters = initial_characters
    results = []
    with requests.Session() as session:
        for number, later in enumerate(texts[1:], 2):
            question = instruction + "\n\nFIRST TEXT:\n" + accumulated + "\n\nSECOND TEXT:\n" + later
            payload = {"model": "qwen3:8b", "think": True, "stream": True,
                       "messages": [{"role": "user", "content": question}],
                       "options": {"num_ctx": 8192, "num_predict": 4096, "seed": 42}, "keep_alive": "10m"}
            label = f"paso{number - 1}-agregar-resumen{number}"
            (root / f"{label}-prompt.txt").write_text(question, encoding="utf-8")
            (root / f"{label}-request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"START {label}", flush=True)
            start, answer, final = time.monotonic(), "", None
            with session.post("http://127.0.0.1:11434/api/chat", json=payload, stream=True, timeout=(15, 180)) as response:
                response.raise_for_status()
                with (root / f"{label}-respuesta.txt").open("w", encoding="utf-8") as output, \
                     (root / f"{label}-raw.jsonl").open("w", encoding="utf-8") as raw:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        item = json.loads(line)
                        raw.write(json.dumps(item, ensure_ascii=False) + "\n")
                        raw.flush()
                        if item.get("error"):
                            raise RuntimeError(item["error"])
                        delta = item.get("message", {}).get("content", "")
                        answer += delta
                        output.write(delta)
                        output.flush()
                        if item.get("done"):
                            final = item
            if not final or final.get("done_reason") != "stop" or not answer.strip():
                raise RuntimeError(f"Incomplete reply for {label}; saved output available")
            accumulated += "\n\n" + answer
            characters += "\n\n" + answer
            (root / f"{label}-acumulado.txt").write_text(characters, encoding="utf-8")
            results.append({"step": label, "seconds": round(time.monotonic() - start, 2),
                            "done_reason": final.get("done_reason"), "prompt_tokens": final.get("prompt_eval_count"),
                            "output_tokens": final.get("eval_count")})
            (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"DONE {results[-1]}", flush=True)
    (root / "PERSONAJES_FINAL.txt").write_text(characters, encoding="utf-8")
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    (root / "originals-check.json").write_text(json.dumps({"unchanged": unchanged, "sha256": hashes}, indent=2), encoding="utf-8")
    assert unchanged
    print("COMPLETE. Originals unchanged.", flush=True)


if __name__ == "__main__":
    main()
