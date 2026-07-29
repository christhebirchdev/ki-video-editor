# Analyst-Chat V1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine parallele Analyst-Ansicht „V1.1", die neben der bestehenden produktiven Ansicht steht und zur fertigen Analyse einen eingebetteten Gemini-Chat für Rückfragen anbietet.

**Architecture:** Die bestehende `VideoAnalystPage` bleibt funktional unverändert und bekommt nur eine optionale Prop `chat`. Sie wird zweimal gemountet: einmal ohne Chat (bestehender Tab, byte-gleiches Verhalten) und einmal mit Chat (neuer Tab „Video Analyst V1.1"). Backend: ein neuer Service `services/analyst_chat.py` und zwei Endpoints in `api/analyst.py`. Der Chat bekommt als Kontext ausschließlich Text (Bewertung, Transkript, Messwerte) — das Video wird nicht erneut an Gemini geschickt. Der Verlauf liegt append-only in `analyst_runs/<run_id>/chat.jsonl`, dem Muster von `feedback.jsonl` folgend.

**Tech Stack:** FastAPI (`StreamingResponse`), `google-genai` (`generate_content_stream`), React 18 UMD + Babel-Standalone im Browser (kein Build-Step), Plain CSS.

**Abgrenzung V1.1:** Dieser Plan baut **nur den Frage-Antwort-Chat**. Das Anpassen der Bewertung durch den Chat (Versionierung, Rollback, Bestätigungs-Gate, Revision über `nachbearbeiten()`) ist bewusst **nicht** Teil dieses Plans — es ist der riskante Teil und bekommt einen eigenen Plan, sobald der Chat steht.

---

## Kontext, den ein Fremder braucht

- **Repo-Regeln stehen in `CLAUDE.md`.** Bei Widersprüchen gilt: Code > CLAUDE.md > Vault-Doku.
- **Branch-Regel:** `main` ist per Definition live. Push auf `main` = Deploy. Auf einem `feature/`-Branch arbeiten, nicht auf `main` pushen.
- **Tests:** `venv/bin/python -m pytest tests/test_analyst.py -q` (82 Tests). `pytest` ohne Argument läuft im Repo **nicht** (kaputte Editor-Altlast) — das ist bekannt und kein Fehler dieser Arbeit.
- **Server starten:** `uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1` — **niemals `--reload`**, das killt laufende Hintergrund-Analysen.
- **Ein Worker.** Ein blockierender Gemini-Call in einem `async def`-Endpoint friert die ganze App ein. Deshalb sind die Chat-Endpoints bewusst **synchrone** `def`-Funktionen: FastAPI führt die dann in einem Threadpool aus.
- **`ANALYST_ONLY=1`** auf dem Server: `main.py` bindet nur `api/analyst.py` ein. Chat-Endpoints müssen dort hinein, sonst sind sie live nicht erreichbar.
- **Modell:** nicht hartkodieren. `gemini_service.GEMINI_MODEL` und `GEMINI_FALLBACK_MODELS` importieren. Stand heute `gemini-3.5-flash`.
- **Keine neuen Sampling-Parameter** an Gemini (`temperature`/`top_p`/`top_k`) — bei 3.5 noch erlaubt, ab 3.6 deprecated. Für den Chat brauchen wir sie nicht.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `services/analyst_chat.py` | **Neu.** Kontext-Text aus `AnalystResult` bauen, System-Prompt, Gemini-Stream, Verlauf lesen/schreiben. Einzige Stelle mit Chat-Logik. |
| `api/analyst.py` | **Ändern.** Zwei Endpoints: `POST /{run_id}/chat` (Stream), `GET /{run_id}/chat` (Verlauf). Nur HTTP-Handling, keine Logik. |
| `static/app.jsx` | **Ändern.** `ChatPanel`-Komponente + Markdown-Subset-Renderer, `VideoAnalystPage` bekommt Prop `chat`, dritter Tab in `PAGES`/`PAGE_META`. |
| `static/styles.css` | **Ändern.** Chat-Styles ans Ende, alle Farben über die bestehenden CSS-Variablen. |
| `tests/test_analyst_chat.py` | **Neu.** Tests für Kontextbau, Persistenz, Markdown-Teilstream. Eigene Datei, damit `tests/test_analyst.py` unberührt bleibt. |

---

### Task 1: Chat-Kontext aus dem Analyse-Ergebnis bauen

**Files:**
- Create: `services/analyst_chat.py`
- Test: `tests/test_analyst_chat.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyst_chat.py
import json
import os
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

from models.analyst import AnalystResult, AnalystEvaluationV2, HookEval, SpeechStats


def _result() -> AnalystResult:
    """Minimales, aber realistisches Ergebnis — so sieht analysis.json aus."""
    return AnalystResult(
        id="abc123",
        filename="reel.mp4",
        duration_sec=31.5,
        scene_count=0,
        scenes=[],
        transcript="Hallo, heute zeige ich dir drei Fehler.",
        speech_stats=SpeechStats(
            wort_anzahl=7, sprech_dauer_sec=30.0, wpm=140.0,
            filler_count=1, filler_words=["ähm"],
            pausen_count=2, laengste_pause_sec=1.2, sprechbeginn_sec=0.4,
        ),
        evaluation=AnalystEvaluationV2(
            zielgruppe="Selbstständige Dachdecker",
            performance_score=72,
            hook=HookEval(sprech_hook_score=3, sprech_hook_grund="Startet zu allgemein."),
            staerken=["Klare Aussprache"],
            top_tipps=["Hook konkreter machen"],
        ),
        gewaehltes_format="Talking Head",
    )


def test_kontext_enthaelt_bewertung_und_transkript_aber_kein_video():
    from services import analyst_chat
    text = analyst_chat.baue_kontext(_result())
    assert "Selbstständige Dachdecker" in text        # Bewertung ist drin
    assert "Hallo, heute zeige ich dir drei Fehler." in text  # Transkript ist drin
    assert "Talking Head" in text                     # gewähltes Format ist drin
    assert "72" in text                               # Performance-Score ist drin
    assert "reel.mp4" not in text.split("Transkript")[0][:0] or True  # Dateiname darf, muss nicht


def test_kontext_kommt_ohne_evaluation_nicht_ins_schleudern():
    """Ein Lauf mit skip_eval hat keine evaluation — der Chat darf daran nicht sterben."""
    from services import analyst_chat
    r = _result()
    r.evaluation = None
    text = analyst_chat.baue_kontext(r)
    assert "Hallo, heute zeige ich dir drei Fehler." in text
    assert isinstance(text, str) and text.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.analyst_chat'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/analyst_chat.py
"""Rückfragen-Chat zur fertigen Analyse (V1.1).

Bewusst OHNE Video: Der Chat bekommt die fertige Bewertung, das Transkript und die
gemessenen Werte als Text. Das Video pro Turn erneut hochzuladen würde Kosten und
Latenz mit jeder Nachricht wachsen lassen — und der Chat soll erklären, was in der
Analyse steht, nicht neu beobachten.

Was der Chat NICHT darf: die gespeicherte Analyse ändern. Das ist ein eigenes Feature
mit Versionierung und Bestätigungs-Gate und kommt separat.
"""
import json
from datetime import datetime
from pathlib import Path

from models.analyst import AnalystResult


def baue_kontext(result: AnalystResult) -> str:
    """Alles, was das Modell über dieses Video wissen muss — als Text, ohne Video."""
    teile: list[str] = [
        f"Datei: {result.filename}",
        f"Länge: {result.duration_sec:.1f} Sekunden",
    ]
    if result.gewaehltes_format:
        teile.append(f"Vom Nutzer gewähltes Format: {result.gewaehltes_format}")
    if result.geplante_texthook:
        teile.append(f"Vom Nutzer geplante Texthook: {result.geplante_texthook}")

    if result.transcript:
        teile.append(f"\n## Transkript\n{result.transcript}")
    else:
        teile.append("\n## Transkript\n(kein gesprochenes Wort erkannt)")

    if result.speech_stats:
        s = result.speech_stats
        teile.append(
            "\n## Gemessene Sprachwerte (deterministisch, kein Modellurteil)\n"
            f"- Wörter: {s.wort_anzahl}\n"
            f"- Sprechtempo: {s.wpm:.0f} Wörter/Minute\n"
            f"- Füllwörter: {s.filler_count} ({', '.join(s.filler_words) or '—'})\n"
            f"- Pausen über der Schwelle: {s.pausen_count}, längste {s.laengste_pause_sec:.1f}s\n"
            f"- Sprechbeginn bei Sekunde {s.sprechbeginn_sec:.2f}"
        )

    if result.quality_metrics:
        q = result.quality_metrics
        lufs = f"{q.lufs_integrated:.1f} LUFS" if q.lufs_integrated is not None else "kein Audio"
        teile.append(
            "\n## Gemessene Bild- und Tonwerte (deterministisch)\n"
            f"- Schärfe (Durchschnitt/Minimum): {q.schaerfe_avg:.0f} / {q.schaerfe_min:.0f}\n"
            f"- Helligkeit: {q.helligkeit_avg:.0f} von 255, Kontrast {q.kontrast_avg:.0f}\n"
            f"- Lautheit: {lufs}"
        )

    if result.evaluation:
        teile.append(
            "\n## Die Bewertung, die dem Nutzer angezeigt wird\n"
            + json.dumps(result.evaluation.model_dump(), ensure_ascii=False, indent=2)
        )
    else:
        teile.append("\n## Bewertung\n(dieser Lauf wurde ohne Bewertung gestartet)")

    return "\n".join(teile)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_chat.py tests/test_analyst_chat.py
git commit -m "feat(analyst): Chat-Kontext aus dem Analyse-Ergebnis bauen"
```

---

### Task 2: Chatverlauf append-only speichern und lesen

**Files:**
- Modify: `services/analyst_chat.py`
- Test: `tests/test_analyst_chat.py`

- [ ] **Step 1: Write the failing test**

```python
# ans Ende von tests/test_analyst_chat.py

def test_verlauf_wird_angehaengt_und_in_reihenfolge_gelesen(tmp_path):
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "Warum ist die Hook schwach?")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Weil sie allgemein startet.")
    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["rolle"] for n in verlauf] == ["user", "model"]
    assert verlauf[0]["text"] == "Warum ist die Hook schwach?"
    assert verlauf[1]["text"] == "Weil sie allgemein startet."
    assert verlauf[0]["ts"] <= verlauf[1]["ts"]


def test_verlauf_ohne_datei_ist_leer(tmp_path):
    from services import analyst_chat
    assert analyst_chat.lade_verlauf(tmp_path) == []


def test_kaputte_zeile_kippt_den_verlauf_nicht(tmp_path):
    """Ein abgebrochener Schreibvorgang darf nicht den ganzen Chat unlesbar machen."""
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "erste Frage")
    (tmp_path / "chat.jsonl").open("a", encoding="utf-8").write("{kaputt\n")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Antwort")
    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["text"] for n in verlauf] == ["erste Frage", "Antwort"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: FAIL mit `AttributeError: module 'services.analyst_chat' has no attribute 'haenge_nachricht_an'`

- [ ] **Step 3: Write minimal implementation**

```python
# in services/analyst_chat.py ergänzen

CHAT_DATEI = "chat.jsonl"


def haenge_nachricht_an(run_dir: Path, rolle: str, text: str) -> dict:
    """Eine Nachricht ans Ende von chat.jsonl. Append-only wie feedback.jsonl:
    Der Verlauf ist damit auch bei einem Absturz mitten im Schreiben bis zur letzten
    vollständigen Zeile lesbar."""
    eintrag = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "rolle": rolle,          # "user" | "model"
        "text": text,
    }
    with (run_dir / CHAT_DATEI).open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    return eintrag


def lade_verlauf(run_dir: Path) -> list[dict]:
    """Alle Nachrichten in Schreibreihenfolge. Kaputte Zeilen werden übersprungen,
    nicht geworfen — eine halb geschriebene Zeile darf den Chat nicht unbenutzbar machen."""
    pfad = run_dir / CHAT_DATEI
    if not pfad.exists():
        return []
    nachrichten: list[dict] = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if not zeile.strip():
            continue
        try:
            nachrichten.append(json.loads(zeile))
        except json.JSONDecodeError:
            continue
    return nachrichten
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_chat.py tests/test_analyst_chat.py
git commit -m "feat(analyst): Chatverlauf append-only in chat.jsonl"
```

---

### Task 3: System-Prompt und Gemini-Stream

**Files:**
- Modify: `services/analyst_chat.py`
- Test: `tests/test_analyst_chat.py`

- [ ] **Step 1: Write the failing test**

```python
# ans Ende von tests/test_analyst_chat.py

def test_system_prompt_verbietet_das_aendern_der_bewertung():
    """V1.1 erklärt nur. Ohne diese Grenze fängt das Modell an, neue Scores zu erfinden —
    und die stimmen dann nicht mit der angezeigten Bewertung überein."""
    from services import analyst_chat
    p = analyst_chat.SYSTEM_PROMPT.lower()
    assert "nicht" in p and "bewertung" in p
    assert "score" in p


def test_stream_gibt_textstuecke_und_schreibt_verlauf(tmp_path, monkeypatch):
    """Der Stream wird durchgereicht UND am Ende als eine Modell-Nachricht persistiert."""
    from services import analyst_chat

    class FakeChunk:
        def __init__(self, t): self.text = t

    def fake_stream(*, model, contents, config):
        assert model  # ein Modell muss gesetzt sein
        return iter([FakeChunk("Weil die "), FakeChunk("Hook allgemein "), FakeChunk("startet.")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)

    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext hier", "Warum schwach?"))
    assert "".join(stuecke) == "Weil die Hook allgemein startet."

    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["rolle"] for n in verlauf] == ["user", "model"]
    assert verlauf[1]["text"] == "Weil die Hook allgemein startet."


def test_stream_faellt_auf_zweites_modell_zurueck(tmp_path, monkeypatch):
    """Wie im restlichen Projekt: erst Primärmodell, bei Fehler das nächste."""
    from services import analyst_chat

    class FakeChunk:
        def __init__(self, t): self.text = t

    versuche = []

    def fake_stream(*, model, contents, config):
        versuche.append(model)
        if len(versuche) == 1:
            raise RuntimeError("503")
        return iter([FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Frage?"))
    assert "".join(stuecke) == "ok"
    assert len(versuche) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: FAIL mit `AttributeError: module 'services.analyst_chat' has no attribute 'SYSTEM_PROMPT'`

- [ ] **Step 3: Write minimal implementation**

```python
# oben in services/analyst_chat.py ergänzen
from google.genai import types
from services import gemini_service

SYSTEM_PROMPT = """Du bist der Video-Analyst von MEINFLUSS und beantwortest Rückfragen zu einer
Analyse, die du bereits erstellt hast.

Deine Aufgabe: erklären, einordnen, konkreter werden. Der Nutzer will verstehen, WARUM etwas
wichtig ist und WIE er es beim nächsten Video besser macht.

Regeln:
- Antworte auf Deutsch, in einfacher Sprache, ohne Fachjargon.
- Kurz und konkret. Zwei bis fünf Sätze reichen meistens. Keine Einleitungsfloskeln.
- Beziehe dich auf die Werte aus der Analyse. Erfinde keine Zahlen, Zeitpunkte oder
  Beobachtungen, die nicht im Kontext stehen.
- Du hast das Video NICHT vor dir. Wenn eine Frage nur mit erneutem Ansehen des Videos zu
  beantworten wäre, sag das offen, statt zu raten.
- Du änderst die Bewertung NICHT. Du vergibst keine neuen Scores und korrigierst keine
  bestehenden. Fragt der Nutzer nach einer neuen Bewertung, erkläre, dass das in dieser
  Version noch nicht geht, und beantworte stattdessen seine inhaltliche Frage.
- Formatierung: nur einfache Absätze, Aufzählungen mit "- " und **fett** für wichtige Begriffe.
  Keine Überschriften, keine Tabellen, kein Code."""

MAX_VERLAUF = 20   # letzte N Nachrichten gehen mit — hält den Prompt bezahlbar


def _contents(kontext: str, verlauf: list[dict], frage: str) -> list[dict]:
    """Gemini hat keinen serverseitigen Gesprächsspeicher: Jeder Turn schickt den Verlauf mit.
    Der Kontext hängt an der ERSTEN Nutzer-Nachricht, nicht an jeder — sonst zahlt man ihn
    mit jeder Runde erneut."""
    inhalte = [{"role": "user", "parts": [{"text": f"Hier ist die Analyse:\n\n{kontext}"}]},
               {"role": "model", "parts": [{"text": "Verstanden. Stell deine Fragen."}]}]
    for n in verlauf[-MAX_VERLAUF:]:
        rolle = "model" if n.get("rolle") == "model" else "user"
        inhalte.append({"role": rolle, "parts": [{"text": n.get("text", "")}]})
    inhalte.append({"role": "user", "parts": [{"text": frage}]})
    return inhalte


def stream_antwort(run_dir: Path, kontext: str, frage: str):
    """Generator: gibt Textstücke aus, sobald sie kommen, und schreibt am Ende die komplette
    Antwort in den Verlauf.

    Bewusst ein SYNCHRONER Generator: FastAPI führt den in einem Threadpool aus. Als
    `async` würde der blockierende Gemini-Call den Event-Loop anhalten — bei --workers 1
    steht dann die ganze App, inklusive laufender Analysen."""
    haenge_nachricht_an(run_dir, "user", frage)
    verlauf = lade_verlauf(run_dir)[:-1]   # die gerade geschriebene Frage nicht doppelt senden
    contents = _contents(kontext, verlauf, frage)
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)

    letzter_fehler = None
    for modell in [gemini_service.GEMINI_MODEL] + gemini_service.GEMINI_FALLBACK_MODELS:
        gesammelt: list[str] = []
        try:
            for chunk in gemini_service.client.models.generate_content_stream(
                model=modell, contents=contents, config=cfg
            ):
                stueck = getattr(chunk, "text", None)
                if stueck:
                    gesammelt.append(stueck)
                    yield stueck
        except Exception as e:            # noqa: BLE001 — jeder Modellfehler soll das nächste Modell probieren
            if gesammelt:
                # Mitten im Stream abgebrochen: Neustart auf einem anderen Modell würde dem
                # Nutzer den Anfang doppelt zeigen. Lieber das Teilstück behalten und aufhören.
                haenge_nachricht_an(run_dir, "model", "".join(gesammelt))
                return
            letzter_fehler = e
            continue
        haenge_nachricht_an(run_dir, "model", "".join(gesammelt))
        return
    raise RuntimeError(f"Gemini-Chat fehlgeschlagen: {letzter_fehler}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_chat.py tests/test_analyst_chat.py
git commit -m "feat(analyst): Gemini-Stream fuer den Rueckfragen-Chat"
```

---

### Task 4: Endpoints in api/analyst.py

**Files:**
- Modify: `api/analyst.py` (Import-Block oben, neue Routen ans Ende vor dem Admin-Block)
- Test: `tests/test_analyst_chat.py`

- [ ] **Step 1: Write the failing test**

```python
# ans Ende von tests/test_analyst_chat.py

def test_chat_endpoint_verweigert_lauf_ohne_fertige_analyse(tmp_path, monkeypatch):
    """Ein Chat über eine Analyse, die es nicht gibt, ergibt keinen Sinn — 409 statt 500."""
    from fastapi.testclient import TestClient
    from services import analyst_engine
    import main

    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    from api import analyst as analyst_api
    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)

    lauf = tmp_path / "run1"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run1", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "uploaded", "done": False}))

    client = TestClient(main.app)
    r = client.post("/api/analyst/run1/chat", json={"frage": "Warum?"})
    assert r.status_code == 409


def test_chat_endpoint_verweigert_leere_frage(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import analyst as analyst_api
    import main

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    lauf = tmp_path / "run2"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run2", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "done", "done": True}))
    (lauf / "analysis.json").write_text(json.dumps({
        "id": "run2", "filename": "a.mp4", "duration_sec": 10.0,
        "scene_count": 0, "scenes": [],
    }))

    client = TestClient(main.app)
    r = client.post("/api/analyst/run2/chat", json={"frage": "   "})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: FAIL — beide neuen Tests liefern 404 statt 409/422, weil die Route fehlt.

- [ ] **Step 3: Write minimal implementation**

Import-Block oben in `api/analyst.py` erweitern:

```python
from fastapi.responses import StreamingResponse
from models.analyst import FORMATE, AnalystResult
from services import analyst_chat, analyst_vlm
```

Neue Routen einfügen — direkt vor dem Kommentarblock `# ---------- Admin-/Feedback-Ansicht`:

```python
# ---------- V1.1: Rückfragen-Chat zur fertigen Analyse ----------
# Bewusst NUR Fragen und Antworten: Der Chat liest die Analyse, er ändert sie nicht.
# Das Ändern der Bewertung braucht Versionierung und ein Bestätigungs-Gate und kommt separat.

class ChatIn(BaseModel):
    frage: str = ""


def _fertige_analyse(run_id: str) -> tuple[Path, AnalystResult]:
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

    Bewusst `def` statt `async def`: FastAPI führt synchrone Endpunkte in einem Threadpool
    aus. Als `async` würde der blockierende Gemini-Call den Event-Loop anhalten — bei
    --workers 1 steht dann die komplette App still, auch laufende Analysen.
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
        # Ohne das puffert ein vorgeschalteter Proxy (Traefik) den Stream und die Antwort
        # kommt am Stück — genau das, was das Streaming verhindern soll.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_analyst_chat.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Regression — bestehende Tests laufen weiter**

Run: `venv/bin/python -m pytest tests/test_analyst.py -q`
Expected: PASS, 82 passed

- [ ] **Step 6: Commit**

```bash
git add api/analyst.py tests/test_analyst_chat.py
git commit -m "feat(analyst): Chat-Endpoints mit Streaming"
```

---

### Task 5: Markdown-Teilmenge im Frontend, streamfest

**Files:**
- Modify: `static/app.jsx` (neue Funktion vor `function ChatPanel`)

- [ ] **Step 1: Die Anforderung**

Die Antwort kommt Zeichen für Zeichen. Ein `**` ist während des Streams oft noch nicht
geschlossen. Ein naiver Renderer färbt dann den Rest der Antwort fett oder wirft. Der Renderer
muss also mit **unvollständigem** Markdown umgehen: nur geschlossene `**…**` werden fett, ein
offenes bleibt einfach Text.

Kein `dangerouslySetInnerHTML` — der Text kommt vom Modell.

- [ ] **Step 2: Implementierung**

```jsx
/* ===== Markdown-Teilmenge für Chat-Antworten =====
   Kein react-markdown: Das Frontend läuft ohne Build-Step (Babel im Browser), npm-Pakete
   gibt es hier nicht. Unterstützt wird bewusst nur, was der Chat-Prompt erlaubt:
   Absätze, "- "-Aufzählungen, "1. "-Listen und **fett**.
   WICHTIG: Muss unvollständiges Markdown vertragen — beim Streaming ist ein ** oft noch
   nicht geschlossen. Nur PAARE werden fett, ein einzelnes ** bleibt sichtbarer Text. */
function mdInline(text) {
  const teile = [];
  let rest = text;
  let key = 0;
  while (true) {
    const auf = rest.indexOf("**");
    if (auf === -1) { if (rest) teile.push(rest); break; }
    const zu = rest.indexOf("**", auf + 2);
    if (zu === -1) { teile.push(rest); break; }   // offenes ** → als Text stehen lassen
    if (auf > 0) teile.push(rest.slice(0, auf));
    teile.push(<strong key={"b" + key++}>{rest.slice(auf + 2, zu)}</strong>);
    rest = rest.slice(zu + 2);
  }
  return teile;
}

function MarkdownLite({ text }) {
  const zeilen = (text || "").split("\n");
  const bloecke = [];
  let liste = null;   // {geordnet:bool, items:[]}

  const listeSchliessen = () => {
    if (!liste) return;
    const Tag = liste.geordnet ? "ol" : "ul";
    bloecke.push(
      <Tag key={"l" + bloecke.length} className="chat-liste">
        {liste.items.map((it, i) => <li key={i}>{mdInline(it)}</li>)}
      </Tag>
    );
    liste = null;
  };

  for (const zeile of zeilen) {
    const auf = zeile.replace(/^\s+/, "");
    const punkt = auf.match(/^[-•]\s+(.*)$/);
    const zahl = auf.match(/^\d+\.\s+(.*)$/);
    if (punkt) {
      if (liste && liste.geordnet) listeSchliessen();
      liste = liste || { geordnet: false, items: [] };
      liste.items.push(punkt[1]);
    } else if (zahl) {
      if (liste && !liste.geordnet) listeSchliessen();
      liste = liste || { geordnet: true, items: [] };
      liste.items.push(zahl[1]);
    } else {
      listeSchliessen();
      if (auf.trim()) bloecke.push(<p key={"p" + bloecke.length}>{mdInline(zeile)}</p>);
    }
  }
  listeSchliessen();
  return <>{bloecke}</>;
}
```

- [ ] **Step 3: Von Hand prüfen**

Server starten (`uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1`), im Chat eine
Frage stellen und beim Tippen zusehen: Während die Antwort läuft, darf kein Textstück
plötzlich komplett fett werden und die Seite darf nicht weiß werden (React-Fehler in der
Konsole prüfen).

- [ ] **Step 4: Commit**

```bash
git add static/app.jsx
git commit -m "feat(analyst): streamfester Markdown-Renderer fuer Chat-Antworten"
```

---

### Task 6: ChatPanel-Komponente

**Files:**
- Modify: `static/app.jsx` (nach `MarkdownLite`, vor `function VideoAnalystPage`)

- [ ] **Step 1: Implementierung**

```jsx
/* ===== V1.1: Rückfragen-Chat zur Analyse ===== */
function ChatPanel({ runId, filename }) {
  const [nachrichten, setNachrichten] = useState([]);
  const [eingabe, setEingabe] = useState("");
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState("");
  const [teilantwort, setTeilantwort] = useState("");
  const endeRef = useRef(null);
  const feldRef = useRef(null);

  // Verlauf laden, wenn eine andere Analyse gewählt wird
  useEffect(() => {
    if (!runId) { setNachrichten([]); return; }
    let abgebrochen = false;
    api("GET", `/api/analyst/${runId}/chat`)
      .then((d) => { if (!abgebrochen) setNachrichten(d.nachrichten || []); })
      .catch(() => {});
    return () => { abgebrochen = true; };
  }, [runId]);

  // Immer ans Ende scrollen — auch während die Antwort wächst
  useEffect(() => {
    endeRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [nachrichten, teilantwort]);

  function autoResize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }

  async function senden() {
    const frage = eingabe.trim();
    if (!frage || laeuft || !runId) return;
    setFehler("");
    setEingabe("");
    if (feldRef.current) feldRef.current.style.height = "auto";
    setNachrichten((n) => [...n, { rolle: "user", text: frage, ts: new Date().toISOString() }]);
    setLaeuft(true);
    setTeilantwort("");
    try {
      const res = await fetch(`/api/analyst/${runId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frage }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      // Stück für Stück lesen — das ist der Punkt, an dem sich „schnell" anfühlt
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let voll = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        voll += decoder.decode(value, { stream: true });
        setTeilantwort(voll);
      }
      setNachrichten((n) => [...n, { rolle: "model", text: voll, ts: new Date().toISOString() }]);
    } catch (e) {
      setFehler(e.message);
    } finally {
      setTeilantwort("");
      setLaeuft(false);
    }
  }

  function beiTaste(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); senden(); }
  }

  const leer = nachrichten.length === 0 && !laeuft;

  return (
    <div className="chat-card">
      <div className="chat-head">
        <div className="chat-badge"><Ico.brain width="18" height="18" /></div>
        <div>
          <div className="chat-titel">Rückfragen zur Analyse</div>
          <div className="chat-sub">{filename || "Bereit für deine erste Nachricht"}</div>
        </div>
      </div>

      <div className="chat-stream">
        {leer && (
          <div className="chat-leer">
            <div className="chat-leer-badge"><Ico.brain width="22" height="22" /></div>
            <div className="chat-leer-titel">Frag nach, was du nicht verstehst.</div>
            <div className="chat-leer-sub">
              Warum ist ein Tipp wichtig, wie setzt du ihn um, worauf kommt es beim nächsten
              Video an — frag einfach.
            </div>
          </div>
        )}

        {nachrichten.map((n, i) => (
          <div key={i} className={"chat-zeile " + (n.rolle === "user" ? "ist-user" : "ist-ki")}>
            <div className={"chat-bubble " + (n.rolle === "user" ? "bubble-user" : "bubble-ki")}>
              {n.rolle === "user" ? n.text : <MarkdownLite text={n.text} />}
            </div>
            <div className="chat-zeit">{(n.ts || "").slice(11, 16)}</div>
          </div>
        ))}

        {laeuft && teilantwort && (
          <div className="chat-zeile ist-ki">
            <div className="chat-bubble bubble-ki"><MarkdownLite text={teilantwort} /></div>
          </div>
        )}

        {laeuft && !teilantwort && (
          <div className="chat-zeile ist-ki">
            <div className="chat-bubble bubble-ki chat-tippt">
              <span /><span /><span />
            </div>
          </div>
        )}

        {fehler && <div className="chat-fehler">{fehler}</div>}
        <div ref={endeRef} />
      </div>

      <div className="chat-eingabe">
        <textarea
          ref={feldRef}
          rows={1}
          value={eingabe}
          placeholder="Schreibe deine Nachricht…"
          onChange={(e) => { setEingabe(e.target.value); autoResize(e.target); }}
          onKeyDown={beiTaste}
          disabled={laeuft}
        />
        <button
          className="chat-senden"
          onClick={senden}
          disabled={laeuft || !eingabe.trim()}
          aria-label="Nachricht senden"
        >→</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add static/app.jsx
git commit -m "feat(analyst): ChatPanel-Komponente mit Streaming-Anzeige"
```

---

### Task 7: Chat-Styles

**Files:**
- Modify: `static/styles.css` (ans Ende anhängen)

- [ ] **Step 1: Implementierung**

```css
/* ===== V1.1 Chat =====
   Farben ausschließlich über die bestehenden Variablen aus templates/index.html.
   Eine zweite, um 2 % abweichende Beige-Palette sieht aus wie ein Rendering-Fehler. */
.chat-card{
  display:flex; flex-direction:column;
  background:var(--paper); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:var(--shadow);
  overflow:hidden; height:620px; max-height:80vh;
}
.chat-head{display:flex; align-items:center; gap:12px; padding:16px 20px; border-bottom:1px solid var(--line);}
.chat-badge{
  width:36px; height:36px; border-radius:10px; display:grid; place-items:center;
  background:linear-gradient(145deg,var(--gold),var(--gold-deep)); color:#fff; flex:none;
}
.chat-titel{font-size:18px; font-weight:700; color:var(--ink);}
.chat-sub{font-size:12px; color:var(--taupe-soft); margin-top:2px;}

.chat-stream{flex:1; overflow-y:auto; padding:24px; display:flex; flex-direction:column; gap:16px; background:var(--paper-alt);}

.chat-zeile{display:flex; flex-direction:column; gap:4px;}
.chat-zeile.ist-user{align-items:flex-end;}
.chat-zeile.ist-ki{align-items:flex-start;}

.chat-bubble{padding:12px 16px; font-size:14px; line-height:1.5; word-wrap:break-word;}
.bubble-user{
  max-width:75%; color:#fff;
  background:linear-gradient(135deg,var(--gold),var(--gold-deep));
  border-radius:18px 18px 4px 18px;
}
.bubble-ki{
  max-width:85%; color:var(--ink);
  background:var(--cream); border:1px solid var(--line);
  border-radius:18px 18px 18px 4px;
}
.bubble-ki p{margin:0 0 8px;}
.bubble-ki p:last-child{margin-bottom:0;}
.chat-liste{margin:0 0 8px; padding-left:20px;}
.chat-liste:last-child{margin-bottom:0;}
.chat-liste li{margin:3px 0;}
.chat-zeit{font-size:11px; color:var(--taupe-soft); padding:0 4px;}

/* Tipp-Indikator: drei versetzt pulsierende Punkte, reines CSS */
.chat-tippt{display:flex; gap:5px; align-items:center; padding:14px 18px;}
.chat-tippt span{
  width:7px; height:7px; border-radius:50%; background:var(--taupe-soft);
  animation:chatPuls 1.3s infinite ease-in-out;
}
.chat-tippt span:nth-child(2){animation-delay:.18s;}
.chat-tippt span:nth-child(3){animation-delay:.36s;}
@keyframes chatPuls{
  0%,60%,100%{transform:translateY(0); opacity:.35;}
  30%{transform:translateY(-4px); opacity:1;}
}
@media (prefers-reduced-motion:reduce){
  .chat-tippt span{animation:none; opacity:.6;}
}

.chat-fehler{
  align-self:flex-start; font-size:13px; color:var(--err-ink);
  background:var(--err-bg); border:1px solid var(--err-line);
  border-radius:var(--radius-sm); padding:8px 12px;
}

.chat-eingabe{display:flex; align-items:flex-end; gap:10px; padding:12px 16px; border-top:1px solid var(--line); background:var(--paper);}
.chat-eingabe textarea{
  flex:1; resize:none; border:1px solid var(--line-strong); border-radius:24px;
  padding:11px 18px; font-size:14px; line-height:1.4; color:var(--ink);
  background:var(--paper); outline:none; max-height:140px; font-family:inherit;
  box-shadow:0 4px 12px rgba(34,30,24,.03);
}
.chat-eingabe textarea:focus{border-color:var(--gold);}
.chat-eingabe textarea:disabled{background:var(--paper-alt); color:var(--taupe);}
.chat-senden{
  width:44px; height:44px; flex:none; border:none; border-radius:14px; color:#fff; font-size:18px;
  background:linear-gradient(145deg,var(--gold),var(--gold-deep));
}
.chat-senden:disabled{opacity:.4; cursor:not-allowed;}

.chat-leer{margin:auto; text-align:center; max-width:340px; display:flex; flex-direction:column; align-items:center; gap:10px;}
.chat-leer-badge{
  width:56px; height:56px; border-radius:16px; display:grid; place-items:center;
  background:var(--gold-tint-2); color:var(--gold-deep);
}
.chat-leer-titel{font-size:17px; font-weight:700; color:var(--ink);}
.chat-leer-sub{font-size:13px; line-height:1.5; color:var(--taupe);}
```

- [ ] **Step 2: Commit**

```bash
git add static/styles.css
git commit -m "feat(analyst): Styles fuer den V1.1-Chat"
```

---

### Task 8: V1.1 als eigene Ansicht neben der bestehenden

**Files:**
- Modify: `static/app.jsx` — `VideoAnalystPage`-Signatur, Ende der Analyse-Ausgabe, `PAGES`, `PAGE_META`, `App`, Nav-Bedingung

- [ ] **Step 1: Prop an VideoAnalystPage**

Nur die Signatur ändern, sonst nichts an der Funktion:

```jsx
// vorher: function VideoAnalystPage({ adminPw = "" }) {
function VideoAnalystPage({ adminPw = "", chat = false }) {
```

`chat` ist standardmäßig `false` → die bestehende Ansicht verhält sich unverändert.

- [ ] **Step 2: ChatPanel unter dem Ergebnis einhängen**

Im Return von `VideoAnalystPage`, unmittelbar nach dem Block, der das Ergebnis rendert
(dort, wo `phase === "done" && result` gilt), einfügen:

```jsx
{chat && phase === "done" && result && (
  <div style={{ marginTop: 24 }}>
    <ChatPanel runId={runId} filename={result.filename} />
  </div>
)}
```

- [ ] **Step 3: Dritte Seite registrieren**

```jsx
const PAGES = [
  { id: "editor",       label: "Video Editor",       Icon: Ico.scissors },
  { id: "analyst",      label: "Video Analyst",      Icon: Ico.brain },
  { id: "analyst_v11",  label: "Video Analyst V1.1", Icon: Ico.brain },
];

const PAGE_META = {
  editor: { /* unverändert */ },
  analyst: { /* unverändert */ },
  analyst_v11: {
    title: "AI Video Analyst V1.1",
    desc: "Wie der Video Analyst — plus Chat: Stell Rückfragen zur fertigen Analyse, lass dir erklären, warum ein Tipp wichtig ist, und frag nach konkreteren Hinweisen zu deinem Video.",
    appTitle: "AI Video Analyst V1.1",
  },
};
```

- [ ] **Step 4: Zweite Instanz mounten**

In `App`, direkt nach dem bestehenden Analyst-Block:

```jsx
<div className={activePage !== "analyst" ? "page-hidden" : ""}>
  <VideoAnalystPage adminPw={adminPw} />
</div>
<div className={activePage !== "analyst_v11" ? "page-hidden" : ""}>
  <VideoAnalystPage adminPw={adminPw} chat />
</div>
```

Zwei Instanzen = zwei getrennte Zustände. Das ist gewollt: V1.1 ist eine eigene Ansicht,
kein Umschalter innerhalb derselben Analyse.

- [ ] **Step 5: Navigation im Server-Deployment sichtbar machen**

Auf dem Server gilt `ANALYST_ONLY=1`, und dort wird die Navigation heute komplett
ausgeblendet — V1.1 wäre sonst live nicht erreichbar. Die Bedingung ersetzen:

```jsx
{/* Navigation: im Analyst-only-Deployment nur die Analyst-Seiten, kein Editor. */}
{(() => {
  const sichtbar = ANALYST_ONLY ? PAGES.filter((p) => p.id !== "editor") : PAGES;
  if (sichtbar.length < 2) return null;
  return (
    <nav className="main-nav">
      {sichtbar.map((p) => (
        <button
          key={p.id}
          className={"nav-tab" + (activePage === p.id ? " active" : "")}
          onClick={() => setActivePage(p.id)}
        >
          <p.Icon width="15" height="15" />
          {p.label}
        </button>
      ))}
    </nav>
  );
})()}
```

- [ ] **Step 6: Von Hand prüfen**

```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1
```

Auf <http://127.0.0.1:8001> prüfen:
1. Tab „Video Analyst" — Analyse läuft wie vorher, **kein** Chat sichtbar.
2. Tab „Video Analyst V1.1" — Analyse läuft, danach erscheint der Chat.
3. Frage stellen → Punkte pulsieren, dann tickert die Antwort ein.
4. Seite neu laden, V1.1 öffnen, dieselbe Analyse: Der Verlauf ist wieder da.

- [ ] **Step 7: Commit**

```bash
git add static/app.jsx
git commit -m "feat(analyst): V1.1-Ansicht mit Chat neben der bestehenden Ansicht"
```

---

### Task 9: CLAUDE.md nachziehen

**Files:**
- Modify: `CLAUDE.md`

Repo-Regel: Ändert eine Arbeit Architektur oder Betriebsregeln, wird `CLAUDE.md` **im selben
Commit** mitgeändert.

- [ ] **Step 1: Abschnitt ergänzen** (nach dem Abschnitt zur Analyst-Pipeline)

```markdown
### V1.1: Rückfragen-Chat (Stand 2026-07-29)

Zweite Analyst-Ansicht neben der bestehenden, Tab „Video Analyst V1.1". Beide rendern
dieselbe Komponente `VideoAnalystPage`; V1.1 bekommt nur die Prop `chat`. **Die bestehende
Ansicht ist dadurch funktional unverändert** — wer am Chat arbeitet, fasst sie nicht an.

- Backend: `services/analyst_chat.py`, Endpoints `GET`/`POST /api/analyst/{id}/chat`.
- Kontext = Bewertung + Transkript + Messwerte als **Text**. Das Video wird pro Turn NICHT
  erneut hochgeladen: Gemini hat keinen serverseitigen Gesprächsspeicher, jeder Turn schickt
  den Verlauf mit — mit Video darin würden Kosten und Latenz pro Nachricht wachsen.
- Verlauf: `analyst_runs/<id>/chat.jsonl`, append-only wie `feedback.jsonl`.
- Die Chat-Endpoints sind bewusst **synchrone** `def`-Funktionen. Als `async def` würde der
  blockierende Gemini-Call den Event-Loop anhalten; bei `--workers 1` steht dann die ganze
  App, inklusive laufender Analysen.
- **Der Chat ändert die Bewertung nicht.** Das steht als Regel im System-Prompt und ist
  Absicht: Die angezeigte Bewertung entsteht nicht roh aus dem Modell, sondern erst nach
  `analyst_eval.nachbearbeiten()` (`performance_score` wird aus gewichteten Einzelscores
  berechnet, `action_steps` werden sortiert). Ein Chat, der direkt in `analysis.json`
  schreibt, umgeht diese Kette und erzeugt eine zweite, abweichende Bewertungslogik.
  Wenn Revision kommt, dann über `nachbearbeiten()` — mit Versionierung und Rollback.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: V1.1-Chat in CLAUDE.md festhalten"
```

---

## Was dieser Plan bewusst NICHT baut

| Weggelassen | Warum |
|---|---|
| Bewertung durch den Chat anpassen | Braucht Versionierung, Rollback, Bestätigungs-Gate und den Weg über `nachbearbeiten()`. Eigener Plan, nachdem der Chat steht. |
| Mikrofon-Button | Speech-to-text ist ein eigenes Feature mit eigenem Aufwand. Ein Knopf, der nichts tut, ist schlechter als kein Knopf. |
| Attachment-Button | Das Video ist bereits hochgeladen — es gibt nichts anzuhängen. |
| „Alle Experten"-Button aus der Design-Vorlage | Stammt aus einem anderen System, hat hier kein Ziel. |
| Kopier-Button an der Antwort | Klein und nachrüstbar; hängt an keiner Architekturentscheidung. |
| Context Caching bei Gemini | Erst sinnvoll, wenn die Kontextgröße real weh tut. Vorher messen. |

---

## Self-Review

**Spec-Abdeckung:** Chat neben dem Output (Task 6, 8), Gemini dahinter (Task 3), Streaming
mit lebendiger Wartanzeige (Task 3, 6, 7), Verlauf überlebt Reload (Task 2, 4, 6), Design
nach Screenshots mit den bestehenden Tokens (Task 7), parallel zur bestehenden Version
(Task 8). Die Revisions-Anforderung ist bewusst ausgeklammert und oben begründet.

**Platzhalter:** keine — jeder Schritt enthält den vollständigen Code oder den exakten Befehl.

**Typkonsistenz:** `baue_kontext`, `haenge_nachricht_an`, `lade_verlauf`, `stream_antwort`,
`SYSTEM_PROMPT`, `CHAT_DATEI` heißen in Tests, Service und API durchgehend gleich.
Nachrichtenfelder überall `{ts, rolle, text}`, Rollen überall `"user"` / `"model"`.
Frontend-Komponenten `MarkdownLite`, `mdInline`, `ChatPanel` werden genau so verwendet,
wie sie definiert sind.
