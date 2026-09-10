"""Isolated Qwen transport/prompt experiment; no project or production changes."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import re
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.storyboard_analysis_settings import character_discovery_messages


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    text = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    system, user = character_discovery_messages(text)
    basic = {"model": "qwen3:8b", "stream": True, "think": False,
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
             "options": {"num_ctx": 8192, "num_predict": 8096, "seed": 42,
                         "temperature": 0.2, "repeat_penalty": 1.08, "repeat_last_n": 1024}}
    cases = []
    for name, minimal, thinking, defaults in [
        ("01_app", False, False, False),
        ("02_app_thinking", False, True, False),
        ("03_app_model_defaults_thinking", False, True, True),
        ("04_question_app_options", True, False, False),
        ("05_question_defaults_thinking", True, True, True),
        ("06_question_defaults_no_thinking", True, False, True),
    ]:
        body = deepcopy(basic)
        body["think"] = thinking
        if minimal:
            body["messages"] = [{"role": "user", "content": "¿Qué personajes salen en este cuento?\n\n" + text}]
        if defaults:
            for key in ("temperature", "repeat_penalty", "repeat_last_n"):
                body["options"].pop(key)
        cases.append((name, body))
    if len(sys.argv) > 2:
        log = Path(sys.argv[2]).read_text(encoding="utf-8")
        match = re.search(r"^REQUEST 12: .*character_appearance.*$", log, re.M)
        if not match:
            raise ValueError("Character appearance request 12 not found")
        base, _ = json.JSONDecoder().raw_decode(log[log.index("{", match.end()):])
        cases = []
        for name, thinking, defaults in [("07_appearance_app", False, False),
                                         ("08_appearance_thinking", True, False),
                                         ("09_appearance_defaults_thinking", True, True)]:
            body = deepcopy(base)
            body["think"] = thinking
            body["stream"] = True
            body["options"]["seed"] = 42
            if defaults:
                for key in ("temperature", "repeat_penalty", "repeat_last_n"):
                    body["options"].pop(key, None)
            cases.append((name, body))
    root = Path("tmp/qwen-chat-comparison") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    print(root.resolve(), flush=True)
    summary = []
    for name, body in cases:
        folder = root / name
        folder.mkdir()
        (folder / "request.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        print("CASE", name, flush=True)
        start = time.monotonic()
        final = {}
        content = ""
        thought_chars = 0
        try:
            with requests.post("http://127.0.0.1:11434/api/chat", json=body, stream=True, timeout=(15, 180)) as response:
                response.raise_for_status()
                with (folder / "raw-response.jsonl").open("w", encoding="utf-8") as raw, (folder / "answer.txt").open("w", encoding="utf-8") as answer:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        raw.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                        raw.flush()
                        if chunk.get("error"):
                            raise RuntimeError(chunk["error"])
                        delta = chunk.get("message", {}).get("content", "")
                        content += delta
                        answer.write(delta)
                        answer.flush()
                        thought_chars += len(chunk.get("message", {}).get("thinking", ""))
                        if chunk.get("done"):
                            final = chunk
            if not final:
                raise RuntimeError("Incomplete stream")
            stats = {"case": name, "seconds": round(time.monotonic()-start, 2),
                     "thinking_characters": thought_chars,
                     **{k: final.get(k) for k in ("done_reason", "eval_count", "prompt_eval_count", "load_duration")}}
            summary.append(stats)
            print(content, flush=True)
            print(json.dumps(stats), flush=True)
        except Exception as exc:
            summary.append({"case": name, "error": str(exc)})
            print("ERROR", exc, flush=True)
        (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
