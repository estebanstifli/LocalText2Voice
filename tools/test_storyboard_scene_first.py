"""Run the production scene-first planner against the saved story; no project writes."""
import json
import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.video_storyboard_planner import plan_video_storyboard


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    root = Path("tmp/scene-first") / datetime.now().strftime("%Y%m%d-%H%M%S")
    root.mkdir(parents=True)
    text = Path("tmp/storyboard-conversation/20260909-181703-131856/original-story.txt").read_text(encoding="utf-8")
    log = Path("projects/Project1/storyboard/debug/analysis-requests-20260908-135127-170798.txt").read_text(encoding="utf-8")
    units = {}
    for match in re.finditer(r"^REQUEST \d+: continuity discovery block[^\n]+", log, re.M):
        request, _ = json.JSONDecoder().raw_decode(log[log.index("{", match.end()):])
        prompt = request["messages"][-1]["content"]
        rows, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("["):])
        units.update({u["unit"]: u for u in rows})
    cues = []
    for unit in units.values():
        start, end = unit["time"].rstrip("s").split("-")
        cues.append({"text": unit["text"], "start_seconds": float(start), "end_seconds": float(end)})
    source = {"title": "Three little pigs - isolated test", "text": text, "duration_seconds": 371.91,
              "voice_start_offset_seconds": 2, "narration_cues": cues}
    settings = {"llm_provider": "ollama", "ollama": {"model": "qwen3:8b", "context_length": 8192},
                "analysis": {"max_block_characters": 12000, "max_output_tokens": 8096}}
    print(root.resolve(), flush=True)
    with (root / "trace.jsonl").open("w", encoding="utf-8") as log_file:
        def trace(event):
            log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            log_file.flush()
            if event.get("kind") in {"status", "request", "response", "warning"}:
                print(event.get("message") or event.get("label"), flush=True)
        def partial(value):
            (root / "partial.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        plan = plan_video_storyboard(source, settings, trace=trace, partial=partial)
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", len(plan["scenes"]), "frames", len(plan["continuity"]["characters"]), "characters", flush=True)


if __name__ == "__main__":
    main()
