# models/project.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class Project(BaseModel):
    id: str
    name: str
    platform: str  # "tiktok" | "reels" | "shorts"
    created_at: datetime
    status: str = "created"  # created | analysed | planned | cut | subtitled | done


class ProjectCreate(BaseModel):
    name: str
    platform: str


class ProjectResponse(BaseModel):
    id: str
    name: str
    platform: str
    created_at: datetime
    status: str
    files: list[str] = []
