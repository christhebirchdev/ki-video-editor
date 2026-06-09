# models/memory.py
from pydantic import BaseModel
from datetime import datetime


class MemoryRule(BaseModel):
    rule: str
    reason: str
    confirmed_at: datetime
    video_count: int = 1


class StyleMemory(BaseModel):
    platform_preferences: dict = {}
    rules: list[MemoryRule] = []
    decision_log: list[dict] = []
