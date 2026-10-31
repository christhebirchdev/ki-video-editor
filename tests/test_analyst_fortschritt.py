"""Gamification (Spec V4, Abschnitt 5, Stand 2026-11-01).

Kategorien: kein Level, sondern der Schnitt-Score (1–5) der letzten 5 Videos.
Gesamtlevel: steigt durch Nutzung (Anzahl Analysen) ODER durch Koennen (Gesamtschnitt) — der
hoehere Weg zaehlt, und ein erreichtes Level sinkt nie.
"""
import json

from services import analyst_fortschritt as af


def lauf(t, wert=3, neu=False, cache=False, **einzeln):
    scores = {d: wert for dims in af.KATEGORIEN.values() for d in dims}
    scores.update(einzeln)
    return {"created_at": f"2026-10-{t:02d}", "scores": scores, "neubewertung": neu, "aus_cache": cache}


def kat(erg, key):
    return next(k for k in erg["kategorien"] if k["key"] == key)


def test_ohne_laeufe():
    e = af.berechne_aus([])
    assert e["gesamt"]["level"] == 0 and kat(e, "hook")["schnitt"] is None


def test_kategorie_ist_schnitt_der_letzten_fuenf():
    laeufe = [lauf(1, 1.0)] + [lauf(i, 4.0) for i in range(2, 7)]   # der 1er faellt raus
    h = kat(af.berechne_aus(laeufe), "hook")
    assert h["schnitt"] == 4.0 and h["position"] == 80.0
    assert "level" not in h


def test_trend_vergleicht_mit_den_fuenf_davor():
    e = af.berechne_aus([lauf(i, 4.0) for i in range(1, 6)] + [lauf(i, 2.0) for i in range(6, 11)])
    assert kat(e, "hook")["trend"] == "runter"


def test_gesamtlevel_durch_nutzung():
    # schwache Scores, aber viele Analysen: Level kommt allein aus der Anzahl
    e = af.berechne_aus([lauf(i, 2.0) for i in range(1, 13)])
    assert e["gesamt"]["level"] == 5          # 12 Analysen


def test_gesamtlevel_durch_koennen():
    e = af.berechne_aus([lauf(i, 4.2) for i in range(1, 4)])
    assert e["gesamt"]["level"] == 8          # 3 Analysen, Gesamtschnitt 4,2


def test_koennen_erst_ab_drei_analysen():
    assert af.berechne_aus([lauf(1, 5.0)])["gesamt"]["level"] == 1


def test_neubewertung_zaehlt_fuer_level_drei():
    e = af.berechne_aus([lauf(1, 2.0), lauf(2, 2.0, neu=True)])
    assert e["gesamt"]["level"] == 3


def test_level_sinkt_nie():
    gut = [lauf(i, 4.0) for i in range(1, 4)]
    e = af.berechne_aus(gut + [lauf(i, 1.5) for i in range(4, 9)])
    assert e["gesamt"]["level"] == af.berechne_aus(gut)["gesamt"]["level"] == 7


def test_cache_laeufe_zaehlen_nicht():
    e = af.berechne_aus([lauf(1, 3), lauf(2, 3, cache=True)])
    assert e["analysen"] == 1 and e["gesamt"]["level"] == 1


def test_naechstes_nennt_beide_wege():
    n = af.berechne_aus([lauf(i, 3.0) for i in range(1, 6)])["gesamt"]["naechstes"]
    assert n == {"level": 5, "analysen": 12, "schnitt": 3.3}


def test_position_dicht_am_anfang_und_monoton():
    p = af.POSITION
    assert all(p[i] < p[i + 1] for i in range(10))
    assert p[5] - p[0] > p[10] - p[8]
    e = af.berechne_aus([lauf(1, 2), lauf(2, 2)])     # 2 von 3 Analysen auf dem Weg zu Level 2
    assert p[1] < e["gesamt"]["position"] < p[2]


def test_fokus_ist_haeufigste_schwache_dimension():
    laeufe = [lauf(i, 4, sprech_hook=2) for i in range(1, 4)] + [lauf(4, 4, skript=3)]
    assert af.berechne_aus(laeufe)["fokus"]["dimension"] == "sprech_hook"
    assert af.berechne_aus([lauf(1, 5)])["fokus"] is None


def test_berechne_liest_laufverzeichnisse(tmp_path):
    d = tmp_path / "r1"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"created_at": "2026-10-01", "vorgaenger": ""}))
    (d / "analysis.json").write_text(json.dumps({"evaluation": {
        "hook": {"sprech_hook_score": 4}, "performance_score": 70}}))
    (tmp_path / "kaputt").mkdir()
    assert af.berechne([d, tmp_path / "kaputt"])["analysen"] == 1
