#!/usr/bin/env python3
"""Misst die Lautheit eines oder mehrerer Videos — mit DERSELBEN Funktion, die der Analyst nutzt.

    source venv/bin/activate
    python tools/audio_messwerte.py leise_referenz.mp4 laute_referenz.mp4

Zweck: den optimalen Lautheits-Bereich empirisch festlegen, statt zu raten.
Nimm zwei Referenzvideos — eines gerade so laut genug, eines kurz vor „zu laut" — und übernimm die
gemessenen LUFS-Werte als untere/obere Grenze.

Wichtig: importiert `audio_metrics` aus dem Service → die Zahlen sind byte-genau die, die später in
der echten Bewertung stehen. Ein separat nachgebautes ffmpeg würde leicht abweichen und den Bereich
unbrauchbar machen.

Die drei Werte:
  - Integrated Loudness (LUFS): der wahrgenommene Durchschnitts-Pegel. DAS ist „zu leise/zu laut".
  - True Peak (dBFS):           die lauteste Einzelspitze. > -1 dBFS = Übersteuerungs-/Clipping-Gefahr.
  - Loudness Range (LU):        Dynamik (Unterschied leise↔laut). Nur zur Info.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Projekt-Root importierbar
from services.analyst_quality import audio_metrics


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    zeilen = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"  ✗ nicht gefunden: {arg}")
            continue
        m = audio_metrics(p)
        if m["lufs_integrated"] is None:
            print(f"  ✗ kein/kaputtes Audio: {p.name}")
            continue
        zeilen.append((p.name, m))

    if not zeilen:
        sys.exit("Keine messbaren Videos.")

    print(f"\n  {'Datei':<34} {'LUFS':>8} {'TruePeak':>10} {'LRA':>7}")
    print("  " + "-" * 61)
    for name, m in zeilen:
        print(f"  {name[:34]:<34} {m['lufs_integrated']:>8} "
              f"{m['true_peak_db']:>10} {m['loudness_range']:>7}")

    if len(zeilen) >= 2:
        lufs = sorted(m["lufs_integrated"] for _, m in zeilen)
        print("\n  → Vorschlag optimaler Bereich (LUFS):")
        print(f"       unten (gerade laut genug): {lufs[0]}")
        print(f"       oben  (kurz vor zu laut):  {lufs[-1]}")
        peaks = [m["true_peak_db"] for _, m in zeilen if m["true_peak_db"] is not None]
        if peaks and max(peaks) > -1.0:
            print(f"  ⚠ True Peak {max(peaks)} dBFS liegt über -1 → das laute Video ist nah am Clipping.")
    print()


if __name__ == "__main__":
    main()
