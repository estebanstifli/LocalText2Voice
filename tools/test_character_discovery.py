"""Isolated first-pass experiment. Never changes a project or runs later passes.

Use --text-file for any book, or --input-log to recover the original narration
from a storyboard request log. --prompt-file permits controlled prompt trials.
Each run saves its exact inputs and streamed response in a new output folder.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.storyboard_analysis_settings import character_discovery_messages


def narration_from_log(path):
    log = Path(path).read_text(encoding="utf-8")
    units = {}
    plain_blocks = []
    for match in re.finditer(r"^REQUEST \d+: continuity discovery block [^\n]+", log, re.M):
        body, _ = json.JSONDecoder().raw_decode(log[log.index("{", match.end()):])
        user = body["messages"][-1]["content"]
        if user.startswith("Produce a compact continuity report"):
            rows, _ = json.JSONDecoder().raw_decode(user[user.index("["):])
            for row in rows:
                units.setdefault(row["unit"], row["text"])
        else:
            plain_blocks.append(user)
    if units and plain_blocks:
        raise ValueError("Mixed input formats; supply a plain text file instead.")
    text = "\n\n".join(units[k] for k in sorted(units)) if units else "\n\n".join(plain_blocks)
    if not text.strip():
        raise ValueError("No discovery narration found.")
    return text


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", type=Path)
    source.add_argument("--input-log", type=Path)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--max-tokens", type=int, default=8096)
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/character-discovery"))
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    text = args.text_file.read_text(encoding="utf-8-sig") if args.text_file else narration_from_log(args.input_log)
    system, user = character_discovery_messages(text)
    if args.prompt_file:
        system = args.prompt_file.read_text(encoding="utf-8-sig")
    body = {"model": args.model, "stream": True, "think": True,
            "messages": [{"role": "user", "content": system + "\n\n" + user}],
            "options": {"temperature": 0.2, "num_ctx": args.num_ctx,
                        "num_predict": args.max_tokens, "repeat_penalty": 1.08, "repeat_last_n": 1024}}
    output = args.output_dir / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)
    (output / "narration.txt").write_text(text, encoding="utf-8")
    (output / "prompt.txt").write_text(system, encoding="utf-8")
    (output / "request.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Output: {output.resolve()}\nNarration: {len(text)} characters", flush=True)
    if args.prepare_only:
        return
    import requests
    try:
        with requests.post(args.url.rstrip("/") + "/api/chat", json=body, stream=True,
                           timeout=(15, 180)) as response:
            response.raise_for_status()
            finished = False
            with (output / "response.txt").open("w", encoding="utf-8") as report, (output / "raw-response.jsonl").open("w", encoding="utf-8") as raw:
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    raw.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    raw.flush()
                    if chunk.get("error"):
                        raise RuntimeError(chunk["error"])
                    content = chunk.get("message", {}).get("content", "")
                    report.write(content)
                    report.flush()
                    print(content, end="", flush=True)
                    if chunk.get("done"):
                        finished = True
                        print("\nFinish reason:", chunk.get("done_reason"), flush=True)
            if not finished:
                raise RuntimeError("Stream ended without completion; response is partial.")
    except Exception as exc:
        (output / "error.txt").write_text(str(exc), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
