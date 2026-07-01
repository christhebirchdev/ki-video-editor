# tests/test_cut_engine_v52.py
"""
Mini-Tests für die vier V5.2-Feinschliff-Regeln — ohne API, ohne Audio-Dateien.
Jede Regel ist aus einem konkreten Feedback-Punkt vom Testreel 5.0 abgeleitet.
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

import numpy as np  # noqa: E402

from models.analysis import WhisperWord, CutClip  # noqa: E402
from services.cut_engine_v52 import (  # noqa: E402
    resolve_inword_zones,
    protect_onsets,
    trim_trailing_murmur,
    compress_silences,
    merge_close_clips,
)


def _w(word, start, end):
    return WhisperWord(word=word, start=start, end=end)


# ===== Regel 1: Stille-Kompression =====

def test_silence_inside_clip_is_compressed():
    """Feedback 'Mini-Pause 3-4s': Stille in Wort-Spanne ('und' 980ms) wird komprimiert."""
    rms = np.full(1200, 0.002, dtype=np.float32)   # 12s
    rms[600:900] = 0.30                             # Sprache 6.0–9.0s
    rms[900:960] = 0.002                            # Stille 9.0–9.6s (600ms!)
    rms[960:995] = 0.30                             # Sprache 9.6–9.95s
    clips = [CutClip(start=6.04, end=9.95, reason="test")]
    out, splits = compress_silences(clips, rms, noise_floor=0.002)
    assert splits == 1
    assert len(out) == 2
    assert abs(out[0].end - 9.10) < 0.05     # Stille-Beginn + 100ms Rest
    assert abs(out[1].start - 9.54) < 0.05   # 60ms vor Stimm-Wiedereinsatz


def test_short_silence_not_compressed():
    """Natürliche Sprechpause (200ms) bleibt unangetastet — kein hektisches Zerhacken."""
    rms = np.full(1200, 0.30, dtype=np.float32)
    rms[700:720] = 0.002  # nur 200ms still
    clips = [CutClip(start=6.0, end=10.0, reason="test")]
    out, splits = compress_silences(clips, rms, noise_floor=0.002)
    assert splits == 0
    assert len(out) == 1


# ===== Regel 2: Onset-Guard =====

def test_onset_guard_restores_word_start():
    """Feedback 'mit abgeschnitten': Clip-Start hinter Wortbeginn → auf Wortbeginn−40ms."""
    words = [_w("herum,", 29.65, 29.97), _w("mit", 30.17, 30.39), _w("KI", 30.39, 30.69)]
    clips = [CutClip(start=30.18, end=33.78, reason="test")]   # Start 10ms IM Wort
    out, fixed = protect_onsets(clips, words)
    assert fixed == 1
    assert abs(out[0].start - (30.17 - 0.04)) < 0.005


def test_onset_guard_leaves_good_starts():
    """Clip-Start bereits vor Wortbeginn (normales Pre-Padding) → unverändert."""
    words = [_w("mit", 30.17, 30.39)]
    clips = [CutClip(start=30.05, end=33.0, reason="test")]
    out, fixed = protect_onsets(clips, words)
    assert fixed == 0
    assert out[0].start == 30.05


# ===== Regel 3: Zonen im Wort → Trim statt Drop =====

def test_inword_zone_becomes_tail_trim():
    """Feedback 'Hektik 22-23s': Verbatim-Äh im Wort-Schwanz → Tail-Trim, Wort bleibt."""
    words = [_w("produziert", 47.57, 48.93)]
    zones = [(47.86, 48.40)]   # Äh im Schwanz, 290ms Kopf bleibt erhalten
    filler, tails = resolve_inword_zones(zones, words, rms_tail_zones=[])
    assert filler == []
    assert len(tails) == 1
    assert abs(tails[0][0] - 47.86) < 0.01 and abs(tails[0][1] - 48.93) < 0.01


def test_rms_tail_trim_wins_over_verbatim():
    """RMS-Messung für das Wort vorhanden → Verbatim-Zone wird verworfen (präziser)."""
    words = [_w("produziert", 47.57, 48.93)]
    zones = [(47.86, 48.40)]
    filler, tails = resolve_inword_zones(zones, words, rms_tail_zones=[(48.56, 48.93)])
    assert filler == [] and tails == []


def test_zone_covering_word_stays_filler():
    """Zone deckt das ganze Wort (echtes Filler-Wort, z. B. als 'ähm' gehört) → Drop bleibt."""
    words = [_w("ähm,", 12.0, 12.4)]
    zones = [(11.98, 12.42)]
    filler, tails = resolve_inword_zones(zones, words, rms_tail_zones=[])
    assert len(filler) == 1 and tails == []


def test_zone_in_pause_stays_filler():
    """Zone ohne Wort-Überlappung (Pause-Filler) → bleibt normale Filler-Zone."""
    words = [_w("Hand,", 49.09, 49.33), _w("dann", 50.6, 50.8)]
    zones = [(49.6, 50.1)]
    filler, tails = resolve_inword_zones(zones, words, rms_tail_zones=[])
    assert len(filler) == 1 and tails == []


# ===== Regel 4: Murmur-Trim am Clip-Ende =====

def test_trailing_murmur_trimmed():
    """Feedback 'äh bei 22s': leises Gemurmel nach kurzem Dip am Clip-Ende → weg.

    Nachbau des echten 'jemand,'-Falls: 830ms (Faktor 1.32×), Dip nur 70ms
    (zu kurz für den globalen Tail-Trim mit 100ms), danach leises Äh.
    """
    rms = np.full(500, 0.002, dtype=np.float32)
    rms[400:460] = 0.30    # 'jemand' Kern 4.0–4.6
    rms[460:467] = 0.002   # Dip 70ms
    rms[467:483] = 0.08    # leises Äh bis Clip-Ende (27% vom Kern)
    words = [_w("du", 3.7, 3.9), _w("jemand,", 4.0, 4.83)]   # 830ms, Faktor ~1.32
    clips = [CutClip(start=3.6, end=4.83, reason="test")]
    out, trimmed = trim_trailing_murmur(clips, words, rms, noise_floor=0.002)
    assert trimmed == 1
    assert out[0].end < 4.72   # deutlich vor dem alten Ende


def test_no_murmur_trim_on_normal_word():
    """Unverdächtiges letztes Wort (Faktor <1.2) → kein Trim."""
    rms = np.full(500, 0.30, dtype=np.float32)
    words = [_w("abgehängt.", 4.0, 4.6)]   # 600ms für 9 Zeichen → Faktor ~0.7
    clips = [CutClip(start=3.5, end=4.6, reason="test")]
    out, trimmed = trim_trailing_murmur(clips, words, rms, noise_floor=0.002)
    assert trimmed == 0


# ===== Regel 3b: Anti-Hektik-Merge =====

def test_merge_close_clips():
    """Clips mit 100ms sauberem Abstand → verbunden."""
    clips = [CutClip(start=1.0, end=2.0, reason="a"), CutClip(start=2.1, end=3.0, reason="b")]
    out, n = merge_close_clips(clips, blocked_zones=[])
    assert n == 1 and len(out) == 1
    assert out[0].start == 1.0 and out[0].end == 3.0


def test_no_merge_across_filler_zone():
    """Gap enthält eine Filler-Zone (das Äh!) → NICHT verbinden."""
    clips = [CutClip(start=1.0, end=2.0, reason="a"), CutClip(start=2.15, end=3.0, reason="b")]
    out, n = merge_close_clips(clips, blocked_zones=[(2.02, 2.12)])
    assert n == 0 and len(out) == 2
