"""
Tests für den Analyse-Cache: dieselbe Videodatei mit denselben Eingaben liefert dasselbe,
bereits gespeicherte Ergebnis — statt eines zweiten, leicht abweichenden Outputs.

Laufen ohne API-Keys und ohne Pipeline: `run_analysis` ist gestubbt, `analyst_vlm.is_available`
ebenfalls. ANALYST_PATH zeigt auf tmp_path, damit die echten `analyst_runs/` unberührt bleiben.
"""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

VIDEO = b"\x00\x01fake-mp4-bytes" * 100
ANDERES_VIDEO = b"\x00\x01voellig-anderes-video" * 100
ERGEBNIS = {"id": "quelle", "filename": "clip.mp4", "duration_sec": 12.0, "scene_count": 0,
            "scenes": [], "evaluation": {"performance_score": 73}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient OHNE `with`: der Lifespan (markiere_abgebrochene_laeufe) würde sonst über die
    echten Läufe laufen. ANALYST_PATH wird zusätzlich in beiden Modulen umgebogen."""
    import main
    from api import analyst as analyst_api
    from services import analyst_engine

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(analyst_api.analyst_vlm, "is_available", lambda: (True, ""))
    gestartet: list[str] = []
    monkeypatch.setattr(analyst_api, "run_analysis", lambda run_id: gestartet.append(run_id))

    c = TestClient(main.app)
    c.gestartet = gestartet
    return c


def _upload(client, inhalt: bytes = VIDEO, name: str = "clip.mp4") -> str:
    r = client.post("/api/analyst/upload", files={"file": (name, inhalt, "video/mp4")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _start(client, run_id: str, format: str = "Talking Head", hook: str = "", json_body=None):
    return client.post(
        f"/api/analyst/{run_id}/start",
        params={"format": format, "planned_text_hook": hook},
        json=json_body,
    )


def _fertig(runs: Path, run_id: str, ergebnis: dict = None):
    """Markiert einen Lauf als abgeschlossen — so, wie die Engine es täte."""
    d = runs / run_id
    (d / "analysis.json").write_text(json.dumps(ergebnis or ERGEBNIS, ensure_ascii=False))
    (d / "status.json").write_text(json.dumps(
        {"phase": "done", "detail": "Analyse abgeschlossen", "done": True, "error": ""}))


def _quelllauf(runs: Path, run_id: str, *, inhalt: bytes = VIDEO, format: str = "Talking Head",
               hook: str = "", engine: str = "v2_hybrid", prompt_version: str = None,
               created_at: str = None, phase: str = "done", mit_analyse: bool = True):
    """Baut einen fertigen Altlauf von Hand — für Fälle, die sich über die API nicht
    herstellen lassen (älteres Datum, alte Prompt-Version, abgebrochener Lauf)."""
    from services.analyst_eval import PROMPT_VERSION

    d = runs / run_id
    (d / "raw").mkdir(parents=True)
    (d / "raw" / "clip.mp4").write_bytes(inhalt)
    (d / "meta.json").write_text(json.dumps({
        "id": run_id, "filename": "clip.mp4",
        "created_at": created_at or datetime.now().isoformat(),
        "sha256": hashlib.sha256(inhalt).hexdigest(),
        "format": format, "planned_text_hook": hook, "engine": engine,
        "prompt_version": prompt_version or PROMPT_VERSION,
    }, ensure_ascii=False))
    (d / "status.json").write_text(json.dumps(
        {"phase": phase, "detail": "", "done": phase == "done", "error": ""}))
    if mit_analyse:
        (d / "analysis.json").write_text(json.dumps({**ERGEBNIS, "id": run_id}, ensure_ascii=False))
    return d


# ---------- Hash beim Upload ----------

def test_upload_schreibt_sha256_und_datei_bleibt_unversehrt(client, tmp_path):
    run_id = _upload(client)
    meta = json.loads((tmp_path / run_id / "meta.json").read_text())
    assert meta["sha256"] == hashlib.sha256(VIDEO).hexdigest()
    assert (tmp_path / run_id / "raw" / "clip.mp4").read_bytes() == VIDEO


def test_verschiedene_dateien_verschiedene_hashes(client, tmp_path):
    a, b = _upload(client, VIDEO), _upload(client, ANDERES_VIDEO)
    hash_a = json.loads((tmp_path / a / "meta.json").read_text())["sha256"]
    hash_b = json.loads((tmp_path / b / "meta.json").read_text())["sha256"]
    assert hash_a != hash_b


# ---------- Treffer ----------

def test_zweiter_upload_liefert_das_gespeicherte_ergebnis(client, tmp_path):
    erster = _upload(client)
    assert _start(client, erster).json()["status"] == "started"
    _fertig(tmp_path, erster)

    zweiter = _upload(client)
    antwort = _start(client, zweiter).json()
    assert antwort["status"] == "cached"
    assert antwort["cached_from"] == erster
    # Die Pipeline darf für den zweiten Lauf NICHT angeworfen worden sein
    assert client.gestartet == [erster]
    # …und das Ergebnis ist byte-gleich, nicht nur „ähnlich"
    assert (tmp_path / zweiter / "analysis.json").read_bytes() == (tmp_path / erster / "analysis.json").read_bytes()


def test_get_liefert_ergebnis_und_cache_hinweis_sofort(client, tmp_path):
    erster = _upload(client)
    _start(client, erster)
    _fertig(tmp_path, erster)
    zweiter = _upload(client)
    _start(client, zweiter)

    daten = client.get(f"/api/analyst/{zweiter}").json()
    assert daten["done"] is True and daten["phase"] == "done"
    assert daten["result"]["evaluation"]["performance_score"] == 73
    assert daten["cached_from"] == erster
    assert daten["cached_at"]          # Datum des Originallaufs für den Hinweistext


def test_bei_mehreren_treffern_gewinnt_der_aelteste(client, tmp_path):
    alt = (datetime.now() - timedelta(days=3)).isoformat()
    neu = (datetime.now() - timedelta(hours=1)).isoformat()
    _quelllauf(tmp_path, "aaaaaaaa", created_at=neu)
    _quelllauf(tmp_path, "zzzzzzzz", created_at=alt)   # Name absichtlich „später", Datum früher

    run_id = _upload(client)
    assert _start(client, run_id).json()["cached_from"] == "zzzzzzzz"


# ---------- Kein Treffer ----------

def test_anderes_format_ist_kein_treffer(client, tmp_path):
    _quelllauf(tmp_path, "quelle01", format="Talking Head")
    run_id = _upload(client)
    assert _start(client, run_id, format="Tutorial").json()["status"] == "started"


def test_andere_texthook_ist_kein_treffer(client, tmp_path):
    _quelllauf(tmp_path, "quelle01", hook="alte Hook")
    run_id = _upload(client)
    assert _start(client, run_id, hook="neue Hook").json()["status"] == "started"


def test_alte_prompt_version_ist_kein_treffer(client, tmp_path):
    """Nach einer Prompt-Änderung (PROMPT_VERSION hochgezählt) darf der Cache nicht das
    Ergebnis der alten Logik ausliefern."""
    _quelllauf(tmp_path, "quelle01", prompt_version="2000-01-01a")
    run_id = _upload(client)
    assert _start(client, run_id).json()["status"] == "started"


def test_abgebrochener_lauf_ist_keine_quelle(client, tmp_path):
    _quelllauf(tmp_path, "quelle01", phase="error")
    run_id = _upload(client)
    assert _start(client, run_id).json()["status"] == "started"


def test_lauf_ohne_analyse_ist_keine_quelle(client, tmp_path):
    _quelllauf(tmp_path, "quelle01", mit_analyse=False)
    run_id = _upload(client)
    assert _start(client, run_id).json()["status"] == "started"


def test_anderes_video_ist_kein_treffer(client, tmp_path):
    _quelllauf(tmp_path, "quelle01", inhalt=VIDEO)
    run_id = _upload(client, ANDERES_VIDEO)
    assert _start(client, run_id).json()["status"] == "started"


def test_altlauf_ohne_hash_ist_keine_quelle(client, tmp_path):
    """Läufe von vor diesem Feature haben kein sha256 in meta.json — sie dürfen nie matchen."""
    d = _quelllauf(tmp_path, "quelle01")
    meta = json.loads((d / "meta.json").read_text())
    del meta["sha256"]
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))

    run_id = _upload(client)
    assert _start(client, run_id).json()["status"] == "started"


# ---------- Force-Rerun (Admin) ----------

def test_force_mit_admin_passwort_umgeht_den_cache(client, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "admin_password", "geheim")
    _quelllauf(tmp_path, "quelle01")

    run_id = _upload(client)
    antwort = _start(client, run_id, json_body={"password": "geheim", "force": True})
    assert antwort.json()["status"] == "started"
    assert client.gestartet == [run_id]


def test_force_ohne_passwort_wird_abgewiesen(client, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "admin_password", "geheim")
    _quelllauf(tmp_path, "quelle01")

    run_id = _upload(client)
    antwort = _start(client, run_id, json_body={"password": "falsch", "force": True})
    assert antwort.status_code == 401
    assert client.gestartet == []          # nichts gestartet
    assert not (tmp_path / run_id / "analysis.json").exists()   # und nichts aus dem Cache kopiert


def test_ohne_force_bleibt_body_folgenlos(client, tmp_path):
    """Ein Body ohne force darf den Cache nicht umgehen — und kein Passwort verlangen."""
    _quelllauf(tmp_path, "quelle01")
    run_id = _upload(client)
    assert _start(client, run_id, json_body={"password": "", "force": False}).json()["status"] == "cached"
