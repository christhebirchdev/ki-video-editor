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
