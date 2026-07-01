#!/usr/bin/env python3
# scripts/v4_audio_report.py
"""
V4-Audio-Wahrheit-Report — ohne API-Call, ohne Render.

Lädt analysis.json + Raw-Video eines Projekts, führt die lokale
Audio-Analyse (Silero VAD + RMS) aus und listet:
- erkannte Pause-Filler (Sprache in Whisper-Pausen) mit Kontext-Wörtern
- erkannte Tail-Trims (Energie-Einbruch in langen Wörtern)

Damit kannst du die Erkennung gegen deine bekannten Stellen abgleichen
("Hand"+Pause-Filler, "jemand" 840ms), BEVOR du einen Cut renderst.

Aufruf (aus dem ki-video-editor-Verzeichnis, venv aktiv):
    python scripts/v4_audio_report.py 534f7f32
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.analysis import VideoAnalysis  # noqa: E402
from services.cut_engine_v2 import estimated_max_word_duration  # noqa: E402
from services import audio_truth  # noqa: E402

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/v4_audio_report.py <project_id>")
        sys.exit(1)
    project_id = sys.argv[1]
    project_path = Path("projects") / project_id

    analysis = VideoAnalysis(**json.loads((project_path / "analysis.json").read_text()))
    videos = sorted(
        f for f in (project_path / "raw").iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        print(f"Kein Raw-Video in {project_path / 'raw'}")
        sys.exit(1)

    words = analysis.whisper_words
    print(f"Projekt {project_id}: {len(words)} Wörter, {analysis.duration_sec:.1f}s\n")

    audio = audio_truth._decode(videos[0])
    vad = audio_truth._vad_segments(audio)
    rms = audio_truth._rms_per_frame(audio)
    print(f"VAD: {len(vad)} Sprachsegmente\n")

    # --- Pause-Filler ---
    zones = audio_truth.detect_pause_fillers(words, vad)
    print(f"=== {len(zones)} PAUSE-FILLER (Sprache in Whisper-Pausen) ===")
    for zs, ze in zones:
        before = next((w.word for w in reversed(words) if w.end <= zs + 0.01), "?")
        after = next((w.word for w in words if w.start >= ze - 0.01), "?")
        print(f"  {zs:7.2f}s – {ze:7.2f}s  ({(ze - zs) * 1000:.0f}ms)   …{before} [FILLER] {after}…")

    # --- Tail-Trims ---
    trims = audio_truth.detect_tail_trims(words, rms, estimated_max_word_duration)
    print(f"\n=== {len(trims)} TAIL-TRIMS (Energie-Einbruch in langen Wörtern) ===")
    for zs, ze in trims:
        w = next((w for w in words if w.start <= zs and w.end >= ze - 0.05), None)
        if w:
            dur = w.end - w.start
            factor = dur / estimated_max_word_duration(w.word)
            print(f"  '{w.word}' {w.start:.2f}s–{w.end:.2f}s ({dur * 1000:.0f}ms, Faktor {factor:.2f}×)"
                  f" → Schnitt bei {zs:.2f}s (spart {(ze - zs) * 1000:.0f}ms Filler)")
        else:
            print(f"  {zs:.2f}s – {ze:.2f}s")

    # --- Verdächtige Wörter, die NICHT getrimmt wurden (Transparenz) ---
    print("\n=== Verdächtige Wörter ohne Energie-Einbruch (bleiben unangetastet) ===")
    for w in words:
        dur = w.end - w.start
        exp = estimated_max_word_duration(w.word)
        factor = dur / exp if exp > 0 else 0
        if dur >= audio_truth.TAIL_MIN_WORD_SEC and factor >= audio_truth.TAIL_FACTOR_MIN:
            if not any(w.start <= zs < w.end for zs, _ in trims):
                print(f"  '{w.word}' {dur * 1000:.0f}ms, Faktor {factor:.2f}×")


if __name__ == "__main__":
    main()
