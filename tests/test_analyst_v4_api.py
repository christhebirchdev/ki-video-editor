"""V4-API: Engine v4, Nutzer-Zuordnung, Historie, Loeschen, Export, Neubewertung, Aufbewahrung.

Ohne API-Keys und ohne Pipeline (run_analysis gestubbt), ANALYST_PATH zeigt auf tmp_path.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

VIDEO = b"\x00\x01fake-mp4-bytes" * 100
A = {"X-Analyst-Nutzer": "nutzer-aaaa-1111", "X-Analyst-Geraet": "nutzer-aaaa-1111"}
B = {"X-Analyst-Nutzer": "nutzer-bbbb-2222", "X-Analyst-Geraet": "nutzer-bbbb-2222"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import main
    from api import analyst as analyst_api
    from config import settings
    from services import analyst_engine

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(analyst_api.analyst_vlm, "is_available", lambda: (True, ""))
    monkeypatch.setattr(settings, "analyst_nutzer_pflicht", False, raising=False)
    gestartet: list[str] = []
    monkeypatch.setattr(analyst_api, "run_analysis", lambda run_id: gestartet.append(run_id))
    c = TestClient(main.app)
    c.gestartet = gestartet
    c.runs = tmp_path
    return c


def _upload(client, headers=None, inhalt=VIDEO) -> str:
    r = client.post("/api/analyst/upload", files={"file": ("clip.mp4", inhalt, "video/mp4")},
                    headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _meta(client, run_id) -> dict:
    return json.loads((client.runs / run_id / "meta.json").read_text())


def _fertig(client, run_id, score=70, ziel="MOFU", evaluation=None):
    d = client.runs / run_id
    ev = evaluation or {"performance_score": score}
    (d / "analysis.json").write_text(json.dumps({
        "id": run_id, "filename": "clip.mp4", "duration_sec": 12.0, "scene_count": 0,
        "scenes": [], "engine": "v4", "gewaehltes_ziel": ziel, "evaluation": ev}))
    (d / "status.json").write_text(json.dumps(
        {"phase": "done", "detail": "Analyse abgeschlossen", "done": True, "error": ""}))


# ---------- Task 2: Engine v4 ----------

def test_v4_braucht_ziel(client):
    rid = _upload(client)
    r = client.post(f"/api/analyst/{rid}/start", params={"format": "Talking Head", "engine": "v4"})
    assert r.status_code == 422


def test_v4_startet_mit_ziel(client):
    rid = _upload(client)
    r = client.post(f"/api/analyst/{rid}/start",
                    params={"format": "Talking Head", "engine": "v4", "ziel": "mofu"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "started"
    m = _meta(client, rid)
    assert m["engine"] == "v4" and m["ziel"] == "MOFU"


def test_engine_dispatcher_kennt_v4(tmp_path, monkeypatch):
    from services import analyst_engine
    aufrufe = []

    def fake_v2(run_dir, video, meta, mode, split=False):
        from models.analyst import AnalystResult
        aufrufe.append((mode, split, meta["engine"]))
        return AnalystResult(id="", filename="a.mp4", duration_sec=1, scene_count=0, scenes=[])
    monkeypatch.setattr(analyst_engine, "_run_v2", fake_v2)
    monkeypatch.setattr(analyst_engine, "_find_video", lambda d: d / "raw" / "a.mp4")
    d = tmp_path / "r1"
    (d / "raw").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"engine": "v4", "ziel": "MOFU"}))
    analyst_engine._run("r1", d)
    assert aufrufe == [("hybrid", True, "v4")]
    assert json.loads((d / "analysis.json").read_text())["engine"] == "v4"
