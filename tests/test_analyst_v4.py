"""V4-Bewertung: Aufwand je Empfehlung, Effekt-Optionen, Vorgaenger-Abgleich, V4-Prompt.

Laeuft ohne API-Keys: getestet werden Modelle, Prompt-Bau und Nachbearbeitung.
"""
from models.analyst import (ActionStep, AnalystEvaluationV2, AnalystResult, EffektVorschlag,
                            Empfehlung)


def test_v4_felder_haben_defaults_und_altlaeufe_parsen():
    assert Empfehlung().aufwand == ""
    assert ActionStep().aufwand == ""
    assert EffektVorschlag().optionen == []
    ev = AnalystEvaluationV2()
    assert ev.naechstes_video == [] and ev.vorgaenger_abgleich == []
    r = AnalystResult(id="x", filename="a.mp4", duration_sec=1, scene_count=0, scenes=[])
    assert r.vorgaenger_id == "" and r.vorgaenger_empfehlungen == []


def test_optionen_tolerieren_einzelstring_und_objekte():
    assert EffektVorschlag(optionen="Ding").optionen == ["Ding"]
    assert EffektVorschlag(optionen=[{"text": "Plopp"}]).optionen == ["Plopp"]


# ---------- V4-Prompt ----------
from services import analyst_eval as ae


def test_v3_prompt_bleibt_ohne_v4_flag_gleich():
    for teil in (None, "eroeffnung", "handwerk"):
        assert ae.build_system_prompt(teil=teil, ziel="MOFU") == \
            ae.build_system_prompt(teil=teil, ziel="MOFU", v4=False)


def test_v4_skill_ohne_woosh_beispiele():
    p = ae.build_system_prompt(teil="handwerk", ziel="MOFU", v4=True)
    assert "Woosh-Ton" not in p
    assert "Soundeffekt (Whoosh, Klick)" not in p
    v3 = ae.build_system_prompt(teil="handwerk", ziel="MOFU")
    assert "Woosh-Ton" in v3   # V3 bleibt eingefroren


def test_v4_vertrag_hat_aufwand_optionen_und_vorgaenger():
    hw = ae.build_system_prompt(teil="handwerk", ziel="MOFU", v4=True)
    assert '"aufwand"' in hw and '"optionen"' in hw and '"vorgaenger_abgleich"' in hw
    er = ae.build_system_prompt(teil="eroeffnung", ziel="MOFU", v4=True)
    assert '"aufwand"' in er                     # Hook-Empfehlungen brauchen ihn auch
    assert '"vorgaenger_abgleich"' not in er     # nur der Handwerk-Call gleicht ab


def test_v4_nachsatz_steht_im_prompt():
    p = ae.build_system_prompt(teil="handwerk", ziel="MOFU", v4=True)
    assert "AUFWAND JE EMPFEHLUNG" in p and "EFFEKTE PASSEND ZUM ZWECK" in p


def test_v4_anker_sind_eindeutig():
    body = ae.load_skill_body(ae.SKILL_PATH_V3)
    for anker, _ in ae.V4_SKILL_ERSETZUNGEN:
        assert body.count(anker) == 1, anker[:50]
    for anker, _ in ae.V4_VERTRAG_ERSETZUNGEN:
        assert ae.v3_schema().count(anker) == 1, anker[:50]


# ---------- Aufwand und V4-Verteilung ----------
from models.analyst import AnalystEvaluationV2 as Ev


def _ev(*empf, score=60):
    return Ev(performance_score=score, empfehlungen=[Empfehlung(**e) for e in empf])


def test_setze_aufwand_tabelle_fuer_erzwungene_und_modellwert_bleibt():
    ev = _ev(
        {"anweisung": "Sprich den ersten Satz neu", "gruppe": "sprechhook", "erzwungen": True,
         "aufwand": "schnitt"},
        {"anweisung": "Schau in die Linse", "gruppe": "blick", "erzwungen": True},
        {"anweisung": "Untertitel rein", "gruppe": "untertitel", "erzwungen": True},
        {"anweisung": "Licht von vorne", "betrifft": "visuelle_aesthetik", "aufwand": "dreh"},
        {"anweisung": "Kürze Sek. 10", "aufwand": "SCHNITT "},
        {"anweisung": "Was Unklares", "betrifft": "skript", "aufwand": "irgendwas"},
    )
    ev = ae.setze_aufwand(ev)
    assert [e.aufwand for e in ev.empfehlungen] == ["dreh", "dreh", "schnitt", "dreh", "schnitt", "dreh"]


def test_v4_verteilung_trennt_dreh_von_schnitt():
    ev = _ev(
        {"zeitpunkt_sek": 0, "anweisung": "Neuer erster Satz", "betrifft": "sprech_hook", "aufwand": "dreh"},
        {"zeitpunkt_sek": 5, "anweisung": "Texthook drüberlegen", "betrifft": "text_hook", "aufwand": "schnitt"},
        {"zeitpunkt_sek": 9, "anweisung": "Licht von vorne", "betrifft": "visuelle_aesthetik", "aufwand": "dreh"},
        {"zeitpunkt_sek": 12, "anweisung": "Pause raus", "aufwand": "schnitt"},
        {"zeitpunkt_sek": 20, "anweisung": "Lauter sprechen", "betrifft": "sprechqualitaet", "aufwand": "dreh"},
        {"zeitpunkt_sek": 25, "anweisung": "Andere Kleidung", "betrifft": "protagonist_auftreten", "aufwand": "dreh"},
    )
    ev = ae.verteile_empfehlungen(ev, ziel="MOFU", v4=True)
    assert {s.aufwand for s in ev.action_steps} == {"schnitt"}
    assert [s.anweisung for s in ev.action_steps] == ["Texthook drüberlegen", "Pause raus"]
    assert len(ev.naechstes_video) == 3 and {s.aufwand for s in ev.naechstes_video} == {"dreh"}
    assert ev.naechstes_video[0].anweisung == "Neuer erster Satz"   # Hook behält Vorrang
    alle = ev.action_steps + ev.naechstes_video + ev.weitere_empfehlungen
    assert len(alle) == 6


def test_v3_verteilung_unveraendert():
    ev = _ev(
        {"zeitpunkt_sek": 0, "anweisung": "Neuer erster Satz", "betrifft": "sprech_hook", "aufwand": "dreh"},
        {"zeitpunkt_sek": 5, "anweisung": "Texthook drüberlegen", "betrifft": "text_hook"},
    )
    ev = ae.verteile_empfehlungen(ev, ziel="MOFU")
    assert ev.naechstes_video == [] and len(ev.action_steps) == 2


# ---------- Effekte ----------
from models.analyst import DynamikEval


def _effekt_ev(optionen_a, optionen_b=None):
    return Ev(dynamik=DynamikEval(urteil="gering"), effekt_vorschlaege=[
        EffektVorschlag(zeitpunkt_sek=12, art="sound", zweck="Pointe", optionen=optionen_a),
        EffektVorschlag(zeitpunkt_sek=20, art="sound", zweck="Zahl", optionen=optionen_b or []),
    ])


def test_v4_effekt_text_aus_optionen():
    ev = ae.baue_effekt_schritt(_effekt_ev(["ein kurzes Ding", "ein Plopp"], ["ein Klick"]), v4=True)
    text = ev.empfehlungen[-1].anweisung
    assert "ein kurzes Ding oder ein Plopp" in text and "ein Klick" in text
    assert "beides passt" in text.lower()
    assert "whoosh" not in text.lower()


def test_v4_effekt_ohne_optionen_neutral():
    ev = ae.baue_effekt_schritt(_effekt_ev([]), v4=True)
    text = ev.empfehlungen[-1].anweisung
    assert "whoosh" not in text.lower() and "Sek. 12" in text


def test_v3_effekt_text_unveraendert():
    ev = ae.baue_effekt_schritt(_effekt_ev(["ein Ding"]))
    assert "ein Whoosh" in ev.empfehlungen[-1].anweisung


# ---------- Vorgaenger ----------
from models.analyst import VorgaengerPunkt


def test_vorgaenger_kontext_nummeriert_und_erklaert_status():
    k = ae.vorgaenger_kontext(["Schneide die Pause raus", "Sprich lauter"])
    assert "VORHERIGE ANALYSE" in k and "1. Schneide die Pause raus" in k and "2. Sprich lauter" in k
    assert "umgesetzt" in k and "teilweise" in k and "offen" in k
    assert ae.vorgaenger_kontext([]) == ""


def test_vorgaenger_abgleich_nur_mit_vorgaenger_und_gueltigem_status():
    r = AnalystResult(id="x", filename="a.mp4", duration_sec=1, scene_count=0, scenes=[], engine="v4")
    ev = Ev(vorgaenger_abgleich=[VorgaengerPunkt(anweisung="A", status="Umgesetzt"),
                                 VorgaengerPunkt(anweisung="B", status="vielleicht"),
                                 VorgaengerPunkt(anweisung="", status="offen")])
    assert ae.pruefe_vorgaenger_abgleich(ev.model_copy(deep=True), r).vorgaenger_abgleich == []
    r.vorgaenger_id = "alt1"
    out = ae.pruefe_vorgaenger_abgleich(ev, r).vorgaenger_abgleich
    assert [(p.anweisung, p.status) for p in out] == [("A", "umgesetzt")]


def test_cache_key_unterscheidet_vorgaenger():
    from services.analyst_cache import cache_key
    m = {"sha256": "abc", "prompt_version": "p", "engine": "v4"}
    assert cache_key(m) != cache_key({**m, "vorgaenger": "alt1"})


def test_lade_vorgaenger_empfehlungen(tmp_path):
    import json
    from services.analyst_engine import lade_vorgaenger_empfehlungen
    d = tmp_path / "alt1"
    d.mkdir()
    (d / "analysis.json").write_text(json.dumps({"evaluation": {
        "action_steps": [{"anweisung": "A"}], "naechstes_video": [{"anweisung": "B"}],
        "weitere_empfehlungen": [{"anweisung": "C"}, {"anweisung": ""}]}}))
    assert lade_vorgaenger_empfehlungen(tmp_path, "alt1") == ["A", "B", "C"]
    assert lade_vorgaenger_empfehlungen(tmp_path, "fehlt") == []
