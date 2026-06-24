# services/analyst_speech.py
"""Deterministische Sprachstatistik aus Whisper-Wörtern (Code statt LLM — verlässlicher)."""
from typing import Optional
from models.analysis import WhisperWord
from models.analyst import SpeechStats
from services.claude_service import _is_filler

PAUSE_THRESHOLD_SEC = 0.5


def compute_speech_stats(words: list[WhisperWord]) -> Optional[SpeechStats]:
    """Berechnet WPM, Füllwörter und Pausen. None wenn kein gesprochener Text."""
    if not words:
        return None
    dauer = max(0.0, words[-1].end - words[0].start)
    fillers = [w.word.strip() for w in words if _is_filler(w.word)]
    gaps = [w2.start - w1.end for w1, w2 in zip(words, words[1:])]
    pausen = [g for g in gaps if g > PAUSE_THRESHOLD_SEC]
    inhalt_woerter = len(words) - len(fillers)
    wpm = inhalt_woerter / (dauer / 60) if dauer > 0 else 0.0
    return SpeechStats(
        wort_anzahl=len(words),
        sprech_dauer_sec=round(dauer, 2),
        wpm=round(wpm, 1),
        filler_count=len(fillers),
        filler_words=fillers,
        pausen_count=len(pausen),
        laengste_pause_sec=round(max(pausen), 2) if pausen else 0.0,
        sprechbeginn_sec=round(words[0].start, 2),
    )
