# Analyst-Parallelbetrieb — Minimalvariante Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zwei Analysen dürfen sich gefahrlos überlappen, ohne dass die CPU-Drosselung fällt oder ein einzelner Upload die App anhält.

**Architecture:** Kein Architekturwechsel. `ANALYST_MAX_CONCURRENT` geht von 1 auf 2 — dafür müssen drei Dinge stimmen, die bei einem Slot nicht auftreten konnten: der Whisper-Modellcache braucht ein Lock, der Upload darf den Event-Loop nicht blockieren, und Läufe, die ein Redeploy erwischt, müssen beim Start aufgeräumt werden.

**Tech Stack:** Bestand. Keine neue Abhängigkeit, kein `requirements.txt`, kein neuer Anbieter.

---

## Warum genau zwei Slots und keine zehn

Aus CLAUDE.md: *„`WhisperModel` wird ohne `num_workers` erzeugt (Default 1), gleichzeitige `transcribe()`-Aufrufe auf derselben Instanz serialisieren also intern."*

Die Transkriptionen laufen also nacheinander, egal wie viele Slots offen sind. Der einzige Gewinn ist Überlappung: Lauf A wartet auf Gemini (CPU frei), Lauf B transkribiert. Bei einem Verhältnis Whisper zu Gemini von etwa 1:1 — gemessen an fünf Server-Läufen, siehe unten — **sättigen zwei Slots die CPU bereits vollständig.** Jeder weitere Slot bringt null Durchsatz, kostet Speicher und erzeugt mehr gleichzeitige Gemini-Uploads. Er würde die Warteschlange nur unsichtbar machen, statt sie zu verkürzen.

**Datengrundlage** (Server-Läufe vom 06./07.08., `elapsed_sec` plus Zeitstempel aus `prompt_log.md`):

| Run | Video | Gesamt |
|---|---:|---:|
| 756944ad | 11,3 s | 43,8 s |
| b114839f | 38,5 s | 43,8 s |
| 4cc7a7e7 | 52,7 s | 49,2 s |
| e8313fd2 | 58,7 s | 75,6 s |
| 870b3d6c | 70,3 s | 73,0 s |

Regression: Gesamt ≈ 32 s + 0,55 × Videolänge. Der Fixanteil ist Gemini, der längenabhängige Whisper. **Diese Zahlen sind eine Richtungsanzeige, keine Messung** — `phasen_sek` fehlt in allen Server-Läufen, weil der Container bis zum 08.08. Code von vor Commit `8ba4d59` fuhr. Task 4 holt die echte Messung nach.

**Bewusst NICHT in diesem Plan:**

| Weggelassen | Warum |
|---|---|
| Whisper auf gehosteten STT auslagern | Bringt den Tail bei 10 parallelen Läufen von ~6 auf ~2 min. 10 gleichzeitige Nutzer sind nicht realistisch — Entscheidung Chris, 08.08. Design liegt geparkt in `2026-08-08-analyst-stt-auslagern.md`. |
| `ANALYST_MAX_CONCURRENT` über 2 | Sättigt nicht mehr, siehe oben. |
| Redis / Celery / Worker-Prozesse | Der vorhandene Semaphore reicht. |
| Mehrere uvicorn-Worker | Würde den prozess-lokalen Semaphore aufheben. |
| Semaphore um die Gemini-Calls | Tier 1 ≈ 150–300 RPM, zwei Läufe brauchen 4–6 Requests. |
| Retention / Cleanup, `_active_runs()`-Optimierung, atomare `status.json`-Writes | Betrieb und Robustheit, nicht Parallelität. Erst bei einigen hundert Altläufen relevant. |

---

## File Structure

| Datei | Änderung |
|---|---|
| `services/whisper_service.py` | Lock um den Modellcache (Task 1) |
| `api/analyst.py` | `upload_video` synchron; `RUNNING_PHASES` importieren (Tasks 2, 3) |
| `services/analyst_engine.py` | `RUNNING_PHASES` + `markiere_abgebrochene_laeufe()` (Task 3) |
| `main.py` | `lifespan` ruft den Reaper (Task 3) |
| `tests/test_analyst.py` | Tests für Lock und Reaper |
| `.env` auf dem VPS | `ANALYST_MAX_CONCURRENT=2` (Task 5) |
| `CLAUDE.md` | Betriebsregeln nachziehen (Task 6) |

---

## Task 0: Arbeitsbranch

- [ ] **Step 1: Branch anlegen**

```bash
cd ~/Documents/Claude/Projects/KI\ Marketing\ Team/ki-video-editor
git checkout -b feature/parallelbetrieb-2-slots
```

---

## Task 1: Lock um den Whisper-Modellcache

**Files:**
- Modify: `services/whisper_service.py:21-36`
- Test: `tests/test_analyst.py`

CLAUDE.md benennt diesen Fall bereits und begründet, warum er bisher offenblieb: *„`_models` ist ein Dict ohne Lock. Starten zwei Läufe kalt gleichzeitig, bauen beide ein `WhisperModel` — kurzzeitig doppelter RAM bei `mem_limit: 5g`. Ein `threading.Lock` wären drei Zeilen; noch nicht gebaut, weil der Fall bei einem Slot nicht auftreten kann."*

**Mit zwei Slots kann er auftreten** — und zwar planmäßig: Der Auto-Deploy baut den Container bei jedem Push neu, danach ist der Cache kalt. Starten zwei Nutzer gleichzeitig, lädt jeder Thread sein eigenes Modell. Das ist die Voraussetzung für Task 5 und muss vorher stehen.

- [ ] **Step 1: Test schreiben**

An `tests/test_analyst.py` anhängen:

```python
def test_whisper_modellcache_laedt_nur_einmal(monkeypatch):
    """Zwei kalt startende Läufe dürfen nicht zwei Modelle in den RAM legen.

    Ab ANALYST_MAX_CONCURRENT=2 ist das kein theoretischer Fall mehr: Nach jedem Redeploy ist
    der Cache kalt, und zwei gleichzeitig gestartete Analysen treffen genau darauf.
    """
    import threading
    from services import whisper_service

    ladevorgaenge = []

    class LangsamesModell:
        def __init__(self, name, **kw):
            ladevorgaenge.append(name)
            threading.Event().wait(0.2)   # Ladefenster aufreißen, in dem der zweite Thread ankommt

    monkeypatch.setattr(whisper_service, "_models", {})
    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("M", (), {"WhisperModel": LangsamesModell}),
    )

    ergebnisse = []
    threads = [
        threading.Thread(target=lambda: ergebnisse.append(whisper_service._get_model("small")))
        for _ in range(2)
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    assert ladevorgaenge == ["small"], f"Modell mehrfach geladen: {ladevorgaenge}"
    assert ergebnisse[0] is ergebnisse[1], "Threads bekamen verschiedene Instanzen"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_analyst.py::test_whisper_modellcache_laedt_nur_einmal -q`
Expected: FAIL mit `AssertionError: Modell mehrfach geladen: ['small', 'small']`

- [ ] **Step 3: Lock einbauen**

In `services/whisper_service.py`: `import threading` oben ergänzen, dann den Cache-Block ersetzen.

**Vorher:**

```python
# Mehrere Modelle parallel gecached (für A/B-Vergleich verschiedener Engines).
_models: dict = {}
DEFAULT_WHISPER_MODEL = "small"  # Default für v5.2 und ältere Engines


def _get_model(model_name: str):
    """..."""
    m = _models.get(model_name)
    if m is None:
        from faster_whisper import WhisperModel
        print(f"  [WHISPER] Lade Modell '{model_name}' (erstes Mal: Download + 1-2 Min, dann gecached)…")
        m = WhisperModel(model_name, device="cpu", compute_type="int8")
        _models[model_name] = m
        print(f"  [WHISPER] Modell '{model_name}' bereit")
    return m
```

**Nachher:**

```python
# Mehrere Modelle parallel gecached (für A/B-Vergleich verschiedener Engines).
_models: dict = {}
# Ab ANALYST_MAX_CONCURRENT>1 können zwei Läufe gleichzeitig auf einen kalten Cache treffen —
# nach jedem Redeploy ist er das. Ohne Lock baut dann jeder Thread sein eigenes WhisperModel:
# kurzzeitig doppelter RAM gegen mem_limit 5g ohne Swap.
_models_lock = threading.Lock()
DEFAULT_WHISPER_MODEL = "small"  # Default für v5.2 und ältere Engines


def _get_model(model_name: str):
    """Lädt ein Modell beim ersten Aufruf (lazy) und cached es pro Name.
    model_name kann eine Größe ("small"/"large-v3") ODER eine HF-Repo-ID eines
    CTranslate2-Modells sein (z.B. "nyrahealth/faster_CrisperWhisper").

    Doppelt geprüft: Der schnelle Pfad ohne Lock ist nach dem Aufwärmen der Normalfall und
    darf keine Transkription hinter einem Lock anstellen. Die zweite Prüfung im Lock fängt
    den Thread ab, der zwischen erster Prüfung und Lock-Erwerb angekommen ist.
    """
    m = _models.get(model_name)
    if m is not None:
        return m
    with _models_lock:
        m = _models.get(model_name)
        if m is None:
            from faster_whisper import WhisperModel
            print(f"  [WHISPER] Lade Modell '{model_name}' (erstes Mal: Download + 1-2 Min, dann gecached)…")
            m = WhisperModel(model_name, device="cpu", compute_type="int8")
            _models[model_name] = m
            print(f"  [WHISPER] Modell '{model_name}' bereit")
    return m
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_analyst.py::test_whisper_modellcache_laedt_nur_einmal -q`
Expected: PASS

- [ ] **Step 5: Committen**

```bash
git add services/whisper_service.py tests/test_analyst.py
git commit -m "fix(whisper): Modellcache thread-sicher — Voraussetzung für zwei Slots"
```

---

## Task 2: Upload blockiert nicht mehr den Event-Loop

**Files:**
- Modify: `api/analyst.py:52`

Der größte Hebel pro geändertem Zeichen. `upload_video` ist `async def`, macht darin aber synchron `shutil.copyfileobj`. Ein einziger großer Upload hält den Event-Loop an — und damit alle Polls, alle Chat-Streams und den Start jeder anderen Analyse. Bei einem Slot fiel das kaum auf, weil ohnehin nur einer arbeitete. Bei zwei ist es der Unterschied zwischen Überlappung und Stillstand.

FastAPI führt synchrone Endpunkte im Threadpool aus. `def` statt `async def` löst es vollständig. Dieselbe Begründung steht bereits im Docstring von `chat_frage`.

- [ ] **Step 1: Endpunkt umstellen**

In `api/analyst.py`:

```python
@router.post("/upload")
def upload_video(file: UploadFile = File(...)):
    """Bewusst `def` statt `async def`: `shutil.copyfileobj` unten ist blockierend. In einer
    `async def`-Funktion würde ein großer Upload den Event-Loop anhalten und bei `--workers 1`
    die ganze App blockieren — inklusive laufender Analysen. Als `def` läuft der Endpunkt im
    Threadpool. Gleiche Begründung wie bei `chat_frage`."""
    run_id = str(uuid.uuid4())[:8]
```

Der übrige Funktionskörper bleibt unverändert.

- [ ] **Step 2: Verifizieren**

Run:
```bash
venv/bin/python -c "
import inspect
from api import analyst
assert not inspect.iscoroutinefunction(analyst.upload_video), 'noch async'
print('upload_video ist synchron — läuft im Threadpool')
"
```
Expected: `upload_video ist synchron — läuft im Threadpool`

- [ ] **Step 3: Analyst-Tests laufen lassen**

Run: `venv/bin/python -m pytest tests/test_analyst.py tests/test_analyst_chat.py -q`
Expected: PASS

- [ ] **Step 4: Committen**

```bash
git add api/analyst.py
git commit -m "fix(analyst): Upload blockiert nicht mehr den Event-Loop"
```

---

## Task 3: Abgebrochene Läufe beim Start aufräumen

**Files:**
- Modify: `services/analyst_engine.py`
- Modify: `api/analyst.py:22`
- Modify: `main.py`
- Test: `tests/test_analyst.py`

Der Auto-Deploy baut den Container bei **jedem** Push neu. Läuft dabei eine Analyse, ist ihr Thread weg — aber `status.json` steht für immer auf `transcribe` oder `evaluate`. Das Frontend pollt endlos, und `_active_runs()` zählt die Leiche als „vor dir in der Schlange". Mit zwei Slots verdoppelt sich die Trefferwahrscheinlichkeit pro Deploy, und die Warteschlangen-Anzeige wird ab jetzt tatsächlich benutzt — vorher war sie bei einem Slot Kosmetik.

- [ ] **Step 1: Test schreiben**

An `tests/test_analyst.py` anhängen:

```python
def test_reaper_markiert_haengende_laeufe(tmp_path, monkeypatch):
    """Nach einem Neustart existiert zu einem 'laufenden' Status kein Thread mehr."""
    import json as _json
    from services import analyst_engine

    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    haengt = tmp_path / "aaa11111"; haengt.mkdir()
    analyst_engine.write_status(haengt, "evaluate", "Analyse läuft…")
    fertig = tmp_path / "bbb22222"; fertig.mkdir()
    analyst_engine.write_status(fertig, "done", "fertig", done=True)

    assert analyst_engine.markiere_abgebrochene_laeufe() == 1
    assert _json.loads((haengt / "status.json").read_text())["phase"] == "error"
    assert _json.loads((fertig / "status.json").read_text())["phase"] == "done"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_analyst.py::test_reaper_markiert_haengende_laeufe -q`
Expected: FAIL mit `AttributeError: module 'services.analyst_engine' has no attribute 'markiere_abgebrochene_laeufe'`

- [ ] **Step 3: `RUNNING_PHASES` in die Engine ziehen und Reaper schreiben**

In `services/analyst_engine.py`, direkt unter die `_SLOTS`-Definition:

```python
# Phasen, in denen ein Lauf als aktiv gilt. Liegt hier statt in api/analyst.py, weil beide
# Module sie brauchen — der Reaper unten und die Warteschlangen-Anzeige im API-Modul.
RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}
```

Ans Dateiende:

```python
def markiere_abgebrochene_laeufe() -> int:
    """Setzt Läufe, die beim letzten Prozessende mitten in der Pipeline standen, auf `error`.

    Nach einem Neustart gibt es zu einem „laufenden" Status keinen Thread mehr. Ohne diesen
    Durchlauf bleibt der Status für immer stehen: das Frontend pollt endlos, und `_active_runs()`
    zählt die Leiche als Vordermann in der Warteschlange. Der Auto-Deploy baut den Container bei
    jedem Push neu — der Fall tritt planmäßig ein, nicht nur bei Abstürzen.

    Rückgabe: Anzahl der aufgeräumten Läufe (fürs Startlog).
    """
    aufgeraeumt = 0
    for d in ANALYST_PATH.iterdir():
        if not d.is_dir():
            continue
        try:
            phase = json.loads((d / "status.json").read_text()).get("phase")
        except (OSError, json.JSONDecodeError):
            continue
        if phase in RUNNING_PHASES:
            write_status(d, "error", error="Serverneustart während der Analyse — bitte neu starten")
            aufgeraeumt += 1
    return aufgeraeumt
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_analyst.py::test_reaper_markiert_haengende_laeufe -q`
Expected: PASS

- [ ] **Step 5: Doppelte Definition in `api/analyst.py` entfernen**

Diese Zeile löschen:

```python
RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}
```

Und den bestehenden Import erweitern:

```python
from services.analyst_engine import ANALYST_PATH, RUNNING_PHASES, run_analysis, write_status
```

- [ ] **Step 6: Reaper beim Start aufrufen**

In `main.py`:

```python
from contextlib import asynccontextmanager
from services.analyst_engine import markiere_abgebrochene_laeufe


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = markiere_abgebrochene_laeufe()
    if n:
        print(f"  [ANALYST] {n} Lauf/Läufe nach Neustart als abgebrochen markiert")
    yield


app = FastAPI(title="KI Video Editor", version="0.1.0", lifespan=lifespan)
```

- [ ] **Step 7: Vollständige Analyst-Suite laufen lassen**

Run: `venv/bin/python -m pytest tests/test_analyst.py tests/test_analyst_chat.py tests/test_server_deploy.py -q`
Expected: PASS, keine Fehler

- [ ] **Step 8: Committen**

```bash
git add services/analyst_engine.py api/analyst.py main.py tests/test_analyst.py
git commit -m "fix(analyst): haengende Laeufe beim Start als abgebrochen markieren"
```

---

## Task 4: Deployen und die echte Messung nachholen

**Kein Code.** Der Slot bleibt in diesem Schritt noch auf 1 — erst wird der Code ausgerollt und gemessen, dann hochgedreht. Zwei Änderungen gleichzeitig machen einen Fehler nicht zuordenbar.

- [ ] **Step 1: Lokal grün, dann mergen**

```bash
venv/bin/python -m pytest tests/test_analyst.py tests/test_analyst_chat.py tests/test_server_deploy.py -q
git checkout main && git merge feature/parallelbetrieb-2-slots
git push          # verlangt die getippte Bestätigung DEPLOY (pre-push-Hook)
```

- [ ] **Step 2: Prüfen, dass der Container wirklich neu gebaut wurde**

```bash
# auf dem VPS
cd /docker/analyst && git log --oneline -1
docker inspect analyst --format '{{.Created}}'
```

Ist das Container-Datum älter als der Commit, hat der Build nicht gegriffen → `docker compose up -d --build`.

- [ ] **Step 3: Startlog prüfen**

```bash
docker compose logs --tail 40 analyst
```

Erwartet: Startet sauber. Falls Altläufe hingen, erscheint die Zeile `[ANALYST] n Lauf/Läufe nach Neustart als abgebrochen markiert`.

- [ ] **Step 4: Einen Lauf über die Live-Seite und `phasen_sek` lesen**

```bash
docker exec analyst python3 -c '
import json, glob, os
p = max(glob.glob("analyst_runs/*/analysis.json"), key=os.path.getmtime)
d = json.load(open(p))
print("video  ", d.get("duration_sec"))
print("phasen ", d.get("phasen_sek"))
print("total  ", d.get("elapsed_sec"))'
```

Das ist die Zahl, die bisher fehlte. Sie beantwortet, ob zwei Slots das Richtige sind:

| `transkript` vs. `bewertung` | Bedeutung | Konsequenz |
|---|---|---|
| ~1× oder mehr | Whisper und Gemini gleichauf, wie geschätzt | 2 Slots sind richtig — weiter mit Task 5 |
| deutlich unter 0,5× | Gemini und Netz dominieren, CPU ist kaum belastet | 3 Slots wären ebenfalls sicher; trotzdem erst mit 2 anfangen und beobachten |
| deutlich über 2× | Whisper dominiert stärker als geschätzt | 2 Slots bringen wenig. Dann lohnt der geparkte STT-Plan doch — `2026-08-08-analyst-stt-auslagern.md` |

- [ ] **Step 5: Zahl in CLAUDE.md eintragen** (siehe Task 6, Step 2)

---

## Task 5: Zweiten Slot freigeben

**Kein Code.** Der Schalter existiert seit jeher.

- [ ] **Step 1: `.env` auf dem VPS ändern**

```bash
cd /docker/analyst && nano .env
```

```
ANALYST_MAX_CONCURRENT=2
```

- [ ] **Step 2: Container neu erstellen**

```bash
docker compose up -d
```

**Nicht `restart`** — die geänderte `env_file` greift erst beim Neuerstellen des Containers.

- [ ] **Step 3: Verifizieren, dass der Wert angekommen ist**

```bash
docker exec analyst python3 -c 'from config import settings; print("Slots:", settings.analyst_max_concurrent)'
```
Expected: `Slots: 2`

- [ ] **Step 4: Überlappung real prüfen**

Zwei Videos in zwei Browser-Tabs kurz nacheinander starten. Erwartet:

- Beide zeigen eine laufende Phase, keiner hängt auf „In Warteschlange".
- Beide Läufe kommen durch, kein `error`.
- Die Gesamtzeit des zweiten ist spürbar kürzer als vorher (er wartet nicht mehr die volle erste Analyse ab, sondern nur dessen Transkription).

Parallel dazu auf dem VPS mitschauen:

```bash
docker stats analyst --no-stream
```

`MEM USAGE` muss deutlich unter dem Limit von 5 GiB bleiben. Reißt es die Grenze, ist der Lock aus Task 1 nicht wirksam — dann sofort zurück auf `ANALYST_MAX_CONCURRENT=1`.

- [ ] **Step 5: n8n gegenprüfen**

Einen bekannten n8n-Workflow starten, während zwei Analysen laufen. Läuft er normal durch, ist die CPU-Drosselung (`cpus: 1.5`) intakt. Das ist der eigentliche Schutz — er bleibt in diesem Plan unangetastet.

---

## Task 6: CLAUDE.md nachziehen

**Files:**
- Modify: `CLAUDE.md`

Pflegeregel des Repos: Betriebsregeln ändern sich, also gehört die Datei in denselben Vorgang.

- [ ] **Step 1: Abschnitt „Mehrere Läufe parallel" aktualisieren**

Der Absatz über `_models` ohne Lock beschreibt einen behobenen Zustand. Punkt 3 der Liste „Vor dem Hochdrehen zu klären" ersetzen durch:

```markdown
3. ~~`_models` ohne Lock~~ — **erledigt 2026-08-08.** `_get_model()` nutzt doppelt geprüftes
   Locking (`_models_lock`), zwei kalt startende Läufe laden nur noch ein Modell. Test:
   `tests/test_analyst.py::test_whisper_modellcache_laedt_nur_einmal`.
```

Und darunter ergänzen:

```markdown
**Stand 2026-08-08: `ANALYST_MAX_CONCURRENT=2` auf dem Server.** Zwei Slots sättigen die CPU
bereits, weil Whisper intern serialisiert und Whisper zu Gemini etwa 1:1 steht. Ein dritter Slot
brächte keinen Durchsatz mehr, nur mehr Speicher und mehr gleichzeitige Gemini-Uploads — er würde
die Warteschlange unsichtbar machen statt sie zu verkürzen.

**Verworfen (2026-08-08): Whisper auf einen gehosteten STT auslagern.** Würde den Tail bei zehn
gleichzeitigen Läufen von ~6 auf ~2 min drücken. Zehn gleichzeitige Nutzer sind nicht realistisch;
der zweite Anbieter samt Neukalibrierung von `PAUSE_THRESHOLD_SEC` steht dafür nicht dafür.
Fertiges Design liegt in `docs/superpowers/plans/2026-08-08-analyst-stt-auslagern.md` — erst wieder
hervorholen, wenn `phasen_sek.transkript` dauerhaft über 2× `phasen_sek.bewertung` liegt.
```

- [ ] **Step 2: Gemessene Phasenzeiten eintragen**

Im Absatz über `phasen_sek` die Zahl aus Task 4 Step 4 ergänzen:

```markdown
**Erste echte Server-Messung (2026-08-08):** `<video>` s Video → transkript `<x>` s /
messwerte `<y>` s / bewertung `<z>` s. Vorher lag in keinem Server-Lauf ein `phasen_sek` vor —
der Container fuhr bis zum 08.08. Code von vor Commit `8ba4d59`.
```

- [ ] **Step 3: Committen und pushen**

```bash
git add CLAUDE.md
git commit -m "docs: zwei Slots, Whisper-Lock, erste Server-Phasenmessung"
git push
```

---

## Rückfallweg

`ANALYST_MAX_CONCURRENT=1` in der VPS-`.env`, `docker compose up -d`. Damit ist der alte Zustand exakt wiederhergestellt. Die Code-Änderungen aus Task 1–3 bleiben dabei sinnvoll und richtig — sie sind Korrektheitsfixes, keine Durchsatzmaßnahmen.

---

## Self-Review

**Abdeckung:**

| Anforderung (Chris, 08.08.) | Task |
|---|---|
| Wenig Aufwand, größter Hebel | 2 (ein Wort), 5 (ein .env-Wert) |
| Server-CPU nicht überlasten | 5 Step 4+5; `cpus: 1.5` bleibt unangetastet |
| Normale Nutzung nicht gefährden | 1 (RAM-Schutz), 2 (kein Einfrieren), 3 (keine Geisterläufe), Rückfallweg |
| Entscheidung auf Daten stellen | 4 |

**Keine Platzhalter** außer den drei bewusst markierten Messwerten in Task 6 Step 2, die erst aus Task 4 Step 4 stammen können.

**Typkonsistenz:** `markiere_abgebrochene_laeufe() -> int` und `RUNNING_PHASES` sind je genau einmal definiert (`services/analyst_engine.py`) und werden in `api/analyst.py`, `main.py` und dem Test identisch verwendet. `_get_model(model_name)` behält Signatur und Rückgabewert.
