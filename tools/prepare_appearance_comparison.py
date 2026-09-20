"""Prepare an offline experiment; only --run contacts Ollama. No app/project writes."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time


FIRST = "Which characters appear in this story? Does any character change their clothing, appearance, or age? Also add a brief 2–3-line summary of what the story is about."
APPEARANCE = "What do the characters look like and wear? Describe only characters not already defined: {names}. Write one line per character: Name: appearance."
COMBINED = "Which characters appear in this story? What do they look like and wear? Does any character change their clothing, appearance, or age? Also add a brief 2–3-line summary of what the story is about. Use sections: Characters (Name: appearance), Changes, Summary."
INITIAL_APPEARANCE = "Describe each character's physical appearance and clothing. One line per character: Name: description."
EXCLUDE_VISUAL = "Describe each character's physical appearance and clothing, except these already visually defined characters: {names}. One line per character."


def extract_entries(answer, sectioned=False):
    """Experimental plain-text parser. Preserve raw replies; flag failures."""
    entries = []
    active = not sectioned
    for line in answer.splitlines():
        clean = re.sub(r"^[\s#>*-]+|^\s*\d+[.)]\s*", "", line).replace("**", "").strip()
        if re.match(r"(?i)^characters\b", clean):
            active = True
            continue
        if re.match(r"(?i)^(?:appearance changes|changes|summary|story summary)\b", clean):
            active = False
        if not active or ":" not in clean:
            continue
        name, description = (v.strip() for v in clean.split(":", 1))
        if name and description and len(name) < 100:
            entries.append((name, description))
    return entries


def call(session, folder, label, messages, *, context_length=8192):
    payload = {"model": "qwen3:8b", "think": True, "stream": True,
               "messages": messages, "options": {"num_ctx": context_length, "num_predict": 4096, "seed": 42},
               "keep_alive": "10m"}
    (folder / f"{label}-request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / f"{label}-prompt.txt").write_text("\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages), encoding="utf-8")
    started, final, answer = time.monotonic(), None, ""
    print(f"START {label}", flush=True)
    with session.post("http://127.0.0.1:11434/api/chat", json=payload, stream=True, timeout=(15, 180)) as response:
        response.raise_for_status()
        with (folder / f"{label}-raw.jsonl").open("w", encoding="utf-8") as raw, \
             (folder / f"{label}-answer.txt").open("w", encoding="utf-8") as output:
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
        raise RuntimeError(f"Incomplete response: {label}. Inspect saved files before continuing.")
    stats = {"label": label, "seconds": round(time.monotonic() - started, 2),
             "prompt_tokens": final.get("prompt_eval_count"), "output_tokens": final.get("eval_count"),
             "done_reason": final.get("done_reason")}
    print(stats, flush=True)
    return answer, stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, help="Run an already prepared experiment directory")
    parser.add_argument("--variant", choices=("A", "B", "both"), default="both")
    parser.add_argument("--visual-exclusion", action="store_true", help="Revised A prompt: exclude only previously visually defined names")
    args = parser.parse_args()
    if args.run is None:
        project = Path("projects/Project5")
        data = json.loads((project / "project.json").read_text(encoding="utf-8"))
        plan = json.loads((project / "storyboard/storyboard.json").read_text(encoding="utf-8"))["plan"]
        passages = [r["text"] for r in plan["continuity"]["discovery_reports"]]
        assert "".join(passages) == data["source_text"], "Saved passages do not match the complete audiobook"
        root = Path("tmp/appearance-comparison") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        root.mkdir(parents=True)
        for i, passage in enumerate(passages, 1):
            (root / f"texto{i}.txt").write_text(passage, encoding="utf-8")
        for name, prompt in (("A1-characters", FIRST), ("A2-appearance", APPEARANCE), ("B-combined", COMBINED)):
            (root / f"{name}-prompt.txt").write_text(prompt, encoding="utf-8")
        manifest = {"title": data["title"], "duration_seconds": plan["source_duration_seconds"],
                    "source_characters": len(data["source_text"]), "blocks": len(passages),
                    "source_sha256": hashlib.sha256(data["source_text"].encode()).hexdigest(),
                    "expected_calls": {"A": len(passages) * 2, "B": len(passages)},
                    "status": "prepared_not_run"}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PREPARED ONLY: {root.resolve()}")
        return
    # Explicit execution gate; requests isn't even imported in preparation mode.
    import requests
    root = args.run.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    first_prompt = (root / "A1-characters-prompt.txt").read_text(encoding="utf-8")
    appearance_prompt = (root / "A2-appearance-prompt.txt").read_text(encoding="utf-8")
    combined_prompt = (root / "B-combined-prompt.txt").read_text(encoding="utf-8")
    passages = [(root / f"texto{i}.txt").read_text(encoding="utf-8") for i in range(1, manifest["blocks"] + 1)]
    assert hashlib.sha256("".join(passages).encode()).hexdigest() == manifest["source_sha256"]
    output = root / ("run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    output.mkdir()
    print(f"OUTPUT: {output}", flush=True)
    if args.visual_exclusion:
        (output / "A-initial-appearance-prompt.txt").write_text(INITIAL_APPEARANCE, encoding="utf-8")
        (output / "A-next-appearance-prompt.txt").write_text(EXCLUDE_VISUAL, encoding="utf-8")
    for variant in ("A", "B") if args.variant == "both" else (args.variant,):
        folder = output / variant
        folder.mkdir()
        registry, stats, attempts = {}, [], []
        with requests.Session() as session:
            for index, passage in enumerate(passages, 1):
                if variant == "A":
                    first = [{"role": "user", "content": first_prompt + "\n\n" + passage}]
                    answer, record = call(session, folder, f"block{index}-characters", first)
                    stats.append(record)
                    question = appearance_prompt.format(names=", ".join(registry) or "none")
                    if args.visual_exclusion:
                        question = EXCLUDE_VISUAL.format(names=", ".join(registry)) if registry else INITIAL_APPEARANCE
                    answer, record = call(session, folder, f"block{index}-appearance", first + [
                        {"role": "assistant", "content": answer}, {"role": "user", "content": question}])
                else:
                    answer, record = call(session, folder, f"block{index}-combined", [
                        {"role": "user", "content": combined_prompt + "\n\n" + passage}])
                stats.append(record)
                entries = extract_entries(answer, sectioned=variant == "B")
                attempts.append({"block": index, "entries": entries, "parse_warning": not bool(entries)})
                # First definition wins exactly; aliases are deliberately not guessed.
                for name, description in entries:
                    existing = next((n for n in registry if n.casefold() == name.casefold()), None)
                    if existing is None:
                        registry[name] = description
                (folder / "definitions.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
                (folder / "definitions.txt").write_text("\n".join(f"{n}: {d}" for n, d in registry.items()), encoding="utf-8")
                (folder / "attempts.json").write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
                (folder / "metrics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
                if not entries and not re.fullmatch(r"(?i)\s*(?:none|no new characters)[.!]?\s*", answer):
                    raise RuntimeError("Review unparsed answer before continuing; no identities silently discarded.")
    print(f"COMPLETE: {output}")


if __name__ == "__main__":
    main()
