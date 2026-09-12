"""Tests fuer die Zielsteuerung der Bewertung (Analyst V3).

Laufen ohne API-Keys: nur Schema-Konstanten und reine Nachbearbeitungs-Funktionen.
"""
import json

import pytest
from fastapi.testclient import TestClient

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


def test_altlauf_ohne_ziel_laedt_unveraendert():
    from models.analyst import AnalystResult
    r = AnalystResult(id="x", filename="c.mp4", duration_sec=1.0, scene_count=0, scenes=[])
    assert r.gewaehltes_ziel == ""


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
