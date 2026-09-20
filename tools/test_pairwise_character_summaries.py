"""Isolated pairwise Ollama experiment. Original summaries are read-only."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

import requests


PROMPT = """Merge these two summaries of consecutive parts of the same story.

Return two sections in plain text:
CHARACTERS: one entry per character, with their name, aliases, role, and any stated appearance or clothing changes in story order. Keep all named characters, including minor ones. Merge aliases only when the summaries clearly identify the same person. A disguise is not a new person when its wearer is identified. Do not confuse a description in a photograph with a change in the person's appearance. Keep uncertain identities or conflicting claims marked as uncertain; do not guess.
STORY SUMMARY: 3-5 lines covering the combined events in chronological order, including the ending if supplied.

Use only these summaries. Do not add facts from your knowledge of the story. Remove repetition, not characters or explicit changes. No JSON, introduction, or commentary.

EARLIER SUMMARY:
{earlier}

LATER SUMMARY:
{later}
"""

FIRST_WINS_PROMPT = """Update an established character list using the next summary of the same story.

The EARLIER SUMMARY is authoritative for existing characters. Copy their names and initial definitions unchanged. Do not rewrite, expand or replace them with later descriptions. Add only genuinely new characters from the LATER SUMMARY, using their first supplied definition.

If a later name clearly refers to an existing person, record it as an alias, not another character. A revealed identity is not a physical change. If the connection is unclear, mark it uncertain rather than guessing.

Keep clothing, age, disguise or physical appearance changes in a separate chronological list under the relevant character; never overwrite the initial definition. Keep previous changes and add only new explicit changes. Actions, emotions and newly mentioned unchanged traits are not appearance changes. Preserve uncertainty; do not invent facts or use outside knowledge.

Return plain text with these sections:
CHARACTERS: the complete accumulated list, initial definitions unchanged, plus aliases.
APPEARANCE CHANGES: previous changes plus new changes, grouped by character, without repetition; say none when there are none.
STORY SUMMARY: update the combined plot in 3-5 lines.

EARLIER SUMMARY:
{earlier}

LATER SUMMARY:
{later}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--first-wins", action="store_true", help="Accumulate 1+2, then +3, +4, +5; preserve first definitions")
    args = parser.parse_args()
    inputs = [args.source / f"resumen{i}.txt" for i in range(1, 6)]
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    root = Path("tmp/first-wins-summaries" if args.first_wins else "tmp/pairwise-summaries") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root.mkdir(parents=True)
    template = FIRST_WINS_PROMPT if args.first_wins else PROMPT
    (root / "prompt-template.txt").write_text(template, encoding="utf-8")
    print(f"OUTPUT: {root.resolve()}", flush=True)
    nodes = [(str(i), path.read_text(encoding="utf-8")) for i, path in enumerate(inputs, 1)]
    results = []
    with requests.Session() as session:
        round_number = 0
        while len(nodes) > 1:
            round_number += 1
            following = []
            for index in range(0, 2 if args.first_wins else len(nodes), 2):
                if index + 1 == len(nodes):
                    following.append(nodes[index])
                    continue
                a, b = nodes[index:index + 2]
                name = f"round{round_number}-{a[0]}+{b[0]}"
                prompt = template.format(earlier=a[1], later=b[1])
                payload = {"model": "qwen3:8b", "think": True, "stream": True,
                           "messages": [{"role": "user", "content": prompt}],
                           "options": {"num_ctx": 8192, "num_predict": 4096, "seed": 42},
                           "keep_alive": "10m"}
                (root / f"{name}-prompt.txt").write_text(prompt, encoding="utf-8")
                (root / f"{name}-request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"START {name}", flush=True)
                start = time.monotonic()
                final, answer = None, ""
                with session.post("http://127.0.0.1:11434/api/chat", json=payload, stream=True,
                                  timeout=(15, 180)) as response:
                    response.raise_for_status()
                    with (root / f"{name}-raw.jsonl").open("w", encoding="utf-8") as raw, \
                         (root / f"{name}-answer.txt").open("w", encoding="utf-8") as output:
                        for line in response.iter_lines():
                            if not line:
                                continue
                            chunk = json.loads(line)
                            raw.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                            raw.flush()
                            if chunk.get("error"):
                                raise RuntimeError(chunk["error"])
                            delta = chunk.get("message", {}).get("content", "")
                            answer += delta
                            output.write(delta)
                            output.flush()
                            if chunk.get("done"):
                                final = chunk
                if not final or final.get("done_reason") != "stop" or not answer.strip():
                    raise RuntimeError(f"Incomplete response for {name}; inspect saved raw output")
                results.append({"step": name, "seconds": round(time.monotonic() - start, 2),
                                "characters": len(answer), "done_reason": final.get("done_reason"),
                                "prompt_eval_count": final.get("prompt_eval_count"),
                                "eval_count": final.get("eval_count")})
                (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"DONE {results[-1]}", flush=True)
                following.append((a[0] + "-" + b[0], answer))
            nodes = following + nodes[2:] if args.first_wins else following
    (root / "FINAL.txt").write_text(nodes[0][1], encoding="utf-8")
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == value for p, value in hashes.items())
    (root / "originals-check.json").write_text(json.dumps({"unchanged": unchanged, "sha256": hashes}, indent=2), encoding="utf-8")
    assert unchanged, "Source files changed during experiment"
    print("COMPLETE. Originals unchanged.", flush=True)


if __name__ == "__main__":
    main()
