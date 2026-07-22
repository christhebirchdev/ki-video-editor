# services/analyst_quality.py
"""
Deterministische Qualitäts-Messwerte für den AI Video Analyst.

Warum: Claude sieht nur Text — ohne Messwerte wäre jede Bewertung von
Soundqualität, Schärfe oder Beleuchtung halluziniert. Diese Werte geben
der Bewertung eine echte Datenbasis.

Bild (OpenCV, auf den ohnehin extrahierten Keyframes):
- Schärfe: Laplacian-Varianz (hoch = scharf; <50 bei 768px deutlich unscharf)
- Helligkeit: Graustufen-Mittel 0–255 (Zielbereich grob 90–160)
- Kontrast: Graustufen-Standardabweichung (>40 ordentlich)

Audio (ffmpeg ebur128, EBU R128):
- Integrated Loudness (LUFS): Reels-Richtwert ~ -14 LUFS
- True Peak (dBFS): > -1 dBFS = Übersteuerungs-Gefahr
"""
import re
import subprocess
from pathlib import Path
from typing import Optional
from models.analyst import QualityMetrics

# Optimaler Lautheits-Bereich (LUFS) — EMPIRISCH aus Chris' zwei Referenzvideos (2026-07-18),
# nicht der geratene Streaming-Richtwert (-14). Single Source of Truth: der Prompt-Text (METRICS_GUIDE)
# baut die Schwelle aus diesen Konstanten. Neue Referenzen? Nur diese zwei Zahlen anfassen.
#   unten  = gerade so laut genug
#   oben   = kurz vor „zu laut" (aktuell nur Info, KEIN Fehler — sonst würde normal-lautes Material
#            wie LeopoldSchultz mit -18.8 fälschlich als „zu laut" markiert)
LOUDNESS_OPTIMAL_LOW = -33.8
LOUDNESS_OPTIMAL_HIGH = -28.2
# Erst DEUTLICH unter der Untergrenze ist der Ton wirklich zu leise (Puffer, damit die Grenze nicht
# auf 0.1 LUFS genau entscheidet). 3 LU ≈ knapp hörbarer Lautheitsunterschied.
LOUDNESS_TOO_QUIET = round(LOUDNESS_OPTIMAL_LOW - 3.0, 1)


def frame_metrics(frame_paths: list[Path]) -> dict:
    """Schärfe/Helligkeit/Kontrast über alle Keyframes (cv2 lazy importiert)."""
    import cv2

    sharp, bright, contrast = [], [], []
    for p in frame_paths:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        sharp.append(float(cv2.Laplacian(img, cv2.CV_64F).var()))
        bright.append(float(img.mean()))
        contrast.append(float(img.std()))
    if not sharp:
        return {"schaerfe_avg": 0.0, "schaerfe_min": 0.0, "helligkeit_avg": 0.0, "kontrast_avg": 0.0}
    return {
        "schaerfe_avg": round(sum(sharp) / len(sharp), 1),
        "schaerfe_min": round(min(sharp), 1),
        "helligkeit_avg": round(sum(bright) / len(bright), 1),
        "kontrast_avg": round(sum(contrast) / len(contrast), 1),
    }


def audio_metrics(video_path: Path) -> dict:
    """Lautheit via ffmpeg ebur128. Leere Werte wenn kein/kaputtes Audio."""
    empty = {"lufs_integrated": None, "loudness_range": None, "true_peak_db": None}
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video_path),
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception:
        return empty
    # WICHTIG: ebur128 loggt laufend Fortschritts-Zeilen (beginnen mit I: -70.0 LUFS).
    # Nur der Block NACH "Summary:" enthält die finalen Werte.
    stderr = out.stderr
    if "Summary:" not in stderr:
        return empty
    summary = stderr.rsplit("Summary:", 1)[1]
    def grab(pattern: str) -> Optional[float]:
        m = re.search(pattern, summary)
        return float(m.group(1)) if m else None
    lufs = grab(r"I:\s*(-?[\d.]+)\s*LUFS")
    if lufs is None:
        return empty
    return {
        "lufs_integrated": lufs,
        "loudness_range": grab(r"LRA:\s*(-?[\d.]+)\s*LU"),
        "true_peak_db": grab(r"Peak:\s*(-?[\d.]+)\s*dBFS"),
    }


def measure(video_path: Path, frame_paths: list[Path]) -> QualityMetrics:
    """Kombiniert Bild- und Audio-Messwerte."""
    return QualityMetrics(**frame_metrics(frame_paths), **audio_metrics(video_path))
