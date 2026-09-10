"""Replay the empty scene-8 reply, then recover its four real intervals with Ollama."""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import storyboard_conversation as c
from app.core import video_storyboard_planner as p


def main():
    root = Path("tmp/visual-recovery") / datetime.now().strftime("%Y%m%d-%H%M%S")
    root.mkdir(parents=True)
    log = Path(sys.argv[1]).read_text(encoding="utf-8")
    position = log.index("REQUEST 15: conversation: image prompts 8/12")
    raw, _ = json.JSONDecoder().raw_decode(log[log.index("{", position):])
    data = json.loads(raw["messages"][-1]["content"])
    settings = {"ollama": {"model": raw["model"], "context_length": raw["options"]["num_ctx"]},
                "analysis": {"max_output_tokens": 8096}}
    calls = 0
    warnings = []
    print(root.resolve(), flush=True)
    with (root / "trace.jsonl").open("w", encoding="utf-8") as output:
        def trace(event):
            output.write(json.dumps(event, ensure_ascii=False) + "\n")
            output.flush()
        def request(schema, instruction, payload, label):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"scenes": []}  # Exact successful-but-empty reply from the user's log.
            print(label, flush=True)
            return p._request_plan(settings, schema, instruction, json.dumps(payload, ensure_ascii=False),
                                   trace=trace, request_label=label, output_tokens_hint=8096)
        rows = c.request_visuals(request, data, raw["messages"][0]["content"],
                                 "scene 8/12", warnings.append, lambda: None)
    (root / "result.json").write_text(json.dumps({"scenes": rows, "warnings": warnings},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Recovered {len(rows)} descriptions in {calls - 1} real calls.", flush=True)


if __name__ == "__main__":
    main()
