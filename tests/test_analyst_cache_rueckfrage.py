"""Cache-Rueckfrage: derselbe Clip darf nicht zweimal verschieden bewertet werden.

Anlass (Kollege von Chris, 2026-09-13): "er hat ein video zweimal hintereinander hochgeladen und
2 verschiedene ergebnisse kamen." Genau das soll der Cache verhindern — die Bewertung entsteht in
einem LLM-Lauf und ist nicht deterministisch.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import api.analyst as api_analyst
    from services import analyst_engine
    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(api_analyst, "ANALYST_PATH", tmp_path)
    from main import app
    return TestClient(app), tmp_path


def _lauf(tmp_path, run_id, *, sha="abc", phase="done", pv="2026-09-14a",
          format="Andere", ziel="", ergebnis=True, created="2026-09-13T10:00:00", **extra):
    d = tmp_path / run_id
    (d / "raw").mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({
        "id": run_id, "filename": "v.mp4", "sha256": sha, "prompt_version": pv,
        "format": format, "ziel": ziel, "engine": "v3" if ziel else "v2_split",
        "created_at": created, **extra}))
    (d / "status.json").write_text(json.dumps({"phase": phase, "message": ""}))
    if ergebnis:
        (d / "analysis.json").write_text(json.dumps(
            {"id": run_id, "filename": "v.mp4", "duration_sec": 10.0, "scene_count": 0,
             "scenes": [], "evaluation": {"performance_score": 77}}))
    return d


# --- Die Suche nach einer frueheren Analyse -----------------------------------------------------

def test_exakt_gleiche_eingaben_werden_gefunden():
    from services.analyst_cache import finde_vorherige_analyse
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _lauf(tmp, "alt11111")
        neu = {"sha256": "abc", "prompt_version": "2026-09-14a", "format": "Andere",
               "ziel": "", "engine": "v2_split"}
        pfad, abweichung = finde_vorherige_analyse(tmp, neu, ausser="neu22222")
        assert pfad is not None and pfad.name == "alt11111"
        assert abweichung == []


def test_neue_prompt_version_findet_den_lauf_trotzdem_und_sagt_warum():
    """DAS ist die Stelle, an der ein normaler Nutzer zwei verschiedene Ergebnisse bekommt: Nach
    einem Deploy hat sich die Prompt-Version geaendert, der strenge Schluessel passt nicht mehr,
    und die zweite Analyse laeuft ungefragt komplett neu. Die Datei ist aber dieselbe — der Nutzer
    soll die Wahl bekommen, statt sie still zu verlieren."""
    from services.analyst_cache import finde_vorherige_analyse
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _lauf(tmp, "alt11111", pv="2026-09-01a")
        neu = {"sha256": "abc", "prompt_version": "2026-09-14a", "format": "Andere",
               "ziel": "", "engine": "v2_split"}
        pfad, abweichung = finde_vorherige_analyse(tmp, neu, ausser="neu22222")
        assert pfad is not None
        assert abweichung == ["prompt_version"]


def test_andere_datei_ist_kein_treffer():
    from services.analyst_cache import finde_vorherige_analyse
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _lauf(tmp, "alt11111", sha="anders")
        neu = {"sha256": "abc", "prompt_version": "x", "format": "Andere", "ziel": "", "engine": "v2_split"}
        assert finde_vorherige_analyse(tmp, neu, ausser="neu22222")[0] is None


def test_anderes_ziel_ist_kein_treffer():
    """Dasselbe Video mit anderem Videoziel ist ein legitim anderes Ergebnis (andere Gewichte)."""
    from services.analyst_cache import finde_vorherige_analyse
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _lauf(tmp, "alt11111", ziel="TOFU")
        neu = {"sha256": "abc", "prompt_version": "2026-09-14a", "format": "Andere",
               "ziel": "BOFU", "engine": "v3"}
        assert finde_vorherige_analyse(tmp, neu, ausser="neu22222")[0] is None


def test_ein_noch_laufender_lauf_wird_gemeldet_statt_ignoriert():
    """Der zweite Upload, waehrend der erste noch rechnet: Bisher war das ein Cache-Miss und der
    Nutzer zahlte eine zweite, abweichende Analyse. Jetzt taucht er als Treffer auf — das Frontend
    kann fragen, statt still neu zu rechnen."""
    from services.analyst_cache import finde_vorherige_analyse
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _lauf(tmp, "alt11111", phase="evaluate", ergebnis=False)
        neu = {"sha256": "abc", "prompt_version": "2026-09-14a", "format": "Andere",
               "ziel": "", "engine": "v2_split"}
        pfad, abweichung = finde_vorherige_analyse(tmp, neu, ausser="neu22222")
        assert pfad is not None and abweichung == ["laeuft_noch"]


# --- Der Endpunkt, den das Frontend vor dem Start fragt -------------------------------------------

def test_cache_check_meldet_den_treffer(client):
    c, tmp = client
    _lauf(tmp, "alt11111", created="2026-09-12T09:30:00")
    _lauf(tmp, "neu22222", ergebnis=False, phase="uploaded")
    r = c.get("/api/analyst/neu22222/cache-check?format=Andere")
    assert r.status_code == 200, r.text
    daten = r.json()
    assert daten["treffer"] is True
    assert daten["run_id"] == "alt11111"
    assert daten["erstellt_am"].startswith("2026-09-12")
    assert daten["abweichung"] == []


def test_cache_check_ohne_treffer(client):
    c, tmp = client
    _lauf(tmp, "neu22222", ergebnis=False, phase="uploaded")
    assert c.get("/api/analyst/neu22222/cache-check?format=Andere").json()["treffer"] is False


def test_uebernehmen_zeigt_das_alte_ergebnis_ohne_neue_analyse(client):
    """Die Antwort auf "nein, ich habe nichts veraendert"."""
    c, tmp = client
    _lauf(tmp, "alt11111")
    d = _lauf(tmp, "neu22222", ergebnis=False, phase="uploaded")
    r = c.post("/api/analyst/neu22222/uebernehmen?von=alt11111")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cached"
    assert (d / "analysis.json").exists()
    assert json.loads((d / "status.json").read_text())["phase"] == "done"
    assert json.loads((d / "meta.json").read_text())["cached_from"] == "alt11111"


def test_uebernehmen_lehnt_einen_lauf_ohne_ergebnis_ab(client):
    c, tmp = client
    _lauf(tmp, "alt11111", ergebnis=False, phase="evaluate")
    _lauf(tmp, "neu22222", ergebnis=False, phase="uploaded")
    assert c.post("/api/analyst/neu22222/uebernehmen?von=alt11111").status_code == 409


def test_neu_true_umgeht_den_cache_ohne_admin_passwort(client, monkeypatch):
    """Die Antwort auf "ja, ich habe das Video veraendert". Ohne diesen Schalter wuerde /start die
    alte Analyse uebernehmen und dem Nutzer widersprechen."""
    import api.analyst as api_analyst
    from services import analyst_vlm
    monkeypatch.setattr(analyst_vlm, "is_available", lambda: (False, "kein Modell"))
    c, tmp = client
    _lauf(tmp, "alt11111")
    _lauf(tmp, "neu22222", ergebnis=False, phase="uploaded")
    r = c.post("/api/analyst/neu22222/start?format=Andere&engine=v2_split&neu=true")
    # 503 heisst: Der Cache wurde NICHT genommen, der Start ging bis zur Modellpruefung durch.
    assert r.status_code == 503, r.text


# --- Frontend ------------------------------------------------------------------------------------

def _appjsx():
    import pathlib
    return pathlib.Path("static/app.jsx").read_text(encoding="utf-8")


def test_das_frontend_fragt_VOR_dem_start():
    """Reihenfolge ist hier der ganze Punkt: Nach dem Start ist das Geld ausgegeben."""
    quelle = _appjsx()
    assert quelle.index("/cache-check?") < quelle.index("/start?${qs}`")


def test_der_dialog_bietet_beide_antworten_an():
    quelle = _appjsx()
    assert "Ja, verändert — neu analysieren" in quelle
    assert "Nein — vorhandene Analyse zeigen" in quelle
    assert "uebernehmeAlteAnalyse" in quelle and "starteTrotzdem" in quelle


def test_ja_schickt_neu_true_mit():
    """Ohne den Schalter wuerde der Server die alte Analyse uebernehmen und seiner eigenen Frage
    widersprechen."""
    import re
    quelle = _appjsx()
    block = re.search(r"async function starteTrotzdem\(\).*?\n  \}\n", quelle, re.S).group()
    assert 'neu: "true"' in block


def test_der_dialog_nutzt_das_design_system():
    quelle = _appjsx()
    css = __import__("pathlib").Path("static/styles.css").read_text(encoding="utf-8")
    assert 'className="dlg-hinter"' in quelle and 'className="dlg"' in quelle
    assert "btn btn-primary" in quelle.split('className="dlg-hinter"')[1][:2000]
    assert ".dlg{" in css and "var(--paper)" in css.split(".dlg{")[1][:200]


def test_bei_noch_laufender_analyse_ist_nein_gesperrt():
    """Es gibt dann schlicht kein Ergebnis zu zeigen."""
    quelle = _appjsx()
    block = quelle.split('className="dlg-knoepfe"')[1][:1200]
    assert 'disabled={rueckfrage.abweichung.includes("laeuft_noch")}' in block


def test_ein_fehler_bei_der_rueckfrage_blockiert_den_start_nicht():
    """Die Rueckfrage ist Komfort, keine Bedingung. Faellt der Endpunkt aus, soll die Analyse
    trotzdem laufen — serverseitig greift der Cache in /start ohnehin weiter."""
    quelle = _appjsx()
    block = quelle.split("/cache-check?")[0][-400:]
    assert "try {" in block
