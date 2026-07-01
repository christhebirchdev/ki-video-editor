#!/usr/bin/env python3
# scripts/v5_outtake_eval.py
"""
Offline-Eval: Lokaler Outtake-Detektor (V5) vs. Gemini-outtake_break-Labels.

Nutzt alle vorhandenen Projekte mit analysis.json als Eval-Set — ohne API-Call,
ohne neue Aufnahme. Pro Projekt:
- Sätze gruppieren, dominantes Gemini-Visual-Label je Satz bestimmen (Ground Truth)
- Lokalen Detektor laufen lassen
- Vergleich: Treffer / verpasst (nur Gemini sah es) / zusätzlich (nur lokal)

"Verpasst" ist die kritische Spalte: das sind die Stellen, die V5 ohne
Gemini-Visual NICHT als Satz-Outtake erkennt. (Hinweis: "zusätzlich" ist nicht
automatisch falsch — der Detektor kann Restarts finden, die Gemini visuell
nicht als Outtake gewertet hat. Stichprobe anhören.)

Aufruf (venv aktiv):
    python scripts/v5_outtake_eval.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.analysis import VideoAnalysis  # noqa: E402
from services.cut_engine_v2 import group_into_sentences, assign_visual_quality  # noqa: E402
from services.outtake_detector import detect_outtake_sentences  # noqa: E402


def main():
    projects_dir = Path("projects")
    totals = {"gemini": 0, "hit": 0, "missed": 0, "extra": 0}
    seen_videos = set()

    for pdir in sorted(projects_dir.iterdir()):
        analysis_path = pdir / "analysis.json"
        if not analysis_path.exists():
            continue
        try:
            data = json.loads(analysis_path.read_text())
            analysis = VideoAnalysis(**data)
        except Exception as e:
            print(f"{pdir.name}: analysis.json nicht lesbar ({e}) — übersprungen")
            continue

        sentences = group_into_sentences(analysis.whisper_words)
        if not sentences:
            continue
        assign_visual_quality(sentences, analysis.visual_phases)

        gemini_outtakes = {s.id for s in sentences if s.visual_quality == "outtake_break"}
        flags = detect_outtake_sentences(sentences)
        local_outtakes = {f.sentence_id for f in flags}
        by_id = {f.sentence_id: f for f in flags}

        hits = gemini_outtakes & local_outtakes
        missed = gemini_outtakes - local_outtakes
        extra = local_outtakes - gemini_outtakes

        # Dedupe-Hinweis: viele Projekte nutzen dasselbe Video
        n_words = len(analysis.whisper_words)
        dup = " (gleiches Video wie zuvor)" if n_words in seen_videos else ""
        seen_videos.add(n_words)

        print(f"\n=== {pdir.name}: {len(sentences)} Sätze, "
              f"{len(gemini_outtakes)} Gemini-Outtakes{dup} ===")
        for sid in sorted(gemini_outtakes | local_outtakes):
            s = next(x for x in sentences if x.id == sid)
            if sid in hits:
                tag = f"✅ TREFFER ({by_id[sid].kind})"
            elif sid in missed:
                tag = "❌ VERPASST (nur Gemini-Visual)"
            else:
                tag = f"➕ zusätzlich lokal ({by_id[sid].kind}, {'Garantie' if by_id[sid].enforced else 'Flag'})"
            print(f"  Satz {sid} [{s.start_sec:.1f}s] {tag}: \"{s.text[:70]}\"")

        totals["gemini"] += len(gemini_outtakes)
        totals["hit"] += len(hits)
        totals["missed"] += len(missed)
        totals["extra"] += len(extra)

    print("\n" + "=" * 70)
    print(f"GESAMT: {totals['gemini']} Gemini-Outtake-Sätze | "
          f"{totals['hit']} lokal getroffen | {totals['missed']} verpasst | "
          f"{totals['extra']} zusätzlich lokal")
    if totals["gemini"]:
        recall = totals["hit"] / totals["gemini"]
        print(f"Recall gegen Gemini-Labels: {recall:.0%}")
        print("→ 'Verpasst'-Stellen im Rohvideo anhören: sind das echte (rein visuelle)")
        print("  Outtakes oder Gemini-Fehlklassifikationen?")


if __name__ == "__main__":
    main()
