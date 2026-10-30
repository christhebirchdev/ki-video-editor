"""Gamification (Spec V4, Abschnitt 5): Level je Kategorie aus den Dimensions-Scores."""
import json

from services import analyst_fortschritt as af


def lauf(t, wert=3, neu=False, cache=False, **einzeln):
    """Ein gezaehlter Lauf; alle Dimensionen auf `wert`, einzelne ueberschreibbar."""
    scores = {d: wert for dims in af.KATEGORIEN.values() for d in dims}
    scores.update(einzeln)
    return {"created_at": f"2026-10-{t:02d}", "scores": scores, "neubewertung": neu, "aus_cache": cache}


def kat(erg, key):
    return next(k for k in erg["kategorien"] if k["key"] == key)


def test_ohne_laeufe_level_null():
    e = af.berechne_aus([])
    assert e["gesamt"]["level"] == 0 and kat(e, "hook")["level"] == 0


def test_erste_analyse_level_eins():
    assert af.berechne_aus([lauf(1, wert=5)])["gesamt"]["level"] == 1


def test_drei_analysen_schwach_level_zwei():
    e = af.berechne_aus([lauf(i, wert=2.5) for i in (1, 2, 3)])
    assert kat(e, "hook")["level"] == 2


def test_neubewertung_und_schnitt_3_4_ergibt_level_5():
    e = af.berechne_aus([lauf(1, 3.4), lauf(2, 3.4), lauf(3, 3.4, neu=True)])
    assert kat(e, "editing")["level"] == 5


def test_fuenf_analysen_ersetzen_neubewertung():
    e = af.berechne_aus([lauf(i, 3.0) for i in range(1, 6)])
    assert kat(e, "hook")["level"] == 4


def test_level_sinkt_nicht():
    gut = [lauf(i, 4.0) for i in range(1, 6)]
    e1 = af.berechne_aus(gut)
    e2 = af.berechne_aus(gut + [lauf(i, 2.0) for i in range(6, 11)])
    assert kat(e2, "hook")["level"] == kat(e1, "hook")["level"] == 7
    assert kat(e2, "hook")["trend"] == "runter"


def test_cache_laeufe_zaehlen_nicht():
    e = af.berechne_aus([lauf(1, 3), lauf(2, 3, cache=True), lauf(3, 3, cache=True)])
    assert e["analysen"] == 1 and e["gesamt"]["level"] == 1


def test_fokus_ist_haeufigste_schwache_dimension():
    laeufe = [lauf(i, 4, sprech_hook=2) for i in range(1, 4)] + [lauf(4, 4, skript=3)]
    assert af.berechne_aus(laeufe)["fokus"]["dimension"] == "sprech_hook"
    assert af.berechne_aus([lauf(1, 5)])["fokus"] is None


def test_gesamtlevel_ist_abgerundeter_mittelwert():
    laeufe = [lauf(i, 3.0, sprech_hook=4.5, text_hook=4.5, visuell_hook=4.5) for i in range(1, 6)]
    e = af.berechne_aus(laeufe)
    levels = [k["level"] for k in e["kategorien"]]
    assert e["gesamt"]["level"] == sum(levels) // 4


def test_position_monoton_und_dicht_am_anfang():
    p = af.POSITION
    assert all(p[i] < p[i + 1] for i in range(10))
    assert p[5] - p[0] > p[10] - p[8]          # erstes Stueck: mehr Stufen auf weniger Strecke
    e = af.berechne_aus([lauf(1, 3), lauf(2, 3)])
    h = kat(e, "hook")
    assert p[1] <= h["position"] < p[2]        # 2 von 3 Analysen zum Level 2


def test_naechstes_ziel_beschreibt_bedingung():
    assert kat(af.berechne_aus([lauf(1, 3)]), "hook")["naechstes"] == {"art": "analysen", "wert": 3}
    e = af.berechne_aus([lauf(i, 3.0) for i in range(1, 6)])
    assert kat(e, "hook")["naechstes"] == {"art": "schnitt", "wert": 3.3}


def test_berechne_liest_laufverzeichnisse(tmp_path):
    d = tmp_path / "r1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"created_at": "2026-10-01", "vorgaenger": ""}))
    (d / "analysis.json").write_text(json.dumps({"evaluation": {
        "hook": {"sprech_hook_score": 4}, "performance_score": 70}}))
    (tmp_path / "kaputt").mkdir()
    e = af.berechne([d, tmp_path / "kaputt"])
    assert e["analysen"] == 1
