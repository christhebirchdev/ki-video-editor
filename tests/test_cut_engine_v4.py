# tests/test_cut_engine_v4.py
"""
Mini-Tests für die V4-Bausteine — komplett ohne API-Calls und ohne Audio-Dateien.

Testet die drei neuen Garantien:
1. Pause-Filler-Detection (pure Funktion mit synthetischen VAD-Segmenten)
2. Gap-Filler-Split im V4-Clip-Builder (Filler in Pause <400ms wird geschnitten)
3. Tail-Trim am Energie-Einbruch (synthetisches RMS-Signal)
4. Selection-Cache-Key: stabil bei gleichem Input, anders bei anderem Input
"""
import os

# config.Settings braucht Keys — für Tests reichen Dummies (echte .env hat Vorrang)
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

import numpy as np  # noqa: E402

from models.analysis import WhisperWord  # noqa: E402
from services import audio_truth  # noqa: E402
from services.cut_engine_v2 import estimated_max_word_duration  # noqa: E402
from services.cut_engine_v4 import (  # noqa: E402
    _sentence_to_clips_v4,
    _selection_cache_key,
)
from services.cut_engine_v2 import Sentence  # noqa: E402


def _w(word, start, end):
    return WhisperWord(word=word, start=start, end=end)


# ===== 1. Pause-Filler-Detection =====

def test_pause_filler_detected_in_gap():
    """Sprache (VAD) in einer 500ms-Whisper-Pause → Filler-Zone."""
    words = [_w("Hand,", 1.0, 1.5), _w("und", 2.0, 2.2)]
    # VAD hört Stimme 1.6–1.9s — mitten in der Pause
    vad = [(0.9, 1.5), (1.6, 1.9), (2.0, 2.3)]
    zones = audio_truth.detect_pause_fillers(words, vad)
    assert len(zones) == 1
    zs, ze = zones[0]
    assert 1.5 <= zs <= 1.65
    assert 1.85 <= ze <= 2.0


def test_no_pause_filler_in_silent_gap():
    """Stille Pause (kein VAD-Segment) → keine Zone. 0-Filler-Pfad funktioniert."""
    words = [_w("Hand,", 1.0, 1.5), _w("und", 2.0, 2.2)]
    vad = [(0.9, 1.5), (2.0, 2.3)]  # nichts in der Pause
    assert audio_truth.detect_pause_fillers(words, vad) == []


def test_short_gap_not_analysed():
    """Pausen <200ms werden nicht analysiert (Whisper-Jitter-Schutz)."""
    words = [_w("Hand,", 1.0, 1.5), _w("und", 1.65, 1.85)]
    vad = [(0.9, 1.9)]  # VAD durchgehend (Übergangslaute)
    assert audio_truth.detect_pause_fillers(words, vad) == []


# ===== 2. Gap-Filler-Split im Clip-Builder =====

def test_clip_builder_splits_at_pause_filler():
    """Filler-Zone in 350ms-Pause (V2 würde durchlaufen) → V4 splittet + clampt."""
    words = [_w("Das", 1.0, 1.3), _w("Hand,", 1.4, 1.9), _w("und", 2.25, 2.45), _w("so", 2.5, 2.7)]
    s = Sentence(id=0, words=words, start_sec=1.0, end_sec=2.7, visual_quality="perfect_take")
    pause_filler = [(1.95, 2.2)]  # Filler in der 350ms-Pause zwischen "Hand," und "und"
    clips = _sentence_to_clips_v4(s, filler_zones=pause_filler, outtake_zones=[], tail_trim_zones=[])
    assert len(clips) == 2
    # Clip 1 endet vor der Zone (geclampt, kein Post-Padding über den Filler)
    assert clips[0].end <= 1.95
    # Clip 2 beginnt nach der Zone
    assert clips[1].start >= 2.2 - 0.13  # max. Pre-Padding 120ms + Toleranz


def test_clip_builder_without_zones_keeps_sentence_whole():
    """0 Zonen → ein durchgehender Clip (Code-Pfad ohne Audio-Signal)."""
    words = [_w("Das", 1.0, 1.3), _w("ist", 1.35, 1.5), _w("gut", 1.55, 1.8)]
    s = Sentence(id=0, words=words, start_sec=1.0, end_sec=1.8, visual_quality="perfect_take")
    clips = _sentence_to_clips_v4(s, filler_zones=[], outtake_zones=[], tail_trim_zones=[])
    assert len(clips) == 1


def test_clip_builder_outtake_guarantee():
    """outtake_break-Satz → garantiert 0 Clips (Code-Garantie wie V2)."""
    words = [_w("Mist", 1.0, 1.4)]
    s = Sentence(id=0, words=words, start_sec=1.0, end_sec=1.4, visual_quality="outtake_break")
    assert _sentence_to_clips_v4(s, [], [], []) == []


def test_clip_builder_tail_trim_zone():
    """Tail-Trim-Zone im Wort → Wort wird am Zonen-Beginn gekürzt ('jemand'-Fall)."""
    # "jemand," 840ms (Faktor 1.33× — V2-Schwelle greift NICHT)
    words = [_w("jemand,", 1.0, 1.84), _w("der", 2.0, 2.2)]
    s = Sentence(id=0, words=words, start_sec=1.0, end_sec=2.2, visual_quality="perfect_take")
    # Energie-Analyse fand den Einbruch bei 1.55s
    tail_zones = [(1.55, 1.84)]
    clips = _sentence_to_clips_v4(s, filler_zones=[], outtake_zones=[], tail_trim_zones=tail_zones)
    # Erster Clip endet am Energie-Einbruch (kein Post-Padding), nicht am Wort-Ende
    assert any(abs(c.end - 1.55) < 0.01 for c in clips)
    assert all(c.end < 1.84 or c.start >= 1.84 for c in clips)


# ===== 3. Tail-Trim-Detection auf synthetischem RMS =====

def test_detect_tail_trim_on_synthetic_rms():
    """[Wort 400ms laut][Stille 150ms][Filler 250ms leiser] in einem 800ms-'Wort'."""
    # 10ms-Frames: Sekunde 0–5 Rauschen, Wort liegt bei 2.0–2.8s
    rms = np.full(500, 0.002, dtype=np.float32)        # Noise-Floor
    rms[200:240] = 0.30                                 # Wortkern 2.00–2.40s
    rms[240:255] = 0.002                                # Einbruch 2.40–2.55s (150ms)
    rms[255:280] = 0.10                                 # Filler 2.55–2.80s (deutlich leiser als Kern)
    words = [_w("Hand,", 2.0, 2.8)]                     # 800ms für 4 Buchstaben → Faktor ~1.7
    zones = audio_truth.detect_tail_trims(words, rms, estimated_max_word_duration)
    assert len(zones) == 1
    zs, ze = zones[0]
    # V4.2: Schnitt = Dip-Beginn (2.40) + 60ms Release
    assert 2.40 <= zs <= 2.52
    assert abs(ze - 2.8) < 0.02  # Zone bis Wort-Ende


def test_no_tail_trim_when_post_dip_is_loud():
    """V4.2: Post-Dip fast so laut wie Wortkern → vermutlich echtes Wort gemerged → kein Trim.

    Regressionsfall Testreel 4.0: abgeschnittene Satzteile durch zu aggressive Trims.
    """
    rms = np.full(500, 0.002, dtype=np.float32)
    rms[200:240] = 0.30   # Wortkern
    rms[240:255] = 0.002  # Einbruch
    rms[255:280] = 0.25   # danach LAUT (0.83× Kern) = echtes Wort, kein Filler
    words = [_w("Hand,", 2.0, 2.8)]
    assert audio_truth.detect_tail_trims(words, rms, estimated_max_word_duration) == []


def test_no_tail_trim_when_post_dip_too_long():
    """V4.2: Post-Dip-Segment >700ms → vermutlich gemergtes echtes Folgewort → kein Trim."""
    rms = np.full(500, 0.002, dtype=np.float32)
    rms[200:240] = 0.30   # Wortkern 2.0–2.4s
    rms[240:255] = 0.002  # Einbruch
    rms[255:335] = 0.10   # 800ms "Filler" — zu lang für ein Äh
    words = [_w("und", 2.0, 3.35)]
    assert audio_truth.detect_tail_trims(words, rms, estimated_max_word_duration) == []


def test_no_tail_trim_for_compound_word():
    """Echtes langes Wort (durchgehend Energie, Faktor ~1.0) bleibt unangetastet."""
    rms = np.full(500, 0.002, dtype=np.float32)
    rms[200:375] = 0.30  # durchgehende Energie 2.0–3.75s
    words = [_w("Vorsorgeuntersuchung", 2.0, 3.75)]  # 20 Zeichen → erwartete ~1.75s, Faktor 1.0
    zones = audio_truth.detect_tail_trims(words, rms, estimated_max_word_duration)
    assert zones == []


def test_tail_trim_empty_rms_safe():
    """Leeres RMS-Array (Audio-Decode-Fehler) → keine Zonen, kein Crash."""
    words = [_w("Hand,", 2.0, 2.8)]
    assert audio_truth.detect_tail_trims(words, np.zeros(0, dtype=np.float32), estimated_max_word_duration) == []


# ===== 3b. V4.2: VAD-Safety verschluckt keine Nachbar-Wörter mehr =====

def test_word_next_to_vad_zone_not_swallowed():
    """Regressionsfall Testreel 4.0: 'dann' 90.81-90.89 wurde von Zone bis 90.80
    + 100ms-Safety verschluckt. Mit VAD-Safety 20ms muss es überleben."""
    words = [_w("möchtest,", 90.07, 90.57), _w("dann", 90.81, 90.89), _w("los", 90.95, 91.2)]
    s = Sentence(id=0, words=words, start_sec=90.07, end_sec=91.2, visual_quality="perfect_take")
    # VAD-Zone in der Pause, endet 10ms vor 'dann' — als präzises 3-Tupel (Safety 20ms)
    zones = [(90.58, 90.80, 0.02)]
    clips = _sentence_to_clips_v4(s, filler_zones=zones, outtake_zones=[], tail_trim_zones=[])
    covered = sum(
        max(0.0, min(90.89, c.end) - max(90.81, c.start)) for c in clips
    )
    assert covered > 0.05, "'dann' darf nicht von der VAD-Zone verschluckt werden"


def test_text_fallback_off_keeps_stretched_word():
    """V4.2: use_text_fallback=False → gedehntes Wort ohne Energie-Einbruch bleibt ganz
    (Regressionsfall: 'und' 1400ms wurde mitten im Laut geschnitten)."""
    words = [_w("und", 2.0, 3.4), _w("dann", 3.5, 3.8)]  # 'und' Faktor ~3.6
    s = Sentence(id=0, words=words, start_sec=2.0, end_sec=3.8, visual_quality="perfect_take")
    clips = _sentence_to_clips_v4(s, [], [], [], use_text_fallback=False)
    # 'und' muss vollständig enthalten sein
    covered = sum(max(0.0, min(3.4, c.end) - max(2.0, c.start)) for c in clips)
    assert covered >= 1.39


# ===== 4. Selection-Cache-Key =====

def test_cache_key_stable_and_input_sensitive():
    k1 = _selection_cache_key("satzblock A", "audio", "regeln")
    k2 = _selection_cache_key("satzblock A", "audio", "regeln")
    k3 = _selection_cache_key("satzblock B", "audio", "regeln")
    assert k1 == k2
    assert k1 != k3
