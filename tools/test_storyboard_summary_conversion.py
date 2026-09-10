"""Exercise the small production converters with saved free answers (no project writes)."""
import json
from datetime import datetime
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p


def main():
    sys.stdout.reconfigure(errors="backslashreplace")
    saved = Path("tmp/scene-first/20260909-193412/plan.json")
    report = json.loads(saved.read_text(encoding="utf-8"))["continuity"]["discovery_reports"][0]
    output = Path("tmp/summary-conversion") / datetime.now().strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    settings = {"ollama": {"model": "qwen3:8b", "context_length": 8192},
                "analysis": {"max_output_tokens": 8096}}
    print(output.resolve(), flush=True)
    with (output / "trace.jsonl").open("w", encoding="utf-8") as log:
        def trace(event):
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
            if event.get("kind") in {"request", "response"}:
                print(event.get("label"), flush=True)
        started = time.monotonic()
        characters = p._request_plan(settings, c.CHARACTER_SCHEMA, c.PROFILE_INSTRUCTIONS["characters"],
            report["characters"] + "\n\n" + report["appearance"], trace=trace,
            request_label="convert characters", output_tokens_hint=8096)
        question = c.LOCATIONS + "\n\n" + report["scenes"]
        places = p._request_free_text(settings, "", question, messages=[{"role": "user", "content": question}],
            trace=trace, request_label="place summary", output_tokens_hint=8096)
        locations = p._request_plan(settings, c.LOCATION_SCHEMA, c.PROFILE_INSTRUCTIONS["locations"], places,
            trace=trace, request_label="convert locations", output_tokens_hint=8096)
        scenes = p._request_plan(settings, c.SCENE_SCHEMA,
            "Convert these proposed scenes to JSON, in the same order. Copy each title and start sentence exactly. Do not invent or rewrite scenes or quotes.",
            report["scenes"], trace=trace, request_label="convert scenes", output_tokens_hint=8096)
    result = {"seconds": round(time.monotonic() - started, 2), "characters": characters,
              "place_summary": places, "locations": locations, "scenes": scenes,
              "quote_matches": [len(c.quote_offsets(report["text"], s["start_quote"])) for s in scenes["scenes"]]}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
