# services/audio_truth.py
"""
V4-only: Lokale, deterministische Audio-Wahrheit (Silero VAD + RMS-Energie).

Ersetzt die Gemini-Audio-Filler-Rolle durch zwei lokale Signale:

1. PAUSE-FILLER: Sprache (VAD) in einer Pause zwischen zwei Whisper-Wörtern.
   Whisper hat dort nichts transkribiert → wenn Silero dort trotzdem Stimme
   hört, ist das praktisch immer ein Filler ("äh" in der Pause) oder ein
   Ansatz-Abbruch. → Filler-Zone.

2. TAIL-TRIM: Whisper merged Filler in lange Wörter ("Hand…äh" = 1360ms).
   Statt der reinen Zeichen-Schätzung (V2-Heuristik) suchen wir per
   RMS-Energie den echten Einbruch IM Wort: [Wort][kurze Stille][Filler].
   Liegt so ein Muster vor → Zone von Stille-Beginn bis Wort-Ende.
   Damit wird auch der "jemand," 840ms/1.33×-Fall trennbar, den die
   Faktor-Schwelle nicht greifen darf.

Alles 100% lokal + deterministisch:
- Silero VAD ist in faster-whisper enthalten (onnxruntime, bereits im venv)
- decode_audio nutzt PyAV (bereits im venv), liest Video direkt
- Ergebnis wird pro Projekt gecacht (v4_audio_truth.json), Key = Datei-Größe+mtime

WICHTIG: Alle Funktionen liefern bei Fehlern/fehlendem Audio leere Listen —
der V4-Code-Pfad funktioniert auch mit 0 erkannten Zonen.
"""
import json
from pathlib import Path
from typing import Optional

import numpy as np

from models.analysis import WhisperWord

SAMPLE_RATE = 16000
FRAME_SEC = 0.01                      # RMS-Auflösung: 10ms-Frames
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SEC)

# --- Pause-Filler-Detection ---
GAP_MIN_SEC = 0.20                    # Pausen unter 200ms werden nicht analysiert
GAP_EDGE_MARGIN_SEC = 0.04            # 40ms Rand der Pause ignorieren (Whisper-Grenzen jittern)
GAP_SPEECH_MIN_SEC = 0.06             # ≥60ms VAD-Sprache in der Pause = Filler
GAP_ZONE_PAD_SEC = 0.03               # Zone leicht aufpolstern (leise Filler-Ränder)

# --- Tail-Trim (Energie-basiert) ---
TAIL_MIN_WORD_SEC = 0.55              # nur Wörter ab 550ms analysieren
TAIL_FACTOR_MIN = 1.25                # ab 1.25× erwarteter Dauer verdächtig (V2-Hard liegt bei 1.7×)
TAIL_HEAD_PROTECT_SEC = 0.15          # erste 150ms des Wortes nie anschneiden
TAIL_SILENCE_MIN_SEC = 0.10           # interner Energie-Einbruch ≥100ms
TAIL_VOICED_AFTER_MIN_SEC = 0.06      # danach ≥60ms Stimme bis Wort-Ende (= der Filler)
TAIL_REL_THRESHOLD = 0.18             # "leise" = unter 18% der Wort-Spitzenenergie (p90)
TAIL_NOISE_FLOOR_MULT = 2.0           # …aber mindestens 2× globaler Noise-Floor
# V4.2: Schutz gegen False-Positives (Testreel 4.0: 'ich'/'du'-Stummel, abgeschnittene Sätze)
TAIL_RELEASE_SEC = 0.06               # Schnitt 60ms NACH Dip-Beginn (Konsonanten-Ausklang schützen)
TAIL_MAX_FILLER_SEC = 0.70            # Post-Dip-Segment >700ms = vermutlich echtes Wort gemerged → kein Trim
TAIL_FILLER_ENERGY_RATIO = 0.55       # Filler muss leiser sein als 55% des Wortkerns, sonst kein Trim

AUDIO_TRUTH_VERSION = 2               # bei Parameter-Änderungen hochzählen → Cache invalidiert


# ===== Low-Level (lazy imports: faster_whisper nur bei echter Audio-Analyse) =====

def _decode(video_path: Path) -> np.ndarray:
    from faster_whisper.audio import decode_audio
    return decode_audio(str(video_path), sampling_rate=SAMPLE_RATE)


def _vad_segments(audio: np.ndarray) -> list[tuple[float, float]]:
    """Silero-VAD-Sprachsegmente in Sekunden. Deterministisch bei gleichem Input."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    opts = VadOptions(
        threshold=0.5,
        min_speech_duration_ms=50,    # auch kurze "äh"s fangen
        min_silence_duration_ms=60,   # feine Segmentierung (Default 2000ms wäre viel zu grob)
        speech_pad_ms=0,              # wir padden selbst kontrolliert
    )
    segs = get_speech_timestamps(audio, opts)
    return [(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE) for s in segs]


def _rms_per_frame(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME_SAMPLES
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[: n * FRAME_SAMPLES].reshape(n, FRAME_SAMPLES)
    return np.sqrt((frames * frames).mean(axis=1))


def _merge_zones(zones: list[tuple[float, float]], min_gap: float = 0.02) -> list[tuple[float, float]]:
    if not zones:
        return []
    zones = sorted(zones)
    merged = [zones[0]]
    for s, e in zones[1:]:
        ls, le = merged[-1]
        if s <= le + min_gap:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


# ===== Signal 1: Filler in Pausen zwischen Wörtern =====

def detect_pause_fillers(
    words: list[WhisperWord],
    vad_segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """
    Pure Funktion (testbar ohne Audio): Pausen zwischen Whisper-Wörtern gegen
    VAD-Sprachsegmente prüfen. Sprache in der Pause = Filler-Zone.
    """
    zones: list[tuple[float, float]] = []
    for a, b in zip(words, words[1:]):
        gap_start, gap_end = a.end, b.start
        if gap_end - gap_start < GAP_MIN_SEC:
            continue
        inner_start = gap_start + GAP_EDGE_MARGIN_SEC
        inner_end = gap_end - GAP_EDGE_MARGIN_SEC
        for vs, ve in vad_segments:
            ov_start = max(vs, inner_start)
            ov_end = min(ve, inner_end)
            if ov_end - ov_start >= GAP_SPEECH_MIN_SEC:
                zones.append((
                    max(gap_start, ov_start - GAP_ZONE_PAD_SEC),
                    min(gap_end, ov_end + GAP_ZONE_PAD_SEC),
                ))
    return _merge_zones(zones)


# ===== Signal 2: Energie-basierter Tail-Trim in verdächtigen Wörtern =====

def detect_tail_trims(
    words: list[WhisperWord],
    rms: np.ndarray,
    estimate_fn,
) -> list[tuple[float, float]]:
    """
    Für Wörter mit Dauer ≥550ms und Faktor ≥1.25× (erwartete Dauer via estimate_fn,
    d. h. estimated_max_word_duration aus cut_engine_v2):
    Suche IM Wort-Zeitfenster das Muster [Stimme][Stille ≥100ms][Stimme bis Ende].
    Gefunden → Zone (Stille-Beginn, Wort-Ende) = der eingeschluckte Filler.

    Pure Funktion über rms-Array (testbar mit synthetischem Signal).
    """
    if len(rms) == 0:
        return []
    noise_floor = float(np.percentile(rms, 5))
    zones: list[tuple[float, float]] = []

    head_frames = int(TAIL_HEAD_PROTECT_SEC / FRAME_SEC)
    silence_frames = int(TAIL_SILENCE_MIN_SEC / FRAME_SEC)
    voiced_after_frames = int(TAIL_VOICED_AFTER_MIN_SEC / FRAME_SEC)

    for w in words:
        duration = w.end - w.start
        if duration < TAIL_MIN_WORD_SEC:
            continue
        expected = estimate_fn(w.word)
        if expected <= 0 or duration / expected < TAIL_FACTOR_MIN:
            continue

        i0 = max(0, int(round(w.start / FRAME_SEC)))
        i1 = min(len(rms), int(round(w.end / FRAME_SEC)))
        seg = rms[i0:i1]
        if len(seg) < head_frames + silence_frames + voiced_after_frames:
            continue

        threshold = max(
            noise_floor * TAIL_NOISE_FLOOR_MULT,
            float(np.percentile(seg, 90)) * TAIL_REL_THRESHOLD,
        )
        quiet = seg < threshold

        # Stille-Runs finden (zusammenhängende quiet-Frames)
        runs: list[tuple[int, int]] = []   # (start_idx, end_idx) exklusiv
        run_start: Optional[int] = None
        for i, q in enumerate(quiet):
            if q and run_start is None:
                run_start = i
            elif not q and run_start is not None:
                runs.append((run_start, i))
                run_start = None
        if run_start is not None:
            runs.append((run_start, len(seg)))

        # Letzten gültigen Run nehmen: lang genug, nicht im Wortkopf,
        # und danach kommt nochmal Stimme (= der Filler) bis zum Wort-Ende.
        max_filler_frames = int(TAIL_MAX_FILLER_SEC / FRAME_SEC)
        for rs, re_ in reversed(runs):
            if re_ - rs < silence_frames:
                continue
            if rs < head_frames:
                continue
            after = seg[re_:]
            if len(after) < voiced_after_frames:
                continue  # Stille reicht bis Wort-Ende → kein Filler dahinter
            if float(np.mean(after)) <= threshold:
                continue  # danach bleibt es leise → kein Filler
            # 🆕 V4.2: False-Positive-Schutz (sonst werden gemergte ECHTE Wörter abgeschnitten)
            if len(after) > max_filler_frames:
                continue  # Post-Dip >700ms → vermutlich echtes Folgewort, kein Filler
            core_energy = float(np.percentile(seg[:rs], 90)) if rs > 0 else 0.0
            if core_energy > 0 and float(np.mean(after)) > core_energy * TAIL_FILLER_ENERGY_RATIO:
                continue  # Post-Dip fast so laut wie Wortkern → vermutlich echtes Wort
            # 🆕 V4.2: Release — 60ms nach Dip-Beginn schneiden (Konsonant klingt aus)
            zone_start = min(w.start + rs * FRAME_SEC + TAIL_RELEASE_SEC, w.end - 0.02)
            zones.append((zone_start, w.end))
            break

    return _merge_zones(zones)


# ===== Orchestrierung + Cache =====

def build_audio_truth(
    video_path: Path,
    words: list[WhisperWord],
    estimate_fn,
    cache_path: Optional[Path] = None,
) -> dict:
    """
    Liefert {"pause_filler_zones": [...], "tail_trim_zones": [...], "vad_segment_count": int}.
    Cache-Key: Video-Größe + mtime + AUDIO_TRUTH_VERSION → Re-Plan kostet 0s.
    Bei jedem Fehler: leere Zonen (Pipeline läuft weiter, wie mit 0 Filler).
    """
    empty = {"pause_filler_zones": [], "tail_trim_zones": [], "vad_segment_count": 0}
    try:
        stat = video_path.stat()
        cache_key = {
            "src_size": stat.st_size,
            "src_mtime": int(stat.st_mtime),
            "version": AUDIO_TRUTH_VERSION,
        }
        if cache_path is not None and cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("cache_key") == cache_key:
                print(f"  [AUDIO-TRUTH] Cache-Hit ({cache_path.name})")
                return cached["truth"]

        print(f"  [AUDIO-TRUTH] Analysiere {video_path.name} (VAD + RMS, lokal)…")
        audio = _decode(video_path)
        vad = _vad_segments(audio)
        rms = _rms_per_frame(audio)

        truth = {
            "pause_filler_zones": [list(z) for z in detect_pause_fillers(words, vad)],
            "tail_trim_zones": [list(z) for z in detect_tail_trims(words, rms, estimate_fn)],
            "vad_segment_count": len(vad),
        }
        print(
            f"  [AUDIO-TRUTH] ✓ {len(truth['pause_filler_zones'])} Pause-Filler, "
            f"{len(truth['tail_trim_zones'])} Tail-Trims, {len(vad)} VAD-Segmente"
        )
        if cache_path is not None:
            cache_path.write_text(json.dumps({"cache_key": cache_key, "truth": truth}, indent=2))
        return truth
    except Exception as e:
        print(f"  [AUDIO-TRUTH] WARN: Analyse fehlgeschlagen ({type(e).__name__}: {e}) — fahre mit 0 Zonen fort")
        return empty
