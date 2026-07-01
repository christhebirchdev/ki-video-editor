#!/usr/bin/env python3
# scripts/v4_visual_ablation.py
"""
Visual-Ablations-Test: Was kostet uns der Wegfall von Gemini-Visual?

Plant denselben V4-Cut zweimal:
  Variante A — analysis.json wie sie ist (mit Gemini-Visual + Audio-Issues)
  Variante B — visual_phases UND audio_issues geleert (= Simulation "V5 ohne Gemini")

Dann wird gemessen:
  1. LEAK-CHECK (Kernfrage): Liegen die bekannten outtake_break-Zeitbereiche
     (aus dem Original-Gemini) im Variante-B-Cut drin? Jede Überlappung >100ms
     = ein Outtake, den nur Gemini-Visual gefangen hätte.
  2. Längen / Clip-Anzahl beider Varianten.
  3. Welche Sätze unterschiedlich behalten/verworfen wurden.

Kosten: 1 Claude-Call für Variante B (A kommt i.d.R. aus dem Selection-Cache).
KEIN Gemini-Call, KEIN Render. cut_plan.json wird NICHT angefasst —
Ergebnisse landen in projects/<id>/ablation_*.json.

Aufruf (venv aktiv):
    python scripts/v4_visual_ablation.py 534f7f32
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.analysis import VideoAnalysis  # noqa: E402
from services.cut_engine_v2 import group_into_sentences, assign_visual_quality  # noqa: E402
from services.cut_engine_v4 import plan_cuts_v4  # noqa: E402
from services.memory_service import load_memory  # noqa: E402

LEAK_THRESHOLD_SEC = 0.10  # >100ms Outtake-Material im Cut = Leak


def _clip_overlap(zones, clips) -> list[tuple[float, float, float]]:
    """Pro Zone: wie viel Sekunden davon stecken in den Clips? → [(zs, ze, overlap_sec)]"""
    result = []
    for zs, ze in zones:
        ov = 0.0
        for c in clips:
            ov += max(0.0, min(ze, c.end) - max(zs, c.start))
        result.append((zs, ze, ov))
    return result


def _kept_sentence_texts(analysis: VideoAnalysis, clips) -> list[str]:
    """Welche Sätze sind (teilweise) im Cut enthalten? Für den Vergleich A vs. B."""
    sentences = group_into_sentences(analysis.whisper_words)
    kept = []
    for s in sentences:
        for c in clips:
            if min(s.end_sec, c.end) - max(s.start_sec, c.start) > 0.2:
                kept.append(f"#{s.id}: {s.text[:60]}")
                break
    return kept


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/v4_visual_ablation.py <project_id>")
        sys.exit(1)
    project_id = sys.argv[1]
    project_path = Path("projects") / project_id

    original = json.loads((project_path / "analysis.json").read_text())
    analysis_a = VideoAnalysis(**original)

    ablated = dict(original)
    ablated["visual_phases"] = []
    ablated["audio_issues"] = []
    analysis_b = VideoAnalysis(**ablated)

    memory = load_memory()
    platform = "short_video"
    try:
        meta = json.loads((project_path / "meta.json").read_text())
        platform = meta.get("platform", platform)
    except Exception:
        pass

    print("=" * 70)
    print("VARIANTE A — mit Gemini-Visual (Ist-Zustand)")
    print("=" * 70)
    plan_a = plan_cuts_v4(analysis_a, memory, platform)

    print()
    print("=" * 70)
    print("VARIANTE B — OHNE Gemini (visual_phases + audio_issues leer = V5-Simulation)")
    print("=" * 70)
    plan_b = plan_cuts_v4(analysis_b, memory, platform)

    # Ergebnisse sichern (cut_plan.json bleibt unberührt)
    (project_path / "ablation_with_visual.json").write_text(plan_a.model_dump_json(indent=2))
    (project_path / "ablation_without_visual.json").write_text(plan_b.model_dump_json(indent=2))

    len_a = sum(c.end - c.start for c in plan_a.clips)
    len_b = sum(c.end - c.start for c in plan_b.clips)

    print()
    print("=" * 70)
    print("ERGEBNIS")
    print("=" * 70)
    print(f"A (mit Visual):  {len(plan_a.clips):2d} Clips, {len_a:5.1f}s")
    print(f"B (ohne Gemini): {len(plan_b.clips):2d} Clips, {len_b:5.1f}s")

    # 1) Leak-Check: Outtake-Zonen aus dem ORIGINAL-Gemini gegen Variante B
    outtake_zones = [
        (p["start_sec"], p["end_sec"])
        for p in original.get("visual_phases", [])
        if p.get("visual_quality") == "outtake_break"
    ]
    print(f"\n--- LEAK-CHECK: {len(outtake_zones)} bekannte outtake_break-Zonen ---")
    if not outtake_zones:
        print("  (Original-Analyse enthält keine outtake_break-Phasen — Check nicht möglich)")
    leaks = 0
    for zs, ze, ov in _clip_overlap(outtake_zones, plan_b.clips):
        status = "❌ LEAK" if ov > LEAK_THRESHOLD_SEC else "✅ draußen"
        if ov > LEAK_THRESHOLD_SEC:
            leaks += 1
        print(f"  Outtake {zs:6.2f}s–{ze:6.2f}s: {ov * 1000:5.0f}ms im B-Cut → {status}")
    if outtake_zones:
        print(f"\n  → {leaks}/{len(outtake_zones)} Outtake-Zonen leaken ohne Gemini-Visual.")
        if leaks == 0:
            print("  → Claude + lokale Audio-Zonen haben ALLE bekannten Outtakes auch ohne Visual entfernt.")
        else:
            print("  → Diese Stellen braucht V5 über lokale Detektoren (Restart/Abbruch) oder Bild-Signal.")

    # 2) Satz-Vergleich
    kept_a = set(_kept_sentence_texts(analysis_a, plan_a.clips))
    kept_b = set(_kept_sentence_texts(analysis_b, plan_b.clips))
    only_a = sorted(kept_a - kept_b)
    only_b = sorted(kept_b - kept_a)
    print(f"\n--- Satz-Unterschiede (A∩B: {len(kept_a & kept_b)} gemeinsam) ---")
    for t in only_a:
        print(f"  nur in A: {t}")
    for t in only_b:
        print(f"  nur in B: {t}")
    if not only_a and not only_b:
        print("  Keine — identische Satzauswahl.")

    print(f"\nPläne gespeichert: ablation_with_visual.json / ablation_without_visual.json")
    print("Hörtest: Pläne bei Bedarf manuell rendern (cut_plan.json wurde nicht verändert).")


if __name__ == "__main__":
    main()
