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
