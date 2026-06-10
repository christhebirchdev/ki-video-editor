# api/pipeline.py
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from api.projects import _load_project, _save_project, _get_project_path
from services.whisper_service import transcribe_with_word_timestamps
from services.gemini_service import analyse_visual_phases
from services.claude_service import plan_cuts as plan_cuts_v1
from services.cut_engine_v2 import plan_cuts_v2
from services.cut_engine_v3 import plan_cuts_v3
from services.ffmpeg_service import execute_cut_plan, get_video_duration
from services.subtitle_service import add_subtitles_to_video
from services.memory_service import load_memory
from services.post_cut_cleanup import post_cut_cleanup
from models.analysis import VideoAnalysis, CutPlan

router = APIRouter()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


@router.post("/{project_id}/analyse")
async def run_analysis(project_id: str):
    """Phase 1: Whisper transkribiert + Gemini analysiert visuelle Phasen."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    raw_path = project_path / "raw"

    video_files = sorted(
        f for f in raw_path.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not video_files:
        raise HTTPException(status_code=400, detail="Keine Video-Dateien im Projekt gefunden")

    video_path = video_files[0]

    # Single source of truth: ffprobe-Dauer des Originalvideos
    duration_sec = get_video_duration(video_path)

    # Whisper: Wort-Timestamps
    whisper_words, full_transcript = transcribe_with_word_timestamps(video_path, language="de")

    # Gemini: Visuelle Phasen + Audio-Issues (gemini_duration wird ignoriert weil oft halluziniert)
    visual_phases, audio_issues, _gemini_duration = analyse_visual_phases(video_path)

    analysis = VideoAnalysis(
        project_id=project_id,
        duration_sec=duration_sec,
        audio_issues=audio_issues,
        whisper_words=whisper_words,
        visual_phases=visual_phases,
        full_transcript=full_transcript,
    )
    (project_path / "analysis.json").write_text(analysis.model_dump_json(indent=2))

    project.status = "analysed"
    _save_project(project)
    return analysis


@router.post("/{project_id}/plan-cuts")
async def run_cut_planning(project_id: str):
    """Phase 2: Schnittplan-Generierung. Engine v1 (Claude-Master) oder v2 (Hybrid)."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    analysis_path = project_path / "analysis.json"

    if not analysis_path.exists():
        raise HTTPException(status_code=400, detail="Erst /analyse ausführen")

    analysis = VideoAnalysis(**json.loads(analysis_path.read_text()))
    memory = load_memory()

    engine = getattr(project, "engine_version", "v1")
    print(f"  [PIPELINE] plan-cuts mit Engine '{engine}'")
    if engine == "v3":
        cut_plan = plan_cuts_v3(analysis, memory, project.platform)
    elif engine == "v2":
        cut_plan = plan_cuts_v2(analysis, memory, project.platform)
    else:
        cut_plan = plan_cuts_v1(analysis, memory, project.platform)

    (project_path / "cut_plan.json").write_text(cut_plan.model_dump_json(indent=2))
    # Status nicht downgraden — wenn schon "cut"/"subtitled" bleibt der Erfolg sichtbar.
    # Sonst würde ein Re-Plan auf einem schon gecutteten Projekt den Status zurücksetzen.
    if project.status not in ("cut", "subtitled"):
        project.status = "planned"
    _save_project(project)
    return cut_plan


@router.post("/{project_id}/execute-cut")
async def run_cut(project_id: str):
    """Phase 3: FFmpeg rendert das finale Video."""
    project = _load_project(project_id)
    project_path = _get_project_path(project_id)
    cut_plan_path = project_path / "cut_plan.json"

    if not cut_plan_path.exists():
        raise HTTPException(status_code=400, detail="Erst /plan-cuts ausführen")

    cut_plan = CutPlan(**json.loads(cut_plan_path.read_text()))

    if not cut_plan.confirmed_by_user:
        raise HTTPException(status_code=400,
                            detail="Schnittplan muss erst bestätigt werden (/api/feedback/{id}/confirm)")

    output_path = execute_cut_plan(cut_plan, project_path / "raw", project_path / "output")

    # Auto-Cleanup: Whisper hört nochmal hin und schneidet Filler-Reste raus
    try:
        cleaned_plan, output_path, stats = post_cut_cleanup(
            output_path, cut_plan, project_path / "raw", project_path / "output"
        )
        if stats["total_fillers_removed"] > 0:
            (project_path / "cut_plan.json").write_text(cleaned_plan.model_dump_json(indent=2))
            print(f"  [PIPELINE] Auto-Cleanup: {stats['total_fillers_removed']} Filler in {stats['iterations']} Iterationen entfernt")
    except Exception as e:
        # Cleanup ist optional — falls es scheitert, nehmen wir den ersten Cut
        print(f"  [PIPELINE] WARN: Post-Cleanup fehlgeschlagen: {e}")
        stats = {"iterations": 0, "total_fillers_removed": 0, "error": str(e)}

    project.status = "cut"
    _save_project(project)
    return {"output": str(output_path), "status": "cut_done", "cleanup_stats": stats}


@router.post("/{project_id}/add-subtitles")
async def run_subtitles(project_id: str):
    """Phase 4: AssemblyAI transkribiert + brennt Untertitel ein."""
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
