# api/analyst.py
"""AI Video Analyst: Upload → Start (BackgroundTask) → Status/Ergebnis pollen."""
import json
import os
import re
import secrets
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from config import settings
from models.analyst import FORMATE, ZIELE, AnalystResult
from services import analyst_cache, analyst_chat, analyst_marke, analyst_nutzer, analyst_vlm
from services.analyst_engine import ANALYST_PATH, RUNNING_PHASES, run_analysis, write_status

router = APIRouter()


def nutzer_der_anfrage(request: Request) -> str:
    """Die Nutzer-ID aus dem Header (siehe services/analyst_nutzer.py). Im Pflichtmodus ohne
    Nutzer: 401 — dann steht kein Agentursystem-Proxy davor, und niemand soll anonym rein."""
    n = analyst_nutzer.aktueller_nutzer(request)
    if settings.analyst_nutzer_pflicht and not n:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return n


def _run_dir(run_id: str, nutzer: str | None = None):
    """`nutzer=None` = keine Zugriffspruefung (Admin-Endpunkte mit Passwort). Fremde Laeufe melden
    404 statt 403: Ob es eine ID gibt, geht einen fremden Nutzer nichts an."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id or ""):
        raise HTTPException(status_code=404, detail="Analyse nicht gefunden")
    d = ANALYST_PATH / run_id
    if not (d / "meta.json").exists():
        raise HTTPException(status_code=404, detail=f"Analyse {run_id} nicht gefunden")
    if nutzer is not None:
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        if not analyst_nutzer.darf_zugreifen(meta, nutzer, settings.analyst_nutzer_pflicht):
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
def upload_video(file: UploadFile = File(...), nutzer: str = Depends(nutzer_der_anfrage)):
    """Bewusst `def` statt `async def`: `shutil.copyfileobj` unten ist blockierend. In einer
    `async def`-Funktion würde ein großer Upload den Event-Loop anhalten und bei `--workers 1`
    die ganze App blockieren — inklusive laufender Analysen. Als `def` läuft der Endpunkt im
    Threadpool. Gleiche Begründung wie bei `chat_frage`."""
    run_id = str(uuid.uuid4())[:8]
    run_dir = ANALYST_PATH / run_id
    (run_dir / "raw").mkdir(parents=True)
    safe_name = re.sub(r"[^\w.\-äöüÄÖÜß ]", "_", file.filename or "video.mp4")
    dest = run_dir / "raw" / safe_name
    # streamt (lädt große Files nicht in den RAM) und liefert den Inhalts-Hash gleich mit —
    # er ist die Grundlage des Analyse-Caches, siehe services/analyst_cache.py
    sha256 = analyst_cache.schreibe_und_hashe(file.file, dest)
    (run_dir / "meta.json").write_text(json.dumps({
        "id": run_id,
        "filename": safe_name,
        "created_at": datetime.now().isoformat(),
        "sha256": sha256,
        "nutzer": nutzer,   # V4: Besitzer ("" = anonym, Uebergang/V3)
    }, ensure_ascii=False))
    write_status(run_dir, "uploaded", "Bereit zur Analyse")
    # Aufbewahrung nebenbei: Der Server laeuft wochenlang ohne Neustart, der Start allein reicht
    # nicht. Ein Verzeichnis-Scan pro Upload ist bei ein paar hundert Laeufen vernachlaessigbar.
    from services.analyst_aufbewahrung import raeume_videos_auf
    raeume_videos_auf(ANALYST_PATH, settings.analyst_video_tage)
    return {"id": run_id, "filename": safe_name}


# ---------- V4: eigene Analysen, Fortschritt, Loeschen, Export ----------
# Statische Pfade stehen VOR allen "/{run_id}"-Routen — sonst haelt FastAPI "meine" fuer eine ID.

def _video_da(run_dir: Path) -> bool:
    raw = run_dir / "raw"
    return raw.is_dir() and any(f.is_file() for f in raw.iterdir())


def _lies_json(pfad: Path) -> dict:
    try:
        return json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _eintrag(run_dir: Path, meta: dict) -> dict:
    analyse = _lies_json(run_dir / "analysis.json")
    ev = analyse.get("evaluation") or {}
    return {
        "id": run_dir.name,
        "filename": meta.get("filename", ""),
        "created_at": meta.get("created_at", ""),
        "engine": meta.get("engine", ""),
        "ziel": meta.get("ziel") or analyse.get("gewaehltes_ziel", ""),
        "format": meta.get("format") or analyse.get("gewaehltes_format", ""),
        "vorgaenger": meta.get("vorgaenger", ""),
        "phase": _lies_json(run_dir / "status.json").get("phase", ""),
        "score": ev.get("performance_score") if ev else None,
        "video_da": _video_da(run_dir),
        "aus_cache": bool(meta.get("cached_from")),
    }


def _meine(request: Request, nutzer: str) -> list[tuple[Path, dict]]:
    # Uebergang Browser-ID → Konto (siehe services/analyst_nutzer.py). Nur im Pflichtmodus: Ohne
    # Proxy davor koennte jeder einen fremden Geraete-Header schicken und Laeufe an sich ziehen.
    if settings.analyst_nutzer_pflicht:
        analyst_nutzer.uebertrage(ANALYST_PATH, analyst_nutzer.geraet(request), nutzer)
    return analyst_nutzer.eigene_laeufe(ANALYST_PATH, nutzer)


@router.get("/meine")
def meine_analysen(request: Request, nutzer: str = Depends(nutzer_der_anfrage)):
    """Die eigenen Analysen, neueste zuerst — Grundlage fuer „Meine Analysen" im Frontend."""
    return {"laeufe": [_eintrag(d, m) for d, m in _meine(request, nutzer)]}


@router.get("/meine/export")
def meine_exportieren(request: Request, nutzer: str = Depends(nutzer_der_anfrage)):
    """Alle eigenen Analysen mit vollem Ergebnis (Datenauskunft, Umzug in ein anderes System)."""
    return {"nutzer": nutzer, "analysen": [
        {**_eintrag(d, m), "analyse": _lies_json(d / "analysis.json") or None,
         "chat": analyst_chat.lade_verlauf(d)}
        for d, m in _meine(request, nutzer)]}


@router.delete("/meine")
def meine_loeschen(request: Request, nutzer: str = Depends(nutzer_der_anfrage)):
    """Alle eigenen Laeufe loeschen — fuer die Account-Loeschung im Agentursystem."""
    if not nutzer:
        raise HTTPException(status_code=400, detail="Ohne Nutzer gibt es nichts zu löschen")
    laeufe = analyst_nutzer.eigene_laeufe(ANALYST_PATH, nutzer)
    for d, _ in laeufe:
        shutil.rmtree(d, ignore_errors=True)
    return {"geloescht": len(laeufe)}


@router.get("/fortschritt")
def fortschritt(request: Request, nutzer: str = Depends(nutzer_der_anfrage)):
    """Level je Kategorie + Gesamtlevel (services/analyst_fortschritt.py)."""
    from services import analyst_fortschritt
    return analyst_fortschritt.berechne([d for d, _ in _meine(request, nutzer)])


@router.post("/{run_id}/marke")
def upload_marke(run_id: str, files: list[UploadFile] = File(...),
                 nutzer: str = Depends(nutzer_der_anfrage)):
    """Optionale Zielgruppen- und Strategiedatei zu einem Lauf (Spec 1.2).

    MEHRERE Dateien, seit der Nutzer die beiden Dokumente aus dem Content-Hub hochlaedt (Vorgabe
    Chris, 2026-09-16): Die Zielgruppen-Datei sagt, WER angesprochen wird, die Strategiedatei,
    WORAUF das Konto hinarbeitet. Nur eine von beiden nehmen zu koennen hiesse, die Haelfte des
    Kontexts wegzuwerfen.

    Was sie freischaltet: `zielgruppe`/`zielgruppen_relevanz`, `zielgruppen_abgleich`,
    Texthook-Varianten in der Sprache der Zielgruppe, die Sprachlevel-Pruefung, die CTA-Passung
    und den Score fuer `protagonist_auftreten`. Worauf sie ausdruecklich NICHT wirkt: alles
    Handwerkliche (Schnitt, Ton, Untertitel, Bild) — der Geltungsbereich steht im Prompt.

    `def` statt `async def` aus demselben Grund wie bei upload_video: Das Lesen und Parsen
    (PDF, docx) ist blockierend und gehoert in den Threadpool.

    Der Text landet in meta.json und damit im Cache-Key: Derselbe Clip mit anderem Markenkontext
    ist ein legitim anderes Ergebnis. Vor dem Start hochladen — ein laufender oder fertiger Lauf
    liest meta.json nicht neu.
    """
    run_dir = _run_dir(run_id, nutzer)
    eingaben = [(f.file.read(), re.sub(r"[^\w.\-äöüÄÖÜß ]", "_", f.filename or "datei.txt"))
                for f in files]
    try:
        text, gekuerzt, namen = analyst_marke.extrahiere_mehrere(eingaben)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    # Ein Feld mit allen Namen statt einer Liste: `marke_datei` ist der SCHALTER, an dem die
    # Nachbearbeitung haengt (erzwinge_marken_abhaengige_felder) und den das Frontend anzeigt.
    # Eine Liste daraus zu machen haette jede dieser Stellen mitgeaendert, ohne etwas zu gewinnen.
    meta["marke_datei"] = ", ".join(namen)
    meta["marke_text"] = text
    meta["marke_gekuerzt"] = gekuerzt
    meta["marke_hash"] = analyst_marke.hash_von(text)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    return {"id": run_id, "dateien": namen, "woerter": len(text.split()),
            "gekuerzt": gekuerzt, "limit": analyst_marke.MAX_WOERTER}


def _eingaben(run_dir, format: str, ziel: str, planned_text_hook: str) -> dict:
    """Die meta-Felder, die den Cache-Schluessel ausmachen — so, wie /start sie schreiben wuerde."""
    from services.analyst_eval import PROMPT_VERSION
    meta = json.loads((run_dir / "meta.json").read_text())
    return {**meta, "format": (format or "").strip(),
            "ziel": (ziel or "").strip().upper(),
            "planned_text_hook": (planned_text_hook or "").strip(),
            "prompt_version": PROMPT_VERSION}


@router.get("/{run_id}/cache-check")
def cache_check(run_id: str, format: str = "", ziel: str = "", planned_text_hook: str = "",
                engine: str = "v2_split", nutzer: str = Depends(nutzer_der_anfrage)):
    """Gibt es zu DIESER Datei mit DIESEN Eingaben schon eine Analyse?

    Das Frontend fragt zwischen Upload und Start. Anlass (Kollege von Chris, 2026-09-13): Er hat
    dasselbe Video zweimal hochgeladen und zwei verschiedene Bewertungen bekommen. Die Automatik in
    /start greift nur bei voelliger Gleichheit — hat sich zwischendurch die Prompt-Version geaendert
    (jeder Deploy), laeuft die zweite Analyse ungefragt komplett neu. Der Nutzer erfaehrt nicht
    einmal, dass es die erste gab.

    Deshalb ist das hier bewusst LOCKERER als der Cache-Schluessel und meldet den Unterschied mit:
    Das Frontend fragt dann nach, ob das Video zwischendurch veraendert wurde.
    """
    run_dir = _run_dir(run_id, nutzer)
    meta = _eingaben(run_dir, format, ziel, planned_text_hook)
    meta["engine"] = engine
    quelle, abweichung = analyst_cache.finde_vorherige_analyse(ANALYST_PATH, meta, ausser=run_id)
    if not quelle:
        return {"treffer": False}
    try:
        quell_meta = json.loads((quelle / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        quell_meta = {}
    # V4: Ein fremder Lauf mit derselben Datei ist fuer diesen Nutzer kein Treffer — sonst verriete
    # die Rueckfrage Dateiname und Datum eines anderen Kunden, und „vorhandene zeigen" liefe in 404.
    if not analyst_nutzer.darf_zugreifen(quell_meta, nutzer, settings.analyst_nutzer_pflicht):
        return {"treffer": False}
    return {"treffer": True, "run_id": quelle.name, "erstellt_am": quell_meta.get("created_at", ""),
            "dateiname": quell_meta.get("filename", ""), "abweichung": abweichung}


@router.post("/{run_id}/uebernehmen")
def uebernehmen(run_id: str, von: str, nutzer: str = Depends(nutzer_der_anfrage)):
    """Die Antwort auf „nein, ich habe am Video nichts veraendert": das vorhandene Ergebnis zeigen.

    Kein Modell-Call, keine Kosten, und vor allem: dieselbe Bewertung wie beim ersten Mal. Genau
    das ist der Zweck des Caches — zwei abweichende Antworten zu demselben Video lassen den Nutzer
    im Unklaren, welche Empfehlungen jetzt gelten.
    """
    run_dir = _run_dir(run_id, nutzer)
    quelle = _run_dir(von, nutzer)
    if not (quelle / "analysis.json").exists():
        raise HTTPException(status_code=409, detail="Diese Analyse ist noch nicht fertig.")
    herkunft = analyst_cache.uebernehmen(quelle, run_dir)
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.update(herkunft)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))
    write_status(run_dir, "done", "Analyse abgeschlossen", done=True)
    return {"id": run_id, "status": "cached", **herkunft}


# V1 ist abgeschafft (Entscheidung Chris, 2026-07-31) und deshalb NICHT mehr wählbar.
# Wichtig für den Prompt: V1 bewertete OHNE Video aus Szenenbeschreibungen und nutzte denselben
# `build_system_prompt()`. Der Skill ist inzwischen für den Modus „du siehst das Video"
# geschrieben — solange V1 aufrufbar wäre, bekäme er dort einen faktisch falschen Prompt.
# Der Code von `_run_v1` liegt noch im Engine-Modul; ihn zu entfernen ist eine eigene Aufräumaktion.
# v2_split = V1.2: derselbe Prompt-Inhalt, aber auf zwei Calls geteilt (Eröffnung / Handwerk).
# Läuft bewusst NEBEN v2_hybrid (V1.1) statt es zu ersetzen — nur so lässt sich messen, ob der
# Split etwas bringt. Beide Engines nutzen dieselben Bewertungsfelder und dieselbe Nachbearbeitung.
ENGINES = {"v2_pure", "v2_hybrid", "v2_split", "v3", "v4"}
# Engines mit Pflicht-Ziel. v4 erbt die komplette V3-Zielsteuerung.
ZIEL_ENGINES = ("v3", "v4")


class StartIn(BaseModel):
    """Optionaler Body von POST /{run_id}/start — nur für den Admin-Force-Rerun.

    Das Passwort steht bewusst im Body und nicht in der Query: Query-Parameter landen in den
    Zugriffslogs von Traefik und Uvicorn, ein Passwort dort wäre im Klartext protokolliert.
    """
    password: str = ""
    force: bool = False


@router.post("/{run_id}/start")
async def start_analysis(
    run_id: str, background: BackgroundTasks, skip_eval: bool = False, engine: str = "v2_hybrid",
    planned_text_hook: str = "", format: str = "", ziel: str = "", neu: bool = False,
    vorgaenger: str = "", body: StartIn | None = None, nutzer: str = Depends(nutzer_der_anfrage),
):
    """engine: v1 (Claude bewertet aus Text) | v2_pure (nur Gemini) | v2_hybrid (Gemini + lokale Messwerte).
    skip_eval=true → nur lokale Rohanalyse (Whisper/Quality), KEIN Bewertungs-Call (nur v1 sinnvoll).
    format: Pflicht, genau EIN Wert aus models.analyst.FORMATE. Die Auswahl ist BINDEND —
    das Modell klassifiziert das Format nicht mehr selbst (der Nutzer kennt sein Video).

    Wurde dieselbe Datei mit denselben Eingaben schon analysiert, wird das gespeicherte Ergebnis
    übernommen statt neu gerechnet (Antwort: status "cached"). Grund: zwei leicht abweichende
    Bewertungen zum selben Video lassen den Nutzer im Unklaren, welche Empfehlungen gelten.
    Der Body {"force": true, "password": "…"} umgeht den Cache — nur mit Admin-Passwort."""
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
    # Ziel ist Pflicht bei v3 und wird bei den älteren Engines ignoriert: Sie kennen es nicht,
    # und ein mitgeschriebenes Ziel würde ihren Cache-Key ohne Wirkung verändern.
    gewaehltes_ziel = (ziel or "").strip().upper() if engine in ZIEL_ENGINES else ""
    if engine in ZIEL_ENGINES:
        if not gewaehltes_ziel:
            raise HTTPException(
                status_code=422,
                detail=f"Bitte ein Ziel wählen. Erlaubt: {', '.join(ZIELE)}",
            )
        if gewaehltes_ziel not in ZIELE:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekanntes Ziel: {gewaehltes_ziel}. Erlaubt: {', '.join(ZIELE)}",
            )
    run_dir = _run_dir(run_id, nutzer)
    status = json.loads((run_dir / "status.json").read_text())
    if status.get("phase") in RUNNING_PHASES:
        raise HTTPException(status_code=409, detail="Analyse läuft bereits")
    # V4-Neubewertung: Vorgaenger muss demselben Nutzer gehoeren und fertig sein. Nur v4 kennt den
    # Abgleich; bei aelteren Engines wird das Feld ignoriert (wie `ziel` bei v2).
    vorgaenger = (vorgaenger or "").strip() if engine == "v4" else ""
    if vorgaenger:
        if vorgaenger == run_id:
            raise HTTPException(status_code=422, detail="Ein Lauf kann nicht sein eigener Vorgänger sein")
        if not (_run_dir(vorgaenger, nutzer) / "analysis.json").exists():
            raise HTTPException(status_code=409, detail="Die vorherige Analyse ist noch nicht fertig.")
    # `neu=true` kommt aus der Rueckfrage „Hast du das Video veraendert?" und braucht deshalb KEIN
    # Admin-Passwort: Der Nutzer hat gerade selbst gesagt, dass die alte Analyse nicht mehr passt.
    # Wuerde /start hier trotzdem den Cache nehmen, widerspraeche das System seiner eigenen Frage.
    force = bool(body and body.force) or neu
    if force and not neu and not _admin_ok(body.password if body else ""):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    from services.analyst_eval import PROMPT_VERSION

    # engine + skip_eval in meta.json persistieren, damit die Engine sie liest
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["skip_eval"] = skip_eval
    meta["engine"] = engine
    meta["planned_text_hook"] = (planned_text_hook or "").strip()
    meta["format"] = gewaehlt
    meta["ziel"] = gewaehltes_ziel
    meta["vorgaenger"] = vorgaenger
    # Teil des Cache-Keys: dieselbe Datei unter geändertem Bewertungs-Prompt ist ein anderer Lauf.
    meta["prompt_version"] = PROMPT_VERSION
    meta_path.write_text(json.dumps(meta, ensure_ascii=False))

    # Cache-Prüfung VOR is_available(): ein gespeichertes Ergebnis braucht kein Modell.
    if not force:
        quelle = analyst_cache.finde_treffer(ANALYST_PATH, analyst_cache.cache_key(meta), run_id)
        if quelle:
            herkunft = analyst_cache.uebernehmen(quelle, run_dir)
            meta.update(herkunft)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False))
            write_status(run_dir, "done", "Analyse abgeschlossen", done=True)
            return {"id": run_id, "status": "cached", **herkunft,
                    "engine": engine, "format": gewaehlt, "ziel": gewaehltes_ziel}

    ok, msg = analyst_vlm.is_available()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    write_status(run_dir, "starting", "Analyse startet…")
    background.add_task(run_analysis, run_id)
    return {"id": run_id, "status": "started", "skip_eval": skip_eval, "engine": engine,
            "format": gewaehlt, "ziel": gewaehltes_ziel}


@router.get("/{run_id}")
async def get_analysis(run_id: str, nutzer: str = Depends(nutzer_der_anfrage)):
    run_dir = _run_dir(run_id, nutzer)
    status = json.loads((run_dir / "status.json").read_text())
    out = {"id": run_id, **status}
    if status.get("phase") in RUNNING_PHASES:
        ids = [name for _, name in _active_runs()]
        total = len(ids)
        ahead = ids.index(run_id) if run_id in ids else 0
        out["queue"] = {"ahead": ahead, "total": total}
    try:
        meta = json.loads((run_dir / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        meta = {}
    if meta.get("cached_from"):
        # Das Frontend zeigt damit „bereits am … analysiert" an — sonst wirkt ein Ergebnis,
        # das nach 0 Sekunden da ist, wie ein Fehler.
        out["cached_from"] = meta["cached_from"]
        out["cached_at"] = meta.get("cached_at", "")
    analysis = run_dir / "analysis.json"
    if status.get("done") and analysis.exists():
        out["result"] = json.loads(analysis.read_text())
    return out


# ---------- V1.1: Rückfragen-Chat zur fertigen Analyse ----------
# Der Chat bekommt das Video mit und kann einen Aspekt auf Anfrage neu ansehen. Was er NICHT
# darf: die gespeicherte Bewertung ändern oder eine konkurrierende Gesamtnote vergeben — die
# entsteht im Code über das ganze Video (analyst_eval.nachbearbeiten). Ein Schreibpfad in
# analysis.json bräuchte Versionierung, Rollback und ein Bestätigungs-Gate; das kommt separat.

class ChatIn(BaseModel):
    frage: str = ""


def _fertige_analyse(run_id: str, nutzer: str | None = None) -> tuple[Path, AnalystResult]:
    """Run-Verzeichnis + geparste Analyse. 409, solange es noch keine fertige Analyse gibt —
    ein Chat über ein Ergebnis, das es nicht gibt, ist kein Serverfehler, sondern ein
    Zustandsproblem."""
    run_dir = _run_dir(run_id, nutzer)
    pfad = run_dir / "analysis.json"
    if not pfad.exists():
        raise HTTPException(status_code=409, detail="Für diesen Lauf gibt es noch keine fertige Analyse")
    return run_dir, AnalystResult(**json.loads(pfad.read_text()))


@router.get("/{run_id}/chat")
def chat_verlauf(run_id: str, nutzer: str = Depends(nutzer_der_anfrage)):
    """Bisheriger Chatverlauf — damit die Ansicht nach einem Reload nicht leer ist."""
    return {"nachrichten": analyst_chat.lade_verlauf(_run_dir(run_id, nutzer))}


@router.post("/{run_id}/chat")
def chat_frage(run_id: str, body: ChatIn, nutzer: str = Depends(nutzer_der_anfrage)):
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
    run_dir, result = _fertige_analyse(run_id, nutzer)
    kontext = analyst_chat.baue_kontext(result)
    return StreamingResponse(
        analyst_chat.stream_antwort(run_dir, kontext, frage),
        media_type="text/plain; charset=utf-8",
        # Ohne diese Header puffert ein vorgeschalteter Proxy (auf dem VPS läuft Traefik davor)
        # den Stream und liefert die Antwort am Stück — genau das, was Streaming verhindern soll.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ---------- V4: Video einer frueheren Analyse abspielen ----------
# Anlass (Chris, 2026-11-01): Beim Oeffnen einer alten Analyse soll man das Video noch einmal sehen,
# um sicher zu sein, dass es das richtige ist. Mit Range-Unterstuetzung, weil Safari (iPhone) ohne
# 206-Antworten gar nicht abspielt und Chrome sonst nicht spulen kann — Starlettes FileResponse kann
# das in der eingesetzten Version (0.38) noch nicht.
#
# Kein Nutzer-Header moeglich: Ein <video>-Tag schickt keine eigenen Header. Ohne Pflichtmodus gilt
# deshalb dieselbe Regel wie ueberall (wer die ID kennt, sieht den Lauf); im Pflichtmodus setzt der
# Proxy den Header bei jeder Anfrage selbst, auch bei dieser.

VIDEO_TYPEN = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
_STUECK = 1024 * 1024


def _datei_stueckweise(pfad: Path, start: int, laenge: int):
    with pfad.open("rb") as f:
        f.seek(start)
        rest = laenge
        while rest > 0:
            daten = f.read(min(_STUECK, rest))
            if not daten:
                break
            rest -= len(daten)
            yield daten


@router.get("/{run_id}/video")
def lauf_video(run_id: str, request: Request, nutzer: str = Depends(nutzer_der_anfrage)):
    run_dir = _run_dir(run_id, nutzer)
    raw = run_dir / "raw"
    videos = sorted(f for f in raw.iterdir() if f.is_file()) if raw.is_dir() else []
    if not videos:
        raise HTTPException(status_code=410, detail="Das Video ist nicht mehr gespeichert.")
    pfad = videos[0]
    groesse = pfad.stat().st_size
    typ = VIDEO_TYPEN.get(pfad.suffix.lower(), "application/octet-stream")
    kopf = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("range", "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return StreamingResponse(_datei_stueckweise(pfad, 0, groesse), media_type=typ,
                                 headers={**kopf, "Content-Length": str(groesse)})
    if m.group(1):
        start = int(m.group(1))
        ende = min(int(m.group(2)), groesse - 1) if m.group(2) else groesse - 1
    else:   # "bytes=-500" = die letzten 500 Bytes
        start, ende = max(0, groesse - int(m.group(2))), groesse - 1
    if start >= groesse or start > ende:
        raise HTTPException(status_code=416, detail="Bereich ausserhalb der Datei",
                            headers={"Content-Range": f"bytes */{groesse}"})
    laenge = ende - start + 1
    return StreamingResponse(_datei_stueckweise(pfad, start, laenge), status_code=206, media_type=typ,
                             headers={**kopf, "Content-Length": str(laenge),
                                      "Content-Range": f"bytes {start}-{ende}/{groesse}"})


# ---------- V4: Lauf loeschen, dasselbe Video neu bewerten ----------

@router.delete("/{run_id}")
def lauf_loeschen(run_id: str, nutzer: str = Depends(nutzer_der_anfrage)):
    """Nur der Besitzer — Laeufe ohne Besitzer (Altlaeufe) loescht kein Nutzer."""
    run_dir = _run_dir(run_id, nutzer)
    if not nutzer or _lies_json(run_dir / "meta.json").get("nutzer") != nutzer:
        raise HTTPException(status_code=404, detail=f"Analyse {run_id} nicht gefunden")
    status = _lies_json(run_dir / "status.json")
    if status.get("phase") in RUNNING_PHASES:
        raise HTTPException(status_code=409, detail="Die Analyse läuft noch")
    shutil.rmtree(run_dir, ignore_errors=True)
    return {"geloescht": run_id}


@router.post("/{run_id}/neu-bewerten")
def neu_bewerten(run_id: str, nutzer: str = Depends(nutzer_der_anfrage)):
    """Neuer Lauf mit derselben Videodatei — z.B. um dasselbe Video gegen ein anderes Ziel zu
    bewerten (Andrea, 2026-10-23). Danach startet das Frontend ihn wie jeden Lauf.

    Hardlink statt Kopie, wo das Dateisystem es kann: kein doppelter Speicher. Ist das Video schon
    weggeraeumt (Aufbewahrungsfrist), 410 — die Analyse selbst gibt es noch, das Video nicht."""
    quelle = _run_dir(run_id, nutzer)
    videos = [f for f in (quelle / "raw").iterdir() if f.is_file()] if (quelle / "raw").is_dir() else []
    if not videos:
        raise HTTPException(status_code=410, detail="Das Video ist nicht mehr gespeichert. Bitte lade es neu hoch.")
    alt = _lies_json(quelle / "meta.json")
    neu_id = str(uuid.uuid4())[:8]
    ziel_dir = ANALYST_PATH / neu_id
    (ziel_dir / "raw").mkdir(parents=True)
    ziel = ziel_dir / "raw" / videos[0].name
    try:
        os.link(videos[0], ziel)
    except OSError:
        shutil.copy2(videos[0], ziel)
    meta = {k: alt[k] for k in ("filename", "sha256", "marke_datei", "marke_text", "marke_gekuerzt",
                                "marke_hash") if k in alt}
    meta.update({"id": neu_id, "created_at": datetime.now().isoformat(), "nutzer": nutzer,
                 "neu_bewertet_von": run_id})
    (ziel_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    write_status(ziel_dir, "uploaded", "Bereit zur Analyse")
    return {"id": neu_id, "filename": meta.get("filename", "")}


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
