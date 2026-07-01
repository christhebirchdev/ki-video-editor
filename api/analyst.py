# api/analyst.py
"""AI Video Analyst: Upload → Start (BackgroundTask) → Status/Ergebnis pollen."""
import json
import re
import shutil
import uuid
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from services import analyst_vlm
from services.analyst_engine import ANALYST_PATH, run_analysis, write_status

router = APIRouter()

RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}


def _run_dir(run_id: str):
    d = ANALYST_PATH / run_id
    if not (d / "meta.json").exists():
        raise HTTPException(status_code=404, detail=f"Analyse {run_id} nicht gefunden")
    return d


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    run_id = str(uuid.uuid4())[:8]
    run_dir = ANALYST_PATH / run_id
    (run_dir / "raw").mkdir(parents=True)
    safe_name = re.sub(r"[^\w.\-äöüÄÖÜß ]", "_", file.filename or "video.mp4")
    dest = run_dir / "raw" / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)  # streamt — lädt große Files nicht in den RAM
    (run_dir / "meta.json").write_text(json.dumps({
        "id": run_id,
        "filename": safe_name,
        "created_at": datetime.now().isoformat(),
    }, ensure_ascii=False))
    write_status(run_dir, "uploaded", "Bereit zur Analyse")
    return {"id": run_id, "filename": safe_name}


@router.post("/{run_id}/start")
async def start_analysis(run_id: str, background: BackgroundTasks, skip_eval: bool = False):
    """skip_eval=true → nur lokale Rohanalyse (Gemma/Whisper/Quality), KEIN Claude-Call."""
    run_dir = _run_dir(run_id)
    status = json.loads((run_dir / "status.json").read_text())
    if status.get("phase") in RUNNING_PHASES:
        raise HTTPException(status_code=409, detail="Analyse läuft bereits")
    ok, msg = analyst_vlm.is_available()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    # skip_eval in meta.json persistieren, damit die Engine es liest
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["skip_eval"] = skip_eval
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    write_status(run_dir, "starting", "Analyse startet…")
    background.add_task(run_analysis, run_id)
    return {"id": run_id, "status": "started", "skip_eval": skip_eval}


@router.get("/{run_id}")
async def get_analysis(run_id: str):
    run_dir = _run_dir(run_id)
    status = json.loads((run_dir / "status.json").read_text())
    out = {"id": run_id, **status}
    analysis = run_dir / "analysis.json"
    if status.get("done") and analysis.exists():
        out["result"] = json.loads(analysis.read_text())
    return out
