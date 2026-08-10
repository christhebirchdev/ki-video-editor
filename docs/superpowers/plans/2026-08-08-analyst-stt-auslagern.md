# Analyst-Skalierung: STT auslagern — Implementation Plan

> **STATUS: GEPARKT (Entscheidung Chris, 2026-08-08).** Nicht ausführen.
> Zehn gleichzeitige Nutzer sind derzeit unrealistisch; der Gewinn (Tail von ~6 auf ~2 min)
> rechtfertigt einen zweiten Anbieter samt Neukalibrierung von `PAUSE_THRESHOLD_SEC` nicht.
> **Aktiver Plan: `2026-08-08-analyst-parallelbetrieb-minimal.md`.**
> Diesen hier wieder hervorholen, wenn `phasen_sek.transkript` auf dem Server dauerhaft über
> 2× `phasen_sek.bewertung` liegt — dann ist Whisper tatsächlich der Engpass.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den einzigen CPU-gebundenen Schritt des Analysten (lokales faster-whisper) auf einen gehosteten STT umstellen, damit ~10 Läufe gleichzeitig auf dem unveränderten VPS laufen können.

**Architecture:** `services/stt_remote.py` extrahiert Audio per ffmpeg und schickt es an die Groq-Transcriptions-API (OpenAI-kompatibel, `whisper-large-v3`, `timestamp_granularities=["word"]`). Rückgabe ist exakt dieselbe Signatur wie `whisper_service.transcribe_with_word_timestamps` → `compute_speech_stats` und alles danach bleiben unberührt. Umgeschaltet wird über `STT_BACKEND` in der `.env`; Default bleibt `local`, damit lokale Arbeit unverändert weiterläuft. Danach ist die Pipeline durchgehend I/O-gebunden und `ANALYST_MAX_CONCURRENT` wirkt zum ersten Mal wirklich.

**Tech Stack:** FastAPI, httpx (bereits gepinnt — **keine neue Abhängigkeit**, kein `requirements.txt`-Anfassen), ffmpeg (bereits im Image), Groq Audio-API.

---

## Warum genau diese Aufgaben und keine mehr

Aus CLAUDE.md, Abschnitt „Mehrere Läufe parallel": *„`WhisperModel` wird ohne `num_workers` erzeugt (Default 1), gleichzeitige `transcribe()`-Aufrufe auf derselben Instanz serialisieren also intern. Zwei Läufe transkribieren **nicht** parallel."*

Das ist der ganze Grund für diesen Plan. `ANALYST_MAX_CONCURRENT` hochzudrehen ändert am Transkriptions-Durchsatz **nichts**, solange Whisper lokal läuft. Entweder der Schritt verlässt die Maschine, oder die Warteschlange bleibt — ein Mittelweg existiert nicht.

**Bewusst NICHT in diesem Plan** (geprüft, für 10 parallele Läufe nicht nötig):

| Weggelassen | Warum |
|---|---|
| Redis / Celery / Task-Queue | Der vorhandene `BoundedSemaphore` reicht, sobald die Arbeit I/O ist. |
| Mehrere uvicorn-Worker | Würde den prozess-lokalen Semaphore aufheben (CLAUDE.md „Lokal starten"). |
| Eigener Worker-Prozess | Threads sind hier richtig: CTranslate2 entfällt, Gemini wartet auf Netz. |
| Semaphore um die Gemini-Calls | Tier 1 ≈ 150–300 RPM, ein Lauf braucht 2–3 Requests. Nicht der Engpass. |
| `threading.Lock` um `_models` | Wird gegenstandslos, sobald `STT_BACKEND=groq` steht (kein Modell-Cache mehr im Spiel). |
| Retention / Cleanup von `analyst_runs` | Betriebsthema, kein Skalierungsthema. Wird bei einigen hundert Altläufen relevant, nicht bei 10 Nutzern. |
| Optimierung von `_active_runs()` | Dito — hängt an derselben Ursache (Anzahl Altläufe). |
| Atomares Schreiben von `status.json` | Robustheit, nicht Skalierung. Kein bekannter Fehlerfall im Betrieb. |
| `requirements.txt` anfassen | CLAUDE.md „Deploy": jede Änderung baut den pip-Layer neu. `httpx` und `ffmpeg` sind da — es gibt keinen Grund, das Risiko einzugehen. |

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `services/stt_remote.py` *(neu)* | Audio-Extraktion + Groq-Call + Parsing nach `WhisperWord`. Einzige Stelle, die den Anbieter kennt. |
| `tests/test_stt_remote.py` *(neu)* | Parsing, Fehlerfälle, Audio-Extraktion. Ohne Netz. |
| `config.py` | `groq_api_key`, `stt_backend`. |
| `services/analyst_engine.py` | Backend-Weiche in `_run_v2`; `RUNNING_PHASES` + `markiere_abgebrochene_laeufe()`. |
| `api/analyst.py` | `RUNNING_PHASES` importieren statt selbst definieren; `upload_video` synchron. |
| `main.py` | `lifespan`, ruft den Reaper beim Start. |
| `.env.example` | Neue Schalter dokumentiert. |
| `CLAUDE.md` | Betriebsregeln nachziehen (Pflicht im selben Commit). |

---

## Task 0: Gate — messen, bevor irgendetwas gebaut wird

**Kein Code.** Dieser Schritt entscheidet, ob der Rest des Plans überhaupt ausgeführt wird.

- [ ] **Step 1: Phasenzeiten vom VPS lesen**

```bash
ssh <vps>   # oder direkt im hPanel-Terminal
docker exec analyst python3 -c '
import json, glob
zeilen = []
for p in sorted(glob.glob("analyst_runs/*/analysis.json")):
    try: d = json.load(open(p))
    except Exception: continue
    ph = d.get("phasen_sek") or {}
    if ph:
        zeilen.append((d.get("duration_sec"), ph.get("transkript"), ph.get("bewertung"), d.get("elapsed_sec")))
print(f"{\"video\":>8} {\"whisper\":>9} {\"gemini\":>8} {\"total\":>8}")
for z in zeilen: print(f"{z[0]:>8} {z[1]:>9} {z[2]:>8} {z[3]:>8}")
print("n =", len(zeilen))
'
```

- [ ] **Step 2: Entscheiden**

- `whisper` ≥ 2× `bewertung` → **weiter mit Task 1.**
- `whisper` ≈ `bewertung` oder kleiner → **Plan abbrechen.** Dann ist der Engpass Gemini-Latenz + Upload-Bandbreite, und ein STT-Wechsel bringt nichts. In dem Fall reicht `ANALYST_MAX_CONCURRENT=4` in der `.env` plus Task 3 und Task 4 aus diesem Plan.
- Keine Zeilen (nur Altläufe ohne `phasen_sek`) → einen frischen Lauf über die Live-Seite machen, dann Step 1 wiederholen.

- [ ] **Step 3: Groq-Key anlegen**

Account auf <https://console.groq.com> anlegen, API-Key erzeugen, auf Dev-Tier hochstufen (Free-Tier hat enge Rate-Limits und ein 25-MB-Upload-Limit). Key notieren — er kommt später in die `.env`, **nicht** ins Repo.

- [ ] **Step 4: Arbeitsbranch anlegen**

```bash
cd ~/Documents/Claude/Projects/KI\ Marketing\ Team/ki-video-editor
git checkout -b feature/stt-remote
```

---

## Task 1: `services/stt_remote.py` — Audio-Extraktion und Parsing

**Files:**
- Create: `services/stt_remote.py`
- Create: `tests/test_stt_remote.py`

- [ ] **Step 1: Test für die Audio-Extraktion schreiben**

Datei `tests/test_stt_remote.py`:

```python
"""Tests für den gehosteten STT (Groq). Kein Netz — der HTTP-Call wird gemockt."""
import subprocess
from pathlib import Path

import pytest

from services import stt_remote


def _synth_video(tmp_path: Path) -> Path:
    """2s Video mit 440-Hz-Ton. ffmpeg ist im Image und lokal vorhanden."""
    out = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", str(out),
    ], check=True, capture_output=True)
    return out


def test_extrahiere_audio_erzeugt_kleine_flac(tmp_path):
    video = _synth_video(tmp_path)
    audio = stt_remote._extrahiere_audio(video, tmp_path)
    assert audio.exists()
    assert audio.suffix == ".flac"
    # Der ganze Sinn des Schritts: deutlich kleiner als das Video.
    assert audio.stat().st_size < video.stat().st_size
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.stt_remote'`

- [ ] **Step 3: `services/stt_remote.py` mit der Extraktion anlegen**

```python
# services/stt_remote.py
"""
Transkription über einen gehosteten STT (Groq, whisper-large-v3) statt lokal.

WARUM: Lokales faster-whisper ist der einzige CPU-gebundene Schritt der Analyse. Auf dem VPS
(`cpus: 1.5`) serialisiert es zusätzlich intern — `WhisperModel` läuft ohne `num_workers`, also
transkribieren zwei Läufe auch bei mehreren Semaphore-Slots nacheinander. Solange dieser Schritt
auf der Maschine liegt, ist paralleles Arbeiten nicht möglich, egal wie die Warteschlange
konfiguriert ist. Ausgelagert ist die gesamte Pipeline I/O-gebunden.

Bewusst OHNE `groq`-SDK: der Endpunkt ist OpenAI-kompatibel, ein `httpx`-POST reicht. `httpx` ist
bereits exakt gepinnt. Eine Änderung an `requirements.txt` baut den pip-Layer im Docker-Build neu
(siehe CLAUDE.md, Abschnitt „Deploy") — dieses Risiko für einen Wrapper einzugehen wäre falsch.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import httpx

from config import settings
from models.analysis import WhisperWord

API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"

# Groq nimmt bis 100 MB (Dev-Tier). 16-kHz-Mono-FLAC sind ~1 MB/Minute → die Grenze greift erst
# bei mehreren Stunden Audio. Sie steht hier trotzdem, damit ein Fehlgriff eine klare deutsche
# Meldung erzeugt statt eines nackten HTTP 413.
MAX_AUDIO_BYTES = 95 * 1024 * 1024

TIMEOUT = httpx.Timeout(300.0, connect=15.0)


def _extrahiere_audio(video_path: Path, ziel_dir: Path) -> Path:
    """Video → 16-kHz-Mono-FLAC.

    Nicht optional: Ohne diesen Schritt ginge das komplette Video ein zweites Mal über den
    VPS-Uplink (es geht ohnehin schon zu Gemini). FLAC statt WAV, weil verlustfrei und rund
    halb so groß; 16 kHz mono, weil Whisper intern genau darauf herunterrechnet.
    """
    audio = ziel_dir / "stt_audio.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "flac", str(audio)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return audio
```

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Test für das Parsing schreiben**

An `tests/test_stt_remote.py` anhängen:

```python
GROQ_ANTWORT = {
    "text": " Hallo Welt ähm heute.",
    "words": [
        {"word": "Hallo", "start": 0.0, "end": 0.42},
        {"word": "Welt", "start": 0.44, "end": 0.90},
        {"word": "ähm", "start": 1.95, "end": 2.20},
        {"word": "heute", "start": 2.25, "end": 2.70},
    ],
}


def test_parse_liefert_whisperwords_und_text():
    words, text = stt_remote._parse(GROQ_ANTWORT)
    assert [w.word for w in words] == ["Hallo", "Welt", "ähm", "heute"]
    assert words[0].start == 0.0 and words[0].end == 0.42
    assert isinstance(words[0], WhisperWord)
    # Text wird getrimmt — Whisper liefert oft ein führendes Leerzeichen.
    assert text == "Hallo Welt ähm heute."


def test_parse_ohne_words_wirft():
    """Ohne Wort-Timestamps gibt es keine Pausenliste — dann ist der Lauf wertlos und muss
    laut scheitern, statt eine leere Sprachstatistik in die Bewertung zu tragen."""
    with pytest.raises(RuntimeError, match="Wort-Timestamps"):
        stt_remote._parse({"text": "Hallo", "segments": []})


def test_parse_stilles_video_ist_kein_fehler():
    """Ein stummes Format ist eine Entscheidung, kein Mangel (siehe neutralisiere_stumme_scores
    in analyst_eval). Leere Wortliste muss durchgehen."""
    words, text = stt_remote._parse({"text": "", "words": []})
    assert words == [] and text == ""


def test_pausenschwelle_ueberlebt_den_anbieterwechsel():
    """Die Werte aus _parse müssen von compute_speech_stats genauso verarbeitet werden wie
    faster-whisper-Wörter — sonst kippt PAUSE_THRESHOLD_SEC still."""
    from services.analyst_speech import compute_speech_stats
    words, _ = stt_remote._parse(GROQ_ANTWORT)
    st = compute_speech_stats(words)
    assert st.filler_count == 1 and st.filler_words == ["ähm"]
    assert st.pausen_count == 1                      # 0.90 → 1.95 = 1.05s
    assert st.pausen[0].start_sec == 0.9
```

- [ ] **Step 6: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: FAIL mit `AttributeError: module 'services.stt_remote' has no attribute '_parse'`

- [ ] **Step 7: `_parse` implementieren**

An `services/stt_remote.py` anhängen:

```python
def _parse(daten: dict) -> tuple[list[WhisperWord], str]:
    """Groq-`verbose_json` → (Wortliste, Volltext).

    `words` fehlt, wenn `timestamp_granularities` nicht durchkam. Das ist kein Randfall zum
    Wegloggen: ohne Wort-Timestamps entsteht keine Pausenliste, und die ist Pflichtinput für
    die Bewertung. Lieber laut scheitern.
    """
    text = (daten.get("text") or "").strip()
    if "words" not in daten:
        raise RuntimeError(
            "STT-Antwort ohne Wort-Timestamps — timestamp_granularities wurde nicht angewendet"
        )
    words = [
        WhisperWord(word=w["word"], start=float(w["start"]), end=float(w["end"]))
        for w in daten["words"]
    ]
    return words, text
```

- [ ] **Step 8: Tests laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: PASS (5 passed)

- [ ] **Step 9: Committen**

```bash
git add services/stt_remote.py tests/test_stt_remote.py
git commit -m "feat(analyst): Audio-Extraktion und Groq-Parsing für gehosteten STT"
```

---

## Task 2: Der eigentliche Transkriptions-Aufruf

**Files:**
- Modify: `services/stt_remote.py`
- Modify: `tests/test_stt_remote.py`

- [ ] **Step 1: Test mit gemocktem HTTP-Call schreiben**

An `tests/test_stt_remote.py` anhängen:

```python
def test_transcribe_remote_end_to_end(tmp_path, monkeypatch):
    video = _synth_video(tmp_path)
    gesendet = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return GROQ_ANTWORT

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, files=None, data=None, headers=None):
            gesendet["url"] = url
            gesendet["data"] = data
            gesendet["auth"] = headers.get("Authorization", "")
            return FakeResponse()

    monkeypatch.setattr(stt_remote.httpx, "Client", FakeClient)
    monkeypatch.setattr(stt_remote.settings, "groq_api_key", "gsk_test")

    words, text = stt_remote.transcribe_remote(video)

    assert [w.word for w in words] == ["Hallo", "Welt", "ähm", "heute"]
    assert text == "Hallo Welt ähm heute."
    assert gesendet["url"] == stt_remote.API_URL
    assert gesendet["auth"] == "Bearer gsk_test"
    # Diese vier Parameter tragen den Determinismus und die Pausenliste — wenn einer wegfällt,
    # merkt man es sonst erst an einer stillen Qualitätsverschlechterung in der Bewertung.
    assert gesendet["data"]["model"] == "whisper-large-v3"
    assert gesendet["data"]["response_format"] == "verbose_json"
    assert gesendet["data"]["timestamp_granularities[]"] == ["word"]
    assert gesendet["data"]["temperature"] == "0"
    assert gesendet["data"]["language"] == "de"


def test_transcribe_remote_ohne_key_wirft(tmp_path, monkeypatch):
    video = _synth_video(tmp_path)
    monkeypatch.setattr(stt_remote.settings, "groq_api_key", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        stt_remote.transcribe_remote(video)
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: FAIL mit `AttributeError: module 'services.stt_remote' has no attribute 'transcribe_remote'`

- [ ] **Step 3: `transcribe_remote` implementieren**

An `services/stt_remote.py` anhängen:

```python
def transcribe_remote(video_path: Path, language: str = "de") -> tuple[list[WhisperWord], str]:
    """Signaturgleich zu `whisper_service.transcribe_with_word_timestamps`.

    Absichtlich identisch: `compute_speech_stats`, `transkript_hash` und die gesamte
    Bewertungskette dahinter bleiben dadurch unverändert. Der Anbieterwechsel endet an dieser
    Funktionsgrenze.

    `temperature=0` pinnt das Sampling wie lokal. Bit-Identität über Läufe hinweg lässt sich bei
    einem gehosteten Dienst nicht garantieren — anders als bei faster-whisper lokal. Das ist der
    bewusst eingegangene Preis; abgesichert wird er über den Vergleich in Task 6.
    """
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY fehlt — STT_BACKEND=groq ohne Key ist nicht lauffähig")

    with tempfile.TemporaryDirectory() as tmp:
        audio = _extrahiere_audio(video_path, Path(tmp))
        groesse = audio.stat().st_size
        if groesse > MAX_AUDIO_BYTES:
            raise RuntimeError(
                f"Audiospur zu groß für den STT ({groesse // 1024 // 1024} MB, max "
                f"{MAX_AUDIO_BYTES // 1024 // 1024} MB) — Video ist zu lang"
            )
        print(f"  [STT] {video_path.name} → {groesse // 1024} KB FLAC an {MODEL}")
        with audio.open("rb") as f:
            with httpx.Client(timeout=TIMEOUT) as client:
                antwort = client.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    files={"file": (audio.name, f, "audio/flac")},
                    data={
                        "model": MODEL,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": ["word"],
                        "temperature": "0",
                        "language": language,
                    },
                )
    antwort.raise_for_status()
    return _parse(antwort.json())
```

- [ ] **Step 4: Tests laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Committen**

```bash
git add services/stt_remote.py tests/test_stt_remote.py
git commit -m "feat(analyst): transcribe_remote — Groq-Call signaturgleich zu whisper_service"
```

---

## Task 3: Backend-Weiche in Config und Engine

**Files:**
- Modify: `config.py`
- Modify: `services/analyst_engine.py:118-124` (der `if mode == "hybrid":`-Block in `_run_v2`)
- Modify: `.env.example`

- [ ] **Step 1: Config erweitern**

In `config.py`, direkt nach dem `whisper_model`-Block einfügen:

```python
    # Woher das Transkript kommt: "local" = faster-whisper auf dieser Maschine,
    # "groq" = gehosteter whisper-large-v3 (services/stt_remote.py).
    # Default "local", damit lokale Arbeit und Altverhalten unverändert bleiben; auf dem VPS
    # steht STT_BACKEND=groq, weil lokales Whisper dort parallele Läufe blockiert.
    stt_backend: str = "local"

    # Nur nötig bei STT_BACKEND=groq. Wie alle Secrets ausschließlich aus der .env —
    # dieses Repo ist öffentlich.
    groq_api_key: str = ""
```

- [ ] **Step 2: `.env.example` erweitern**

In `.env.example`, unterhalb von `WHISPER_MODEL=small` einfügen:

```
# Woher das Transkript kommt: "local" (faster-whisper, CPU) oder "groq" (gehostet).
# Auf dem VPS "groq": lokales Whisper serialisiert intern und blockiert parallele Läufe.
STT_BACKEND=local
# Nur bei STT_BACKEND=groq nötig (https://console.groq.com):
GROQ_API_KEY=gsk_...
```

- [ ] **Step 3: Test für die Weiche schreiben**

An `tests/test_stt_remote.py` anhängen:

```python
def test_engine_nutzt_backend_schalter(monkeypatch, tmp_path):
    """Die Weiche muss in _run_v2 sitzen, nicht im Aufrufer — sonst laufen v1 und v2 auseinander."""
    import config
    from services import analyst_engine

    gerufen = []
    monkeypatch.setattr(config.settings, "stt_backend", "groq")
    monkeypatch.setattr(
        stt_remote, "transcribe_remote",
        lambda v, **kw: (gerufen.append(v) or ([], "")),
    )
    assert analyst_engine._transkribiere.__module__ == "services.analyst_engine"
    analyst_engine._transkribiere(tmp_path / "egal.mp4")
    assert len(gerufen) == 1
```

- [ ] **Step 4: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py::test_engine_nutzt_backend_schalter -q`
Expected: FAIL mit `AttributeError: module 'services.analyst_engine' has no attribute '_transkribiere'`

- [ ] **Step 5: Weiche in `analyst_engine.py` einbauen**

Neue Funktion, direkt über `_run_v2` einfügen:

```python
def _transkribiere(video: Path) -> tuple[list, str]:
    """Eine Stelle für die Backend-Entscheidung. Beide Zweige liefern dieselbe Struktur,
    dahinter ist der Code identisch."""
    if settings.stt_backend == "groq":
        from services.stt_remote import transcribe_remote
        return transcribe_remote(video)
    from services.whisper_service import transcribe_with_word_timestamps
    return transcribe_with_word_timestamps(video, model_name=settings.whisper_model)
```

Dann in `_run_v2` den hybrid-Block ersetzen. **Vorher:**

```python
    if mode == "hybrid":
        from services.whisper_service import transcribe_with_word_timestamps, transkript_hash
        write_status(run_dir, "transcribe", "Transkription läuft…")
        with messe_phase(phasen, "transkript"):
            words, transcript = transcribe_with_word_timestamps(video, model_name=settings.whisper_model)
```

**Nachher:**

```python
    if mode == "hybrid":
        from services.whisper_service import transkript_hash  # reine Hash-Funktion, lädt kein Modell
        write_status(run_dir, "transcribe", "Transkription läuft…")
        with messe_phase(phasen, "transkript"):
            words, transcript = _transkribiere(video)
```

- [ ] **Step 6: Tests laufen lassen, Erfolg bestätigen**

Run: `venv/bin/python -m pytest tests/test_stt_remote.py tests/test_analyst.py -q`
Expected: PASS (8 + 82 passed)

- [ ] **Step 7: Committen**

```bash
git add config.py .env.example services/analyst_engine.py tests/test_stt_remote.py
git commit -m "feat(analyst): STT_BACKEND-Schalter, Default bleibt local"
```

---

## Task 4: Upload blockiert nicht mehr den Event-Loop

**Files:**
- Modify: `api/analyst.py:52` (`async def upload_video`)

Ohne diesen Fix ist die gewonnene Parallelität wertlos: `upload_video` ist `async def`, macht darin aber synchron `shutil.copyfileobj`. Ein einziger 100-MB-Upload hält den Event-Loop an — und damit alle Polls, alle Chat-Streams und den Start jeder anderen Analyse. FastAPI führt synchrone Endpunkte im Threadpool aus; ein `def` statt `async def` löst das vollständig. Dieselbe Begründung steht bereits im Docstring von `chat_frage`.

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

Der Rest des Funktionskörpers bleibt unverändert.

- [ ] **Step 2: Verifizieren, dass der Endpunkt weiter funktioniert**

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

## Task 5: Abgebrochene Läufe beim Start aufräumen

**Files:**
- Modify: `services/analyst_engine.py`
- Modify: `api/analyst.py:22` (`RUNNING_PHASES`)
- Modify: `main.py`
- Modify: `tests/test_analyst.py`

Der Auto-Deploy baut den Container bei **jedem** Push neu (CLAUDE.md „Deploy"). Läuft dabei eine Analyse, ist ihr Thread weg — aber `status.json` steht für immer auf `transcribe`/`evaluate`. Das Frontend pollt endlos, und `_active_runs()` zählt die Leiche als „vor dir in der Schlange". Bei einem Nutzer fällt das nicht auf, bei zehn sofort. Genau dieselbe Ursache steht schon in CLAUDE.md unter „`uvicorn` niemals mit `--reload`" — nur dass ein Redeploy sie planmäßig auslöst.

- [ ] **Step 1: Test schreiben**

An `tests/test_analyst.py` anhängen:

```python
def test_reaper_markiert_haengende_laeufe(tmp_path, monkeypatch):
    """Nach einem Neustart existiert kein Thread mehr zu einem 'laufenden' Status."""
    from services import analyst_engine

    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    haengt = tmp_path / "aaa11111"; haengt.mkdir()
    analyst_engine.write_status(haengt, "evaluate", "Analyse läuft…")
    fertig = tmp_path / "bbb22222"; fertig.mkdir()
    analyst_engine.write_status(fertig, "done", "fertig", done=True)

    assert analyst_engine.markiere_abgebrochene_laeufe() == 1

    import json
    assert json.loads((haengt / "status.json").read_text())["phase"] == "error"
    assert json.loads((fertig / "status.json").read_text())["phase"] == "done"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `venv/bin/python -m pytest tests/test_analyst.py::test_reaper_markiert_haengende_laeufe -q`
Expected: FAIL mit `AttributeError: module 'services.analyst_engine' has no attribute 'markiere_abgebrochene_laeufe'`

- [ ] **Step 3: `RUNNING_PHASES` in die Engine ziehen und Reaper schreiben**

In `services/analyst_engine.py`, unter die `_SLOTS`-Definition:

```python
# Phasen, in denen ein Lauf als aktiv gilt. Liegt hier statt in api/analyst.py, weil beide
# Module sie brauchen — der Reaper unten und die Warteschlangen-Anzeige im API-Modul.
RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}
```

Und ans Dateiende:

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

In `api/analyst.py` die Zeile

```python
RUNNING_PHASES = {"queued", "starting", "scenes", "transcribe", "describe", "quality", "evaluate"}
```

löschen und den bestehenden Import erweitern:

```python
from services.analyst_engine import ANALYST_PATH, RUNNING_PHASES, run_analysis, write_status
```

- [ ] **Step 6: Reaper beim Start aufrufen**

In `main.py` oben ergänzen und `app` umbauen:

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

Run: `venv/bin/python -m pytest tests/test_analyst.py tests/test_analyst_chat.py tests/test_stt_remote.py tests/test_server_deploy.py -q`
Expected: PASS, keine Fehler

- [ ] **Step 8: Committen**

```bash
git add services/analyst_engine.py api/analyst.py main.py tests/test_analyst.py
git commit -m "fix(analyst): hängende Läufe beim Start als abgebrochen markieren"
```

---

## Task 6: Qualitäts-Gegenprobe — der Punkt, den man nicht überspringen darf

**Files:**
- Create: `tools/vergleiche_stt.py`

`whisper-small` lokal → `whisper-large-v3` gehostet ist ein **Modellwechsel**, kein Hosting-Wechsel. `PAUSE_THRESHOLD_SEC = 0.8` wurde aus echtem Nutzer-Feedback (Läufe 25b8b2f6, 093dc5a7) von 0.5 hochgezogen und ist gegen den Wortlaut von `small` kalibriert. Ein besseres Modell erkennt mehr Wörter → weniger Phantompausen → die Bewertung wird eher besser, aber die Zahlen verschieben sich. Das muss man gesehen haben, bevor es live geht.

- [ ] **Step 1: Vergleichs-Skript schreiben**

Datei `tools/vergleiche_stt.py`:

```python
#!/usr/bin/env python3
"""Lokales Whisper gegen den gehosteten STT auf denselben Videos vergleichen.

Zeigt, was der Modellwechsel an der Sprachstatistik ändert — Wortzahl, Füllwörter und vor
allem die Pausenliste, die als Pflichtinput in die Bewertung geht.

    python3 tools/vergleiche_stt.py <run-id> [run-id ...]

Erwartet, dass GROQ_API_KEY in der .env steht. Macht KEINEN Gemini-Call.
"""
import sys
from pathlib import Path

from config import settings
from services.analyst_speech import compute_speech_stats
from services.stt_remote import transcribe_remote
from services.whisper_service import transcribe_with_word_timestamps


def _video(run_id: str) -> Path:
    dateien = [f for f in (Path("analyst_runs") / run_id / "raw").iterdir() if f.is_file()]
    if not dateien:
        raise SystemExit(f"Kein Video in analyst_runs/{run_id}/raw")
    return dateien[0]


def main(run_ids: list[str]) -> None:
    for run_id in run_ids:
        video = _video(run_id)
        print(f"\n=== {run_id}  ({video.name}) ===")
        lokal, _ = transcribe_with_word_timestamps(video, model_name=settings.whisper_model)
        fern, _ = transcribe_remote(video)
        for name, words in (("lokal  ", lokal), ("gehostet", fern)):
            st = compute_speech_stats(words)
            if st is None:
                print(f"{name}: kein gesprochener Text")
                continue
            pausen = ", ".join(f"{p.start_sec}s/{p.dauer_sec}s" for p in st.pausen) or "keine"
            print(f"{name}: {st.wort_anzahl:>4} Wörter | WPM {st.wpm:>6} | "
                  f"Filler {st.filler_count} {st.filler_words} | Pausen {st.pausen_count}")
            print(f"          → {pausen}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
```

- [ ] **Step 2: Auf mindestens drei echten Läufen ausführen**

```bash
source venv/bin/activate
ls analyst_runs | head -20          # drei Läufe mit vorhandenem raw/-Video aussuchen
python3 tools/vergleiche_stt.py <id1> <id2> <id3>
```

- [ ] **Step 3: Ergebnis bewerten**

Erwartet und **in Ordnung**: gehostet findet mehr Wörter, dadurch weniger und/oder kürzere Pausen. Das ist die gewünschte Richtung — Phantompausen entstehen genau aus nicht erkannten Wörtern.

**Stopp-Kriterium:** Findet gehostet *mehr* Pausen als lokal oder verschieben sich `start_sec`-Werte um mehr als ~0.3 s bei gleichen Wörtern, nicht ausrollen. Dann stimmen die Wort-Timestamps nicht und die Pausen-Urteile im Prompt zeigen auf die falschen Stellen.

- [ ] **Step 4: Determinismus des gehosteten STT prüfen**

```bash
python3 -c "
from pathlib import Path
from services.stt_remote import transcribe_remote
v = next((Path('analyst_runs/<id1>/raw')).iterdir())
a, ta = transcribe_remote(v)
b, tb = transcribe_remote(v)
print('Wortzahl:', len(a), len(b))
print('Text identisch:', ta == tb)
print('Timestamps identisch:', [(w.start, w.end) for w in a] == [(w.start, w.end) for w in b])
"
```

Zweimal derselbe Text bei identischer Datei ist das Minimum. Weicht die Wortzahl ab, ist das genau das Problem, das lokal mit `temperature=0.0` gelöst wurde — dann Task 8 (Fallback-Option) statt Rollout.

- [ ] **Step 5: Committen**

```bash
git add tools/vergleiche_stt.py
git commit -m "tools: STT-Vergleich lokal vs. gehostet (Sprachstatistik + Pausen)"
```

---

## Task 7: CLAUDE.md nachziehen und ausrollen

**Files:**
- Modify: `CLAUDE.md`

CLAUDE.md ist laut eigener Pflege-Regel im **selben Commit** zu ändern, wenn sich Architektur oder Betriebsregeln ändern. Beides trifft hier zu.

- [ ] **Step 1: Abschnitt „Analyst-Pipeline" korrigieren**

Punkt 1 ersetzen:

```markdown
1. **Transkription** — Backend über `STT_BACKEND` (`config.py`): `local` = `faster-whisper`, CPU,
   int8, Modell aus `settings.whisper_model`; `groq` = gehostetes `whisper-large-v3` über
   `services/stt_remote.py`. **Auf dem VPS steht `groq`**, lokal `local`.
```

- [ ] **Step 2: Abschnitt „Mehrere Läufe parallel" ersetzen**

Der Absatz „Was ein zweiter Slot bringt — und was nicht" beschreibt jetzt nur noch `STT_BACKEND=local`. Ersetzen durch:

```markdown
**Was ein zweiter Slot bringt — und was nicht.**

Bei `STT_BACKEND=local`: `WhisperModel` wird ohne `num_workers` erzeugt (Default 1),
gleichzeitige `transcribe()`-Aufrufe auf derselben Instanz serialisieren intern. Zwei Läufe
transkribieren **nicht** parallel; der Gewinn entsteht allein aus der Überlappung (Lauf A wartet
auf Gemini, Lauf B transkribiert). Mehr als **2** ist bei 1.5 CPUs Risiko ohne Gegenwert.

Bei `STT_BACKEND=groq` (Server, seit 2026-08-08): Die Pipeline ist durchgehend I/O-gebunden —
ffmpeg-Audioextraktion (Sekunden), STT-Call, Gemini-Upload, Gemini-Call. Erst hier wirkt der
Semaphore wirklich; **10** ist der eingestellte Wert. Der nächste Engpass ist dann nicht mehr die
CPU, sondern die **Upload-Bandbreite** des VPS: jeder Lauf schiebt das komplette Video zu Gemini.
Das ist nicht durch Code zu beheben.

**Warum ausgelagert wurde:** Nicht wegen der Modellqualität, sondern weil lokales Whisper der
einzige CPU-gebundene Schritt war *und* intern serialisiert. Solange er auf der Maschine lag, war
paralleles Arbeiten unabhängig von der Semaphore-Einstellung unmöglich.
```

- [ ] **Step 3: Kalibrierungs-Warnung ergänzen**

Direkt unter den `PAUSE_THRESHOLD_SEC`-Absatz:

```markdown
**Wechsel des STT-Modells berührt diese Schwelle.** `PAUSE_THRESHOLD_SEC` und die
Füllwort-Erkennung sind gegen den Wortlaut von `whisper-small` kalibriert. Ein anderes Modell
erkennt andere Wörter, und nicht erkannte Wörter sehen im Code exakt wie Sprechpausen aus. Vor
jedem STT-Wechsel: `python3 tools/vergleiche_stt.py <run-id> …` auf mindestens drei echten Läufen.
Mehr Pausen als vorher = nicht ausrollen.
```

- [ ] **Step 4: Deploy-Abschnitt ergänzen**

```markdown
**`.env` auf dem VPS** (nach Änderung `docker compose up -d`, **nicht** `restart` — `env_file`
greift erst beim Neuerstellen des Containers):

```
STT_BACKEND=groq
GROQ_API_KEY=gsk_...
ANALYST_MAX_CONCURRENT=10
```

`STT_BACKEND` und `GROQ_API_KEY` gehören zusammen: ohne Key wirft `transcribe_remote` sofort,
statt still auf lokales Whisper zurückzufallen. Ein stiller Fallback wäre schlimmer — er würde
unter Last genau die CPU-Spitze erzeugen, die hier vermieden werden soll.
```

- [ ] **Step 5: Offenen Punkt streichen**

Unter „Offene Punkte" entfällt der Eintrag zur ungeklärten Gemini-Tier-Frage nicht — der bleibt.
Stattdessen ergänzen:

```markdown
- Upload-Bandbreite des VPS bei ~10 parallelen Gemini-Uploads ungemessen. Erst relevant, wenn
  die Läufe nach dem STT-Wechsel weiterhin lange dauern.
```

- [ ] **Step 6: Committen und mergen**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md auf STT_BACKEND und parallele Läufe nachgezogen"

venv/bin/python -m pytest tests/test_analyst.py tests/test_analyst_chat.py tests/test_stt_remote.py tests/test_server_deploy.py -q
```

Expected: alles grün. **Erst dann:**

```bash
git checkout main && git merge feature/stt-remote
git push          # verlangt die getippte Bestätigung DEPLOY (pre-push-Hook)
```

- [ ] **Step 7: `.env` auf dem VPS setzen und Container neu bauen**

```bash
# auf dem VPS
cd /docker/analyst
nano .env        # STT_BACKEND=groq, GROQ_API_KEY=gsk_..., ANALYST_MAX_CONCURRENT=10
docker compose up -d          # NICHT restart
docker compose logs -f analyst | head -30
```

Im Log muss beim Start die Reaper-Zeile erscheinen (oder gar nichts, wenn nichts hing).

- [ ] **Step 8: Ein echter Lauf über die Live-Seite**

Ein bekanntes Video hochladen, Ergebnis mit einem früheren Lauf desselben Videos vergleichen. Danach:

```bash
docker exec analyst python3 -c '
import json, glob, os
p = max(glob.glob("analyst_runs/*/analysis.json"), key=os.path.getmtime)
d = json.load(open(p)); print(p, d.get("phasen_sek"), d.get("elapsed_sec"))
'
```

`phasen_sek["transkript"]` muss deutlich unter dem Wert aus Task 0 liegen. Tut es das nicht, greift die Weiche nicht — `STT_BACKEND` prüfen.

---

## Task 8: Rückfallweg (nur ausführen, wenn Task 6 das Stopp-Kriterium reißt)

Kein Code nötig. `STT_BACKEND=local` in der VPS-`.env` setzen, `docker compose up -d`, `ANALYST_MAX_CONCURRENT` zurück auf `2`. Damit ist der alte Zustand exakt wiederhergestellt — genau deshalb ist der Schalter eine Weiche und kein Ersatz.

Dann zuerst Deepgram `nova-3` als Alternative prüfen: kein LLM, native Wort-Timestamps, ~$0,0043/min Batch. Nur `services/stt_remote.py` wird ausgetauscht, alles andere in diesem Plan bleibt gültig.

---

## Self-Review

**Abdeckung gegen die Analyse:**

| Anforderung | Task |
|---|---|
| Whisper von der Maschine holen | 1, 2, 3 |
| Kein Gemini als Transkript-Quelle | Anbieterwahl in Task 1 (`whisper-large-v3` bei Groq) |
| Parallelität wirksam machen | 3 (Weiche) + 7 Step 7 (`ANALYST_MAX_CONCURRENT=10`) |
| Upload blockiert nicht den Event-Loop | 4 |
| Geisterläufe nach Redeploy | 5 |
| Kalibrierung nicht still kippen lassen | 6 |
| CLAUDE.md-Pflegeregel | 7 |
| Rückfallweg | 8 |

**Keine Platzhalter:** Jeder Code-Schritt enthält den vollständigen Code, jeder Befehl die erwartete Ausgabe.

**Typkonsistenz:** `transcribe_remote(video_path, language="de") -> tuple[list[WhisperWord], str]` ist signaturgleich zu `transcribe_with_word_timestamps` und wird in Task 3 (`_transkribiere`), Task 6 (`vergleiche_stt.py`) und den Tests identisch aufgerufen. `_parse(daten: dict)`, `_extrahiere_audio(video_path, ziel_dir)`, `markiere_abgebrochene_laeufe() -> int` und `RUNNING_PHASES` sind je genau einmal definiert.
