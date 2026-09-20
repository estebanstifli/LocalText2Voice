"""B for the whole book, then append-only character additions. No project writes."""
from datetime import datetime
import argparse
import hashlib
import json
from pathlib import Path
import re

import requests

from prepare_appearance_comparison import call


ADDITIONS = "Which characters appear in the second text but not in the first? List only those characters and their descriptions."


def sections(answer):
    """Separate the three report sections, not individual character entries."""
    result, current = {"characters": [], "changes": [], "summary": []}, None
    for line in answer.splitlines(keepends=True):
        heading = line.strip().strip("#* :").lower().split("(")[0].rstrip(" :")
        if heading in result:
            current = heading
        elif current:
            result[current].append(line)
    values = {k: "".join(v).strip() for k, v in result.items()}
    if any(not v for v in values.values()):
        raise ValueError("Review saved report: missing Characters, Changes or Summary section")
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--three", action="store_true", help="Fresh run with three balanced paragraph-aligned passages")
    args = parser.parse_args()
    source = Path("tmp/appearance-comparison/20260910-222219-724702")
    approved = source / "B-first-20260911-051912-984681"
    originals = [source / f"texto{i}.txt" for i in range(1, 6)] + [approved / "block1-combined-answer.txt"]
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
    prompt = (source / "B-combined-prompt.txt").read_text(encoding="utf-8")
    passages = [p.read_text(encoding="utf-8") for p in originals[:5]]
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256("".join(passages).encode()).hexdigest() == manifest["source_sha256"]
    if args.three:
        text = "".join(passages)
        boundaries = [m.end() for m in re.finditer(r"\n\s*\n", text)]
        if len(boundaries) < 2:
            raise ValueError("Not enough paragraph boundaries for three passages")
        first_cut = min(boundaries, key=lambda b: abs(b - len(text) / 3))
        second_cut = min((b for b in boundaries if first_cut < b < len(text)), key=lambda b: abs(b - 2 * len(text) / 3))
        passages = [text[:first_cut], text[first_cut:second_cut], text[second_cut:]]
        assert len(passages) == 3 and all(passages) and "".join(passages) == text
    target = source / (("B-three-" if args.three else "B-full-") + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    target.mkdir()
    print(f"OUTPUT: {target.resolve()}", flush=True)
    for i, passage in enumerate(passages, 1):
        (target / f"texto{i}.txt").write_text(passage, encoding="utf-8")
    (target / "input-sizes.json").write_text(json.dumps({"characters": [len(p) for p in passages],
        "source_sha256": manifest["source_sha256"], "num_ctx": 8192, "num_predict": 4096}, indent=2), encoding="utf-8")
    metrics, reports = [], []

    def status(state, detail):
        (target / "status.json").write_text(json.dumps({"status": state, "detail": detail,
            "metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")

    (target / "B-prompt.txt").write_text(prompt, encoding="utf-8")
    (target / "additions-prompt.txt").write_text(ADDITIONS, encoding="utf-8")
    try:
        start_index = 0
        if not args.three:
            status("running", "Reusing approved first-block answer")
            first = originals[-1].read_text(encoding="utf-8")
            (target / "resumen1.txt").write_text(first, encoding="utf-8")
            (target / "reused-first-block.json").write_text(json.dumps({"source": str(approved.resolve()),
                "metrics": json.loads((approved / "metrics.json").read_text(encoding="utf-8"))}, indent=2), encoding="utf-8")
            reports.append(sections(first))
            start_index = 1
        with requests.Session() as session:
            for i, passage in enumerate(passages[start_index:], start_index + 1):
                status("running", f"Phase 1: B block {i}/{len(passages)}")
                answer, metric = call(session, target, f"block{i}-combined", [
                    {"role": "user", "content": prompt + "\n\n" + passage}])
                metrics.append(metric)
                (target / f"resumen{i}.txt").write_text(answer, encoding="utf-8")
                reports.append(sections(answer))
            # Verbatim concatenation; no LLM rewrite of story or change evidence.
            for field, filename in (("summary", "HISTORIA_CONCATENADA.txt"), ("changes", "CAMBIOS_POR_TRAMO.txt")):
                (target / filename).write_text("\n\n".join(f"TRAMO {i}\n{r[field]}" for i, r in enumerate(reports, 1)), encoding="utf-8")
            accumulated = reports[0]["characters"]
            (target / "personajes-acumulados-1.txt").write_text(accumulated, encoding="utf-8")
            for i, report in enumerate(reports[1:], 2):
                status("running", f"Phase 2: additions from block {i}/{len(passages)}")
                question = ADDITIONS + "\n\nFIRST TEXT:\n" + accumulated + "\n\nSECOND TEXT:\n" + report["characters"]
                answer, metric = call(session, target, f"novedades-tramo{i}", [{"role": "user", "content": question}])
                metrics.append(metric)
                accumulated += "\n\n" + answer
                (target / f"personajes-acumulados-{i}.txt").write_text(accumulated, encoding="utf-8")
            (target / "PERSONAJES_FINAL.txt").write_text(accumulated, encoding="utf-8")
        status("completed", f"{len(passages)} source blocks ({start_index} reused), then {len(passages)-1} additions calls. Outputs are raw, not manually corrected.")
        print("COMPLETE", flush=True)
    except Exception as exc:
        status("failed", str(exc))
        raise
    finally:
        unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
        (target / "originals-check.json").write_text(json.dumps({"unchanged": unchanged, "sha256": hashes}, indent=2), encoding="utf-8")
        assert unchanged


if __name__ == "__main__":
    main()
