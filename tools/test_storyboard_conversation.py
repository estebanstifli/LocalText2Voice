"""Three short chat turns against Ollama, with shared history, no project writes."""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import requests

PROMPTS = [
    "Which characters appear in this story? Does any character change their clothing, appearance, or age? Also add a brief 2–3-line summary of what the story is about.",
    "Which visual scenes would you illustrate in this story? For each scene, give the sentence from the original text that starts that scene.",
    "What does each character look like and wear? Give one consistent visual design per character, not alternatives. For humans only, specify hair length and color, or baldness. Respect the story; if these details are missing, choose them once. Keep the initial appearance separate from any clothing or appearance changes explicitly described in the story.",
]


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", type=Path)
    source.add_argument("--resume-dir", type=Path)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--after-turn", type=int, help="Resume immediately after this saved turn, ignoring later turns")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    if args.resume_dir:
        if not args.prompt_file:
            parser.error("--resume-dir requires --prompt-file")
        last = max(int(p.stem.split('-')[-1]) for p in args.resume_dir.glob('request-*.json'))
        if args.after_turn is not None:
            last = args.after_turn
        previous = json.loads((args.resume_dir / f"request-{last}.json").read_text(encoding="utf-8"))
        history = previous["messages"] + [{"role": "assistant", "content": (args.resume_dir / f"answer-{last}.txt").read_text(encoding="utf-8")}]
        story = (args.resume_dir / "original-story.txt").read_text(encoding="utf-8")
        prompts = [args.prompt_file.read_text(encoding="utf-8-sig").strip()]
        first_turn = last + 1
    else:
        story = args.text_file.read_text(encoding="utf-8-sig")
        history = []
        prompts = PROMPTS
        first_turn = 1
    root = Path("tmp/storyboard-conversation") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    (root / "original-story.txt").write_text(story, encoding="utf-8")
    metrics = []
    print("Output:", root.resolve(), flush=True)
    with (root / "conversation.txt").open("w", encoding="utf-8") as transcript:
        for index, prompt in enumerate(prompts, first_turn):
            history.append({"role": "user", "content": prompt + ("\n\n" + story if index == 1 else "")})
            body = {"model": args.model, "stream": True, "think": True,
                    "messages": deepcopy(history),
                    "options": {"num_ctx": 8192, "num_predict": 8096}}
            (root / f"request-{index}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"TURN {index}: {prompt}", flush=True)
            start = time.monotonic()
            content = ""
            done = {}
            thinking_chars = 0
            with requests.post(args.url.rstrip("/") + "/api/chat", json=body, stream=True, timeout=(15, 180)) as response:
                response.raise_for_status()
                with (root / f"raw-response-{index}.jsonl").open("w", encoding="utf-8") as raw, (root / f"answer-{index}.txt").open("w", encoding="utf-8") as answer:
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
                        thinking_chars += len(chunk.get("message", {}).get("thinking", ""))
                        if chunk.get("done"):
                            done = chunk
            if not done or done.get("done_reason") != "stop" or not content.strip():
                raise RuntimeError(f"Turn {index} did not finish normally; stop before building on an incomplete answer: {done.get('done_reason')}")
            # Only final answers enter later turns, never internal reasoning traces.
            history.append({"role": "assistant", "content": content})
            transcript.write(f"QUESTION {index}\n{prompt}\n\nANSWER {index}\n{content}\n\n")
            transcript.flush()
            stats = {"turn": index, "seconds": round(time.monotonic()-start, 2),
                     "thinking_characters": thinking_chars,
                     **{key: done.get(key) for key in ("done_reason", "prompt_eval_count", "eval_count", "load_duration")}}
            metrics.append(stats)
            (root / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            print(content, flush=True)
            print(json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
