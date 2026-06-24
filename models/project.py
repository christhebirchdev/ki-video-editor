# models/project.py
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

EngineVersion = Literal["v1", "v2", "v3", "v4", "v5", "v5.2", "v5.3", "v5.4"]

# MultiCut: ein langes Video (z.B. Podcast) → viele eigenständige Shorts.
# mode="single" = bisheriges Verhalten (Default, Backwards-kompatibel).
ProjectMode = Literal["single", "multicut"]
ContentType = Literal["tofu", "mofu"]


class Project(BaseModel):
    id: str
    name: str
    platform: str
    created_at: datetime
    status: str = "created"
    engine_version: EngineVersion = "v1"  # Default für Backwards-Kompatibilität
    subtitles_enabled: bool = False       # Auto-Untertitel nach dem Rohschnitt
    subtitle_style: str = "bold_pop_cyan"
    mode: ProjectMode = "single"
    content_type: Optional[ContentType] = None  # nur bei mode="multicut" relevant


class ProjectCreate(BaseModel):
    name: str
    platform: str
    engine_version: EngineVersion = "v1"
    subtitles_enabled: bool = False
    subtitle_style: str = "bold_pop_cyan"
    mode: ProjectMode = "single"
    content_type: Optional[ContentType] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    platform: str
    created_at: datetime
    status: str
    engine_version: EngineVersion = "v1"
    subtitles_enabled: bool = False
    subtitle_style: str = "bold_pop_cyan"
    mode: ProjectMode = "single"
    content_type: Optional[ContentType] = None
    files: list[str] = []
