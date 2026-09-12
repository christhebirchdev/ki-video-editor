# Analyst V3 — Stufe 1: Zielsteuerung und Priorisierung nach Schwere

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine neue Engine `v3` bewertet Videos gegen ein vom Nutzer gewaehltes Ziel (TOFU/MOFU/BOFU), sortiert die Top-3-Handlungsempfehlungen nach Schwere statt nach Zeitpunkt und zeigt Lob nur noch dort, wo die Scores es decken. `v2_hybrid` bleibt unveraendert lauffaehig.

**Architecture:** Kein paralleler Code-Baum. `engine="v3"` kommt als neuer Wert in die bestehende Engine-Whitelist; das Schema waechst additiv (alle neuen Felder mit Defaults); die drei betroffenen Nachbearbeitungs-Funktionen verzweigen am Funktionsanfang. Die Verzweigung haengt an `result.gewaehltes_ziel` — nicht an `result.engine`, weil `engine` in `analyst_engine._run()` erst NACH dem Aufruf von `nachbearbeiten()` gesetzt wird und zum Bewertungszeitpunkt noch auf dem Default steht. Leeres Ziel = altes Verhalten, Zeile fuer Zeile.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, pytest, React (Babel-Standalone, `static/app.jsx`)

**Spec:** `docs/superpowers/specs/2026-09-12-analyst-output-design.md`, Abschnitte 1, 3, 11.

---

## Wichtig vor dem Start

**Testumgebung:** Tests laufen NICHT ueber `venv/bin/python` aus dem Mac-venv, wenn sie ueber die
Geraete-Shell gestartet werden — die laeuft in einer Linux-VM. Dort einmalig:

```bash
pip3 install fastapi==0.115.0 pydantic==2.9.0 pydantic-settings==2.5.2 pytest httpx \
  python-multipart google-genai anthropic assemblyai
```

`tests/test_models.py` und `tests/test_services.py` haben vorbestehende Importfehler und sind nicht
Teil der gruenen Suite. Referenz-Suite fuer diesen Plan:

```bash
python3 -m pytest tests/test_analyst_cache.py tests/test_server_deploy.py -q
```

**Push:** Die Geraete-Shell hat keine GitHub-Credentials. Commits ja, `git push` muss Chris im
Mac-Terminal ausloesen.

---

## File Structure

| Datei | Verantwortung | Aenderung |
|---|---|---|
| `models/analyst.py` | Schema, Konstanten | `ZIELE`, `SCORE_GEWICHTE_JE_ZIEL`, `Staerke`, `gewaehltes_ziel` |
| `services/analyst_eval.py` | Prompt-Bau + deterministische Nachbearbeitung | drei verzweigende Funktionen, `PROMPT_VERSION` |
| `services/analyst_eval_skill_v3.md` | V3-Bewertungs-Prompt | neu (Kopie + Ziel-Abschnitt + `betrifft` bei Staerken) |
| `services/analyst_gemini_eval.py` | Gemini-Call | Ziel-Instruktion in den User-Teil |
| `services/analyst_cache.py` | Cache-Key | Ziel im Key |
| `api/analyst.py` | Upload/Start-Endpunkt | `ziel`-Parameter, Validierung, `ENGINES` |
| `services/analyst_engine.py` | Dispatcher | `v3` in der Whitelist |
| `static/app.jsx` | Frontend | Ziel-Dropdown, Score-Label |
| `tests/test_analyst_ziel.py` | neu | Gewichte, Priorisierung, Lob, Untertitel-Regel entfaellt (Stufe 2) |

---

## Task 1: Ziele und zielabhaengige Gewichte im Schema

**Files:**
- Modify: `models/analyst.py` (nach `FORMATE`, ca. Zeile 300)
- Test: `tests/test_analyst_ziel.py` (neu)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyst_ziel.py
"""Tests fuer die Zielsteuerung der Bewertung (Analyst V3).

Laufen ohne API-Keys: nur Schema-Konstanten und reine Nachbearbeitungs-Funktionen.
"""
from models.analyst import ZIELE, SCORE_GEWICHTE_JE_ZIEL


def test_ziele_sind_die_drei_funnel_stufen():
    assert ZIELE == ("TOFU", "MOFU", "BOFU")


def test_jede_zielspalte_summiert_auf_100():
    for ziel in ZIELE:
        assert sum(SCORE_GEWICHTE_JE_ZIEL[ziel].values()) == 100, ziel


def test_tofu_gewichtet_die_hook_am_staerksten():
    tofu = SCORE_GEWICHTE_JE_ZIEL["TOFU"]
    mofu = SCORE_GEWICHTE_JE_ZIEL["MOFU"]
    hook_tofu = tofu["sprech_hook"] + tofu["text_hook"] + tofu["visuell_hook"]
    hook_mofu = mofu["sprech_hook"] + mofu["text_hook"] + mofu["visuell_hook"]
    assert hook_tofu > hook_mofu
    assert tofu["visuell_hook"] > mofu["visuell_hook"]
    assert mofu["spannungsbogen"] > tofu["spannungsbogen"]
    assert tofu["visuelle_aesthetik"] > mofu["visuelle_aesthetik"]


def test_cta_nur_bei_bofu_gewichtet():
    assert SCORE_GEWICHTE_JE_ZIEL["TOFU"]["cta"] == 0
    assert SCORE_GEWICHTE_JE_ZIEL["MOFU"]["cta"] == 0
    assert SCORE_GEWICHTE_JE_ZIEL["BOFU"]["cta"] > 0


def test_alle_ziele_kennen_dieselben_dimensionen():
    schluessel = [set(SCORE_GEWICHTE_JE_ZIEL[z]) for z in ZIELE]
    assert schluessel[0] == schluessel[1] == schluessel[2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: FAIL mit `ImportError: cannot import name 'ZIELE'`

- [ ] **Step 3: Write the implementation**

In `models/analyst.py`, direkt nach der Konstanten `FORMATE` einfuegen:

```python
# Vom Nutzer beim Upload gewaehltes Videoziel (genau EINES, Pflicht bei Engine v3). Wie FORMATE
# Single Source of Truth: Die API validiert dagegen, der Prompt bekommt die Auswahl als FAKT.
# Anzeige im Frontend in Nutzersprache, hier nur die Funnel-Stufe:
#   TOFU = "Neue Menschen erreichen" | MOFU = "Vertrauen und Expertenstatus aufbauen"
#   BOFU = "Kundenanfragen gewinnen"
# Kein Wert "Mischung": Wer alles auswaehlen kann, bekommt kein scharfes Urteil, und das Modell
# bekommt eine Ausrede, sich nicht festzulegen (Vorgabe Chris, 2026-09-11).
ZIELE = ("TOFU", "MOFU", "BOFU")

# Score-Gewichte je Ziel. Summe je Spalte = 100, damit der Score zwischen den Zielen dieselbe
# Skala hat — vergleichbar sind zwei Laeufe damit trotzdem nur bei GLEICHEM Ziel, deshalb nennt
# das Frontend-Label das Ziel mit ("78 . gemessen an: ...").
#
# Herleitung (Vorgabe Chris, 2026-09-12):
# - TOFU: Hook am wichtigsten, alle drei Ebenen; Spannungsbogen am unwichtigsten; Bildqualitaet hoch.
# - MOFU: visuelle Hook faellt deutlich, Sprech-/Text-Hook bleiben hoch, Spannungsbogen steigt stark,
#   Bildqualitaet sinkt.
# - BOFU: wie MOFU, ergaenzt um den CTA.
#
# `untertitel_vorhanden`, `untertitel_gestaltung`, `audioqualitaet` und `cta` sind in Stufe 1 noch
# nicht bewertet — sie stehen hier bereits mit ihrem Zielgewicht, damit die Spaltensumme stimmt und
# Stufe 2 nur die Dimensionen ergaenzen muss, nicht die Tabelle. `berechne_performance_score`
# ueberspringt Dimensionen ohne Score automatisch und verteilt ihr Gewicht proportional.
SCORE_GEWICHTE_JE_ZIEL = {
    "TOFU": {
        "sprech_hook": 16, "text_hook": 16, "visuell_hook": 13,
        "spannungsbogen": 7, "struktur": 7, "untertitel_vorhanden": 8,
        "schnitt_pacing": 7, "untertitel_gestaltung": 3,
        "sprechqualitaet": 8, "visuelle_aesthetik": 10, "audioqualitaet": 5,
        "cta": 0,
    },
    "MOFU": {
        "sprech_hook": 15, "text_hook": 15, "visuell_hook": 7,
        "spannungsbogen": 15, "struktur": 10, "untertitel_vorhanden": 10,
        "schnitt_pacing": 6, "untertitel_gestaltung": 3,
        "sprechqualitaet": 10, "visuelle_aesthetik": 6, "audioqualitaet": 3,
        "cta": 0,
    },
    "BOFU": {
        "sprech_hook": 14, "text_hook": 14, "visuell_hook": 6,
        "spannungsbogen": 13, "struktur": 9, "untertitel_vorhanden": 9,
        "schnitt_pacing": 5, "untertitel_gestaltung": 3,
        "sprechqualitaet": 9, "visuelle_aesthetik": 6, "audioqualitaet": 3,
        "cta": 9,
    },
}

# Welche Dimension in welchem Output-Block erscheint (Frontend Stufe 3, Lob-Filter Stufe 1).
KATEGORIEN = {
    "hook": ("sprech_hook", "text_hook", "visuell_hook"),
    "mittelteil": ("spannungsbogen", "struktur", "untertitel_vorhanden", "cta"),
    "editing": ("schnitt_pacing", "untertitel_gestaltung"),
    "auftreten": ("sprechqualitaet", "visuelle_aesthetik", "audioqualitaet"),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add models/analyst.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): Ziele und zielabhaengige Score-Gewichte im Schema"
```

---

## Task 2: `gewaehltes_ziel` im Ergebnis-Modell

**Files:**
- Modify: `models/analyst.py` (`AnalystResult`, am Ende bei `gewaehltes_format`)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

An `tests/test_analyst_ziel.py` anhaengen:

```python
def test_altlauf_ohne_ziel_laedt_unveraendert():
    from models.analyst import AnalystResult
    r = AnalystResult(id="x", filename="c.mp4", duration_sec=1.0, scene_count=0, scenes=[])
    assert r.gewaehltes_ziel == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py::test_altlauf_ohne_ziel_laedt_unveraendert -q`
Expected: FAIL mit `AttributeError: 'AnalystResult' object has no attribute 'gewaehltes_ziel'`

- [ ] **Step 3: Write the implementation**

In `models/analyst.py`, in `AnalystResult` direkt unter `gewaehltes_format`:

```python
    gewaehltes_ziel: str = ""           # vom Nutzer gewaehltes Videoziel (Pflicht bei Engine v3,
                                        # genau eines aus ZIELE). Leer = Altlauf oder v2 → die
                                        # Nachbearbeitung verhaelt sich wie vor V3. Dieses Feld ist
                                        # der Schalter fuer die V3-Logik, NICHT `engine`:
                                        # analyst_engine._run() setzt `result.engine` erst NACH dem
                                        # Aufruf von nachbearbeiten(), dort stuende sonst der Default.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add models/analyst.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): gewaehltes_ziel in AnalystResult"
```

---

## Task 3: `v3` als Engine registrieren, Ziel im Start-Endpunkt

**Files:**
- Modify: `api/analyst.py:77` (`ENGINES`), `api/analyst.py:90-150` (`start_analysis`)
- Modify: `services/analyst_engine.py:97` (Whitelist im Dispatcher)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

An `tests/test_analyst_ziel.py` anhaengen. Die `client`-Fixture wird aus
`tests/test_analyst_cache.py` uebernommen — siehe dort Zeile 22–38; kopiere sie wortgleich
in diese Datei, inklusive der Imports `json`, `pytest`, `TestClient`, `main`, `analyst_api`,
`analyst_engine`:

```python
def _lauf_anlegen(client, tmp_path):
    """Upload simulieren: Lauf-Verzeichnis mit meta.json und status.json."""
    d = tmp_path / "lauf1"
    d.mkdir()
    (d / "clip.mp4").write_bytes(b"\x00\x01fake" * 50)
    (d / "meta.json").write_text(json.dumps({"filename": "clip.mp4", "sha256": "abc"}))
    (d / "status.json").write_text(json.dumps({"phase": "uploaded"}))
    return "lauf1"


def test_v3_ohne_ziel_wird_abgelehnt(client, tmp_path):
    run_id = _lauf_anlegen(client, tmp_path)
    r = client.post(f"/api/analyst/{run_id}/start?engine=v3&format=Talking Head")
    assert r.status_code == 422
    assert "Ziel" in r.json()["detail"]


def test_v3_mit_unbekanntem_ziel_wird_abgelehnt(client, tmp_path):
    run_id = _lauf_anlegen(client, tmp_path)
    r = client.post(f"/api/analyst/{run_id}/start?engine=v3&format=Talking Head&ziel=VIRAL")
    assert r.status_code == 422


def test_v3_mit_ziel_startet_und_schreibt_meta(client, tmp_path):
    run_id = _lauf_anlegen(client, tmp_path)
    r = client.post(f"/api/analyst/{run_id}/start?engine=v3&format=Talking Head&ziel=MOFU")
    assert r.status_code == 200
    meta = json.loads((tmp_path / run_id / "meta.json").read_text())
    assert meta["ziel"] == "MOFU"
    assert meta["engine"] == "v3"


def test_v2_braucht_kein_ziel(client, tmp_path):
    run_id = _lauf_anlegen(client, tmp_path)
    r = client.post(f"/api/analyst/{run_id}/start?engine=v2_hybrid&format=Talking Head")
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k v3 or v2_braucht`
Expected: FAIL — `v3` ist keine erlaubte Engine (422 mit "Unbekannte Engine")

- [ ] **Step 3: Write the implementation**

`api/analyst.py` Zeile 77:

```python
ENGINES = {"v2_pure", "v2_hybrid", "v2_split", "v3"}
```

Import oben ergaenzen (dort, wo `FORMATE` importiert wird):

```python
from models.analyst import FORMATE, ZIELE
```

In `start_analysis` die Signatur um `ziel` erweitern:

```python
async def start_analysis(
    run_id: str, background: BackgroundTasks, skip_eval: bool = False, engine: str = "v2_hybrid",
    planned_text_hook: str = "", format: str = "", ziel: str = "", body: StartIn | None = None,
):
```

Direkt nach der Format-Validierung (nach dem `if gewaehlt not in FORMATE`-Block) einfuegen:

```python
    # Ziel ist Pflicht bei v3 und wird bei den aelteren Engines ignoriert: Sie kennen es nicht,
    # und ein mitgeschriebenes Ziel wuerde ihren Cache-Key ohne Wirkung veraendern.
    gewaehltes_ziel = (ziel or "").strip().upper() if engine == "v3" else ""
    if engine == "v3":
        if not gewaehltes_ziel:
            raise HTTPException(
                status_code=422,
                detail=f"Bitte ein Ziel waehlen. Erlaubt: {', '.join(ZIELE)}",
            )
        if gewaehltes_ziel not in ZIELE:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekanntes Ziel: {gewaehltes_ziel}. Erlaubt: {', '.join(ZIELE)}",
            )
```

Im meta-Block (nach `meta["format"] = gewaehlt`):

```python
    meta["ziel"] = gewaehltes_ziel
```

Beide `return`-Dicts um `"ziel": gewaehltes_ziel` ergaenzen.

`services/analyst_engine.py` Zeile 97:

```python
    if engine not in ("v1", "v2_pure", "v2_hybrid", "v2_split", "v3"):
```

und Zeile 104 so erweitern, dass `v3` denselben Pfad wie `v2_hybrid` nimmt (Vorverarbeitung ist
identisch, nur Prompt und Nachbearbeitung unterscheiden sich):

```python
    elif engine in ("v2_hybrid", "v2_split", "v3"):
        # v3 nutzt dieselbe Vorverarbeitung wie v2_hybrid — nur Bewertungs-Prompt und
        # Nachbearbeitung unterscheiden sich. Andernfalls waere der Vergleich wertlos
        # (dieselbe Regel wie bei v2_split).
        result = _run_v2(run_dir, video, meta, mode="hybrid", split=(engine == "v2_split"))
```

In `_run_v2` muss das Ziel aus `meta` in das Ergebnis wandern. Dort, wo `gewaehltes_format`
gesetzt wird, analog ergaenzen:

```python
    result.gewaehltes_ziel = meta.get("ziel", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add api/analyst.py services/analyst_engine.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): Engine v3 mit Pflicht-Videoziel"
```

---

## Task 4: Ziel im Cache-Key

**Files:**
- Modify: `services/analyst_cache.py` (`cache_key`, ca. Zeile 38-57)
- Test: `tests/test_analyst_cache.py`

- [ ] **Step 1: Write the failing test**

An `tests/test_analyst_cache.py` anhaengen:

```python
def test_unterschiedliches_ziel_ist_ein_anderer_key():
    from services import analyst_cache
    basis = {"sha256": "abc", "prompt_version": "v1", "format": "Talking Head",
             "engine": "v3", "planned_text_hook": ""}
    a = analyst_cache.cache_key({**basis, "ziel": "TOFU"})
    b = analyst_cache.cache_key({**basis, "ziel": "BOFU"})
    assert a != b


def test_altlauf_ohne_ziel_behaelt_seinen_key_stabil():
    """Ein Lauf ohne Ziel-Feld darf denselben Key liefern wie ein Lauf mit leerem Ziel —
    sonst verlieren alle gespeicherten v2-Laeufe ihre Cache-Treffer."""
    from services import analyst_cache
    basis = {"sha256": "abc", "prompt_version": "v1", "format": "Talking Head",
             "engine": "v2_hybrid", "planned_text_hook": ""}
    assert analyst_cache.cache_key(basis) == analyst_cache.cache_key({**basis, "ziel": ""})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_cache.py -q -k ziel`
Expected: FAIL — `test_unterschiedliches_ziel_ist_ein_anderer_key`, beide Keys sind gleich

- [ ] **Step 3: Write the implementation**

In `services/analyst_cache.py`, `cache_key`, das Rueckgabe-Tupel erweitern:

```python
    return (
        meta["sha256"],
        meta.get("format", ""),
        meta.get("ziel", ""),
        meta.get("planned_text_hook", ""),
        meta.get("engine", ""),
        meta["prompt_version"],
    )
```

Und in den Docstring aufnehmen:

```
    `ziel` gehoert dazu, weil die Score-Gewichte bei v3 vom Ziel abhaengen: Dieselbe Datei mit
    einem anderen Ziel ist ein legitim anderes Ergebnis. Ohne das Feld liefert der Cache das
    Ergebnis des zuerst gewaehlten Ziels — ein sichtbarer Fehler, kein stiller.
    Altlaeufe und v2 haben `ziel` nicht bzw. leer; `.get(..., "")` haelt ihren Key stabil.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_cache.py -q`
Expected: 17 passed (15 bestehende + 2 neue)

- [ ] **Step 5: Commit**

```bash
git add services/analyst_cache.py tests/test_analyst_cache.py
git commit -m "feat(analyst): Videoziel im Cache-Key"
```

---

## Task 5: Zielabhaengige Score-Berechnung

**Files:**
- Modify: `services/analyst_eval.py:1057-1086` (`berechne_performance_score`)
- Test: `tests/test_analyst_ziel.py`

Die Funktion bekommt das Ziel als Parameter. `nachbearbeiten()` reicht es durch; ohne Ziel gilt
`SCORE_GEWICHTE` unveraendert.

- [ ] **Step 1: Write the failing test**

An `tests/test_analyst_ziel.py` anhaengen:

```python
def _eval_mit_scores(sprech=3, text=3, visuell=3, spannung=3, struktur=3,
                     sprechq=3, aesthetik=3, schnitt=3):
    from models.analyst import (AnalystEvaluationV2, HookEval, StrukturEval,
                                ScoreProbleme, ScoreKommentar)
    return AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=sprech, text_hook_score=text, visuell_hook_score=visuell),
        struktur=StrukturEval(score=struktur),
        sprechqualitaet=ScoreProbleme(score=sprechq),
        visuelle_aesthetik=ScoreProbleme(score=aesthetik),
        spannungsbogen=ScoreKommentar(score=spannung),
        schnitt_pacing=ScoreKommentar(score=schnitt),
    )


def test_ohne_ziel_rechnet_die_alten_gewichte():
    from services.analyst_eval import berechne_performance_score
    ev = _eval_mit_scores()
    alt = berechne_performance_score(ev, ziel="").performance_score
    ev2 = berechne_performance_score(_eval_mit_scores(), ziel=None).performance_score
    assert alt == ev2
    assert 0 < alt < 100


def test_schwacher_hook_kostet_bei_tofu_mehr_als_bei_mofu():
    from services.analyst_eval import berechne_performance_score
    tofu = berechne_performance_score(
        _eval_mit_scores(sprech=1, text=1, visuell=1), ziel="TOFU").performance_score
    mofu = berechne_performance_score(
        _eval_mit_scores(sprech=1, text=1, visuell=1), ziel="MOFU").performance_score
    assert tofu < mofu


def test_schwacher_spannungsbogen_kostet_bei_mofu_mehr_als_bei_tofu():
    from services.analyst_eval import berechne_performance_score
    tofu = berechne_performance_score(_eval_mit_scores(spannung=1), ziel="TOFU").performance_score
    mofu = berechne_performance_score(_eval_mit_scores(spannung=1), ziel="MOFU").performance_score
    assert mofu < tofu


def test_unbekanntes_ziel_faellt_auf_die_alten_gewichte_zurueck():
    from services.analyst_eval import berechne_performance_score
    a = berechne_performance_score(_eval_mit_scores(), ziel="QUATSCH").performance_score
    b = berechne_performance_score(_eval_mit_scores(), ziel="").performance_score
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k gewichte or kostet or ziel_faellt`
Expected: FAIL mit `TypeError: berechne_performance_score() got an unexpected keyword argument 'ziel'`

- [ ] **Step 3: Write the implementation**

Import in `services/analyst_eval.py` ergaenzen (bei den anderen Modell-Importen):

```python
from models.analyst import SCORE_GEWICHTE_JE_ZIEL
```

`berechne_performance_score` ersetzen:

```python
def berechne_performance_score(parsed: AnalystEvaluationV2, ziel: str = "") -> AnalystEvaluationV2:
    """Gesamtscore aus den Einzel-Scores statt aus dem Bauch des Modells.

    Jede Dimension wird auf 0..1 normalisiert (1-5 → (s-1)/4; der Text-Hook auf 0-5 → s/5, weil dort
    die 0 „fehlt komplett" bedeutet und nicht „Modell hat nichts gesagt"). Nicht bewertbare
    Dimensionen (None, z.B. Sprech-Hook in einem stummen Video) fallen raus und ihr Gewicht verteilt
    sich proportional auf den Rest — sonst wuerde ein bewusst stummes Format doppelt bestraft.

    `ziel` (V3): Ist es gesetzt und bekannt, gelten die Gewichte dieses Ziels. Sonst gilt
    SCORE_GEWICHTE wie vor V3 — das haelt alle gespeicherten Laeufe und
    tools/replay_nachbearbeitung.py unveraendert.
    """
    gewichte = SCORE_GEWICHTE_JE_ZIEL.get((ziel or "").upper(), SCORE_GEWICHTE)
    dimensionen = {
        "sprech_hook": (parsed.hook.sprech_hook_score, 1),
        "text_hook": (parsed.hook.text_hook_score, 0),
        "visuell_hook": (parsed.hook.visuell_hook_score, 1),
        "sprechqualitaet": (parsed.sprechqualitaet.score, 1),
        "visuelle_aesthetik": (parsed.visuelle_aesthetik.score, 1),
        "spannungsbogen": (parsed.spannungsbogen.score, 1),
        "struktur": (parsed.struktur.score, 1),
        "schnitt_pacing": (parsed.schnitt_pacing.score, 1),
    }
    summe = gewicht_gesamt = 0.0
    for name, (score, minimum) in dimensionen.items():
        if score is None or score < minimum:
            continue  # nicht bewertbar, oder 0 als Modell-Default statt echter Bewertung
        gewicht = gewichte.get(name, 0)
        if not gewicht:
            continue  # Dimension zaehlt bei diesem Ziel nicht (z.B. cta ausserhalb BOFU)
        summe += gewicht * (score - minimum) / (5 - minimum)
        gewicht_gesamt += gewicht
    if gewicht_gesamt:
        parsed.performance_score = round(summe / gewicht_gesamt * 100)
    return parsed
```

In `nachbearbeiten()` den Aufruf anpassen:

```python
    parsed = berechne_performance_score(parsed, ziel=getattr(result, "gewaehltes_ziel", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_eval.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): Score nach zielabhaengigen Gewichten"
```

---

## Task 6: Schwere je Empfehlung berechnen

**Files:**
- Modify: `services/analyst_eval.py` (neue Hilfsfunktion vor `verteile_empfehlungen`, ca. Zeile 130)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

```python
def test_schwere_waechst_mit_gewicht_und_faellt_mit_score():
    from services.analyst_eval import schwere_der_dimension
    # MOFU: spannungsbogen 15, schnitt_pacing 6
    stark_betroffen = schwere_der_dimension("spannungsbogen", 1, "MOFU")
    schwach_betroffen = schwere_der_dimension("schnitt_pacing", 1, "MOFU")
    assert stark_betroffen > schwach_betroffen
    assert schwere_der_dimension("spannungsbogen", 5, "MOFU") == 0.0
    assert schwere_der_dimension("spannungsbogen", 1, "MOFU") == 15.0


def test_schwere_ohne_dimension_ist_null():
    from services.analyst_eval import schwere_der_dimension
    assert schwere_der_dimension("", 3, "MOFU") == 0.0
    assert schwere_der_dimension("gibtsnicht", 3, "MOFU") == 0.0


def test_schwere_ohne_score_ist_null():
    from services.analyst_eval import schwere_der_dimension
    assert schwere_der_dimension("spannungsbogen", None, "MOFU") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k schwere`
Expected: FAIL mit `ImportError: cannot import name 'schwere_der_dimension'`

- [ ] **Step 3: Write the implementation**

In `services/analyst_eval.py`, direkt vor `verteile_empfehlungen`:

```python
def schwere_der_dimension(name: str, score, ziel: str) -> float:
    """Wie viel Punkte diese Dimension am Gesamtscore kostet: Gewicht x (1 - normalisierter Score).

    Damit ist die Reihenfolge der Handlungsempfehlungen an dieselbe Zahl gebunden wie der Score —
    was den Score am staerksten drueckt, steht oben. Vorher sortierte verteile_empfehlungen() nach
    `zeitpunkt_sek`, wodurch ein kosmetischer Tipp bei Sekunde 2 einen gravierenden Mangel bei
    Sekunde 20 verdraengen konnte (Vorgabe Chris, 2026-09-11).

    Rueckgabe 0.0, wenn die Dimension unbekannt ist, kein Score vorliegt oder das Ziel sie nicht
    gewichtet. Empfehlungen ohne `betrifft` (videospezifische Schritte mit echter Sekundenangabe)
    landen damit rechnerisch unten — ihren Platz sichert weiterhin MAX_SAMMEL_OBEN.
    """
    if score is None:
        return 0.0
    gewichte = SCORE_GEWICHTE_JE_ZIEL.get((ziel or "").upper(), SCORE_GEWICHTE)
    gewicht = gewichte.get(name, 0)
    if not gewicht:
        return 0.0
    minimum = 0 if name == "text_hook" else 1
    if score < minimum:
        return 0.0
    norm = (score - minimum) / (5 - minimum)
    return round(gewicht * (1 - norm), 3)


def dimensions_scores(parsed: AnalystEvaluationV2) -> dict:
    """Score je Dimensionsname — eine Stelle fuer die Zuordnung Feld → Name.

    Wird von der Schwere-Sortierung, der Definition kritischer Maengel und dem Lob-Filter genutzt.
    """
    return {
        "sprech_hook": parsed.hook.sprech_hook_score,
        "text_hook": parsed.hook.text_hook_score,
        "visuell_hook": parsed.hook.visuell_hook_score,
        "sprechqualitaet": parsed.sprechqualitaet.score,
        "visuelle_aesthetik": parsed.visuelle_aesthetik.score,
        "spannungsbogen": parsed.spannungsbogen.score,
        "struktur": parsed.struktur.score,
        "schnitt_pacing": parsed.schnitt_pacing.score,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_eval.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): Schwere einer Dimension als Sortiergrundlage"
```

---

## Task 7: Kritische Maengel

**Files:**
- Modify: `services/analyst_eval.py` (neue Funktion nach `dimensions_scores`)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

```python
def test_schwacher_score_in_schwerer_dimension_ist_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(spannung=2)
    assert "spannungsbogen" in kritische_dimensionen(ev, "MOFU")   # Gewicht 15
    assert "spannungsbogen" not in kritische_dimensionen(ev, "TOFU")  # Gewicht 7


def test_schwacher_hook_ist_immer_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(visuell=2)
    assert "visuell_hook" in kritische_dimensionen(ev, "BOFU")   # Gewicht 6, trotzdem kritisch


def test_fehlende_texthook_ist_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(text=0)
    ev.hook.text_hook_vorhanden = False
    assert "text_hook" in kritische_dimensionen(ev, "TOFU")


def test_gute_scores_haben_keinen_kritischen_mangel():
    from services.analyst_eval import kritische_dimensionen
    assert kritische_dimensionen(_eval_mit_scores(sprech=4, text=4, visuell=4, spannung=4,
                                                  struktur=4, sprechq=4, aesthetik=4,
                                                  schnitt=4), "MOFU") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k kritisch`
Expected: FAIL mit `ImportError: cannot import name 'kritische_dimensionen'`

- [ ] **Step 3: Write the implementation**

```python
# Ein kritischer Mangel erzwingt einen Platz in den Top 3 (Vorgabe Chris, 2026-09-11).
# Deterministisch definiert, weil eine Prompt-Regel dafuer wieder nur eine Bitte waere — dieselbe
# Lektion wie bei P2 in docs/offene-fixes-analyst.md (Pflicht ohne Schwelle wird zu Boilerplate).
KRITISCH_SCORE = 2          # „2 oder schlechter"
KRITISCH_GEWICHT = 10       # ab diesem Zielgewicht zaehlt ein schwacher Score als kritisch
HOOK_DIMENSIONEN = ("sprech_hook", "text_hook", "visuell_hook")


def kritische_dimensionen(parsed: AnalystEvaluationV2, ziel: str) -> set:
    """Namen der Dimensionen mit einem kritischen Mangel.

    Kritisch ist:
    - Score <= KRITISCH_SCORE in einer Dimension mit Zielgewicht >= KRITISCH_GEWICHT,
    - jeder Hook-Score <= KRITISCH_SCORE, unabhaengig vom Gewicht (die ersten Sekunden
      entscheiden ueber alles Weitere),
    - eine vollstaendig fehlende Text-Hook (Score 0 bei text_hook heisst „fehlt komplett",
      nicht „nicht bewertet").
    """
    gewichte = SCORE_GEWICHTE_JE_ZIEL.get((ziel or "").upper(), SCORE_GEWICHTE)
    kritisch = set()
    for name, score in dimensions_scores(parsed).items():
        if score is None:
            continue
        if name == "text_hook" and score == 0:
            kritisch.add(name)
            continue
        if score > KRITISCH_SCORE or score < 1:
            continue
        if name in HOOK_DIMENSIONEN or gewichte.get(name, 0) >= KRITISCH_GEWICHT:
            kritisch.add(name)
    return kritisch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_eval.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): kritische Maengel deterministisch definiert"
```

---

## Task 8: `verteile_empfehlungen` nach Schwere sortieren

**Files:**
- Modify: `services/analyst_eval.py:135-195` (`verteile_empfehlungen`)
- Test: `tests/test_analyst_ziel.py`

Die Signatur bekommt `parsed` wie bisher plus das Ziel. Ohne Ziel bleibt die Sortierung nach
Zeitpunkt — Zeile fuer Zeile das alte Verhalten.

- [ ] **Step 1: Write the failing test**

```python
def _mit_empfehlungen(*eintraege, **scores):
    """eintraege: (zeitpunkt_sek, anweisung, betrifft, gruppe)"""
    from models.analyst import Empfehlung
    ev = _eval_mit_scores(**scores)
    ev.empfehlungen = [
        Empfehlung(zeitpunkt_sek=t, anweisung=a, betrifft=b, gruppe=g)
        for t, a, b, g in eintraege
    ]
    return ev


def test_ohne_ziel_bleibt_die_sortierung_nach_zeitpunkt():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (2.0, "frueh und harmlos", "schnitt_pacing", ""),
        (20.0, "spaet und schwer", "spannungsbogen", ""),
        spannung=1, schnitt=4,
    )
    out = verteile_empfehlungen(ev, ziel="")
    assert out.action_steps[0].anweisung == "frueh und harmlos"


def test_mit_ziel_steht_der_schwerere_mangel_oben():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (2.0, "frueh und harmlos", "schnitt_pacing", ""),
        (20.0, "spaet und schwer", "spannungsbogen", ""),
        spannung=1, schnitt=4,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "spaet und schwer"


def test_kritischer_mangel_steht_vor_allem_anderen():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (1.0, "a", "schnitt_pacing", ""),
        (2.0, "b", "sprechqualitaet", ""),
        (3.0, "c", "struktur", ""),
        (4.0, "kritisch", "spannungsbogen", ""),
        spannung=1, schnitt=4, sprechq=4, struktur=4,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "kritisch"


def test_bei_gleicher_schwere_gewinnt_der_fruehere_zeitpunkt():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (20.0, "spaet", "spannungsbogen", ""),
        (5.0, "frueh", "spannungsbogen", ""),
        spannung=2,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "frueh"


def test_hoher_score_ohne_kritischen_mangel_zeigt_weniger_als_drei_schritte():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (5.0, "feinschliff a", "schnitt_pacing", ""),
        (9.0, "feinschliff b", "struktur", ""),
        (12.0, "feinschliff c", "sprechqualitaet", ""),
        sprech=5, text=5, visuell=5, spannung=5, struktur=5, sprechq=5, aesthetik=5, schnitt=5,
    )
    ev.performance_score = 92
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert len(out.action_steps) <= 2
    assert len(out.weitere_empfehlungen) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k sortierung or schwerere or kritischer_mangel_steht or gleicher_schwere or weniger_als_drei`
Expected: FAIL mit `TypeError: verteile_empfehlungen() got an unexpected keyword argument 'ziel'`

- [ ] **Step 3: Write the implementation**

Konstante oben bei `TOP_ACTION_STEPS` ergaenzen:

```python
# Ab diesem Score gilt ein Video als stark: Ohne kritischen Mangel werden dann hoechstens
# TOP_STEPS_BEI_GUTEM_SCORE Schritte gezeigt statt drei erzwungen. Anlass Lauf 56748c94
# (Score 92, alle Dimensionen 4-5): Schritt 1 war „Ersetze das Text-Overlay '16 LITER'" — der
# Umbau des staerksten Elements im Video (Vorgabe Chris, 2026-09-11).
GUTER_SCORE = 85
TOP_STEPS_BEI_GUTEM_SCORE = 2
```

`verteile_empfehlungen` ersetzen (die Buendelung bleibt unveraendert, nur Signatur, Sortierschluessel
und der Deckel aendern sich):

```python
def verteile_empfehlungen(parsed: AnalystEvaluationV2, ziel: str = "") -> AnalystEvaluationV2:
    """Buendeln → sortieren → Top-N abtrennen. Deterministisch im Code statt per Prompt-Regel.

    Das Modell liefert eine flache `empfehlungen`-Liste und entscheidet nur, WELCHE Eintraege
    dieselbe Handlung sind (Feld `gruppe`) — das ist Urteil. Der Rest ist Arithmetik.

    Sortierung ohne `ziel` (v2 und Altlaeufe): nach Zeitpunkt, unveraendert.
    Sortierung mit `ziel` (v3): kritische Maengel zuerst, dann Hook, dann Schwere absteigend,
    bei Gleichstand der fruehere Zeitpunkt. Die Schwere haengt an denselben Gewichten wie der
    Score — was den Score am staerksten drueckt, steht oben.

    Liefert das Modell `empfehlungen` nicht (Altlauf/altes Schema), bleiben die geparsten
    action_steps unveraendert stehen.
    """
    if not parsed.empfehlungen:
        return parsed
    gruppen: dict[str, list[Empfehlung]] = {}
    for i, e in enumerate(parsed.empfehlungen):
        # Gebuendelt wird nur, wenn Label UND Anweisung uebereinstimmen. Das Label allein reicht
        # NICHT: das Modell nutzt `gruppe` sonst als KATEGORIE ("alles Einblendungen") und wirft
        # verschiedene Handlungen zusammen (Lauf 702f9c11).
        label = (e.gruppe or "").strip().lower()
        key = f"{label}\x00{_normtext(e.anweisung)}" if label else f"\x00einzeln{i}"
        gruppen.setdefault(key, []).append(e)

    kritisch = kritische_dimensionen(parsed, ziel) if ziel else set()
    scores = dimensions_scores(parsed)

    # (Sortierschluessel, Gruppen-Label, Schritt)
    schritte: list[tuple[tuple, str, ActionStep]] = []
    for eintraege in gruppen.values():
        eintraege.sort(key=lambda e: e.zeitpunkt_sek)
        erste = eintraege[0]
        zeitpunkt = erste.zeitpunkt_sek   # eine Gruppe zaehlt ab ihrem FRUEHESTEN Vorkommen
        betrifft = (erste.betrifft or "").strip()
        if ziel:
            schluessel = (
                0 if betrifft in kritisch else 1,
                0 if betrifft in HOOK_DIMENSIONEN else 1,
                -schwere_der_dimension(betrifft, scores.get(betrifft), ziel),
                zeitpunkt,
            )
        else:
            schluessel = (zeitpunkt,)
        schritte.append((
            schluessel,
            (erste.gruppe or "").strip().lower(),
            ActionStep(
                zeitpunkt=_zeit_label([e.zeitpunkt_sek for e in eintraege]),
                anweisung=erste.anweisung,
            ),
        ))
    schritte.sort(key=lambda t: t[0])

    # Weniger als drei Schritte, wenn das Video stark ist und nichts Kritisches offen steht.
    obergrenze = TOP_ACTION_STEPS
    if ziel and not kritisch and parsed.performance_score >= GUTER_SCORE:
        obergrenze = TOP_STEPS_BEI_GUTEM_SCORE

    # Hoechstens zwei Sammel-Tipps in den Top N (Vorgabe Chris). Ein Platz bleibt so fuer einen
    # videospezifischen Schritt mit echter Sekundenangabe frei. Hook- und Anlauf-Schritte zaehlen
    # NICHT zum Deckel — sie betreffen die ersten Sekunden und behalten Vorrang.
    oben: list[ActionStep] = []
    rest: list[ActionStep] = []
    sammel = 0
    for _, gruppe, schritt in schritte:
        if gruppe in NUR_UNTEN:
            rest.append(schritt)
            continue
        ist_sammel = gruppe in SAMMEL_GRUPPEN
        if len(oben) < obergrenze and not (ist_sammel and sammel >= MAX_SAMMEL_OBEN):
            oben.append(schritt)
            sammel += ist_sammel
        else:
            rest.append(schritt)

    parsed.action_steps = oben
    parsed.weitere_empfehlungen = rest
    return parsed
```

In `nachbearbeiten()` die letzte Zeile anpassen:

```python
    return verteile_empfehlungen(parsed, ziel=getattr(result, "gewaehltes_ziel", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_eval.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): Top-3 nach Schwere statt nach Zeitpunkt (nur v3)"
```

---

## Task 9: Regressionsnachweis — Altlaeufe duerfen sich nicht aendern

**Files:**
- Test: manuell ueber `tools/replay_nachbearbeitung.py`

Dieser Task schreibt keinen Code. Er ist das Abnahmekriterium aus Spec 11.3 und muss bestehen,
bevor Task 10 beginnt.

- [ ] **Step 1: Replay als A/B-Vergleich**

Das Werkzeug meldet als Ausgangslage schon „66 von 93 Laeufen aendern sich" (vorbestehende Drift der
Altlaeufe gegenueber der heutigen Nachbearbeitung). Eine absolute 0 ist deshalb kein Kriterium.
Gemessen wird der Unterschied, den DIESE Aenderung macht:

```bash
python3 tools/replay_nachbearbeitung.py > /tmp/replay_neu.txt
git stash push -- services/analyst_eval.py && python3 tools/replay_nachbearbeitung.py > /tmp/replay_alt.txt && git stash pop
diff /tmp/replay_alt.txt /tmp/replay_neu.txt && echo "KEIN UNTERSCHIED"
```

Expected: `KEIN UNTERSCHIED`.

- [ ] **Step 2: Bei Abweichung**

Ein Unterschied heisst, die Verzweigung greift auch ohne Ziel. Pruefe in dieser Reihenfolge:
1. Steht in `berechne_performance_score` wirklich `SCORE_GEWICHTE` als Fallback (nicht ein Ziel)?
2. Ist der Sortierschluessel bei leerem `ziel` exakt `(zeitpunkt,)`?
3. Ist `obergrenze` bei leerem `ziel` immer `TOP_ACTION_STEPS`?

- [ ] **Step 3: Bestehende Suite**

Run: `python3 -m pytest tests/test_analyst_cache.py tests/test_server_deploy.py tests/test_analyst_ziel.py -q`
Expected: alle passed

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "test(analyst): Replay bestaetigt Score-Diff 0 fuer Altlaeufe"
```

---

## Task 10: Staerken mit Dimensionsbezug und Lob-Schwelle

**Files:**
- Modify: `models/analyst.py` (`Staerke`, `AnalystEvaluationV2.staerken`)
- Modify: `services/analyst_eval.py` (neue Funktion `filtere_staerken`, Aufruf in `nachbearbeiten`)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

```python
def test_staerke_unter_der_schwelle_wird_gestrichen():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=2, text=2, visuell=2, schnitt=5)
    ev.staerken = [Staerke(text="Deine Hook sitzt", betrifft="sprech_hook"),
                   Staerke(text="Sauber geschnitten", betrifft="schnitt_pacing")]
    out = filtere_staerken(ev, ziel="MOFU")
    assert [s.text for s in out.staerken] == ["Sauber geschnitten"]


def test_hoechstens_zwei_staerken_pro_kategorie():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=5, text=5, visuell=5)
    ev.staerken = [Staerke(text=f"lob {i}", betrifft=b) for i, b in enumerate(
        ["sprech_hook", "text_hook", "visuell_hook"])]
    out = filtere_staerken(ev, ziel="MOFU")
    assert len(out.staerken) == 2


def test_staerke_ohne_bezug_wird_gestrichen():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=5)
    ev.staerken = [Staerke(text="irgendwas Nettes", betrifft="")]
    assert filtere_staerken(ev, ziel="MOFU").staerken == []


def test_ohne_ziel_bleiben_staerken_unangetastet():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=1)
    ev.staerken = [Staerke(text="Altlauf-Lob", betrifft="")]
    assert len(filtere_staerken(ev, ziel="").staerken) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k staerke`
Expected: FAIL mit `ImportError: cannot import name 'Staerke'`

- [ ] **Step 3: Write the implementation**

In `models/analyst.py`, vor `AnalystEvaluationV2`:

```python
class Staerke(BaseModel):
    """EIN positiver Punkt, mit dem Bezug zu der Dimension, aus der er stammt.

    `betrifft` ist Pflicht, damit der Code pruefen kann, ob die Scores das Lob decken: Eine
    Staerke wird nur angezeigt, wenn ihre Kategorie mindestens eine Dimension mit Score >= 4 hat.
    Ohne dieses Feld war „erfinde kein Lob" eine reine Prompt-Bitte — dieselbe Falle wie bei P2
    in docs/offene-fixes-analyst.md, wo eine Pflicht ohne Schwelle zu Boilerplate wurde.

    Altlaeufe lieferten `staerken` als Liste von Strings. Der Validator unten nimmt beide Formen
    entgegen, damit gespeicherte Ergebnisse unveraendert laden.
    """
    text: str = ""
    betrifft: str = ""    # Name aus SCORE_GEWICHTE_JE_ZIEL, oder leer
```

In `AnalystEvaluationV2` das Feld `staerken` ersetzen:

```python
    staerken: list[Staerke] = Field(default_factory=list)   # positives Feedback, siehe Staerke
```

und einen Validator ergaenzen (neben `_null_ist_sekunde_null`):

```python
    @field_validator("staerken", mode="before")
    @classmethod
    def _strings_bleiben_lesbar(cls, v):
        """Altlaeufe (und das V2-Schema) liefern Strings statt Objekten. Ohne diese Umwandlung
        wuerde jedes gespeicherte Ergebnis beim Laden mit einem Validierungsfehler brechen —
        und ein abgebrochener Lauf ist die teuerste aller Antworten."""
        if not isinstance(v, list):
            return v
        return [{"text": e, "betrifft": ""} if isinstance(e, str) else e for e in v]
```

In `services/analyst_eval.py`, neue Funktion nach `kritische_dimensionen`:

```python
LOB_SCHWELLE = 4          # ab diesem Score darf eine Kategorie gelobt werden
MAX_LOB_PRO_KATEGORIE = 2


def filtere_staerken(parsed: AnalystEvaluationV2, ziel: str = "") -> AnalystEvaluationV2:
    """Lob nur dort, wo die eigenen Scores es decken (V3).

    Eine Staerke ueberlebt, wenn ihre Kategorie mindestens eine Dimension mit Score >= LOB_SCHWELLE
    hat. Je Kategorie hoechstens MAX_LOB_PRO_KATEGORIE Eintraege, Reihenfolge wie geliefert.
    Staerken ohne `betrifft` fallen raus: Ohne Bezug ist nicht pruefbar, worauf sie sich stuetzen.

    Ohne `ziel` (v2, Altlaeufe) bleibt die Liste unveraendert.
    """
    if not ziel or not parsed.staerken:
        return parsed
    scores = dimensions_scores(parsed)
    kategorie_von = {d: k for k, dims in KATEGORIEN.items() for d in dims}
    gedeckt = {
        k for k, dims in KATEGORIEN.items()
        if any((scores.get(d) or 0) >= LOB_SCHWELLE for d in dims)
    }
    behalten, gezaehlt = [], {}
    for s in parsed.staerken:
        k = kategorie_von.get((s.betrifft or "").strip())
        if k is None or k not in gedeckt:
            continue
        if gezaehlt.get(k, 0) >= MAX_LOB_PRO_KATEGORIE:
            continue
        gezaehlt[k] = gezaehlt.get(k, 0) + 1
        behalten.append(s)
    parsed.staerken = behalten
    return parsed
```

Import in `analyst_eval.py` ergaenzen:

```python
from models.analyst import KATEGORIEN
```

In `nachbearbeiten()` vor `berechne_performance_score` einfuegen:

```python
    # Nach allen Score-Deckelungen: Der Lob-Filter liest die FERTIGEN Scores. Stuende er davor,
    # wuerde Lob zu einer Dimension ueberleben, die deckle_score_auf_probleme danach absenkt.
    parsed = filtere_staerken(parsed, ziel=getattr(result, "gewaehltes_ziel", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 30 passed

- [ ] **Step 5: Frontend gegen die neue Form absichern**

In `static/app.jsx`, im Staerken-Block (ca. Zeile 1180) `{s}` ersetzen durch:

```jsx
{ev.staerken.map((s, i) => <li key={i}>{typeof s === "string" ? s : s.text}</li>)}
```

- [ ] **Step 6: Commit**

```bash
git add models/analyst.py services/analyst_eval.py static/app.jsx tests/test_analyst_ziel.py
git commit -m "feat(analyst): Lob nur wo die Scores es decken"
```

---

## Task 11: V3-Prompt

**Files:**
- Create: `services/analyst_eval_skill_v3.md`
- Modify: `services/analyst_eval.py` (`SKILL_PATH` → Auswahl, `build_system_prompt`, `PROMPT_VERSION`)
- Modify: `services/analyst_gemini_eval.py` (Ziel-Instruktion)
- Test: `tests/test_analyst_ziel.py`

- [ ] **Step 1: Write the failing test**

```python
def test_v3_prompt_nennt_das_ziel_als_fakt():
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(ziel="MOFU")
    assert "MOFU" in p
    assert "FAKT" in p


def test_ohne_ziel_entsteht_exakt_der_v2_prompt():
    from services.analyst_eval import build_system_prompt
    assert build_system_prompt() == build_system_prompt(ziel="")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q -k prompt`
Expected: FAIL mit `TypeError: build_system_prompt() got an unexpected keyword argument 'ziel'`

- [ ] **Step 3: Write the implementation**

Kopie anlegen:

```bash
cp services/analyst_eval_skill.md services/analyst_eval_skill_v3.md
```

In `services/analyst_eval_skill_v3.md` ganz oben nach der Rollenbeschreibung einfuegen:

```markdown
## Videoziel

Der Nutzer hat vor der Analyse angegeben, wofuer dieses Video gedacht ist. Diese Angabe ist ein
FAKT, keine Einschaetzung — widersprich ihr nicht. Bewerte gegen GENAU dieses Ziel:

- **TOFU — neue Menschen erreichen.** Die Hook entscheidet fast alles: Sprech-, Text- und visuelle
  Ebene. Ein ausgefeilter Spannungsbogen ist hier zweitrangig; ein schwacher Einstieg ist toedlich.
- **MOFU — Vertrauen und Expertenstatus aufbauen.** Sprech- und Text-Hook bleiben wichtig, die
  visuelle Hook deutlich weniger. Entscheidend ist, ob die Aufmerksamkeit bis zum Ende getragen wird.
- **BOFU — Kundenanfragen gewinnen.** Wie MOFU, zusaetzlich zaehlt der Call to Action: Gibt es einen,
  ist er konkret, kommt er an der richtigen Stelle?

Trag das Ziel unveraendert in das Feld `funnel` ein.
```

Und im Schema-Abschnitt die `staerken`-Zeile ersetzen:

```markdown
- staerken: 1-2 ECHTE positive Aspekte JE BEREICH, hoechstens 6 insgesamt. Jeder Eintrag ist ein
  Objekt {"text": "...", "betrifft": "<Dimensionsname>"}. `betrifft` ist PFLICHT und muss einer der
  Namen sein: sprech_hook, text_hook, visuell_hook, spannungsbogen, struktur, schnitt_pacing,
  sprechqualitaet, visuelle_aesthetik. Gibt es in einem Bereich nichts ehrlich Gutes zu sagen,
  schreib dort NICHTS — erfinde kein Lob. Der Code streicht Lob, das die Scores nicht decken.
```

In `services/analyst_eval.py`:

```python
PROMPT_VERSION = "2026-09-12a"   # V3: Zielsteuerung, Schwere-Priorisierung, Lob-Schwelle

SKILL_PATH = Path(__file__).with_name("analyst_eval_skill.md")
SKILL_PATH_V3 = Path(__file__).with_name("analyst_eval_skill_v3.md")
```

`load_skill_body()` (Zeile 1215) ersetzen:

```python
def load_skill_body(pfad: Path | None = None) -> str:
    """Liest den Skill-Body und entfernt das YAML-Frontmatter.

    `pfad` waehlt die Skill-Datei: ohne Angabe der V2-Skill, mit SKILL_PATH_V3 der V3-Skill.
    Zwei Dateien statt Verzweigungen im selben Text — der Prompt ist inhaltlich anders, nicht
    parametrisiert.
    """
    text = (pfad or SKILL_PATH).read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()
```

`build_system_prompt` ersetzen:

```python
def build_system_prompt(teil: str | None = None, ziel: str = "") -> str:
    """Skill-Body (Logik) + optionale Editing-Referenz + strikter JSON-Vertrag (Pipeline-Modus).

    `teil` steuert den V1.2-Split. `ziel` schaltet auf den V3-Skill um; ohne Ziel entsteht exakt
    der bisherige Prompt — die Versionen laufen so nebeneinander und bleiben vergleichbar.
    """
    pfad = SKILL_PATH_V3 if ziel else None
    parts = [_skill_fuer(teil, pfad)]
    if ziel:
        parts.append(
            f"ZIEL DIESES VIDEOS (vom Nutzer vor der Analyse angegeben — das ist ein FAKT, nicht "
            f"deine Einschaetzung): „{ziel}“. Bewerte gegen genau dieses Ziel und trag es "
            f"unveraendert in das Feld funnel ein."
        )
    ref = load_reference()
    if ref:
        parts.append(
            "--- ANGEHÄNGTE REFERENZ: VIDEO-ANALYSE (EDITING + SKRIPT + TECHNIK/AUFTRETEN) ---\n"
            "Zusätzliche Urteilsgrundlage für hook, struktur, spannungsbogen, schnitt_pacing, sprechqualitaet, visuelle_aesthetik und top_tipps. "
            "KEIN Ausgabe-Template — der Output bleibt strikt knapp + JSON wie unten definiert.\n\n"
            + ref
        )
    parts.append(_schema_fuer(teil))
    return "\n\n".join(parts)
```

`_skill_fuer` bekommt den Pfad durchgereicht:

```python
def _skill_fuer(teil: str | None, pfad=None) -> str:
    """Skill-Abschnitte für einen Teil-Call. `teil=None` → alles (V1.1-Verhalten, unverändert)."""
    body = load_skill_body(pfad)
```

In `services/analyst_gemini_eval.py` Zeile 235 den Aufruf erweitern:

```python
    system = analyst_eval.build_system_prompt(ziel=getattr(result, "gewaehltes_ziel", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_analyst_ziel.py -q`
Expected: 32 passed

- [ ] **Step 5: Commit**

```bash
git add services/analyst_eval_skill_v3.md services/analyst_eval.py services/analyst_gemini_eval.py tests/test_analyst_ziel.py
git commit -m "feat(analyst): V3-Prompt mit Zielsteuerung, PROMPT_VERSION 2026-09-12a"
```

---

## Task 12: Frontend — Ziel-Dropdown, Engine-Umschalter, Score-Label

**Files:**
- Modify: `static/app.jsx` (Konstanten oben; `VideoAnalystPage` ca. Zeile 690-1160)

- [ ] **Step 1: Konstanten und State**

Bei den anderen Konstanten oben in `static/app.jsx`:

```jsx
// Anzeige in Nutzersprache, Wert = Funnel-Stufe. Reihenfolge = Funnel-Reihenfolge.
const ZIELE = [
  { wert: "TOFU", label: "Neue Menschen erreichen" },
  { wert: "MOFU", label: "Vertrauen und Expertenstatus aufbauen" },
  { wert: "BOFU", label: "Kundenanfragen gewinnen" },
];
const ZIEL_LABEL = Object.fromEntries(ZIELE.map((z) => [z.wert, z.label]));
```

In `VideoAnalystPage` bei den anderen `useState`-Zeilen:

```jsx
  const [ziel, setZiel] = useState("");
  // Solange V3 nicht freigegeben ist, bleibt v2_hybrid der Default. Umschalten nur in der
  // Admin-Ansicht — Endnutzer sehen weiterhin genau eine Variante (Entscheidung 2026-08-10).
  const [engine, setEngine] = useState("v2_hybrid");
```

- [ ] **Step 2: Dropdown rendern**

Direkt unter dem Format-Block (nach dem schliessenden `</div>` der Format-Auswahl):

```jsx
          {engine === "v3" && (
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                Ziel des Videos <span style={{ color: "var(--danger, #c0392b)" }}>*</span>
              </label>
              <select
                value={ziel}
                onChange={(e) => setZiel(e.target.value)}
                disabled={phase === "running"}
                style={{ width: "100%", padding: "8px 10px", fontSize: 14,
                         border: "1px solid var(--line-strong)", borderRadius: 8 }}
              >
                <option value="">Bitte wählen …</option>
                {ZIELE.map((z) => <option key={z.wert} value={z.wert}>{z.label}</option>)}
              </select>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                Wofür ist dieses Video gedacht? Die Bewertung richtet sich danach — ein Video für
                Reichweite wird anders beurteilt als eines, das Anfragen bringen soll.
              </div>
            </div>
          )}

          {!!adminPw && (
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontSize: 13 }}>
              Version
              <select value={engine} onChange={(e) => setEngine(e.target.value)}
                      disabled={phase === "running"}>
                <option value="v2_hybrid">V2 (aktuell)</option>
                <option value="v3">V3 (Zielsteuerung)</option>
              </select>
            </label>
          )}
```

- [ ] **Step 3: Start-Bedingung und Request**

`canStart` um das Ziel erweitern — die bestehende Definition (`file && format`) wird zu:

```jsx
  const canStart = !!file && !!format && (engine !== "v3" || !!ziel);
```

In `startAnalysis` die URL erweitern. Die bestehende Zeile baut den Query-String mit `format` und
`planned_text_hook`; `engine` und `ziel` kommen dazu:

```jsx
    const qs = new URLSearchParams({
      engine,
      format,
      planned_text_hook: plannedTextHook,
      ...(engine === "v3" ? { ziel } : {}),
    });
    const r = await api("POST", `/api/analyst/${runId}/start?${qs}`, koerper);
```

- [ ] **Step 4: Score-Label**

Im Ergebnis-Block das feste Label ersetzen:

```jsx
                <div className="analyst-score-label">
                  {result.gewaehltes_ziel
                    ? `gemessen an: ${ZIEL_LABEL[result.gewaehltes_ziel] || result.gewaehltes_ziel}`
                    : "Performance-Score von 100"}
                </div>
```

Und den bestehenden `{ev.funnel && <span className="analyst-funnel">{ev.funnel}</span>}` nur noch
ohne Ziel zeigen, damit dieselbe Information nicht zweimal dasteht:

```jsx
                {!result.gewaehltes_ziel && ev.funnel && (
                  <span className="analyst-funnel">{ev.funnel}</span>
                )}
```

- [ ] **Step 5: Manuell pruefen**

```bash
python3 -m uvicorn main:app --reload --port 8000
```

Im Browser auf `http://localhost:8000`:
1. Ohne Admin-Login: kein Versions-Umschalter, kein Ziel-Dropdown, Start wie bisher moeglich.
2. Mit Admin-Login, Version V3: Ziel-Dropdown erscheint, Start bleibt gesperrt bis ein Ziel gewaehlt ist.
3. Ein Video mit Ziel „Neue Menschen erreichen" analysieren → Label zeigt „gemessen an: Neue Menschen erreichen".
4. Dasselbe Video mit Ziel „Kundenanfragen gewinnen" → neue Analyse laeuft (kein Cache-Treffer), Score weicht ab.

- [ ] **Step 6: Commit**

```bash
git add static/app.jsx
git commit -m "feat(analyst): Ziel-Dropdown, Versionsumschalter in der Admin-Ansicht, Score-Label"
```

---

## Task 13: Abschluss

- [ ] **Step 1: Volle Suite**

Run: `python3 -m pytest tests/test_analyst_cache.py tests/test_server_deploy.py tests/test_analyst_ziel.py -q`
Expected: alle passed

- [ ] **Step 2: Replay erneut als A/B gegen den Stand vor diesem Branch**

```bash
python3 tools/replay_nachbearbeitung.py > /tmp/replay_v3.txt
git checkout main -- services/ models/
python3 tools/replay_nachbearbeitung.py > /tmp/replay_main.txt
git checkout feature/analyst-v3-stufe1 -- services/ models/
diff /tmp/replay_main.txt /tmp/replay_v3.txt && echo "KEIN UNTERSCHIED"
```

Expected: `KEIN UNTERSCHIED` — kein gespeicherter Altlauf aendert sich durch den ganzen Branch.

- [ ] **Step 3: Zwei echte Laeufe vergleichen**

Dasselbe Video einmal mit V2 und einmal mit V3 (Ziel MOFU) analysieren, dann:

Run: `python3 tools/vergleiche_laeufe.py analyst_runs/<v2-id>/prompt_log.md analyst_runs/<v3-id>/prompt_log.md`
Expected: Der Input-Block (Transkript, Sprachstatistik, Pausen, Messwerte) ist identisch. Abweichungen
dort heissen, dass die Vorverarbeitung nicht wirklich gleich lief — dann ist der Vergleich wertlos.

- [ ] **Step 4: Push durch Chris**

Die Geraete-Shell hat keine GitHub-Credentials. Chris loest `git push` im Mac-Terminal aus; der
Auto-Deploy-Runner baut danach von allein.

**Achtung Deploy:** `ANALYST_ONLY=1` auf dem VPS blendet den Editor aus, nicht die Admin-Ansicht.
V3 ist dort nach dem Deploy ueber den Versions-Umschalter erreichbar, sobald das Admin-Passwort
eingegeben ist. Der Default bleibt `v2_hybrid`.

---

## Was Stufe 1 NICHT enthaelt

Diese Punkte aus der Spec stehen in Stufe 2 und 3 und sind hier bewusst offen:

- Untertitel gesplittet (`untertitel_vorhanden` / `untertitel_gestaltung`), Audioqualitaet, CTA als
  bewertete Dimensionen. Ihre Gewichte stehen bereits in `SCORE_GEWICHTE_JE_ZIEL`; ohne Score werden
  sie von `berechne_performance_score` uebersprungen und ihr Gewicht verteilt sich proportional.
- Die vier Kategorie-Aufklapper im Frontend; der bestehende Sammel-Aufklapper bleibt in Stufe 1.
- Brand-/Zielgruppen-Datei und `zielgruppen_abgleich`.
- Die Umstellung des Default-Engine auf `v3`.
