# models/analysis.py
from pydantic import BaseModel
from typing import Optional


class TakeAnalysis(BaseModel):
    take_id: str
    file: str
    start_seconds: float
    end_seconds: float
    transcript: str
    energy: str                     # "hoch" | "mittel" | "niedrig"
    eye_contact: str                # "gut" | "unterbrochen" | "schlecht"
    voice_quality: str              # "klar" | "leise" | "heiser"
    issues: list[str]
    gemini_description: str


class VideoAnalysis(BaseModel):
    project_id: str
    takes: list[TakeAnalysis]
    full_transcript: str
    total_duration_seconds: float
    repeated_content: list[dict]    # [{"text": "...", "take_ids": ["take_1", "take_3"]}]


class CutDecision(BaseModel):
    take_id: str
    keep: bool
    reason: str
    in_point: float
    out_point: float
    order: int                      # Reihenfolge im Endvideo (0-basiert)


class CutPlan(BaseModel):
    project_id: str
    decisions: list[CutDecision]
    claude_reasoning: str
    confirmed_by_user: bool = False
