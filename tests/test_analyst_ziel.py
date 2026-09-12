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


def _eval_mit_scores(sprech=3, text=3, visuell=3, spannung=3, struktur=3,
                     sprechq=3, aesthetik=3, schnitt=3):
    from models.analyst import (AnalystEvaluationV2, HookEval, StrukturEval,
                                ScoreProbleme, ScoreKommentar)
    return AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=sprech, text_hook_score=text, visuell_hook_score=visuell),
        struktur=StrukturEval(score=struktur),
        sprechqualitaet=ScoreProbleme(score=sprechq),
        visuelle_aesthetik=ScoreProbleme(score=aesthetik),
        spannungsbogen=ScoreKommentar(score=spannung),
        schnitt_pacing=ScoreKommentar(score=schnitt),
    )


def test_ohne_ziel_rechnet_die_alten_gewichte():
    from services.analyst_eval import berechne_performance_score
    a = berechne_performance_score(_eval_mit_scores(), ziel="").performance_score
    b = berechne_performance_score(_eval_mit_scores(), ziel=None).performance_score
    assert a == b
    assert 0 < a < 100


def test_schwacher_hook_kostet_bei_tofu_mehr_als_bei_mofu():
    from services.analyst_eval import berechne_performance_score
    tofu = berechne_performance_score(
        _eval_mit_scores(sprech=1, text=1, visuell=1), ziel="TOFU").performance_score
    mofu = berechne_performance_score(
        _eval_mit_scores(sprech=1, text=1, visuell=1), ziel="MOFU").performance_score
    assert tofu < mofu


def test_schwacher_spannungsbogen_kostet_bei_mofu_mehr_als_bei_tofu():
    from services.analyst_eval import berechne_performance_score
    tofu = berechne_performance_score(_eval_mit_scores(spannung=1), ziel="TOFU").performance_score
    mofu = berechne_performance_score(_eval_mit_scores(spannung=1), ziel="MOFU").performance_score
    assert mofu < tofu


def test_unbekanntes_ziel_faellt_auf_die_alten_gewichte_zurueck():
    from services.analyst_eval import berechne_performance_score
    a = berechne_performance_score(_eval_mit_scores(), ziel="QUATSCH").performance_score
    b = berechne_performance_score(_eval_mit_scores(), ziel="").performance_score
    assert a == b


def test_schwere_waechst_mit_gewicht_und_faellt_mit_score():
    from services.analyst_eval import schwere_der_dimension
    # MOFU: spannungsbogen 15, schnitt_pacing 6
    assert schwere_der_dimension("spannungsbogen", 1, "MOFU") > schwere_der_dimension("schnitt_pacing", 1, "MOFU")
    assert schwere_der_dimension("spannungsbogen", 5, "MOFU") == 0.0
    assert schwere_der_dimension("spannungsbogen", 1, "MOFU") == 15.0


def test_schwere_ohne_dimension_ist_null():
    from services.analyst_eval import schwere_der_dimension
    assert schwere_der_dimension("", 3, "MOFU") == 0.0
    assert schwere_der_dimension("gibtsnicht", 3, "MOFU") == 0.0


def test_schwere_ohne_score_ist_null():
    from services.analyst_eval import schwere_der_dimension
    assert schwere_der_dimension("spannungsbogen", None, "MOFU") == 0.0


def test_schwacher_score_in_schwerer_dimension_ist_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(spannung=2)
    assert "spannungsbogen" in kritische_dimensionen(ev, "MOFU")      # Gewicht 15
    assert "spannungsbogen" not in kritische_dimensionen(ev, "TOFU")  # Gewicht 7


def test_schwacher_hook_ist_immer_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(visuell=2)
    assert "visuell_hook" in kritische_dimensionen(ev, "BOFU")   # Gewicht 6, trotzdem kritisch


def test_fehlende_texthook_ist_kritisch():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(text=0)
    ev.hook.text_hook_vorhanden = False
    assert "text_hook" in kritische_dimensionen(ev, "TOFU")


def test_gute_scores_haben_keinen_kritischen_mangel():
    from services.analyst_eval import kritische_dimensionen
    ev = _eval_mit_scores(sprech=4, text=4, visuell=4, spannung=4,
                          struktur=4, sprechq=4, aesthetik=4, schnitt=4)
    assert kritische_dimensionen(ev, "MOFU") == set()


def _mit_empfehlungen(*eintraege, **scores):
    """eintraege: (zeitpunkt_sek, anweisung, betrifft, gruppe)"""
    from models.analyst import Empfehlung
    ev = _eval_mit_scores(**scores)
    ev.empfehlungen = [
        Empfehlung(zeitpunkt_sek=t, anweisung=a, betrifft=b, gruppe=g)
        for t, a, b, g in eintraege
    ]
    return ev


def test_ohne_ziel_bleibt_die_sortierung_nach_zeitpunkt():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (2.0, "frueh und harmlos", "schnitt_pacing", ""),
        (20.0, "spaet und schwer", "spannungsbogen", ""),
        spannung=1, schnitt=4,
    )
    out = verteile_empfehlungen(ev, ziel="")
    assert out.action_steps[0].anweisung == "frueh und harmlos"


def test_mit_ziel_steht_der_schwerere_mangel_oben():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (2.0, "frueh und harmlos", "schnitt_pacing", ""),
        (20.0, "spaet und schwer", "spannungsbogen", ""),
        spannung=1, schnitt=4,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "spaet und schwer"


def test_kritischer_mangel_steht_vor_allem_anderen():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (1.0, "a", "schnitt_pacing", ""),
        (2.0, "b", "sprechqualitaet", ""),
        (3.0, "c", "struktur", ""),
        (4.0, "kritisch", "spannungsbogen", ""),
        spannung=1, schnitt=4, sprechq=4, struktur=4,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "kritisch"


def test_bei_gleicher_schwere_gewinnt_der_fruehere_zeitpunkt():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (20.0, "spaet", "spannungsbogen", ""),
        (5.0, "frueh", "spannungsbogen", ""),
        spannung=2,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert out.action_steps[0].anweisung == "frueh"


def test_hoher_score_ohne_kritischen_mangel_zeigt_weniger_als_drei_schritte():
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (5.0, "feinschliff a", "schnitt_pacing", ""),
        (9.0, "feinschliff b", "struktur", ""),
        (12.0, "feinschliff c", "sprechqualitaet", ""),
        sprech=5, text=5, visuell=5, spannung=5, struktur=5, sprechq=5, aesthetik=5, schnitt=5,
    )
    ev.performance_score = 92
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert len(out.action_steps) <= 2
    assert len(out.weitere_empfehlungen) >= 1


def test_staerke_unter_der_schwelle_wird_gestrichen():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=2, text=2, visuell=2, schnitt=5)
    ev.staerken = [Staerke(text="Deine Hook sitzt", betrifft="sprech_hook"),
                   Staerke(text="Sauber geschnitten", betrifft="schnitt_pacing")]
    out = filtere_staerken(ev, ziel="MOFU")
    assert [s.text for s in out.staerken] == ["Sauber geschnitten"]


def test_hoechstens_zwei_staerken_pro_kategorie():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=5, text=5, visuell=5)
    ev.staerken = [Staerke(text=f"lob {i}", betrifft=b) for i, b in enumerate(
        ["sprech_hook", "text_hook", "visuell_hook"])]
    out = filtere_staerken(ev, ziel="MOFU")
    assert len(out.staerken) == 2


def test_staerke_ohne_bezug_wird_gestrichen():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=5)
    ev.staerken = [Staerke(text="irgendwas Nettes", betrifft="")]
    assert filtere_staerken(ev, ziel="MOFU").staerken == []


def test_ohne_ziel_bleiben_staerken_unangetastet():
    from models.analyst import Staerke
    from services.analyst_eval import filtere_staerken
    ev = _eval_mit_scores(sprech=1)
    ev.staerken = [Staerke(text="Altlauf-Lob", betrifft="")]
    assert len(filtere_staerken(ev, ziel="").staerken) == 1


def test_altlauf_mit_staerken_als_strings_laedt_weiter():
    """~93 gespeicherte Laeufe haben staerken als Liste von Strings. Bricht das Laden,
    ist jedes gespeicherte Ergebnis unlesbar."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{"staerken": ["Guter Schnitt", "Klare Sprache"]})
    assert [s.text for s in ev.staerken] == ["Guter Schnitt", "Klare Sprache"]
    assert all(s.betrifft == "" for s in ev.staerken)


def test_v3_prompt_nennt_das_ziel_als_fakt():
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(ziel="MOFU")
    assert "MOFU" in p
    assert "FAKT" in p


def test_ohne_ziel_entsteht_exakt_der_v2_prompt():
    from services.analyst_eval import build_system_prompt
    assert build_system_prompt() == build_system_prompt(ziel="")


def test_v3_prompt_verlangt_betrifft_bei_staerken():
    """Prueft die staerken-Zeile, nicht nur das Wort „betrifft": Das kommt schon im V2-Prompt vor,
    dort aber fuer die `empfehlungen`. Ein reiner Wort-Test waere auch ohne V3 gruen."""
    from services.analyst_eval import build_system_prompt
    v3 = build_system_prompt(ziel="TOFU")
    v2 = build_system_prompt()
    assert '{"text": "…", "betrifft": "<Dimensionsname>"}' in v3
    assert '{"text": "…", "betrifft": "<Dimensionsname>"}' not in v2
    assert "erfinde kein Lob" in v3


def test_v3_laeuft_ueber_die_zwei_call_bewertung(monkeypatch, tmp_path):
    """v3 muss dieselbe Bauart nutzen wie die produktive v2_split — sonst misst ein
    Vergleich V2/V3 zwei Aenderungen gleichzeitig (Zielsteuerung UND Call-Struktur)."""
    import json
    from services import analyst_engine

    gesehen = {}

    def falsches_run_v2(run_dir, video, meta, mode, split=False):
        gesehen["mode"] = mode
        gesehen["split"] = split
        raise RuntimeError("Abbruch nach der Weichenstellung")

    monkeypatch.setattr(analyst_engine, "_run_v2", falsches_run_v2)
    monkeypatch.setattr(analyst_engine, "_find_video", lambda d: tmp_path / "clip.mp4")
    d = tmp_path / "lauf"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"engine": "v3", "ziel": "MOFU"}))
    (d / "status.json").write_text(json.dumps({"phase": "uploaded"}))
    try:
        analyst_engine._run("lauf", d)
    except RuntimeError:
        pass
    assert gesehen == {"mode": "hybrid", "split": True}


def test_split_prompt_kennt_das_ziel():
    from services.analyst_eval import build_system_prompt
    for teil in ("eroeffnung", "handwerk"):
        p = build_system_prompt(teil=teil, ziel="BOFU")
        assert "BOFU" in p, teil
        assert "Videoziel" in p, teil
