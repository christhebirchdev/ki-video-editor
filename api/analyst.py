# api/analyst.py
"""AI Video Analyst: Upload → Start (BackgroundTask) → Status/Ergebnis pollen."""
import json
import re
import secrets
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from config import settings
from models.analyst import FORMATE, AnalystResult
from services import analyst_chat, analyst_vlm
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


# ---------- V1.1: Rückfragen-Chat zur fertigen Analyse ----------
# Bewusst NUR Fragen und Antworten: Der Chat liest die Analyse, er ändert sie nicht.
# Das Ändern der Bewertung braucht Versionierung, Rollback und ein Bestätigungs-Gate und
# muss über analyst_eval.nachbearbeiten() laufen — das kommt separat.

class ChatIn(BaseModel):
    frage: str = ""


def _fertige_analyse(run_id: str) -> tuple[Path, AnalystResult]:
    """Run-Verzeichnis + geparste Analyse. 409, solange es noch keine fertige Analyse gibt —
    ein Chat über ein Ergebnis, das es nicht gibt, ist kein Serverfehler, sondern ein
    Zustandsproblem."""
    run_dir = _run_dir(run_id)
    pfad = run_dir / "analysis.json"
    if not pfad.exists():
        raise HTTPException(status_code=409, detail="Für diesen Lauf gibt es noch keine fertige Analyse")
    return run_dir, AnalystResult(**json.loads(pfad.read_text()))


@router.get("/{run_id}/chat")
def chat_verlauf(run_id: str):
    """Bisheriger Chatverlauf — damit die Ansicht nach einem Reload nicht leer ist."""
    return {"nachrichten": analyst_chat.lade_verlauf(_run_dir(run_id))}


@router.post("/{run_id}/chat")
def chat_frage(run_id: str, body: ChatIn):
    """Antwort wird gestreamt, damit die Anzeige sofort etwas zeigt statt zu warten.

    Bewusst `def` statt `async def`: FastAPI führt synchrone Endpunkte in einem Threadpool aus.
    Als `async def` würde der blockierende Gemini-Call den Event-Loop anhalten — bei
    `--workers 1` steht dann die komplette App still, auch laufende Hintergrund-Analysen.
    """
    frage = (body.frage or "").strip()
    if not frage:
        raise HTTPException(status_code=422, detail="Bitte eine Frage eingeben")
    if len(frage) > 4000:
        raise HTTPException(status_code=422, detail="Frage ist zu lang (max. 4000 Zeichen)")
    run_dir, result = _fertige_analyse(run_id)
    kontext = analyst_chat.baue_kontext(result)
    return StreamingResponse(
        analyst_chat.stream_antwort(run_dir, kontext, frage),
        media_type="text/plain; charset=utf-8",
        # Ohne diese Header puffert ein vorgeschalteter Proxy (auf dem VPS läuft Traefik davor)
        # den Stream und liefert die Antwort am Stück — genau das, was Streaming verhindern soll.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


class AusschnittIn(BaseModel):
    start_sec: float = 0.0
    end_sec: float = 0.0
    frage: str = ""


# Kürzer als 1 s liefert Gemini kaum Substanz, länger als 60 s ist keine „Stelle" mehr,
# sondern das halbe Video — und dann ist der reguläre Analyselauf das richtige Werkzeug.
AUSSCHNITT_MIN_SEC = 1.0
AUSSCHNITT_MAX_SEC = 60.0


@router.post("/{run_id}/chat/ausschnitt")
def chat_ausschnitt(run_id: str, body: AusschnittIn):
    """Schaut das Video für ein Zeitfenster erneut an und beurteilt diese Stelle.

    Läuft über dieselbe Urteilsgrundlage wie die Hauptanalyse (analyst_eval_skill.md), aber
    mit eigenem Ausgabe-Vertrag: Der Ausschnitt liefert KEINEN Gesamtscore und KEINE
    action_steps — die berechnet der Code über das ganze Video. Das Ergebnis landet als
    normale Nachricht im Chatverlauf und wird nicht in analysis.json geschrieben.
    """
    run_dir, result = _fertige_analyse(run_id)

    start = round(max(0.0, body.start_sec), 1)
    ende = round(body.end_sec, 1)
    if ende <= start:
        raise HTTPException(status_code=422, detail="Ende muss nach dem Start liegen")
    if result.duration_sec and ende > result.duration_sec + 0.5:
        raise HTTPException(
            status_code=422,
            detail=f"Das Video ist nur {result.duration_sec:.1f} Sekunden lang",
        )
    dauer = ende - start
    if dauer < AUSSCHNITT_MIN_SEC:
        raise HTTPException(status_code=422, detail=f"Der Ausschnitt muss mindestens {AUSSCHNITT_MIN_SEC:.0f} Sekunde lang sein")
    if dauer > AUSSCHNITT_MAX_SEC:
        raise HTTPException(
            status_code=422,
            detail=f"Höchstens {AUSSCHNITT_MAX_SEC:.0f} Sekunden am Stück — für mehr ist der reguläre Analyselauf da",
        )
    if len(body.frage or "") > 4000:
        raise HTTPException(status_code=422, detail="Frage ist zu lang (max. 4000 Zeichen)")

    try:
        analyst_chat._video_pfad(run_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return StreamingResponse(
        analyst_chat.stream_ausschnitt(run_dir, result, start, ende, body.frage or ""),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


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
    # Chat-Bewertungen wandern zusätzlich in den lesbaren Verlauf, damit man beim Auswerten
    # nicht feedback.jsonl und chat.jsonl von Hand über die IDs zusammenführen muss.
    if eintrag["field_id"].startswith("chat."):
        analyst_chat.schreibe_verlauf_md(run_dir)
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
