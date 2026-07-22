# api/analyst.py
"""AI Video Analyst: Upload → Start (BackgroundTask) → Status/Ergebnis pollen."""
import json
import re
import secrets
import shutil
import uuid
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel
from config import settings
from models.analyst import FORMATE
from services import analyst_vlm
from services.analyst_engine import ANALYST_PATH, run_analysis, write_status

router = APIRouter()

RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}


def _run_dir(run_id: str):
    d = ANALYST_PATH / run_id
    if not (d / "meta.json").exists():
        raise HTTPException(status_code=404, detail=f"Analyse {run_id} nicht gefunden")
    return d


def _active_runs():
    """Aktive Läufe (laufend + wartend), sortiert nach created_at → [(created_at, id), …].
    ponytail: liest die Statusdateien bei jedem Poll — reicht für die paar parallelen Nutzer."""
    active = []
    for d in ANALYST_PATH.iterdir():
        if not d.is_dir():
            continue
        try:
            phase = json.loads((d / "status.json").read_text()).get("phase")
            created = json.loads((d / "meta.json").read_text()).get("created_at", "")
        except (OSError, json.JSONDecodeError):
            continue
        if phase in RUNNING_PHASES:
            active.append((created, d.name))
    active.sort()
    return active


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


ENGINES = {"v1", "v2_pure", "v2_hybrid"}


@router.post("/{run_id}/start")
async def start_analysis(
    run_id: str, background: BackgroundTasks, skip_eval: bool = False, engine: str = "v1",
    planned_text_hook: str = "", format: str = "",
):
    """engine: v1 (Claude bewertet aus Text) | v2_pure (nur Gemini) | v2_hybrid (Gemini + lokale Messwerte).
    skip_eval=true → nur lokale Rohanalyse (Whisper/Quality), KEIN Bewertungs-Call (nur v1 sinnvoll).
    format: Pflicht, genau EIN Wert aus models.analyst.FORMATE. Die Auswahl ist BINDEND —
    das Modell klassifiziert das Format nicht mehr selbst (der Nutzer kennt sein Video)."""
    if engine not in ENGINES:
        raise HTTPException(status_code=422, detail=f"Unbekannte Engine '{engine}'. Erlaubt: {', '.join(sorted(ENGINES))}")
    gewaehlt = (format or "").strip()
    if not gewaehlt:
        raise HTTPException(status_code=422, detail=f"Bitte ein Format wählen. Erlaubt: {', '.join(FORMATE)}")
    if gewaehlt not in FORMATE:
        raise HTTPException(
            status_code=422,
            detail=f"Unbekanntes Format: {gewaehlt}. Erlaubt: {', '.join(FORMATE)}",
        )
    run_dir = _run_dir(run_id)
    status = json.loads((run_dir / "status.json").read_text())
    if status.get("phase") in RUNNING_PHASES:
        raise HTTPException(status_code=409, detail="Analyse läuft bereits")
    ok, msg = analyst_vlm.is_available()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    # engine + skip_eval in meta.json persistieren, damit die Engine sie liest
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["skip_eval"] = skip_eval
    meta["engine"] = engine
    meta["planned_text_hook"] = (planned_text_hook or "").strip()
    meta["format"] = gewaehlt
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    write_status(run_dir, "starting", "Analyse startet…")
    background.add_task(run_analysis, run_id)
    return {"id": run_id, "status": "started", "skip_eval": skip_eval, "engine": engine, "format": gewaehlt}


@router.get("/{run_id}")
async def get_analysis(run_id: str):
    run_dir = _run_dir(run_id)
    status = json.loads((run_dir / "status.json").read_text())
    out = {"id": run_id, **status}
    if status.get("phase") in RUNNING_PHASES:
        ids = [name for _, name in _active_runs()]
        total = len(ids)
        ahead = ids.index(run_id) if run_id in ids else 0
        out["queue"] = {"ahead": ahead, "total": total}
    analysis = run_dir / "analysis.json"
    if status.get("done") and analysis.exists():
        out["result"] = json.loads(analysis.read_text())
    return out


# ---------- Admin-/Feedback-Ansicht (Experten-Feedback sammeln) ----------
# Bewusst NUR sammeln: kein Auto-Fix, kein Auto-Commit. Die Auswertung passiert später
# auf Befehl, mit dem Menschen am Merge-Knopf.

class FeedbackIn(BaseModel):
    password: str = ""
    field_id: str = ""            # z.B. "hook.sprech", "action_steps", "performance_score"
    verdict: str = ""             # "up" | "down" | "" (nur Text, ohne Daumen)
    text: str = ""                # was genau / warum / wie besser


def _admin_ok(pw: str) -> bool:
    """Konstant-Zeit-Vergleich, damit das Passwort nicht über Antwortzeiten erratbar ist."""
    return bool(pw) and secrets.compare_digest(pw, settings.admin_password)


@router.post("/admin/verify")
async def admin_verify(body: FeedbackIn):
    """Prüft das Admin-Passwort. Das Frontend schaltet die Feedback-Felder erst nach OK frei —
    die Prüfung liegt hier auf dem Server, nicht im JavaScript."""
    if not _admin_ok(body.password):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    return {"ok": True}


@router.post("/{run_id}/feedback")
async def save_feedback(run_id: str, body: FeedbackIn):
    """Hängt einen Feedback-Eintrag an analyst_runs/<id>/feedback.jsonl an.

    Angehängt statt überschrieben: So bleibt sichtbar, wie sich ein Urteil über die Zeit ändert.
    Für die Auswertung gilt der JEWEILS LETZTE Eintrag pro (run_id, field_id).
    """
    if not _admin_ok(body.password):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    if not body.field_id.strip():
        raise HTTPException(status_code=422, detail="field_id fehlt")
    from services.analyst_eval import PROMPT_VERSION

    run_dir = _run_dir(run_id)
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    eintrag = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "filename": meta.get("filename", ""),
        "format": meta.get("format", ""),
        "prompt_version": PROMPT_VERSION,   # Feedback ohne Versionsbezug wird später irreführend
        "field_id": body.field_id.strip(),
        "verdict": body.verdict.strip(),
        "text": body.text.strip(),
    }
    with (run_dir / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    return {"ok": True, "gespeichert": eintrag["ts"]}


@router.get("/{run_id}/feedback")
async def load_feedback(run_id: str):
    """Bereits gegebenes Feedback dieses Runs (letzter Eintrag pro Feld) — damit die Admin-Ansicht
    beim erneuten Öffnen zeigt, was schon bewertet wurde."""
    pfad = _run_dir(run_id) / "feedback.jsonl"
    if not pfad.exists():
        return {}
    letzte: dict[str, dict] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.strip():
            e = json.loads(zeile)
            letzte[e["field_id"]] = {"verdict": e.get("verdict", ""), "text": e.get("text", "")}
    return letzte
