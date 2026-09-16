"""V3 ist die Standardversion fuer alle Kunden (Vorgabe Chris, 2026-09-16).

Bis heute lief V3 nur in der Admin-Ansicht: `ANALYST_ENGINE` stand auf `v2_split`, und alles, was
an `engine === "v3"` haengt — Ziel-Dropdown, Zielgruppen-/Strategie-Upload, die vier Kategorien,
die Hook-Vorschlaege — war fuer Kunden unsichtbar.
"""
import pathlib


def _appjsx():
    return pathlib.Path("static/app.jsx").read_text(encoding="utf-8")


def test_der_default_ist_v3():
    import re
    quelle = _appjsx()
    treffer = re.search(r'const ANALYST_ENGINE = "([^"]+)"', quelle)
    assert treffer and treffer.group(1) == "v3", "Kunden bekommen sonst weiter V2"


def test_der_admin_kann_weiterhin_auf_v2_umschalten():
    """Der Vergleich V2/V3 ist die Grundlage jeder Aussage darueber, ob V3 besser ist. Faellt der
    Umschalter weg, faellt auch die Vergleichbarkeit weg."""
    quelle = _appjsx()
    block = quelle.split("Bewertungs-Version umschalten")[1][:900]
    assert 'value="v3"' in block
    # Der V2-Wert steht als Konstante da, damit er nicht an zwei Stellen gepflegt werden muss.
    assert "ANALYST_ENGINE_ALT" in block
    assert 'const ANALYST_ENGINE_ALT = "v2_split"' in quelle


def test_ziel_und_zielgruppendateien_sind_fuer_kunden_sichtbar():
    """Beide haengen an `engine === "v3"`. Mit V3 als Default greift das fuer jeden — genau das
    ist der Sinn der Umstellung."""
    quelle = _appjsx()
    assert 'engine === "v3" && (' in quelle
    for feld in ("Ziel des Videos", "Zielgruppe &amp; Strategie (optional)"):
        assert feld in quelle, feld


def test_ohne_ziel_laesst_sich_nicht_starten():
    """Das Ziel ist bei V3 Pflicht (Spec 1.1) — der Server lehnt den Start sonst mit 422 ab.
    Der Knopf muss das vorher abfangen, sonst laeuft der Nutzer in eine Fehlermeldung."""
    quelle = _appjsx()
    assert 'const canStart = !!analysisFile && !!format && (engine !== "v3" || !!ziel)' in quelle


def test_der_hinweis_nennt_was_wirklich_fehlt():
    """Bis heute stand dort immer "Bitte waehle zuerst eine Videodatei aus" — mit V3 als Default
    blockiert aber auch ein fehlendes Ziel den Start, und der Nutzer sucht am falschen Ende."""
    quelle = _appjsx()
    assert "Bitte wähle zuerst eine Videodatei aus." not in quelle
    assert "fehlendeEingaben" in quelle


def test_cache_umgehen_bleibt_admin_only():
    """Vorgabe Chris: im Kundenbereich weg, im Admin-Bereich behalten. Zwei Ergebnisse zum selben
    Video sind genau die Verwirrung, die der Cache verhindern soll."""
    quelle = _appjsx()
    stelle = quelle.index("Cache umgehen")
    davor = quelle[max(0, stelle - 700):stelle]
    assert "{!!adminPw && (" in davor
