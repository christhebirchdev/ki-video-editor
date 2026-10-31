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


# ---------- Task 7: Nutzer und Zugriff ----------

def test_upload_schreibt_nutzer(client):
    rid = _upload(client, A)
    assert _meta(client, rid)["nutzer"] == A["X-Analyst-Nutzer"]


def test_ungueltige_nutzer_id_wird_ignoriert(client):
    rid = _upload(client, {"X-Analyst-Nutzer": "../etc/passwd"})
    assert "nutzer" not in _meta(client, rid) or _meta(client, rid)["nutzer"] == ""


def test_fremder_nutzer_sieht_lauf_nicht(client):
    rid = _upload(client, A)
    _fertig(client, rid)
    assert client.get(f"/api/analyst/{rid}", headers=B).status_code == 404
    assert client.get(f"/api/analyst/{rid}/chat", headers=B).status_code == 404
    assert client.post(f"/api/analyst/{rid}/start", headers=B,
                       params={"format": "Talking Head", "engine": "v4", "ziel": "MOFU"}).status_code == 404
    assert client.get(f"/api/analyst/{rid}", headers=A).status_code == 200


def test_ohne_header_bleibt_erlaubt_ausser_im_pflichtmodus(client, monkeypatch):
    from config import settings
    rid = _upload(client, A)
    _fertig(client, rid)
    assert client.get(f"/api/analyst/{rid}").status_code == 200
    monkeypatch.setattr(settings, "analyst_nutzer_pflicht", True)
    assert client.get(f"/api/analyst/{rid}").status_code == 401
    assert client.get(f"/api/analyst/{rid}", headers=A).status_code == 200


def test_pflichtmodus_lauf_ohne_besitzer_ist_gesperrt(client, monkeypatch):
    from config import settings
    rid = _upload(client)            # Altlauf ohne Nutzer
    monkeypatch.setattr(settings, "analyst_nutzer_pflicht", True)
    assert client.get(f"/api/analyst/{rid}", headers=A).status_code == 404


# ---------- Task 8: Historie, Loeschen, Export, Uebergang, Neubewertung ----------

def test_meine_listet_nur_eigene_neueste_zuerst(client):
    r1 = _upload(client, A); _fertig(client, r1, score=60)
    r2 = _upload(client, A); _fertig(client, r2, score=75)
    _upload(client, B)
    m = client.get("/api/analyst/meine", headers=A).json()["laeufe"]
    assert [x["id"] for x in m] == [r2, r1]
    assert m[0]["score"] == 75 and m[0]["ziel"] == "MOFU" and m[0]["video_da"] is True
    assert m[0]["phase"] == "done"


def test_meine_ohne_nutzer_ist_leer(client):
    _upload(client, A)
    assert client.get("/api/analyst/meine").json()["laeufe"] == []


def test_loeschen_nur_eigener_lauf(client):
    rid = _upload(client, A)
    assert client.delete(f"/api/analyst/{rid}", headers=B).status_code == 404
    assert client.delete(f"/api/analyst/{rid}").status_code == 404   # ohne Nutzer kein Loeschen
    assert client.delete(f"/api/analyst/{rid}", headers=A).status_code == 200
    assert not (client.runs / rid).exists()


def test_alle_eigenen_loeschen(client):
    r1 = _upload(client, A); r2 = _upload(client, A); r3 = _upload(client, B)
    assert client.delete("/api/analyst/meine", headers=A).json()["geloescht"] == 2
    assert not (client.runs / r1).exists() and not (client.runs / r2).exists()
    assert (client.runs / r3).exists()


def test_export_liefert_analysen(client):
    rid = _upload(client, A); _fertig(client, rid, score=81)
    ex = client.get("/api/analyst/meine/export", headers=A).json()
    assert ex["nutzer"] == A["X-Analyst-Nutzer"]
    assert ex["analysen"][0]["id"] == rid
    assert ex["analysen"][0]["analyse"]["evaluation"]["performance_score"] == 81


def test_uebergang_geraet_zu_konto(client, monkeypatch):
    from config import settings
    alt = {"X-Analyst-Nutzer": "browser-1234-abcd", "X-Analyst-Geraet": "browser-1234-abcd"}
    rid = _upload(client, alt)
    monkeypatch.setattr(settings, "analyst_nutzer_pflicht", True)
    konto = {"X-Analyst-Nutzer": "konto-42-xyz-0000", "X-Analyst-Geraet": "browser-1234-abcd"}
    m = client.get("/api/analyst/meine", headers=konto).json()["laeufe"]
    assert [x["id"] for x in m] == [rid]
    assert _meta(client, rid)["nutzer"] == "konto-42-xyz-0000"


def test_uebergang_nicht_ohne_pflichtmodus(client):
    alt = {"X-Analyst-Nutzer": "browser-1234-abcd", "X-Analyst-Geraet": "browser-1234-abcd"}
    _upload(client, alt)
    fremd = {"X-Analyst-Nutzer": "jemand-anderes-01", "X-Analyst-Geraet": "browser-1234-abcd"}
    assert client.get("/api/analyst/meine", headers=fremd).json()["laeufe"] == []


def test_neu_bewerten_mit_gleichem_video(client):
    rid = _upload(client, A); _fertig(client, rid)
    r = client.post(f"/api/analyst/{rid}/neu-bewerten", headers=A)
    assert r.status_code == 200, r.text
    neu = r.json()["id"]
    assert neu != rid
    mn, ma = _meta(client, neu), _meta(client, rid)
    assert mn["sha256"] == ma["sha256"] and mn["nutzer"] == A["X-Analyst-Nutzer"]
    assert list((client.runs / neu / "raw").iterdir())
    assert client.post(f"/api/analyst/{rid}/neu-bewerten", headers=B).status_code == 404


def test_neu_bewerten_ohne_video_410(client):
    rid = _upload(client, A)
    for f in (client.runs / rid / "raw").iterdir():
        f.unlink()
    assert client.post(f"/api/analyst/{rid}/neu-bewerten", headers=A).status_code == 410


def test_start_mit_vorgaenger(client):
    alt = _upload(client, A); _fertig(client, alt)
    neu = _upload(client, A)
    r = client.post(f"/api/analyst/{neu}/start", headers=A, params={
        "format": "Talking Head", "engine": "v4", "ziel": "MOFU", "vorgaenger": alt})
    assert r.status_code == 200, r.text
    assert _meta(client, neu)["vorgaenger"] == alt


def test_start_mit_fremdem_oder_unfertigem_vorgaenger(client):
    fremd = _upload(client, B); _fertig(client, fremd)
    unfertig = _upload(client, A)
    neu = _upload(client, A)
    p = {"format": "Talking Head", "engine": "v4", "ziel": "MOFU"}
    assert client.post(f"/api/analyst/{neu}/start", headers=A,
                       params={**p, "vorgaenger": fremd}).status_code == 404
    assert client.post(f"/api/analyst/{neu}/start", headers=A,
                       params={**p, "vorgaenger": unfertig}).status_code == 409


def test_fortschritt_endpunkt(client):
    for _ in range(3):
        rid = _upload(client, A)
        _fertig(client, rid, evaluation={"performance_score": 70, "hook": {"sprech_hook_score": 4}})
    e = client.get("/api/analyst/fortschritt", headers=A).json()
    # 3 Analysen + Hook-Schnitt 4,0 (die anderen Bereiche ohne Score) → Koennens-Weg: Level 7
    assert e["analysen"] == 3 and e["gesamt"]["level"] == 7
    assert next(k for k in e["kategorien"] if k["key"] == "hook")["schnitt"] == 4.0
    assert client.get("/api/analyst/fortschritt", headers=B).json()["analysen"] == 0


# ---------- Task 10: Aufbewahrung ----------

def test_alte_videos_werden_geloescht_analyse_bleibt(tmp_path):
    from datetime import datetime, timedelta
    from services.analyst_aufbewahrung import raeume_videos_auf
    jetzt = datetime(2026, 10, 30)
    for name, alter in (("alt", 31), ("jung", 5)):
        d = tmp_path / name
        (d / "raw").mkdir(parents=True)
        (d / "raw" / "v.mp4").write_bytes(b"x")
        (d / "analysis.json").write_text("{}")
        (d / "meta.json").write_text(json.dumps({"created_at": (jetzt - timedelta(days=alter)).isoformat()}))
        (d / "status.json").write_text(json.dumps({"phase": "done"}))
    assert raeume_videos_auf(tmp_path, 30, jetzt) == 1
    assert not list((tmp_path / "alt" / "raw").iterdir())
    assert (tmp_path / "alt" / "analysis.json").exists()
    assert json.loads((tmp_path / "alt" / "meta.json").read_text())["video_geloescht"]
    assert list((tmp_path / "jung" / "raw").iterdir())
    assert raeume_videos_auf(tmp_path, 0, jetzt) == 0      # 0 = ausgeschaltet


def test_cache_check_verraet_keine_fremden_laeufe(client):
    fremd = _upload(client, B); _fertig(client, fremd)
    (client.runs / fremd / "meta.json").write_text(json.dumps({**_meta(client, fremd),
        "format": "Talking Head", "engine": "v4", "ziel": "MOFU", "prompt_version": "x"}))
    eigen = _upload(client, A)
    r = client.get(f"/api/analyst/{eigen}/cache-check", headers=A,
                   params={"format": "Talking Head", "ziel": "MOFU", "engine": "v4"})
    assert r.json() == {"treffer": False}


# ---------- Video einer frueheren Analyse abspielen ----------

def test_video_endpunkt_ganz_und_mit_range(client):
    rid = _upload(client, A)
    r = client.get(f"/api/analyst/{rid}/video", headers=A)
    assert r.status_code == 200 and r.content == VIDEO
    assert r.headers["accept-ranges"] == "bytes"
    r = client.get(f"/api/analyst/{rid}/video", headers={**A, "Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == VIDEO[:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(VIDEO)}"
    r = client.get(f"/api/analyst/{rid}/video", headers={**A, "Range": "bytes=10-"})
    assert r.status_code == 206 and r.content == VIDEO[10:]


def test_video_endpunkt_fremd_und_geloescht(client):
    rid = _upload(client, A)
    assert client.get(f"/api/analyst/{rid}/video", headers=B).status_code == 404
    for f in (client.runs / rid / "raw").iterdir():
        f.unlink()
    assert client.get(f"/api/analyst/{rid}/video", headers=A).status_code == 410


def test_video_range_ausserhalb(client):
    rid = _upload(client, A)
    r = client.get(f"/api/analyst/{rid}/video", headers={**A, "Range": f"bytes={len(VIDEO)+5}-"})
    assert r.status_code == 416
