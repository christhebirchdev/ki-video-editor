# api/subtitles.py
"""
Untertitel-API (V1, Stil "Bold Pop Cyan").

POST  /{id}/generate   → Whisper auf rough_cut → subtitles.json
GET   /{id}            → subtitles.json (Frontend-Overlay liest hieraus)
PATCH /{id}/settings   → Position (Drag), enabled (Checkbox), style (Dropdown)
POST  /{id}/burn       → ASS + ffmpeg → output/subtitled_cut.mp4
GET   /styles          → verfügbare Stile fürs Dropdown
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from api.projects import _load_project, _get_project_path
from services.subtitle_engine import (
    generate_subtitles,
    load_subtitles,
    update_subtitle_settings,
    burn_subtitles,
    AVAILABLE_STYLES,
    DEFAULT_STYLE,
)

router = APIRouter()


class PositionPatch(BaseModel):
    x_pct: Optional[float] = None
    y_pct: Optional[float] = None


class SettingsPatch(BaseModel):
    position: Optional[PositionPatch] = None
    enabled: Optional[bool] = None
    style: Optional[str] = None
    font_size: Optional[int] = None
    font_family: Optional[str] = None
    text_color: Optional[str] = None
    highlight_color: Optional[str] = None


@router.get("/styles")
async def list_styles():
    return {
        "default": DEFAULT_STYLE,
        "styles": [{"id": k, "label": v["label"]} for k, v in AVAILABLE_STYLES.items()],
    }


@router.post("/{project_id}/generate")
async def generate(project_id: str, style: Optional[str] = None):
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    cut = project_path / "output" / "rough_cut.mp4"
    if not cut.exists():
        raise HTTPException(status_code=400, detail="Erst execute-cut ausführen (rough_cut.mp4 fehlt)")
    doc = generate_subtitles(
        cut, project_path,
        style=style or getattr(project, "subtitle_style", DEFAULT_STYLE),
    )
    return doc


@router.get("/{project_id}")
async def get_subtitles(project_id: str):
    _load_project(project_id)
    doc = load_subtitles(_get_project_path(project_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Noch keine Untertitel generiert")
    return doc


@router.patch("/{project_id}/settings")
async def patch_settings(project_id: str, patch: SettingsPatch):
    _load_project(project_id)
    settings_patch: dict = {}
    if patch.position is not None:
        settings_patch["position"] = {
            k: v for k, v in patch.position.model_dump().items() if v is not None
        }
    if patch.enabled is not None:
        settings_patch["enabled"] = patch.enabled
    if patch.style is not None:
        settings_patch["style"] = patch.style
    if patch.font_size is not None:
        settings_patch["font_size"] = patch.font_size
    if patch.font_family is not None:
        settings_patch["font_family"] = patch.font_family
    if patch.text_color is not None:
        settings_patch["text_color"] = patch.text_color
    if patch.highlight_color is not None:
        settings_patch["highlight_color"] = patch.highlight_color
    try:
        return update_subtitle_settings(_get_project_path(project_id), settings_patch)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{project_id}/burn")
async def burn(project_id: str):
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    try:
        out = burn_subtitles(project_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    project.status = "subtitled"
    from api.projects import _save_project
    _save_project(project)
    return {"output": out.name, "status": "subtitled"}
