# services/analyst_frames.py
"""
Deterministische Frame-Extraktion + Dedup für den AI Video Analyst.

Ziel: bei jedem Lauf identische Frames + nichts verpassen. Wir ziehen dicht (FPS),
behalten jeden Frame, der sich vom letzten behaltenen sichtbar unterscheidet — auch bei
KLEINEN, LOKALEN Änderungen (z.B. Untertitel-Wortwechsel). Darum zwei Signale:
globaler Mittelwert (Schnitt/Setting-Wechsel) UND blockweises Maximum (lokale Textänderung).
"""
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

FPS = 2.0           # Sampling: alle 0,5s ein Frame
FRAME_MAX_DIM = 640
THUMB = 128         # Vergleichsauflösung
BLOCK = 8           # Blockgröße fürs lokale Signal (klein = empfindlich für kleine Textänderungen)
GLOBAL_MAD = 7.0    # mittlere abs. Differenz (0–255) → Schnitt/Setting-Wechsel
BLOCK_MAD = 18.0    # max. Differenz über BLOCKxBLOCK-Blöcke → lokale Änderung (Text taucht auf/wechselt)
MAX_FRAMES = 120    # Sicherheits-Cap (lange Videos) → gleichmäßig ausdünnen


def probe_duration(video_path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def extract_frames(video_path: Path, out_dir: Path, fps: float = FPS) -> list[tuple[float, Path]]:
    """Zieht Frames mit fester Rate. Zeitstempel = i/fps. Deterministisch."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = (
        f"fps={fps},scale='trunc(iw*min(1,{FRAME_MAX_DIM}/max(iw,ih))/2)*2'"
        f":'trunc(ih*min(1,{FRAME_MAX_DIM}/max(iw,ih))/2)*2'"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-i", str(video_path), "-vf", vf, "-q:v", "3",
         str(out_dir / "f_%05d.jpg")],
        capture_output=True, check=True, stdin=subprocess.DEVNULL,
    )
    files = sorted(out_dir.glob("f_*.jpg"))
    return [(i / fps, p) for i, p in enumerate(files)]


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    """Mittlere absolute Differenz (global)."""
    return float(np.mean(np.abs(a - b)))


def _block_max(a: np.ndarray, b: np.ndarray) -> float:
    """Größte mittlere Differenz über BLOCKxBLOCK-Blöcke — fängt LOKALE Änderungen (Text)."""
    diff = np.abs(a - b)
    n = THUMB // BLOCK
    return float(diff.reshape(n, BLOCK, n, BLOCK).mean(axis=(1, 3)).max())


def _changed(a: np.ndarray, b: np.ndarray) -> bool:
    return _mad(a, b) >= GLOBAL_MAD or _block_max(a, b) >= BLOCK_MAD


def _thumb(path: Path):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (THUMB, THUMB)).astype("float32")


def dedup(frames: list[tuple[float, Path]]) -> list[tuple[float, Path]]:
    """Behält den ersten Frame + jeden, der sich vom letzten behaltenen sichtbar unterscheidet."""
    keep: list[tuple[float, Path]] = []
    last = None
    for ts, p in frames:
        t = _thumb(p)
        if t is None:
            continue
        if last is None or _changed(t, last):
            keep.append((ts, p))
            last = t
    return _cap(keep)


def _cap(frames: list[tuple[float, Path]], max_frames: int = MAX_FRAMES) -> list[tuple[float, Path]]:
    if len(frames) <= max_frames:
        return frames
    step = len(frames) / max_frames
    return [frames[int(i * step)] for i in range(max_frames)]


if __name__ == "__main__":  # ponytail: Selbstcheck der Dedup-Entscheidung (ohne ffmpeg/cv2-I/O)
    base = np.zeros((THUMB, THUMB), "float32")
    assert _changed(base, base) is False
    big = np.full((THUMB, THUMB), 200, "float32")
    assert _changed(base, big) is True                      # globaler Wechsel
    local = base.copy(); local[100:120, 10:120] = 255       # kleine Textzeile unten
    assert _changed(base, local) is True, "lokale Textänderung muss erkannt werden"
    word = base.copy(); word[104:116, 40:72] = 255           # ein kurzes Wort (~12x32 px)
    assert _changed(base, word) is True, "kleine Wort-Änderung muss erkannt werden"
    print("analyst_frames self-check OK")
