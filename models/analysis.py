# models/analysis.py
"""
Drei-Quellen-Architektur:
1. Whisper liefert Wort-Timestamps (Audio-Wahrheit, sekundengenau)
2. Gemini liefert visuelle Phasen-Analyse (perfect_take/natural_glance/etc.)
3. Claude orchestriert beide Quellen zu einem präzisen ffmpeg-Schnittplan
"""
from pydantic import BaseModel
from typing import Literal, Optional

# --- Whisper: Wort für Wort mit Zeitstempeln ---

class WhisperWord(BaseModel):
    start: float           # Sekunden (float, Whisper liefert das so)
    end: float
    word: str


# --- Gemini Visual: Phasen der Performance ---

VisualQuality = Literal[
    "perfect_take",            # Stabiler, direkter Blickkontakt + hohe Sprechenergie
    "thinking_glance",         # Kurzer Blick weg WÄHREND flüssig + energetisch weitergesprochen wird — IST OK
    "script_reading_silence",  # Schweigt, schaut auf Notizen, sammelt sich
    "outtake_break",           # Stimme bricht, Grimasse, Kopfschütteln — eindeutiger Outtake
    "idle",                    # Start/Endphase ohne Aktion
]


class VisualPhase(BaseModel):
    start_sec: float
    end_sec: float
    visual_quality: VisualQuality
    description: str = ""


# --- Gemini Audio: was Gemini akustisch erkennt (ergänzt Whisper) ---

AudioIssueType = Literal[
    "filler",            # "Ähm", "Äh", "Uh" — auch wenn Whisper sie textlich übersehen/falsch transkribiert hat
    "long_pause",        # Pause >800ms mitten im Satz
    "voice_break",       # Stimme stockt, Lautstärke fällt abrupt ab, Versprecher
    "mispronunciation",  # Versprecher / falsch ausgesprochenes Wort
]


class AudioIssue(BaseModel):
    start_sec: float
    end_sec: float
    type: AudioIssueType
    text_heard: str = ""              # Was Gemini akustisch wahrgenommen hat
    confidence: str = "medium"        # "low" | "medium" | "high"
    description: str = ""


# --- Gesamtanalyse ---

class VideoAnalysis(BaseModel):
    project_id: str
    duration_sec: float
    whisper_words: list[WhisperWord]
    visual_phases: list[VisualPhase]
    audio_issues: list[AudioIssue] = []
    full_transcript: str = ""


# --- Claude-Output: finale Schnitt-Clips ---

class CutClip(BaseModel):
    """Ein Segment des finalen Cuts. Sekunden (float) — direkt für ffmpeg."""
    start: float
    end: float
    reason: str = ""


class CutPlan(BaseModel):
    project_id: str
    clips: list[CutClip]
    claude_reasoning: str = ""
    confirmed_by_user: bool = False
