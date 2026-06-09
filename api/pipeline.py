# api/pipeline.py
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from api.projects import _load_project, _save_project, _get_project_path
from services.gemini_service import analyse_video
from services.claude_service import plan_cuts
from services.ffmpeg_service import execute_cut_plan
from services.subtitle_service import add_subtitles_to_video
from services.memory_service import load_memory, log_decision

router = APIRouter()


@router.post("/{project_id}/analyse")
async def run_analysis(project_id: str):
    """Phase 1: Gemini analysiert alle Videos im Projekt."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    raw_path = project_path / "raw"

    video_files = sorted(
        list(raw_path.glob("*.mp4")) +
        list(raw_path.glob("*.mov")) +
        list(raw_path.glob("*.MP4"))
    )
    if not video_files:
        raise HTTPException(status_code=400, detail="Keine Video-Dateien im Projekt gefunden")

    analysis = analyse_video(project_id, video_files)
    (project_path / "analysis.json").write_text(analysis.model_dump_json(indent=2))

    project.status = "analysed"
    _save_project(project)
    return analysis


@router.post("/{project_id}/plan-cuts")
async def run_cut_planning(project_id: str):
    """Phase 2: Claude erstellt Schnittplan auf Basis der Gemini-Analyse."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    analysis_path = project_path / "analysis.json"

    if not analysis_path.exists():
        raise HTTPException(status_code=400, detail="Erst /analyse ausführen")

    from models.analysis import VideoAnalysis
    analysis = VideoAnalysis(**json.loads(analysis_path.read_text()))
    memory = load_memory()
    cut_plan = plan_cuts(analysis, memory, project.platform)

    (project_path / "cut_plan.json").write_text(cut_plan.model_dump_json(indent=2))
    project.status = "planned"
    _save_project(project)
    return cut_plan


@router.post("/{project_id}/execute-cut")
async def run_cut(project_id: str):
    """Phase 3: FFmpeg schneidet das Video nach dem bestätigten Schnittplan."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    cut_plan_path = project_path / "cut_plan.json"

    if not cut_plan_path.exists():
        raise HTTPException(status_code=400, detail="Erst /plan-cuts ausführen")

    from models.analysis import CutPlan
    cut_plan = CutPlan(**json.loads(cut_plan_path.read_text()))

    if not cut_plan.confirmed_by_user:
        raise HTTPException(status_code=400,
                            detail="Schnittplan muss erst bestätigt werden (/api/feedback/{id}/confirm)")

    output_path = execute_cut_plan(cut_plan, project_path / "raw", project_path / "output")
    project.status = "cut"
    _save_project(project)
    return {"output": str(output_path), "status": "cut_done"}


@router.post("/{project_id}/add-subtitles")
async def run_subtitles(project_id: str):
    """Phase 4: AssemblyAI transkribiert und Untertitel werden eingebrannt."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    rough_cut = project_path / "output" / "rough_cut.mp4"

    if not rough_cut.exists():
        raise HTTPException(status_code=400, detail="Erst /execute-cut ausführen")

    result = add_subtitles_to_video(rough_cut, project_path)
    project.status = "subtitled"
    _save_project(project)
    return result


@router.get("/{project_id}/preview/{filename}")
async def get_preview(project_id: str, filename: str):
    """Liefert eine Video-Datei zum Anschauen im Browser."""
    video_path = _get_project_path(project_id) / "output" / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return FileResponse(str(video_path), media_type="video/mp4")
