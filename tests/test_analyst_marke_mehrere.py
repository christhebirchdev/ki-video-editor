"""Zielgruppen- und Strategiedatei aus dem Content-Hub (Vorgabe Chris, 2026-09-16).

Aus einer allgemeinen "Marken-Datei" werden ZWEI benannte Dokumente. Beide gehoeren in denselben
Lauf: Die Zielgruppen-Datei sagt, WER angesprochen wird, die Strategiedatei, WORAUF das Konto
hinarbeitet. Nur eine von beiden hochladen zu koennen hiesse, die Haelfte des Kontexts wegzuwerfen.
"""
import io
import json

import pytest
from fastapi.testclient import TestClient

from services import analyst_marke


# --- Mehrere Dateien zu einem Kontext ----------------------------------------------------------

def test_zwei_dateien_werden_zu_einem_text_mit_ueberschriften():
    """Die Ueberschrift je Datei ist kein Schmuck: Ohne sie steht im Prompt ein Block, in dem
    Zielgruppen-Beschreibung und Strategie ineinanderlaufen, und das Modell kann nicht mehr sagen,
    worauf es sich beruft."""
    text, gekuerzt, namen = analyst_marke.extrahiere_mehrere([
        (b"Zielgruppe: Coaches im DACH-Raum.", "zielgruppe.md"),
        (b"Strategie: drei Reels pro Woche, Fokus auf Vertrauen.", "strategie.md"),
    ])
    assert "zielgruppe.md" in text and "strategie.md" in text
    assert "Coaches im DACH-Raum" in text and "drei Reels pro Woche" in text
    assert namen == ["zielgruppe.md", "strategie.md"]
    assert gekuerzt is False


def test_eine_einzelne_datei_geht_weiterhin():
    text, gekuerzt, namen = analyst_marke.extrahiere_mehrere(
        [(b"Zielgruppe: Coaches.", "zielgruppe.md")])
    assert "Coaches" in text and namen == ["zielgruppe.md"]


def test_das_wortlimit_gilt_fuer_die_SUMME():
    """Zwei Dokumente reissen das Limit schneller als eines. Gekuerzt wird am Ende des Ganzen,
    nicht je Datei — sonst faellt aus beiden die Haelfte weg statt aus dem laengeren."""
    text, gekuerzt, _ = analyst_marke.extrahiere_mehrere([
        (("wort " * 2000).encode(), "a.md"),
        (("wort " * 2000).encode(), "b.md"),
    ])
    assert gekuerzt is True
    assert len(text.split()) <= analyst_marke.MAX_WOERTER


def test_eine_kaputte_datei_nennt_ihren_namen():
    """Bei zwei Uploads muss die Fehlermeldung sagen, WELCHE Datei nicht geht."""
    with pytest.raises(ValueError) as e:
        analyst_marke.extrahiere_mehrere([
            (b"Zielgruppe: Coaches.", "zielgruppe.md"),
            (b"x", "strategie.pages"),
        ])
    assert "strategie.pages" in str(e.value)


# --- Endpunkt -----------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import api.analyst as api_analyst
    from services import analyst_engine
    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(api_analyst, "ANALYST_PATH", tmp_path)
    from main import app
    return TestClient(app), tmp_path


def _lauf(tmp_path, run_id="abc12345"):
    d = tmp_path / run_id
    (d / "raw").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": run_id, "filename": "v.mp4", "sha256": "s"}))
    (d / "status.json").write_text(json.dumps({"phase": "uploaded", "message": ""}))
    return d


def test_der_endpunkt_nimmt_zwei_dateien(client):
    c, tmp = client
    d = _lauf(tmp)
    r = c.post("/api/analyst/abc12345/marke", files=[
        ("files", ("zielgruppe.md", b"Zielgruppe: Coaches im DACH-Raum.", "text/markdown")),
        ("files", ("strategie.md", b"Strategie: Vertrauen aufbauen.", "text/markdown")),
    ])
    assert r.status_code == 200, r.text
    assert r.json()["dateien"] == ["zielgruppe.md", "strategie.md"]
    meta = json.loads((d / "meta.json").read_text())
    assert "Coaches" in meta["marke_text"] and "Vertrauen" in meta["marke_text"]
    assert meta["marke_datei"] == "zielgruppe.md, strategie.md"


def test_der_endpunkt_nimmt_weiterhin_eine_datei(client):
    c, tmp = client
    _lauf(tmp)
    r = c.post("/api/analyst/abc12345/marke",
               files=[("files", ("profil.pdf", b"%PDF-1.4 kaputt", "application/pdf"))])
    # Ein kaputtes PDF ist ein 422 mit Klartext, kein Absturz.
    assert r.status_code in (200, 422), r.text


# --- Frontend -----------------------------------------------------------------------------------

def _appjsx():
    import pathlib
    return pathlib.Path("static/app.jsx").read_text(encoding="utf-8")


def test_das_feld_nennt_die_dateien_aus_dem_content_hub():
    quelle = _appjsx()
    assert "Content-Hub" in quelle
    assert "Strategie" in quelle
    assert "Marke / Zielgruppe (optional)" not in quelle, "die alte, unspezifische Beschriftung"


def test_das_feld_erlaubt_mehrere_dateien():
    """PDF steht in `accept` an erster Stelle — es ist das Format, das aus dem Content-Hub kommt.
    Die anderen bleiben erlaubt: Sie funktionieren, und sie wegzunehmen waere ein Rueckschritt
    fuer niemanden."""
    import re
    quelle = _appjsx()
    stelle = quelle.index('accept=".pdf')
    block = quelle[stelle:stelle + 400]
    assert "multiple" in block
    assert re.search(r'accept="\.pdf[^"]*"', quelle)
