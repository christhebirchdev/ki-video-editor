#!/usr/bin/env python3
"""Beweist oder widerlegt, ob unsere Whisper-Einstellungen deterministisch sind.

    source venv/bin/activate
    python tools/whisper_determinismus_test.py analyst_runs/d3b7e068/raw/LeopoldSchultz02.mp4

Transkribiert DIESELBE Datei N-mal mit den EXAKTEN Einstellungen aus whisper_service.py
und vergleicht Wortanzahl, Pausen und Wortlaut. Danach dasselbe mit temperature=0.0 gepinnt.

Hintergrund: faster-whisper setzt per Default temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0].
Bei 0.0 wird greedy dekodiert (deterministisch). Reißt ein Segment aber die Schwelle
compression_ratio_threshold (2.4) oder log_prob_threshold (-1.0), wird mit temperature 0.2,
dann 0.4 usw. NEU dekodiert — und Sampling bei t>0 ist stochastisch und ungeseedet.
Zusätzlich ist condition_on_previous_text=True Default: eine einzige Abweichung früh im
Video verändert alles danach.
"""
import subprocess
import sys
from pathlib import Path

N = 3
BASIS = dict(language="de", word_timestamps=True, vad_filter=True, beam_size=1)


def lauf(modell, video: str, **extra) -> dict:
    segs, _ = modell.transcribe(video, **BASIS, **extra)
    woerter = [w for s in segs if s.words for w in s.words]
    luecken = [
        (round(b.start - a.end, 2), round(a.end, 2))
        for a, b in zip(woerter, woerter[1:])
        if b.start - a.end > 0.5
    ]
    return {
        "n": len(woerter),
        "text": " ".join(w.word.strip() for w in woerter),
        "pausen": luecken,
    }


def pruefe(titel: str, modell, video: str, **extra) -> bool:
    print(f"\n{'=' * 74}\n{titel}\n{'=' * 74}")
    ergebnisse = [lauf(modell, video, **extra) for _ in range(N)]
    for i, e in enumerate(ergebnisse, 1):
        pausen = ", ".join(f"{d}s@{t}s" for d, t in e["pausen"]) or "keine"
        print(f"  Lauf {i}: {e['n']} Wörter | Pausen >0.5s: {pausen}")
    gleich = len({e["text"] for e in ergebnisse}) == 1
    print(f"\n  → {'✓ DETERMINISTISCH' if gleich else '✗ NICHT DETERMINISTISCH — Wortlaut weicht ab!'}")
    if not gleich:
        for i, e in enumerate(ergebnisse, 1):
            print(f"     [{i}] {e['text'][:150]}…")
    return gleich


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    video = sys.argv[1]
    if not Path(video).exists():
        sys.exit(f"Nicht gefunden: {video}")

    from faster_whisper import WhisperModel
    print(f"Lade Modell 'small' (wie config.py) … Datei: {video}")
    print(f"md5: {subprocess.run(['md5', '-q', video], capture_output=True, text=True).stdout.strip()}")
    modell = WhisperModel("small", device="cpu", compute_type="int8")

    jetzt_ok = pruefe(f"A) AKTUELLE Einstellungen ({N}× dieselbe Datei)", modell, video)
    gepinnt_ok = pruefe(
        f"B) MIT temperature=0.0 gepinnt, ohne Fallback-Leiter ({N}×)",
        modell, video, temperature=0.0, condition_on_previous_text=False,
    )

    print(f"\n{'=' * 74}\nFAZIT\n{'=' * 74}")
    if jetzt_ok and gepinnt_ok:
        print("  Beide deterministisch → die Ursache liegt NICHT in dieser Session.")
        print("  Verdacht dann: anderer Prozess/andere Modellversion beim alten Lauf.")
    elif not jetzt_ok and gepinnt_ok:
        print("  BEWIESEN: Die Default-temperature-Leiter ist die Ursache.")
        print("  Fix: temperature=0.0 in whisper_service.transcribe_with_word_timestamps().")
    elif not jetzt_ok and not gepinnt_ok:
        print("  Auch gepinnt instabil → tiefer liegende Quelle (VAD/int8/Threading).")
        print("  Nächster Test: vad_filter=False, dann compute_type='float32'.")
    else:
        print("  Unerwartet: aktuell stabil, gepinnt nicht. Bitte Output oben prüfen.")
    print(f"\n  ACHTUNG n={N}: Abweichung beweist Instabilität. Übereinstimmung beweist NICHTS —")
    print("  drei gleiche Läufe können Zufall sein. Für ein echtes Urteil N auf 10 hochsetzen.")


if __name__ == "__main__":
    main()
