# api/projects.py
import uuid
import json
import shutil
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from models.project import ProjectCreate, ProjectResponse, Project
from config import PROJECTS_PATH

router = APIRouter()


def _get_project_path(project_id: str) -> Path:
    return PROJECTS_PATH / project_id


def _load_project(project_id: str) -> Project:
    meta_file = _get_project_path(project_id) / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail=f"Projekt {project_id} nicht gefunden")
    return Project(**json.loads(meta_file.read_text()))


def _save_project(project: Project):
    meta_file = _get_project_path(project.id) / "meta.json"
    meta_file.write_text(project.model_dump_json(indent=2))


@router.post("/", response_model=ProjectResponse)
async def create_project(data: ProjectCreate):
    project_id = str(uuid.uuid4())[:8]
    project_path = _get_project_path(project_id)
    (project_path / "raw").mkdir(parents=True, exist_ok=True)
    (project_path / "output").mkdir(exist_ok=True)

    project = Project(
        id=project_id,
        name=data.name,
        platform=data.platform,
        created_at=datetime.now(),
        engine_version=data.engine_version,
        subtitles_enabled=data.subtitles_enabled,
        subtitle_style=data.subtitle_style,
        mode=data.mode,
        content_type=data.content_type,
    )
    _save_project(project)
    return ProjectResponse(**project.model_dump(), files=[])


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    project = _load_project(project_id)
    raw_path = _get_project_path(project_id) / "raw"
    files = [f.name for f in raw_path.iterdir() if f.is_file()]
    return ProjectResponse(**project.model_dump(), files=files)


@router.post("/{project_id}/upload")
async def upload_files(project_id: str, files: list[UploadFile] = File(...)):
    project = _load_project(project_id)
    raw_path = _get_project_path(project_id) / "raw"
    saved = []
    for upload in files:
        dest = raw_path / upload.filename
        content = await upload.read()
        dest.write_bytes(content)
        # HDR (iPhone HLG) EINMAL nach SDR normalisieren → ganze Pipeline ist danach SDR,
        # Untertitel überall korrekt + Vorschau == Export. SDR-Quellen bleiben unangetastet.
        try:
            from services.ingest import normalize_hdr_to_sdr
            if normalize_hdr_to_sdr(dest):
                print(f"  [INGEST] {upload.filename}: HDR→SDR normalisiert")
        except Exception as e:
            print(f"  [INGEST] WARN: Normalisierung fehlgeschlagen ({e}) — Original behalten")
        saved.append(upload.filename)
    return {"project_id": project_id, "uploaded": saved}


@router.get("/")
async def list_projects():
    projects = []
    for p in PROJECTS_PATH.iterdir():
        if p.is_dir():
            meta = p / "meta.json"
            if meta.exists():
                projects.append(json.loads(meta.read_text()))
    return {"projects": projects}
