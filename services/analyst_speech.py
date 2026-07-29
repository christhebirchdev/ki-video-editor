# services/analyst_speech.py
"""Deterministische Sprachstatistik aus Whisper-Wörtern (Code statt LLM — verlässlicher)."""
from typing import Optional
from models.analysis import WhisperWord
from models.analyst import Pause, SpeechStats
from services.claude_service import _is_filler

# Ab wann eine Sprechlücke überhaupt als Pause gemeldet wird. War 0.5s — damit landeten regelmäßig
# Pausen von 0.5–0.6s als Schnitt-Empfehlung im Output, die der Nutzer beim Zuschauen gar nicht
# wahrnimmt (Feedback Runs 25b8b2f6 „sek 3 und sek 7 in ordnung" und 093dc5a7 „nehme ich persönlich
# keine sprechpause wahr"). Unterhalb dieser Schwelle ist eine Lücke Atmen, kein Loch.
PAUSE_THRESHOLD_SEC = 0.8


def compute_speech_stats(words: list[WhisperWord]) -> Optional[SpeechStats]:
    """Berechnet WPM, Füllwörter und Pausen. None wenn kein gesprochener Text."""
    if not words:
        return None
    dauer = max(0.0, words[-1].end - words[0].start)
    fillers = [w.word.strip() for w in words if _is_filler(w.word)]
    # Position MIT ausgeben, nicht nur die Dauer: das Modell muss wissen, WO es hinhören soll.
    pausen = [
        Pause(start_sec=round(w1.end, 2), end_sec=round(w2.start, 2),
              dauer_sec=round(w2.start - w1.end, 2))
        for w1, w2 in zip(words, words[1:])
        if w2.start - w1.end > PAUSE_THRESHOLD_SEC
    ]
    inhalt_woerter = len(words) - len(fillers)
    wpm = inhalt_woerter / (dauer / 60) if dauer > 0 else 0.0
    return SpeechStats(
        wort_anzahl=len(words),
        sprech_dauer_sec=round(dauer, 2),
        wpm=round(wpm, 1),
        filler_count=len(fillers),
        filler_words=fillers,
        pausen_count=len(pausen),
        laengste_pause_sec=max((p.dauer_sec for p in pausen), default=0.0),
        pausen=pausen,
        sprechbeginn_sec=round(words[0].start, 2),
    )
