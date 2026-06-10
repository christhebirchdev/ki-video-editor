# models/project.py
from pydantic import BaseModel
from typing import Literal
from datetime import datetime

EngineVersion = Literal["v1", "v2", "v3"]


class Project(BaseModel):
    id: str
    name: str
    platform: str
    created_at: datetime
    status: str = "created"
    engine_version: EngineVersion = "v1"  # Default für Backwards-Kompatibilität


class ProjectCreate(BaseModel):
    name: str
    platform: str
    engine_version: EngineVersion = "v1"


class ProjectResponse(BaseModel):
    id: str
    name: str
    platform: str
    created_at: datetime
    status: str
    engine_version: EngineVersion = "v1"
    files: list[str] = []
