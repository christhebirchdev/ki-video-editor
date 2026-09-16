"""Tests fuer die optionale Marken-/Zielgruppen-Datei (Analyst V3, Spec 1.2 und 3.2).

Laufen ohne API-Keys: Textextraktion, Kuerzung, Cache-Key, Prompt-Aufbau.
"""
import pytest

from services import analyst_marke


# --- Textextraktion ---------------------------------------------------------------------------

def test_md_und_txt_werden_direkt_gelesen():
    for name in ("marke.md", "zielgruppe.txt"):
        text, gekuerzt = analyst_marke.extrahiere("Wir sprechen Coaches an.".encode(), name)
        assert text == "Wir sprechen Coaches an."
        assert gekuerzt is False


def test_unbekannte_endung_wird_abgelehnt():
    with pytest.raises(ValueError) as e:
        analyst_marke.extrahiere(b"egal", "marke.pages")
    assert ".pages" in str(e.value)


def test_docx_wird_ausgelesen():
    docx = pytest.importorskip("docx")
    import io
    d = docx.Document()
    d.add_paragraph("Zielgruppe: Coaches im DACH-Raum.")
    d.add_paragraph("Tonalitaet: direkt.")
    puffer = io.BytesIO()
    d.save(puffer)
    text, _ = analyst_marke.extrahiere(puffer.getvalue(), "marke.docx")
    assert "Coaches im DACH-Raum" in text
    assert "Tonalitaet: direkt" in text


def test_leere_datei_wird_abgelehnt():
    """Eine Datei ohne lesbaren Text ist ein Bedienfehler, kein leerer Kontext: Ein gescanntes PDF
    ohne Textebene wuerde sonst still als 'Marke hinterlegt' durchgehen und die bedingt bewertbaren
    Dimensionen (protagonist_auftreten, zielgruppen_relevanz) freischalten, ohne Grundlage."""
    with pytest.raises(ValueError):
        analyst_marke.extrahiere(b"   \n  ", "marke.txt")


# --- Kuerzung ---------------------------------------------------------------------------------

def test_lange_datei_wird_auf_das_wortlimit_gekuerzt():
    text, gekuerzt = analyst_marke.extrahiere(
        ("wort " * (analyst_marke.MAX_WOERTER + 500)).encode(), "marke.md")
    assert gekuerzt is True
    assert len(text.split()) == analyst_marke.MAX_WOERTER


def test_kurze_datei_wird_nicht_gekuerzt():
    text, gekuerzt = analyst_marke.extrahiere(("wort " * 10).encode(), "marke.md")
    assert gekuerzt is False


# --- Prompt-Aufbau ----------------------------------------------------------------------------

def test_marke_landet_nur_im_v3_prompt():
    from services.analyst_eval import build_system_prompt
    v2 = build_system_prompt()
    v2_mit_marke = build_system_prompt(marke="Zielgruppe: Coaches.")
    assert v2 == v2_mit_marke, "ohne Ziel bleibt der V2-Prompt unveraendert"

    v3 = build_system_prompt(ziel="TOFU")
    v3_mit_marke = build_system_prompt(ziel="TOFU", marke="Zielgruppe: Coaches.")
    assert "Zielgruppe: Coaches." in v3_mit_marke
    assert "Zielgruppe: Coaches." not in v3


def test_geltungsbereich_steht_im_prompt():
    """Spec 1.2: Der Kontext darf handwerkliche Urteile NICHT beeinflussen. Ohne diesen Satz
    verwaessert eine hochglanzpolierte Marken-Datei den Schnitt- und Ton-Score."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(ziel="TOFU", marke="Zielgruppe: Coaches.")
    for dimension in ("schnitt_pacing", "audioqualitaet", "visuelle_aesthetik"):
        assert dimension in p.split("ZIELGRUPPE UND STRATEGIE")[1][:2000], dimension


# --- Cache ------------------------------------------------------------------------------------

def test_cache_key_unterscheidet_laeufe_mit_und_ohne_marke():
    from services.analyst_cache import cache_key
    basis = {"sha256": "abc", "prompt_version": "x", "format": "Talking Head", "ziel": "TOFU"}
    ohne = cache_key(dict(basis))
    mit = cache_key(dict(basis, marke_hash="deadbeef"))
    assert ohne != mit


def test_altlauf_ohne_marke_behaelt_seinen_key():
    """Sonst faellt der gesamte gespeicherte Cache auf einen Schlag aus."""
    from services.analyst_cache import cache_key
    alt = {"sha256": "abc", "prompt_version": "x", "format": "Talking Head"}
    assert cache_key(alt) == cache_key(dict(alt, marke_hash=""))


# --- Zielgruppen-Abgleich (Spec 3.2) ------------------------------------------------------------

def test_zielgruppen_abgleich_ist_ein_enum():
    from models.analyst import ZIELGRUPPEN_ABGLEICH, AnalystEvaluationV2
    assert ZIELGRUPPEN_ABGLEICH == ("trifft_kern", "teilweise", "breiteres_publikum", "andere_zielgruppe")
    e = AnalystEvaluationV2()
    assert e.zielgruppen_abgleich == "", "leer, solange keine Marken-Datei vorliegt"


def test_zielgruppen_abgleich_verwirft_erfundene_werte():
    """Gleiches Muster wie BlickEval: Ein Modellwert ausserhalb der Liste wird still verworfen,
    statt ins Frontend durchzuschlagen."""
    from models.analyst import AnalystEvaluationV2
    assert AnalystEvaluationV2(zielgruppen_abgleich="passt sehr gut").zielgruppen_abgleich == ""
    assert AnalystEvaluationV2(zielgruppen_abgleich="teilweise").zielgruppen_abgleich == "teilweise"


# --- Code als Notnagel: ohne Marken-Datei gibt es keine markenabhaengigen Urteile ---------------

def _result(marke: str = "", ziel: str = "TOFU"):
    from models.analyst import AnalystResult
    return AnalystResult(id="t", filename="t.mp4", duration_sec=10.0, scene_count=0, scenes=[],
                         gewaehltes_ziel=ziel, marke_datei="marke.md" if marke else "")


def test_ohne_marke_werden_markenabhaengige_felder_geleert():
    """Der Prompt verlangt es schon ('liegen dir keine Daten vor: leer'). Die P2-Lektion aus
    docs/offene-fixes-analyst.md sagt: Eine Prompt-Pflicht ohne Durchsetzung im Code wird
    Textbaustein. Also setzt der Code sie durch."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import erzwinge_marken_abhaengige_felder

    e = AnalystEvaluationV2(
        zielgruppen_abgleich="trifft_kern",
        zielgruppen_relevanz="Trifft die Zielgruppe sehr gut.",
        protagonist_auftreten={"score": 4, "beschreibung": "Ruhig und sachlich.",
                               "probleme": ["wirkt zu flach"]},
    )
    e = erzwinge_marken_abhaengige_felder(e, _result(marke=""))
    assert e.zielgruppen_abgleich == ""
    assert e.zielgruppen_relevanz == ""
    assert e.protagonist_auftreten.score is None
    assert e.protagonist_auftreten.probleme == []
    assert e.protagonist_auftreten.beschreibung == "Ruhig und sachlich.", "die Beschreibung bleibt"


def test_mit_marke_bleiben_die_felder_stehen():
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import erzwinge_marken_abhaengige_felder

    e = AnalystEvaluationV2(
        zielgruppen_abgleich="teilweise",
        zielgruppen_relevanz="Spricht eher Fortgeschrittene an.",
        protagonist_auftreten={"score": 4, "beschreibung": "Ruhig.", "probleme": ["zu flach"]},
    )
    e = erzwinge_marken_abhaengige_felder(e, _result(marke="x"))
    assert e.zielgruppen_abgleich == "teilweise"
    assert e.protagonist_auftreten.score == 4
    assert e.protagonist_auftreten.probleme == ["zu flach"]


def test_v2_laeufe_bleiben_unberuehrt():
    """Ohne Ziel ist es ein V2-Lauf; der kennt die Felder gar nicht und darf sich nicht aendern."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import erzwinge_marken_abhaengige_felder

    e = AnalystEvaluationV2(protagonist_auftreten={"score": 4, "beschreibung": "x"})
    e = erzwinge_marken_abhaengige_felder(e, _result(marke="", ziel=""))
    assert e.protagonist_auftreten.score == 4


# --- Endpunkt ----------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api.analyst as api_analyst
    from services import analyst_engine
    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(api_analyst, "ANALYST_PATH", tmp_path)
    from main import app
    return TestClient(app), tmp_path


def _lauf(tmp_path, run_id="abc12345"):
    import json
    d = tmp_path / run_id
    (d / "raw").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": run_id, "filename": "v.mp4", "sha256": "s"}))
    (d / "status.json").write_text(json.dumps({"phase": "uploaded", "message": ""}))
    return d


def test_upload_schreibt_text_und_hash_in_meta(client):
    import json
    c, tmp = client
    d = _lauf(tmp)
    r = c.post("/api/analyst/abc12345/marke",
               files=[("files", ("profil.md", b"Zielgruppe: Coaches im DACH-Raum.", "text/markdown"))])
    assert r.status_code == 200, r.text
    # >= statt ==: Seit mehrere Dateien moeglich sind, bekommt jede eine Ueberschrift mit
    # ihrem Namen, und die zaehlt mit. Die Zahl dient der Kuerzungs-Schwelle, nicht der Statistik.
    assert r.json()["woerter"] >= 4
    meta = json.loads((d / "meta.json").read_text())
    assert meta["marke_datei"] == "profil.md"
    assert "Coaches" in meta["marke_text"]
    assert meta["marke_hash"]


def test_upload_lehnt_unbekannten_typ_mit_klartext_ab(client):
    c, tmp = client
    _lauf(tmp)
    r = c.post("/api/analyst/abc12345/marke",
               files=[("files", ("marke.pages", b"x", "application/octet-stream"))])
    assert r.status_code == 422
    assert ".md" in r.json()["detail"], "die Fehlermeldung nennt, was stattdessen geht"
    assert "marke.pages" in r.json()["detail"], "und WELCHE Datei nicht geht"


# --- Frontend ----------------------------------------------------------------------------------

def _appjsx():
    import pathlib
    return pathlib.Path("static/app.jsx").read_text(encoding="utf-8")


def test_frontend_kennt_jeden_wert_des_abgleichs():
    """Drift-Schutz: Kommt im Backend ein Enum-Wert dazu, faellt er im Frontend sonst still unter
    den Tisch — `ABGLEICH_HINWEIS[unbekannt]` ist undefined und rendert nichts."""
    import re
    from models.analyst import ZIELGRUPPEN_ABGLEICH
    block = re.search(r"const ABGLEICH_HINWEIS = \{.*?\n\};", _appjsx(), re.S).group()
    for wert in ZIELGRUPPEN_ABGLEICH:
        if wert == "trifft_kern":
            assert wert not in block, "Treffer ist der Normalfall und braucht keinen Hinweis"
        else:
            assert wert in block, wert


def test_marken_upload_laeuft_vor_dem_start():
    """Reihenfolge ist hier keine Kosmetik: /start liest meta.json und baut daraus den Cache-Key.
    Nach dem Start hochgeladen waere die Datei fuer diesen Lauf wirkungslos."""
    quelle = _appjsx()
    upload = quelle.index("/marke`")
    start = quelle.index("/start?${qs}`")
    assert upload < start


def test_marken_feld_haengt_an_v3():
    """Die aelteren Engines kennen das Feld nicht — ein Upload dort waere ohne Wirkung und wuerde
    genau die Erwartung wecken, die der Lauf nicht einloest."""
    import re
    quelle = _appjsx()
    stelle = quelle.index("Zielgruppe &amp; Strategie (optional)")
    davor = quelle[max(0, stelle - 900):stelle]
    assert re.search(r'engine === "v3" && \(', davor)


# --- Frontend-Feinschliff (Feedback Chris, 2026-09-13) ------------------------------------------

def test_der_reiter_ausfuehrliches_feedback_ist_weg():
    """"der reiter: Ausfuehrliches Feedback, kann weg." `top_tipps` wird weiterhin erzeugt, aber
    nicht mehr angezeigt — die drei Handlungsschritte sagen dasselbe konkreter."""
    quelle = _appjsx()
    assert "Ausführliches Feedback" not in quelle
    assert "ev.top_tipps" not in quelle


def test_die_score_kachel_sagt_dass_man_sie_oeffnen_kann():
    """"Eine Person, die nicht weiss, dass sie draufklicken kann, wird es vielleicht nicht
    verstehen." Der Hinweis steht IN der Kachel, nicht als Satz darueber — sonst liest ihn nur,
    wer ohnehin schon liest."""
    import re
    quelle = _appjsx()
    block = re.search(r"function ScoreChip\(.*?\n\}\n", quelle, re.S).group()
    assert 'className="sc-mehr"' in block
    css = __import__("pathlib").Path("static/styles.css").read_text(encoding="utf-8")
    assert ".sc-mehr{" in css
    assert '.sc-klapp[open]>summary .sc-mehr{display:none;}' in css, "offen braucht es ihn nicht"


def test_die_zielgruppe_bekommt_ein_ziel_emoji():
    assert "🎯" in _appjsx()


def test_die_neuen_felder_nutzen_das_design_system():
    """Ziel-Dropdown, Texthook-Feld und Marken-Upload benutzen jetzt .select/.input/.dropzone
    statt eigener Inline-Styles — sonst sieht jedes neue Feld anders aus als der Rest."""
    quelle = _appjsx()
    assert 'className="select"\n                value={ziel}' in quelle
    assert 'className="input"\n              type="text"\n              value={plannedTextHook}' in quelle
    assert 'className="dropzone dropzone-klein"' in quelle
    assert 'type="file"' in quelle and "hidden" in quelle


def test_chip_und_feedback_teilen_sich_eine_rasterzelle():
    """Screenshot Chris, 2026-09-13: In der Admin-Ansicht lief der Label-Text unter den
    Bewertungspunkten durch. Ursache war nicht die Schrift, sondern das Raster — Chip und
    Feedback-Block waren Geschwister in `.sc-grid` und belegten damit ZWEI Zellen. Jede Kachel
    stand auf halber Breite."""
    quelle = _appjsx()
    stelle = quelle.index("kategorieChips(ev, k.key")
    block = quelle[stelle:stelle + 400]
    assert 'className="sc-zelle"' in block
    assert "React.Fragment" not in block


def test_label_und_bewertung_stehen_untereinander():
    """Fuenf Elemente in einer Zeile passen in eine 228px-Kachel nicht. Label oben, Punkte und
    Wert darunter — dann darf das Label so lang sein, wie es sein muss."""
    css = __import__("pathlib").Path("static/styles.css").read_text(encoding="utf-8")
    assert ".sc-top{display:flex;flex-direction:column" in css
    # Der Ueberlauf selbst: ohne Umbruchregel stand ein zu langes Label ueber seinem Kasten hinaus.
    assert "overflow-wrap:anywhere" in css.split(".sc-label{")[1][:200]
