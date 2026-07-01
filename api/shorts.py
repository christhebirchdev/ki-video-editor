# api/shorts.py
"""
MultiCut-API: Planung, Auswahl und Batch-Schnitt der Shorts.

Flow:
1. POST /api/shorts/{id}/plan      → Claude findet Kandidaten (braucht analysis.json)
2. GET  /api/shorts/{id}           → Kandidaten + Status (Frontend pollt das beim Batch)
3. PATCH /api/shorts/{id}/{sid}    → Short an-/abwählen
4. POST /api/shorts/{id}/execute   → Batch starten (post_processing: segment | v52)
5. GET  /api/shorts/{id}/{sid}/preview → fertiges Short streamen
"""
import json
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from api.projects import _load_project, _get_project_path
from models.analysis import VideoAnalysis
from models.shorts import PostProcessing
from services.shorts_planner import plan_shorts
from services.shorts_executor import (
    load_shorts_plan, save_shorts_plan, is_batch_running, run_batch,
)

router = APIRouter()


class ShortPatch(BaseModel):
    selected: bool


class ExecuteRequest(BaseModel):
    post_processing: PostProcessing = "segment"


def _require_multicut(project_id: str):
    project = _load_project(project_id)
    if getattr(project, "mode", "single") != "multicut":
        raise HTTPException(status_code=400, detail="Projekt ist kein MultiCut-Projekt")
    return project


@router.post("/{project_id}/plan")
async def create_shorts_plan(project_id: str):
    """Kandidaten finden. Voraussetzung: /analyse ist gelaufen (analysis.json existiert)."""
    project = _require_multicut(project_id)
    project_path = _get_project_path(project_id)
    analysis_path = project_path / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=400, detail="Erst /analyse ausführen")
    if is_batch_running(project_id):
        raise HTTPException(status_code=409, detail="Batch läuft — Neuplanung erst danach")

    content_type = project.content_type or "tofu"
    analysis = VideoAnalysis(**json.loads(analysis_path.read_text()))
    plan = plan_shorts(analysis, content_type)
    save_shorts_plan(project_path, plan)
    return plan


@router.get("/{project_id}")
async def get_shorts(project_id: str):
    """Aktueller Stand — Frontend pollt das während des Batches.

    'live' = läuft der Batch-Prozess WIRKLICH gerade (In-Memory-Lock)?
    Nach einem Server-Crash kann batch_status stale auf "running" stehen —
    live=False sagt dem Frontend dann: Polling stoppen, Re-Run ist möglich.
    """
    plan = load_shorts_plan(_get_project_path(project_id))
    if plan is None:
        raise HTTPException(status_code=404, detail="Noch kein Shorts-Plan — erst /plan ausführen")
    return {**plan.model_dump(), "live": is_batch_running(project_id)}


@router.patch("/{project_id}/{short_id}")
async def patch_short(project_id: str, short_id: str, patch: ShortPatch):
    """Short an-/abwählen (alle sind per Default an)."""
    project_path = _get_project_path(project_id)
    plan = load_shorts_plan(project_path)
    if plan is None:
        raise HTTPException(status_code=404, detail="Kein Shorts-Plan vorhanden")
    if is_batch_running(project_id):
        raise HTTPException(status_code=409, detail="Batch läuft — Auswahl ist gesperrt")

    for s in plan.shorts:
        if s.id == short_id:
            s.selected = patch.selected
            save_shorts_plan(project_path, plan)
            return s
    raise HTTPException(status_code=404, detail=f"Short {short_id} nicht gefunden")


@router.post("/{project_id}/execute")
async def execute_shorts(project_id: str, req: ExecuteRequest, background: BackgroundTasks):
    """Batch starten: alle gewählten Shorts sequentiell schneiden."""
    _require_multicut(project_id)
    project_path = _get_project_path(project_id)
    plan = load_shorts_plan(project_path)
    if plan is None:
        raise HTTPException(status_code=400, detail="Erst /plan ausführen")
    if is_batch_running(project_id):
        raise HTTPException(status_code=409, detail="Batch läuft bereits")
    if not any(s.selected for s in plan.shorts):
        raise HTTPException(status_code=400, detail="Kein Short ausgewählt")

    background.add_task(run_batch, project_id, req.post_processing)
    n = sum(1 for s in plan.shorts if s.selected and s.status != "done")
    return {"status": "started", "post_processing": req.post_processing, "shorts_to_cut": n}


@router.get("/{project_id}/{short_id}/preview")
async def preview_short(project_id: str, short_id: str):
    """Fertiges Short streamen — verfügbar sobald status='done'."""
    video_path = _get_project_path(project_id) / "output" / "shorts" / f"{short_id}.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Short noch nicht gerendert")
    return FileResponse(str(video_path), media_type="video/mp4")
