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
    # Gegen die Tabelle rechnen, nicht gegen eine feste Zahl: Bei Score 1 (= normalisiert 0)
    # ist die Schwere per Definition das volle Gewicht. Ein hartkodierter Wert bricht bei jeder
    # Umverteilung, ohne dass an der Mechanik etwas falsch waere.
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL
    assert schwere_der_dimension("spannungsbogen", 1, "MOFU") == float(
        SCORE_GEWICHTE_JE_ZIEL["MOFU"]["spannungsbogen"])


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


def test_v3_schema_verlangt_staerken_als_objekt():
    """Sonst widerspricht der Ausgabe-Vertrag dem Skill-Text — und das Modell folgt dem Vertrag."""
    from services.analyst_eval import build_system_prompt
    v3 = build_system_prompt(ziel="MOFU")
    assert '"staerken": [{"text"' in v3
    assert '"staerken": ["<1-3' not in v3


def test_v2_schema_bleibt_bei_strings():
    from services.analyst_eval import build_system_prompt
    v2 = build_system_prompt()
    assert '"staerken": ["<1-3' in v2
    assert '"staerken": [{"text"' not in v2


def test_v3_staerken_vertrag_steht_auch_im_handwerk_teil():
    """staerken gehoert laut TEIL_FELDER zum Handwerk-Call — dort muss der Vertrag stehen."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    assert '"staerken": [{"text"' in p


# --- Videoende: Nachlauf messen statt raten (Lauf dc5c0a3d) ---

def _result_mit(duration=25.0, sprechbeginn=0.0, sprech_dauer=24.7, ziel="MOFU",
                lufs=-14.0, true_peak=-2.0):
    from models.analyst import AnalystResult, SpeechStats, QualityMetrics
    return AnalystResult(
        id="x", filename="c.mp4", duration_sec=duration, scene_count=0, scenes=[],
        gewaehltes_ziel=ziel,
        speech_stats=SpeechStats(wort_anzahl=80, sprech_dauer_sec=sprech_dauer, wpm=180,
                                 filler_count=0, filler_words=[], pausen_count=0,
                                 laengste_pause_sec=0.0, sprechbeginn_sec=sprechbeginn),
        quality_metrics=QualityMetrics(lufs_integrated=lufs, true_peak_db=true_peak),
    )


def test_kurzer_nachlauf_verlangt_puffer():
    """Lauf dc5c0a3d: 0,24s Nachlauf, das Modell empfahl trotzdem zu kuerzen."""
    from services.analyst_eval import baue_videoende_schritt
    ev = _eval_mit_scores()
    out = baue_videoende_schritt(ev, _result_mit(duration=24.92, sprech_dauer=24.68))
    schritte = [e for e in out.empfehlungen if e.gruppe == "videoende"]
    assert len(schritte) == 1
    assert "Puffer" in schritte[0].anweisung or "puffer" in schritte[0].anweisung


def test_nachlauf_im_korridor_erzeugt_keinen_schritt():
    from services.analyst_eval import baue_videoende_schritt
    for nachlauf in (1.0, 1.5, 2.0):
        ev = _eval_mit_scores()
        out = baue_videoende_schritt(ev, _result_mit(duration=24.7 + nachlauf, sprech_dauer=24.7))
        assert [e for e in out.empfehlungen if e.gruppe == "videoende"] == [], nachlauf


def test_langer_nachlauf_verlangt_kuerzen():
    from services.analyst_eval import baue_videoende_schritt
    ev = _eval_mit_scores()
    out = baue_videoende_schritt(ev, _result_mit(duration=30.0, sprech_dauer=24.7))
    schritte = [e for e in out.empfehlungen if e.gruppe == "videoende"]
    assert len(schritte) == 1
    assert "kürz" in schritte[0].anweisung.lower()


def test_videoende_verwirft_die_modell_empfehlung():
    """Das Modell darf zum Videoende nichts mehr selbst formulieren — es hat sich geirrt."""
    from models.analyst import Empfehlung
    from services.analyst_eval import baue_videoende_schritt
    ev = _eval_mit_scores()
    ev.empfehlungen = [Empfehlung(zeitpunkt_sek=23.0, betrifft="spannungsbogen",
                                  anweisung="Beende das Video direkt nach dem letzten Wort, um "
                                            "unnötigen Leerlauf am Ende zu vermeiden.")]
    out = baue_videoende_schritt(ev, _result_mit(duration=24.92, sprech_dauer=24.68))
    assert not any("Leerlauf" in e.anweisung for e in out.empfehlungen)


def test_videoende_ohne_ziel_bleibt_unveraendert():
    from models.analyst import Empfehlung
    from services.analyst_eval import baue_videoende_schritt
    ev = _eval_mit_scores()
    ev.empfehlungen = [Empfehlung(zeitpunkt_sek=23.0, anweisung="Leerlauf am Ende vermeiden")]
    out = baue_videoende_schritt(ev, _result_mit(duration=24.92, sprech_dauer=24.68, ziel=""))
    assert len(out.empfehlungen) == 1


def test_videoende_ohne_gesprochenes_wort_wird_nicht_geprueft():
    from services.analyst_eval import baue_videoende_schritt
    ev = _eval_mit_scores()
    r = _result_mit(duration=24.92, sprech_dauer=0.0)
    r.speech_stats.wort_anzahl = 0
    assert baue_videoende_schritt(ev, r).empfehlungen == []


# --- Lautstaerke: Zielkorridor statt Schaetzung (Lauf dc5c0a3d) ---

def test_zu_leiser_ton_nennt_ist_und_soll():
    from services.analyst_eval import baue_lautstaerke_schritt
    out = baue_lautstaerke_schritt(_eval_mit_scores(), _result_mit(lufs=-35.8, true_peak=-18.0))
    schritte = [e for e in out.empfehlungen if e.gruppe == "lautstaerke"]
    assert len(schritte) == 1
    text = schritte[0].anweisung
    assert "-35.8" in text or "-35,8" in text
    assert "-14" in text


def test_lautstaerke_im_korridor_erzeugt_keinen_schritt():
    from services.analyst_eval import baue_lautstaerke_schritt
    for lufs in (-17.0, -14.0, -11.0):
        out = baue_lautstaerke_schritt(_eval_mit_scores(), _result_mit(lufs=lufs))
        assert [e for e in out.empfehlungen if e.gruppe == "lautstaerke"] == [], lufs


def test_uebersteuerung_wird_genannt():
    from services.analyst_eval import baue_lautstaerke_schritt
    out = baue_lautstaerke_schritt(_eval_mit_scores(), _result_mit(lufs=-14.0, true_peak=0.5))
    schritte = [e for e in out.empfehlungen if e.gruppe == "lautstaerke"]
    assert len(schritte) == 1
    assert "übersteuert" in schritte[0].anweisung.lower() or "spitze" in schritte[0].anweisung.lower()


def test_lautstaerke_verwirft_die_modell_empfehlung():
    from models.analyst import Empfehlung
    from services.analyst_eval import baue_lautstaerke_schritt
    ev = _eval_mit_scores()
    ev.empfehlungen = [Empfehlung(zeitpunkt_sek=0.0, betrifft="sprechqualitaet",
                                  anweisung="Hebe die Lautstärke der gesamten Tonspur um ca. "
                                            "3 Dezibel an.")]
    out = baue_lautstaerke_schritt(ev, _result_mit(lufs=-14.0))
    assert not any("Dezibel" in e.anweisung for e in out.empfehlungen)


def test_lautstaerke_ohne_ziel_bleibt_unveraendert():
    from models.analyst import Empfehlung
    from services.analyst_eval import baue_lautstaerke_schritt
    ev = _eval_mit_scores()
    ev.empfehlungen = [Empfehlung(zeitpunkt_sek=0.0, anweisung="Lautstärke um 3 Dezibel anheben")]
    out = baue_lautstaerke_schritt(ev, _result_mit(lufs=-35.8, ziel=""))
    assert len(out.empfehlungen) == 1


def test_ohne_messwert_kein_lautstaerke_schritt():
    from services.analyst_eval import baue_lautstaerke_schritt
    out = baue_lautstaerke_schritt(_eval_mit_scores(), _result_mit(lufs=None))
    assert [e for e in out.empfehlungen if e.gruppe == "lautstaerke"] == []


# --- Hoechstens eine Empfehlung je Dimension in den Top 3 (Lauf dc5c0a3d) ---

def test_nur_eine_empfehlung_je_dimension_in_den_top_drei():
    """Lauf dc5c0a3d: zwei von drei Schritten betrafen sprech_hook."""
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (0.0, "Formuliere den ersten Satz um", "sprech_hook", "sprechhook"),
        (0.0, "Starte mit der steilen These", "sprech_hook", ""),
        (10.0, "Spannungsbogen halten", "spannungsbogen", ""),
        (15.0, "Schnitt straffen", "schnitt_pacing", ""),
        sprech=2, spannung=2, schnitt=2,
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    betroffen = [s.anweisung for s in out.action_steps]
    assert len([a for a in betroffen if "Satz um" in a or "steilen These" in a]) == 1
    assert len(out.action_steps) == 3


def test_empfehlungen_ohne_dimension_duerfen_mehrfach_oben_stehen():
    """Videospezifische Schritte ohne `betrifft` meinen verschiedene Stellen — sie sind keine Dubletten."""
    from services.analyst_eval import verteile_empfehlungen
    ev = _mit_empfehlungen(
        (3.0, "Bei Sekunde 3 eine Grafik einblenden", "", ""),
        (9.0, "Bei Sekunde 9 einen Schnitt setzen", "", ""),
        (14.0, "Bei Sekunde 14 den Ton absenken", "", ""),
    )
    out = verteile_empfehlungen(ev, ziel="MOFU")
    assert len(out.action_steps) == 3


# =====================================================================================
# Stufe 2, Teil 1 — Schema: die neuen Felder im Modell
# =====================================================================================

def test_neue_dimensionsfelder_haben_defaults():
    """88+ gespeicherte Altlaeufe kennen diese Felder nicht — ohne Default laedt keiner mehr."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2()
    assert ev.untertitel.score is None
    assert ev.untertitel.gestaltung_score is None
    assert ev.audioqualitaet.score == 0
    assert ev.audioqualitaet.probleme == []
    assert ev.cta.score == 0
    assert ev.funnel_wirkung == ""
    assert ev.funnel_wirkung_grund == ""


def test_untertitel_docstring_nennt_das_fehlen_nicht_mehr_als_formatentscheidung():
    """Seit 2026-08-06 ist das Fehlen ein Mangel (siehe erzwinge_untertitel_empfehlung).
    Der alte Docstring behauptete das Gegenteil und fuehrte den naechsten Leser in die Irre."""
    from models.analyst import UntertitelEval
    assert "KEIN Mangel" not in (UntertitelEval.__doc__ or "")


def test_struktur_cta_bleibt_als_beobachtung_erhalten():
    """Der Bool zieht nicht um — er beschreibt, OB das Element da ist, nicht wie gut es ist."""
    from models.analyst import StrukturElemente
    assert StrukturElemente().cta is False


def test_neue_objektfelder_gefaehrden_die_nachbarlisten_nicht():
    """Ein neues Objekt-Feld kann das Modell dazu bringen, das Objekt-Muster auf Nachbarn zu
    uebertragen (real passiert bei staerken -> top_tipps). Die Listenfelder bleiben Strings."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(top_tipps=["Kuerze den Anlauf."], texthook_varianten=["Drei Fehler"])
    assert ev.top_tipps == ["Kuerze den Anlauf."]
    assert ev.texthook_varianten == ["Drei Fehler"]


def test_alle_gespeicherten_altlaeufe_laden_weiter():
    """88+ gespeicherte Laeufe: ein neues Pflichtfeld ohne Default macht sie alle unlesbar."""
    import glob
    import os
    from models.analyst import AnalystResult
    wurzel = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "analyst_runs")
    pfade = sorted(glob.glob(os.path.join(wurzel, "*", "analysis.json")))
    if not pfade:
        pytest.skip("keine gespeicherten Laeufe vorhanden")
    for p in pfade:
        with open(p, encoding="utf-8") as fh:
            AnalystResult(**json.load(fh))


# =====================================================================================
# Stufe 2, Teil 2 — Score-Integration: Code-Regeln und Gewichtung
# =====================================================================================

def _result_ziel(ziel="MOFU", transcript="Hallo, heute zeige ich dir etwas.",
                 lufs=-14.0, true_peak=-2.0):
    """Lauf mit gesetztem Ziel — die Stufe-2-Regeln greifen nur dort."""
    from models.analyst import AnalystResult, QualityMetrics
    return AnalystResult(
        id="x", filename="c.mp4", duration_sec=25.0, scene_count=0, scenes=[],
        gewaehltes_ziel=ziel, transcript=transcript,
        quality_metrics=(QualityMetrics(lufs_integrated=lufs, true_peak_db=true_peak)
                         if lufs is not None or true_peak is not None else None),
    )


# --- Teil A: Untertitel als zwei bewertete Dimensionen ---

def test_ohne_gesprochenes_wort_sind_untertitel_nicht_bewertbar():
    """Es gibt nichts zu untertiteln — null heisst „nicht bewertbar", nicht „schlecht"."""
    from services.analyst_eval import setze_untertitel_scores
    ev = _eval_mit_scores()
    ev.untertitel.vorhanden = False
    ev.untertitel.score = 4
    ev.untertitel.gestaltung_score = 4
    out = setze_untertitel_scores(ev, _result_ziel(transcript=""))
    assert out.untertitel.score is None
    assert out.untertitel.gestaltung_score is None


def test_gesprochen_ohne_untertitel_ist_ein_kritischer_mangel():
    """Vorgabe Chris 2026-08-06: mitlaufende Untertitel sind Pflicht, sobald gesprochen wird."""
    from services.analyst_eval import setze_untertitel_scores
    ev = _eval_mit_scores()
    ev.untertitel.vorhanden = False
    ev.untertitel.score = 5           # das Modell darf sich hier nicht durchsetzen
    ev.untertitel.gestaltung_score = 5
    out = setze_untertitel_scores(ev, _result_ziel())
    assert out.untertitel.score == 1
    assert out.untertitel.gestaltung_score is None   # nichts da, was gestaltet sein koennte


def test_vorhandene_untertitel_behalten_das_modellurteil():
    from services.analyst_eval import setze_untertitel_scores
    ev = _eval_mit_scores()
    ev.untertitel.vorhanden = True
    ev.untertitel.score = 4
    ev.untertitel.gestaltung_score = 2
    out = setze_untertitel_scores(ev, _result_ziel())
    assert out.untertitel.score == 4
    assert out.untertitel.gestaltung_score == 2


def test_untertitel_scores_ohne_ziel_bleiben_unangetastet():
    """V2 ist die eingefrorene Vergleichsbasis."""
    from services.analyst_eval import setze_untertitel_scores
    ev = _eval_mit_scores()
    ev.untertitel.vorhanden = False
    ev.untertitel.score = 5
    out = setze_untertitel_scores(ev, _result_ziel(ziel=""))
    assert out.untertitel.score == 5


def test_fehlende_untertitel_kosten_beim_gesamtscore():
    """Die Dimension muss auch wirklich in die Rechnung laufen, nicht nur im Modell stehen."""
    from services.analyst_eval import berechne_performance_score, setze_untertitel_scores
    mit = _eval_mit_scores()
    mit.untertitel.vorhanden = True
    mit.untertitel.score = 5
    mit.untertitel.gestaltung_score = 5
    ohne = _eval_mit_scores()
    ohne.untertitel.vorhanden = False
    r = _result_ziel()
    a = berechne_performance_score(setze_untertitel_scores(mit, r), ziel="MOFU").performance_score
    b = berechne_performance_score(setze_untertitel_scores(ohne, r), ziel="MOFU").performance_score
    assert b < a


# --- Teil B: Audioqualitaet ---

def test_leiser_ton_deckelt_die_audioqualitaet_hart():
    """Lauf dc5c0a3d: -35,8 LUFS ist auf dem Handy praktisch unhoerbar — keine 5, egal wie sauber."""
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 5
    out = deckle_audioqualitaet(ev, _result_ziel(lufs=-35.8, true_peak=-18.0))
    assert out.audioqualitaet.score == 2


def test_leichte_abweichung_deckelt_auf_drei():
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 5
    out = deckle_audioqualitaet(ev, _result_ziel(lufs=-20.0))
    assert out.audioqualitaet.score == 3


def test_uebersteuerung_deckelt_die_audioqualitaet():
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 5
    out = deckle_audioqualitaet(ev, _result_ziel(lufs=-14.0, true_peak=0.5))
    assert out.audioqualitaet.score == 3


def test_lautheit_im_korridor_deckelt_nicht():
    from services.analyst_eval import deckle_audioqualitaet
    for lufs in (-17.0, -14.0, -11.0):
        ev = _eval_mit_scores()
        ev.audioqualitaet.score = 5
        out = deckle_audioqualitaet(ev, _result_ziel(lufs=lufs))
        assert out.audioqualitaet.score == 5, lufs


def test_deckel_hebt_einen_schwachen_score_nie_an():
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 1
    out = deckle_audioqualitaet(ev, _result_ziel(lufs=-35.8))
    assert out.audioqualitaet.score == 1


def test_ohne_audio_ist_die_audioqualitaet_nicht_bewertbar():
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 4
    out = deckle_audioqualitaet(ev, _result_ziel(lufs=None, true_peak=None))
    assert out.audioqualitaet.score is None


def test_audioqualitaet_ohne_ziel_bleibt_unangetastet():
    from services.analyst_eval import deckle_audioqualitaet
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 5
    out = deckle_audioqualitaet(ev, _result_ziel(ziel="", lufs=-35.8))
    assert out.audioqualitaet.score == 5


def test_benannte_audio_probleme_deckeln_den_score():
    """Dieselbe Regel wie bei sprechqualitaet und visuelle_aesthetik."""
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _eval_mit_scores()
    ev.audioqualitaet.score = 5
    ev.audioqualitaet.probleme = ["Deutlicher Hall im Raum.", "Dauerhaftes Rauschen im Hintergrund."]
    assert deckle_score_auf_probleme(ev).audioqualitaet.score == 3


# --- Teil C: CTA ---

def test_cta_zaehlt_bei_bofu_und_nicht_bei_mofu():
    """Gleiche Bewertung, zwei Ziele: nur bei BOFU darf der CTA den Score bewegen."""
    from services.analyst_eval import berechne_performance_score

    def score(ziel, cta):
        ev = _eval_mit_scores()
        ev.cta.score = cta
        return berechne_performance_score(ev, ziel=ziel).performance_score

    assert score("BOFU", 1) < score("BOFU", 5)
    assert score("MOFU", 1) == score("MOFU", 5)


def test_cta_ohne_bewertung_faellt_aus_dem_score():
    """Score 0 ist der Modell-Default, keine echte Bewertung — er darf nicht als 1 durchschlagen."""
    from services.analyst_eval import berechne_performance_score
    ohne = berechne_performance_score(_eval_mit_scores(), ziel="BOFU").performance_score
    ev = _eval_mit_scores()
    ev.cta.score = 0
    assert berechne_performance_score(ev, ziel="BOFU").performance_score == ohne


# --- Teil D: Funnel-Wirkung ---

def test_gueltige_funnel_wirkung_bleibt_stehen():
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "tofu"
    ev.funnel_wirkung_grund = "Kurz, breit angesprochen, kein Fachbegriff."
    out = pruefe_funnel_wirkung(ev, _result_ziel())
    assert out.funnel_wirkung == "TOFU"
    assert out.funnel_wirkung_grund


def test_ungueltige_funnel_wirkung_wird_geleert():
    """Kein Rateversuch: ein erfundener Wert ist schlechter als keiner."""
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "Mischung"
    out = pruefe_funnel_wirkung(ev, _result_ziel())
    assert out.funnel_wirkung == ""


def test_funnel_wirkung_darf_dem_ziel_widersprechen():
    """Genau dieser Widerspruch ist die wertvollste Information — der Code buegelt ihn nicht glatt."""
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "BOFU"
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel="TOFU"))
    assert out.funnel_wirkung == "BOFU"


def test_funnel_bleibt_das_gewaehlte_ziel():
    """`funnel` ist die Absicht, `funnel_wirkung` die Einschaetzung — zwei Felder, zwei Quellen."""
    from services.analyst_eval import nachbearbeiten
    ev = _eval_mit_scores()
    ev.funnel = "MOFU"
    ev.funnel_wirkung = "TOFU"
    out = nachbearbeiten(ev, _result_ziel(ziel="MOFU"))
    assert out.funnel == "MOFU"
    assert out.funnel_wirkung == "TOFU"


def test_funnel_wirkung_ohne_ziel_bleibt_unangetastet():
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "Quatsch"
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel=""))
    assert out.funnel_wirkung == "Quatsch"


# --- Die Vier-Stellen-Falle ---

def test_dimensions_scores_deckt_die_gewichtstabelle_vollstaendig():
    """DER Test gegen die Vier-Stellen-Falle: Eine Dimension mit Gewicht, die dimensions_scores
    nicht kennt, wird stillschweigend uebersprungen — ihr Gewicht verteilt sich auf den Rest, und
    niemandem faellt auf, dass die Dimension gar nicht bewertet wird."""
    from models.analyst import AnalystEvaluationV2, SCORE_GEWICHTE_JE_ZIEL, ZIELE
    from services.analyst_eval import dimensions_scores
    bekannt = set(dimensions_scores(AnalystEvaluationV2()))
    for ziel in ZIELE:
        assert set(SCORE_GEWICHTE_JE_ZIEL[ziel]) == bekannt, ziel


def test_die_vier_neuen_dimensionen_sind_wirklich_da():
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import dimensions_scores
    neu = {"untertitel_vorhanden", "untertitel_gestaltung", "audioqualitaet", "cta"}
    assert neu <= set(dimensions_scores(AnalystEvaluationV2()))


def test_jede_gewichtete_dimension_hat_eine_kategorie():
    """Der Lob-Filter laeuft ueber KATEGORIEN — eine Dimension ohne Kategorie kann nie gelobt werden."""
    from models.analyst import KATEGORIEN, SCORE_GEWICHTE_JE_ZIEL
    zugeordnet = {d for dims in KATEGORIEN.values() for d in dims}
    assert set(SCORE_GEWICHTE_JE_ZIEL["BOFU"]) <= zugeordnet


def test_score_rechnung_nutzt_dieselbe_zuordnung_wie_die_sortierung():
    """Zwei getrennte Dimensions-Dicts laufen beim naechsten Feld auseinander."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import DIMENSION_MINIMUM, dimensions_scores
    assert set(DIMENSION_MINIMUM) <= set(dimensions_scores(AnalystEvaluationV2()))
    assert DIMENSION_MINIMUM["text_hook"] == 0


# =====================================================================================
# Stufe 2, Teil 3 — Prompt: Ausgabe-Vertrag und Skill-Text
# =====================================================================================

def test_v3_vertrag_kennt_alle_neuen_felder():
    """Ein Feld nur im Skill-Text zu beschreiben reicht NICHT — das Modell folgt dem Vertrag."""
    from services.analyst_eval import build_system_prompt
    v3 = build_system_prompt(ziel="MOFU")
    # `"cta"` steht als Bool schon in struktur.elemente — geprueft wird deshalb die
    # Top-Level-Zeile, nicht der blosse Feldname.
    for feld in ("gestaltung_score", '\n  "audioqualitaet":', '\n  "cta":',
                 '\n  "funnel_wirkung":', '\n  "funnel_wirkung_grund":'):
        assert feld in v3, feld


def test_v2_vertrag_kennt_keines_der_neuen_felder():
    """V2 ist die eingefrorene Vergleichsbasis — aendert sich der Prompt, ist der Vergleich futsch."""
    from services.analyst_eval import build_system_prompt
    v2 = build_system_prompt()
    for feld in ("gestaltung_score", '\n  "audioqualitaet":', '\n  "cta":',
                 '\n  "funnel_wirkung":'):
        assert feld not in v2, feld


def test_handwerk_call_liefert_die_handwerks_felder():
    """_schema_fuer filtert den Vertrag ueber TEIL_FELDER — ein Feld ohne Eintrag erscheint in
    KEINEM der beiden Calls."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    for feld in ('\n  "audioqualitaet":', '\n  "cta":', '\n  "untertitel":', "gestaltung_score"):
        assert feld in p, feld
    assert '"funnel_wirkung"' not in p


def test_eroeffnungs_call_liefert_die_funnel_wirkung():
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="eroeffnung", ziel="MOFU")
    assert '\n  "funnel_wirkung":' in p
    assert '\n  "funnel_wirkung_grund":' in p
    assert '\n  "audioqualitaet":' not in p


def test_merge_traegt_die_neuen_handwerks_felder_hinueber():
    """merge_teilergebnisse kopiert entlang TEIL_FELDER['handwerk'] — fehlt dort ein Eintrag,
    faellt das Feld beim Zusammenfuehren still weg."""
    from services.analyst_eval import merge_teilergebnisse
    eroeffnung = _eval_mit_scores()
    eroeffnung.funnel_wirkung = "TOFU"
    handwerk = _eval_mit_scores()
    handwerk.audioqualitaet.score = 4
    handwerk.cta.score = 2
    handwerk.untertitel.gestaltung_score = 5
    out = merge_teilergebnisse(eroeffnung, handwerk)
    assert out.audioqualitaet.score == 4
    assert out.cta.score == 2
    assert out.untertitel.gestaltung_score == 5
    assert out.funnel_wirkung == "TOFU"


def test_v3_skill_verlangt_keine_abschrift_des_ziels_mehr():
    """Lauf dc5c0a3d: `funnel` war exakt das gewaehlte Ziel, weil der Skill woertlich das Abschreiben
    verlangte. Fuer `funnel_wirkung` muss dort das Gegenteil stehen — scharf genug, dass das Modell
    nicht weiter abschreibt."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(ziel="MOFU")
    assert "funnel_wirkung" in p
    assert "NICHT das gewählte Ziel" in p


def test_v3_skill_erklaert_die_neuen_dimensionen():
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    for wort in ("Audioqualität", "Call to Action", "gestaltung_score"):
        assert wort in p, wort


def test_v2_skill_bleibt_unangetastet():
    """Die V2-Skilldatei ist Teil der eingefrorenen Vergleichsbasis."""
    from services.analyst_eval import load_skill_body
    body = load_skill_body()
    for wort in ("funnel_wirkung", "gestaltung_score", "audioqualitaet"):
        assert wort not in body, wort


def test_v3_staerken_duerfen_sich_auf_die_neuen_dimensionen_beziehen():
    """filtere_staerken ordnet ueber `betrifft` einer Kategorie zu — steht der Name nicht im
    Vertrag, kann das Modell eine gute Audiospur nie loben."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    zeile = [z for z in p.splitlines() if z.startswith('  "staerken":')][0]
    for name in ("untertitel_vorhanden", "untertitel_gestaltung", "audioqualitaet", "cta"):
        assert name in zeile, name


def test_prompt_version_wurde_hochgezaehlt():
    """Betriebsregel: bei jeder inhaltlichen Prompt-Aenderung hochzaehlen, sonst ist Feedback zu
    zwei verschiedenen Prompts nicht mehr auseinanderzuhalten."""
    from services.analyst_eval import PROMPT_VERSION
    assert PROMPT_VERSION == "2026-09-13f"


def test_v3_verlangt_hoechstens_eine_empfehlung_je_dimension():
    """Lauf dc5c0a3d: zwei der drei Top-Schritte betrafen `sprech_hook` — „Formuliere deinen ersten
    gesprochenen Satz um" und „Starte direkt mit der steilen These des Experten". Derselbe Mangel,
    zwei Plaetze von dreien. Die Code-Regel in verteile_empfehlungen wirft den zweiten nur weg;
    zwei Freitexte sinnvoll verschmelzen kann nur das Modell — deshalb steht die Regel im Prompt."""
    from services.analyst_eval import SKILL_PATH_V3, load_skill_body
    v3 = " ".join(load_skill_body(SKILL_PATH_V3).split())
    v2 = " ".join(load_skill_body().split())
    assert "Höchstens EINE Empfehlung je Bewertungsdimension" in v3
    assert "Höchstens EINE Empfehlung je Bewertungsdimension" not in v2


def test_v3_verlangt_umgekehrt_zu_jeder_schwachen_dimension_eine_empfehlung():
    """Untergrenze zur selben Regel: Ohne eigene Empfehlung setzt der Code einen generischen
    Standardsatz ein, der das Video nicht kennt. Die Obergrenze allein wuerde das Modell sonst
    dazu verleiten, lieber gar nichts zu schreiben."""
    from services.analyst_eval import SKILL_PATH_V3, load_skill_body
    v3 = " ".join(load_skill_body(SKILL_PATH_V3).split())
    assert "Score 3 oder schlechter gehört eine eigene Empfehlung" in v3
    assert "Standardsatz" in v3
# --- Objekt-Muster-Spillover (erster echter V3-Lauf, 2026-09-12) -------------------------------

def test_top_tipps_als_objekte_brechen_den_lauf_nicht():
    """Realer Abbruch: Seit staerken Objekte sind, lieferte das Modell auch top_tipps als
    [{"text": ...}] — Pydantic brach den fertig bezahlten Lauf ab."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{"top_tipps": [
        {"text": "Stelle am Ende eine Frage."},
        {"text": "Erhöhe die Geschwindigkeit."},
        "Schon ein String",
    ]})
    assert ev.top_tipps == ["Stelle am Ende eine Frage.", "Erhöhe die Geschwindigkeit.",
                            "Schon ein String"]


def test_objekt_muster_greift_auch_bei_den_anderen_listenfeldern():
    from models.analyst import AnalystEvaluationV2, ScoreProbleme, UntertitelEval
    ev = AnalystEvaluationV2(**{
        "texthook_varianten": [{"text": "Mit 46 nochmal Mutter"}],
        "texthook_maengel": [{"mangel": "laenge"}],
    })
    assert ev.texthook_varianten == ["Mit 46 nochmal Mutter"]
    assert ev.texthook_maengel == ["laenge"]
    assert ScoreProbleme(**{"score": 3, "probleme": [{"text": "Gegenlicht"}],
                            "hinweise": [{"text": "Kopfraum knapp"}]}).probleme == ["Gegenlicht"]
    assert UntertitelEval(**{"vorhanden": True, "maengel": [{"text": "position"}]}).maengel == ["position"]


def test_objekt_ohne_bekannten_schluessel_nimmt_den_ersten_string():
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{"top_tipps": [{"hinweis": "Irgendein Text", "score": 3}]})
    assert ev.top_tipps == ["Irgendein Text"]


def test_strings_bleiben_unveraendert():
    """Der Normalfall darf sich nicht ändern — sonst wäre der Validator selbst das Risiko."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{"top_tipps": ["a", "b"], "texthook_varianten": ["c"]})
    assert ev.top_tipps == ["a", "b"]
    assert ev.texthook_varianten == ["c"]


# --- Notnagel-Messung: erzwungene Schritte sind als solche erkennbar ---------------------------
# Hintergrund: Die Standardsaetze bleiben, sollen aber langfristig fast nie mehr greifen. Dafuer
# muss man sie zaehlen koennen — im fertigen Ergebnis sieht man ihnen heute nichts mehr an.


def _result(**kw):
    from models.analyst import AnalystResult
    return AnalystResult(id="x", filename="v.mp4", duration_sec=43.1, scene_count=0, scenes=[], **kw)


def _stats(sprechbeginn=1.6, dauer=20.0):
    from models.analyst import SpeechStats
    return SpeechStats(wort_anzahl=10, sprech_dauer_sec=dauer, wpm=120.0, filler_count=0,
                       filler_words=[], pausen_count=0, laengste_pause_sec=0.0,
                       sprechbeginn_sec=sprechbeginn)


def test_erzwungene_empfehlung_ist_markiert_modell_empfehlung_nicht():
    """`erzwungen` trennt den generischen Rueckfalltext vom individuellen Modell-Text."""
    from models.analyst import AnalystEvaluationV2, Empfehlung
    from services.analyst_eval import erzwinge_blick_empfehlung
    ev = AnalystEvaluationV2(
        blickkontakt={"urteil": "abgelesen"},
        empfehlungen=[Empfehlung(zeitpunkt_sek=12.0, anweisung="Schneide den Versprecher raus.")],
    )
    ev = erzwinge_blick_empfehlung(ev)
    modell, notnagel = ev.empfehlungen[0], ev.empfehlungen[-1]
    assert modell.erzwungen is False
    assert notnagel.gruppe == "blick" and notnagel.erzwungen is True


def test_alle_fuenf_erzwinge_funktionen_markieren_ihre_schritte():
    """Genau die fuenf `erzwinge_*`-Funktionen sind Notnaegel. Faellt eine aus der Markierung,
    zaehlt das Werkzeug sie als Modell-Leistung und die Quote wird zu gut."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import (erzwinge_anlauf_schnitt, erzwinge_blick_empfehlung,
                                       erzwinge_empfehlungen_bei_schwachen_scores,
                                       erzwinge_hook_empfehlungen, erzwinge_untertitel_empfehlung)

    # 1) Hook-Empfehlungen (Sprech- und Texthook)
    ev = erzwinge_hook_empfehlungen(AnalystEvaluationV2(
        hook={"sprech_hook_score": 2, "text_hook_vorhanden": True, "text_hook_score": 2}))
    assert ev.empfehlungen and all(e.erzwungen for e in ev.empfehlungen)
    assert {e.gruppe for e in ev.empfehlungen} == {"sprechhook", "texthook"}

    # 2) Schwache Dimensions-Scores
    ev = erzwinge_empfehlungen_bei_schwachen_scores(
        AnalystEvaluationV2(spannungsbogen={"score": 2}))
    assert ev.empfehlungen and all(e.erzwungen for e in ev.empfehlungen)

    # 3) Anlauf-Schnitt
    ev = erzwinge_anlauf_schnitt(
        AnalystEvaluationV2(),
        _result(gewaehltes_format="Talking Head", speech_stats=_stats(1.6)))
    assert ev.empfehlungen[0].gruppe == "anlauf" and ev.empfehlungen[0].erzwungen is True

    # 4) Blickrichtung
    ev = erzwinge_blick_empfehlung(AnalystEvaluationV2(blickkontakt={"urteil": "abgelesen"}))
    assert ev.empfehlungen[0].erzwungen is True

    # 5) Untertitel
    ev = erzwinge_untertitel_empfehlung(
        AnalystEvaluationV2(untertitel={"vorhanden": False}),
        _result(transcript="Hallo, hier spricht jemand."))
    assert ev.empfehlungen[0].gruppe == "untertitel" and ev.empfehlungen[0].erzwungen is True


def test_baue_schritte_sind_kein_notnagel():
    """`baue_*_schritt` baut aus Modell-Urteil plus Messwert — das ist die gewollte Arbeitsteilung,
    kein Rueckfall. Wuerde es mitzaehlen, waere die Quote dauerhaft unbrauchbar hoch."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import baue_lautstaerke_schritt, baue_pausen_schritt
    ev = baue_pausen_schritt(AnalystEvaluationV2(
        pausen_urteile=[{"start_sec": 12.0, "urteil": "raus"}]))
    assert ev.empfehlungen and all(e.erzwungen is False for e in ev.empfehlungen)

    ev = baue_lautstaerke_schritt(
        AnalystEvaluationV2(),
        _result(gewaehltes_ziel="TOFU", quality_metrics={"lufs_integrated": -35.8}))
    assert ev.empfehlungen and all(e.erzwungen is False for e in ev.empfehlungen)


def test_erzwungen_ueberlebt_bis_in_die_action_steps():
    """Ohne die Uebergabe in verteile_empfehlungen waere im gespeicherten Lauf nichts messbar."""
    from models.analyst import AnalystEvaluationV2, Empfehlung
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(AnalystEvaluationV2(empfehlungen=[
        Empfehlung(zeitpunkt_sek=0.0, gruppe="blick", anweisung="Blick in die Linse.",
                   erzwungen=True),
        Empfehlung(zeitpunkt_sek=5.0, anweisung="Schneide den Versprecher raus."),
    ]))
    assert [s.erzwungen for s in ev.action_steps] == [True, False]


def test_altlauf_ohne_das_feld_laedt_unveraendert():
    """88 gespeicherte Laeufe kennen `erzwungen` nicht — Default False, kein Schema-Bruch."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{"action_steps": [{"zeitpunkt": "x", "anweisung": "y"}]})
    assert ev.action_steps[0].erzwungen is False


# =====================================================================================
# Nutzer-Feedback aus Lauf d9988b7d — „keine Auffaelligkeiten = 5/5"
#
# Der Nutzer schrieb es dreimal (sprechqualitaet, visuelle_aesthetik, audioqualitaet):
# „wenn keine auffaelligkeiten sollte der score auch eine 5/5 sein". Der strukturelle Grund steht
# in docs/analyst_knowledge_luecken.md: Sieben der zwoelf bewerteten Dimensionen hatten ueberhaupt
# keine Score-Anker. Ohne Anker rutscht ein Modell zur Mitte — es gibt keinen Grund, 5 zu vergeben,
# wenn nirgends steht, wie eine 5 aussieht (belegt fuer visuelle_aesthetik: 5 von 5 Laeufen exakt 3).
# =====================================================================================

# Die Dimensionen, die einen ANKER-Block im V3-Skill haben muessen. `visuelle_aesthetik` ist das
# Vorbild und stand schon vorher da — sie ist hier mit drin, damit ein Umbau des Vorbilds auffaellt.
ANKER_DIMENSIONEN = [
    "visuell_hook", "struktur", "spannungsbogen", "schnitt_pacing", "sprechqualitaet",
    "visuelle_aesthetik", "untertitel_vorhanden", "untertitel_gestaltung", "audioqualitaet", "cta",
]


def _v3_text():
    from services.analyst_eval import SKILL_PATH_V3, load_skill_body
    return load_skill_body(SKILL_PATH_V3)


def _ankerblock(name: str) -> str:
    """Der Text von der ANKER-Ueberschrift bis zur naechsten Leerzeile-Ueberschrift."""
    text = _v3_text()
    kopf = f"ANKER für `{name}"
    start = text.index(kopf)
    rest = text[start:]
    ende = rest.index("\n\n")
    # Zeilenumbrueche raus: Die Skill-Datei bricht bei 100 Zeichen um, ein geprueftes Satzstueck
    # laege sonst zufaellig auf zwei Zeilen.
    return " ".join(rest[:ende].split())


def test_jede_bewertete_dimension_hat_einen_score_anker():
    """Die groesste Einzelluecke aus der Lueckenanalyse: sieben Dimensionen ohne jeden Anker."""
    text = _v3_text()
    for name in ANKER_DIMENSIONEN:
        assert f"ANKER für `{name}" in text, name


def test_jeder_anker_nutzt_die_ganze_skala():
    """Ein halber Anker (nur 5, 3, 1 — so stand visuell_hook da) laesst 4 und 2 unbesetzt und
    schiebt genau deshalb zur Mitte."""
    for name in ANKER_DIMENSIONEN:
        block = _ankerblock(name)
        for stufe in ("**5**", "**4**", "**3**", "**2**", "**1**"):
            assert stufe in block, f"{name}: {stufe}"


def test_die_fuenf_braucht_keine_auszeichnung():
    """Der Kern des Feedbacks: Die 5 muss erreichbar sein, wenn nichts auffaellt — nicht erst bei
    herausragender Leistung. Steht die Regel nur einmal global, uebersieht sie das Modell beim
    Bewerten einer einzelnen Dimension; deshalb in JEDEM Ankerblock."""
    for name in ANKER_DIMENSIONEN:
        block = _ankerblock(name)
        assert "Abwesenheit von Mängeln genügt" in block, name


def test_die_fuenf_regel_steht_auch_als_eigener_abschnitt():
    text = _v3_text()
    assert "## Score-Anker — wann eine 5 eine 5 ist" in text
    assert "Es braucht keine Auszeichnung für eine 5" in text


def test_schnitt_pacing_zieht_nicht_mehr_zur_mitte():
    """c-7 der Lueckenanalyse: „Lieber vorsichtig als falsch" ohne Format-Massstab ist eine
    Einladung, immer 3 zu vergeben — dieselbe Pflicht-Mechanik, die fuer die visuelle Aesthetik
    (KB 8.5, Laeufe 30d6b472/82bda700) schon abgeschafft wurde."""
    text = _v3_text()
    assert "Zurückhaltung heißt NICHT, im Zweifel 3 zu vergeben" in text


def test_die_anker_gelten_nur_fuer_v3():
    """V2 ist die eingefrorene Vergleichsbasis der A/B-Messung."""
    from services.analyst_eval import load_skill_body
    v2 = load_skill_body()
    for name in ("sprechqualitaet", "audioqualitaet", "cta"):
        assert f"ANKER für `{name}" not in v2, name
    assert "Abwesenheit von Mängeln genügt" not in v2


def test_die_anker_erreichen_beide_teil_calls():
    """Die Anker nuetzen nichts, wenn `_skill_fuer` sie aus dem Call herausfiltert — bis auf die
    visuelle Hook liegen alle Dimensionen im Handwerks-Call."""
    from services.analyst_eval import build_system_prompt
    handwerk = build_system_prompt(teil="handwerk", ziel="MOFU")
    for name in ("sprechqualitaet", "audioqualitaet", "cta", "struktur"):
        assert f"ANKER für `{name}" in handwerk, name


# --- Lauf d9988b7d, Feedback zu `hook.visuell`: „mini zoom wurde nicht erkannt?" ---------------

def test_kleine_bewegungen_zaehlen_ausdruecklich():
    """Der Nutzer hatte einen kleinen Zoom in der Eröffnung; das Modell meldete ihn nicht. Die
    Vorgeschichte ist dieselbe wie bei `eroeffnung_hat_bewegung` (Läufe 225cf73b, b08f73bd,
    7230d0f8): Das Modell empfahl einen Zoom, den es längst gab."""
    text = " ".join(_v3_text().split())
    assert "Auch KLEINE Bewegungen zählen" in text
    assert "leichter Punch-In" in text
    assert "Ein mini Zoom ist keine 1" in text


def test_das_standbild_bleibt_die_eins():
    """Die Untergrenze darf durch die Aufwertung kleiner Bewegungen nicht verschwimmen: 1 ist
    ausschliesslich das Bild, in dem WIRKLICH nichts passiert."""
    block = _ankerblock("visuell_hook")
    assert "1** — ein reines Standbild" in block


def test_der_visuell_hook_anker_erreicht_den_eroeffnungs_call():
    from services.analyst_eval import build_system_prompt
    assert "ANKER für `visuell_hook" in build_system_prompt(teil="eroeffnung", ziel="MOFU")


# =====================================================================================
# Lauf d9988b7d, Feedback zu `performance_score`: „hier ergaenzen ob das videoziel mit dem video
# erreicht werden kann oder es am ziel vorbeigeht. wenn es vorbeigeht bitte eine empfehlung geben
# wie man das video gestalten muesste, das es zum ziel passt. erklaerung bitte beispielhaft an dem
# inhalt des videos"
#
# `funnel_wirkung` sagt bisher nur, WAS das Video tut. Was fehlt, ist WAS ZU TUN WAERE — und zwar
# am Inhalt dieses Videos, nicht allgemein.
# =====================================================================================

def test_die_empfehlung_ist_ein_eigenes_feld_mit_leerem_default():
    """Altlaeufe kennen das Feld nicht — Default leer, sonst bricht das Laden der 89 Laeufe."""
    from models.analyst import AnalystEvaluationV2
    assert AnalystEvaluationV2().funnel_wirkung_empfehlung == ""


def test_die_empfehlung_ueberlebt_die_formpruefung():
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "TOFU"
    ev.funnel_wirkung_grund = "Kurz und breit angesprochen."
    ev.funnel_wirkung_empfehlung = "Erklär ab Sekunde 8 kürzer und stell vorne eine Frage."
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel="MOFU"))
    assert out.funnel_wirkung_empfehlung


def test_ohne_abweichung_gibt_es_nichts_zu_empfehlen():
    """Das Frontend zeigt den Block nur bei Abweichung. Steht die Empfehlung trotzdem da, ist sie
    unsichtbarer Ballast im gespeicherten Lauf — und im Chat eine Quelle fuer Widersprueche."""
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "MOFU"
    ev.funnel_wirkung_empfehlung = "Irgendwas, das das Modell trotzdem geschrieben hat."
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel="MOFU"))
    assert out.funnel_wirkung_empfehlung == ""


def test_ohne_belastbare_wirkung_faellt_auch_die_empfehlung_weg():
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "Mischung"
    ev.funnel_wirkung_empfehlung = "Mach es kürzer."
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel="MOFU"))
    assert out.funnel_wirkung_empfehlung == ""


def test_ohne_ziel_bleibt_die_empfehlung_unangetastet():
    """V2 ist die eingefrorene Vergleichsbasis — die Nachbearbeitung darf dort nichts aendern."""
    from services.analyst_eval import pruefe_funnel_wirkung
    ev = _eval_mit_scores()
    ev.funnel_wirkung = "TOFU"
    ev.funnel_wirkung_empfehlung = "Bleibt stehen."
    out = pruefe_funnel_wirkung(ev, _result_ziel(ziel=""))
    assert out.funnel_wirkung_empfehlung == "Bleibt stehen."


def test_die_empfehlung_steht_im_v3_vertrag_und_nicht_im_v2():
    from services.analyst_eval import build_system_prompt
    assert '\n  "funnel_wirkung_empfehlung":' in build_system_prompt(ziel="MOFU")
    assert "funnel_wirkung_empfehlung" not in build_system_prompt()


def test_die_empfehlung_kommt_aus_dem_eroeffnungs_call():
    """Vier-Stellen-Falle: ohne TEIL_FELDER-Eintrag erscheint das Feld in KEINEM Call und faellt
    beim Zusammenfuehren still weg."""
    from services.analyst_eval import TEIL_FELDER, build_system_prompt, merge_teilergebnisse
    assert "funnel_wirkung_empfehlung" in TEIL_FELDER["eroeffnung"]
    assert '\n  "funnel_wirkung_empfehlung":' in build_system_prompt(teil="eroeffnung", ziel="MOFU")
    # Nur die VERTRAGS-Zeile darf im Handwerks-Call fehlen: Der Abschnitt „Videoziel" steht in
    # beiden Calls (ABSCHNITT_ZUORDNUNG: BEIDE), der Feldname taucht dort also als Prosa auf.
    assert '\n  "funnel_wirkung_empfehlung":' not in build_system_prompt(teil="handwerk", ziel="MOFU")
    eroeffnung = _eval_mit_scores()
    eroeffnung.funnel_wirkung_empfehlung = "Kürze den Erklärteil."
    out = merge_teilergebnisse(eroeffnung, _eval_mit_scores())
    assert out.funnel_wirkung_empfehlung == "Kürze den Erklärteil."


def test_der_skill_verlangt_die_empfehlung_am_inhalt_des_videos():
    """„erklaerung bitte beispielhaft an dem inhalt des videos" — ein allgemeiner Ratschlag
    („mach es kuerzer") ist genau das, was der Nutzer nicht wollte."""
    text = " ".join(_v3_text().split())
    assert "funnel_wirkung_empfehlung" in text
    assert "am INHALT dieses Videos" in text
    assert "Stimmen Ziel und Wirkung überein, bleibt das Feld leer" in text


# =====================================================================================
# Lauf d9988b7d, Feedback zu `zielgruppe`: „falls die zielgruppen und branddaten vorhanden sind,
# soll hier ergaenzt werden, inwiefern das video relevant fuer die zielgruppe ist."
#
# Die Brand-/Zielgruppen-Datei gibt es noch NICHT. Vorbereitet wird nur das Feld: Solange dem
# Modell keine solchen Daten vorliegen, bleibt es leer und unsichtbar — das ist der gewollte
# Zustand, kein Fehler.
# =====================================================================================

def test_zielgruppen_relevanz_ist_ein_eigenes_feld_mit_leerem_default():
    from models.analyst import AnalystEvaluationV2
    assert AnalystEvaluationV2().zielgruppen_relevanz == ""


def test_zielgruppen_relevanz_steht_im_v3_vertrag_und_nicht_im_v2():
    from services.analyst_eval import build_system_prompt
    assert '\n  "zielgruppen_relevanz":' in build_system_prompt(ziel="MOFU")
    assert "zielgruppen_relevanz" not in build_system_prompt()


def test_zielgruppen_relevanz_kommt_aus_dem_eroeffnungs_call():
    """Vier-Stellen-Falle — das Feld gehoert zu `zielgruppe` und damit in denselben Call."""
    from services.analyst_eval import TEIL_FELDER, build_system_prompt, merge_teilergebnisse
    assert "zielgruppen_relevanz" in TEIL_FELDER["eroeffnung"]
    assert '\n  "zielgruppen_relevanz":' in build_system_prompt(teil="eroeffnung", ziel="MOFU")
    assert '\n  "zielgruppen_relevanz":' not in build_system_prompt(teil="handwerk", ziel="MOFU")
    eroeffnung = _eval_mit_scores()
    eroeffnung.zielgruppen_relevanz = "Trifft die Zielgruppe, weil …"
    out = merge_teilergebnisse(eroeffnung, _eval_mit_scores())
    assert out.zielgruppen_relevanz == "Trifft die Zielgruppe, weil …"


def test_der_skill_bindet_die_relevanz_an_vorliegende_daten():
    """Ohne die Bedingung raet das Modell die Zielgruppen-Passung aus dem Video zusammen — das
    waere eine zweite, schwaechere Fassung von `zielgruppe`."""
    text = " ".join(_v3_text().split())
    assert "zielgruppen_relevanz" in text
    assert "Liegen dir keine solchen Daten vor, bleibt das Feld LEER" in text


# =====================================================================================
# Auftreten des Protagonisten vor der Kamera (Vorgabe Chris, 2026-09-13)
#
# „Bei der Kategorie Auftreten Bild und Ton ergaenze bitte einen Score fuer das Auftreten der
# Person vor der Kamera, also des Protagonisten. Darin kannst du auch die Blickrichtung zum
# Beispiel mit integrieren. Du solltest das energetische Auftreten der Person auch bewerten. Das
# soll aber nur bewertbar sein, wenn der Analyst Daten zur Personal Brand hat und zur Person, also
# zum Protagonisten. Denn nur dann kann auch bewertet werden, ob die Energie gut oder schlecht ist.
# Falls keine Info, bitte nur die Energie beschreiben, aber nicht bewerten."
#
# Die Brand-/Zielgruppen-Datei gibt es noch NICHT — dieselbe Lage wie bei `zielgruppen_relevanz`.
# Der Zustand „beschreiben, nicht bewerten" ist deshalb der GEWOLLTE Normalzustand, kein Fehler.
# =====================================================================================

def test_protagonist_auftreten_bleibt_ohne_daten_unbewertet():
    """Der Kern der Vorgabe: ohne Daten zur Person kein Score. Eine Zahl ohne Massstab waere
    geraten — ob ruhige Sachlichkeit passend oder zu flach ist, haengt an der Person."""
    from models.analyst import ProtagonistEval
    p = ProtagonistEval()
    assert p.score is None
    assert p.beschreibung == ""
    assert p.probleme == [] and p.hinweise == []


def test_protagonist_eval_erbt_von_score_probleme():
    """Weniger Code: `score`, `probleme`, `hinweise` und der Objekt-zu-String-Validator stehen
    schon in ScoreProbleme. Neu ist nur `beschreibung` und der None-Default fuer `score`."""
    from models.analyst import ProtagonistEval, ScoreProbleme
    assert issubclass(ProtagonistEval, ScoreProbleme)
    # Der geerbte Validator muss weiter greifen (Objekt-Muster faerbt auf Nachbarfelder ab).
    assert ProtagonistEval(probleme=[{"text": "wirkt abgelesen"}]).probleme == ["wirkt abgelesen"]


def test_protagonist_auftreten_ist_ein_feld_der_bewertung():
    from models.analyst import AnalystEvaluationV2, ProtagonistEval
    assert isinstance(AnalystEvaluationV2().protagonist_auftreten, ProtagonistEval)


def test_protagonist_auftreten_ist_in_allen_drei_zielen_gewichtet():
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL, ZIELE
    for ziel in ZIELE:
        assert SCORE_GEWICHTE_JE_ZIEL[ziel]["protagonist_auftreten"] > 0, ziel


def test_das_auftreten_wiegt_bei_mofu_und_bofu_mehr_als_bei_tofu():
    """KB 11: Bei TOFU traegt der Einstieg (massentauglicher Hook, Scroll-Stopp), die Person ist
    oft gar nicht der Punkt. Bei MOFU entsteht Vertrauen ueber die Person („Protagonisten-Story =
    Beziehungsvertrauen/Nahbarkeit"), bei BOFU verkauft der Creator sich selbst."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL
    tofu = SCORE_GEWICHTE_JE_ZIEL["TOFU"]["protagonist_auftreten"]
    assert SCORE_GEWICHTE_JE_ZIEL["MOFU"]["protagonist_auftreten"] > tofu
    assert SCORE_GEWICHTE_JE_ZIEL["BOFU"]["protagonist_auftreten"] > tofu


def test_das_gewicht_fuer_das_auftreten_kam_aus_sprechqualitaet_und_aesthetik():
    """Die Dimension nimmt sich ihre Punkte dort, wo ihre Urteile bisher mitliefen —
    `sprechqualitaet` (Ausdruckskraft) und `visuelle_aesthetik` (Blickrichtung).

    Frueher prueste dieser Test zusaetzlich, dass die Kategorie „Auftreten" ihr Gesamtgewicht
    behaelt (23/19/18). Das gilt seit Stufe 3 nicht mehr: Fuer `einblendungen` und `soundeffekte`
    gibt „Auftreten" bewusst 2 Punkte je Ziel an „Editing" ab, weil die Kategorie mit 10 von 100
    zu klein fuer drei Dimensionen war. Die Herkunft des Auftreten-Gewichts bleibt davon unberuehrt
    und ist das, was dieser Test sichert.
    """
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL as G
    # `sprechqualitaet` und `visuelle_aesthetik` liegen unter ihrem Stand vor der Dimension
    # (6/7/6 bzw. 8/3/3 waren die Ausgangswerte vor dem Abzug).
    assert G["TOFU"]["sprechqualitaet"] <= 6 and G["MOFU"]["sprechqualitaet"] <= 7
    assert G["MOFU"]["visuelle_aesthetik"] <= 3 and G["BOFU"]["visuelle_aesthetik"] <= 3
    # Audioqualitaet hat nie ein Urteil zur Person getragen — sie beurteilt die Aufnahme.
    # Sie darf sich nur durch spaetere Umverteilungen aendern, nicht durch das Auftreten.
    assert G["TOFU"]["audioqualitaet"] >= G["MOFU"]["audioqualitaet"]


def test_protagonist_auftreten_gehoert_zur_kategorie_auftreten():
    from models.analyst import KATEGORIEN
    assert "protagonist_auftreten" in KATEGORIEN["auftreten"]


def test_dimensions_scores_kennt_den_protagonisten():
    """Vier-Stellen-Falle: Ohne Eintrag hier zaehlt die Dimension weder fuer den Score noch fuer
    die Priorisierung der Handlungsempfehlungen."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import dimensions_scores
    ev = AnalystEvaluationV2()
    assert "protagonist_auftreten" in dimensions_scores(ev)
    ev.protagonist_auftreten.score = 4
    assert dimensions_scores(ev)["protagonist_auftreten"] == 4


def test_ohne_branddaten_verschiebt_die_neue_dimension_den_gesamtscore_nicht():
    """Solange `score` None ist, ueberspringt berechne_performance_score die Dimension und
    verteilt ihr Gewicht proportional auf den Rest. Der Gesamtscore ist deshalb HEUTE exakt
    derselbe wie ohne die neue Dimension — sie schaltet sich erst mit den Branddaten ein."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL
    from services.analyst_eval import (DIMENSION_MINIMUM, berechne_performance_score,
                                       dimensions_scores)
    ev = _eval_mit_scores(sprech=4, text=3, visuell=2, spannung=5, struktur=3,
                          sprechq=4, aesthetik=2, schnitt=3)
    assert ev.protagonist_auftreten.score is None
    for ziel in ("TOFU", "MOFU", "BOFU"):
        ohne = {k: v for k, v in SCORE_GEWICHTE_JE_ZIEL[ziel].items()
                if k != "protagonist_auftreten"}
        summe = gesamt = 0.0
        for name, score in dimensions_scores(ev).items():
            minimum = DIMENSION_MINIMUM.get(name, 1)
            if score is None or score < minimum or not ohne.get(name, 0):
                continue
            summe += ohne[name] * (score - minimum) / (5 - minimum)
            gesamt += ohne[name]
        erwartet = round(summe / gesamt * 100)
        assert berechne_performance_score(ev, ziel=ziel).performance_score == erwartet, ziel


def test_mit_branddaten_zaehlt_der_protagonist_fuer_den_score():
    """Die Gegenprobe: Sobald ein Score da ist, muss er den Gesamtscore auch bewegen."""
    from services.analyst_eval import berechne_performance_score
    ohne = berechne_performance_score(_eval_mit_scores(), ziel="MOFU").performance_score
    ev = _eval_mit_scores()
    ev.protagonist_auftreten.score = 1
    assert berechne_performance_score(ev, ziel="MOFU").performance_score < ohne


def test_benannte_probleme_deckeln_auch_den_protagonisten_score():
    """Dieselbe Regel wie bei sprechqualitaet/visuelle_aesthetik/audioqualitaet: Wer Maengel
    benennt, darf keine 5 vergeben (Lauf 041770c1)."""
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _eval_mit_scores()
    ev.protagonist_auftreten.score = 5
    ev.protagonist_auftreten.probleme = ["liest sichtbar ab", "Betonung traegt den Kern nicht"]
    assert deckle_score_auf_probleme(ev).protagonist_auftreten.score == 3


def test_energie_und_blick_bleiben_eigene_felder():
    """Die beiden Einzelurteile FLIESSEN in die neue Dimension ein, sie werden nicht ersetzt:
    erzwinge_blick_empfehlung und baue_effekt_schritt haengen an ihnen."""
    from models.analyst import AnalystEvaluationV2, BlickEval, EnergieEval
    ev = AnalystEvaluationV2()
    assert isinstance(ev.energie, EnergieEval)
    assert isinstance(ev.blickkontakt, BlickEval)
    from services.analyst_eval import erzwinge_blick_empfehlung
    ev.blickkontakt.urteil = "abgelesen"
    assert erzwinge_blick_empfehlung(ev).empfehlungen


def test_v3_vertrag_kennt_protagonist_auftreten_und_v2_nicht():
    """V2 ist die eingefrorene Vergleichsbasis (89 Laeufe, A/B-Regressionstest)."""
    from services.analyst_eval import build_system_prompt
    assert '\n  "protagonist_auftreten":' in build_system_prompt(ziel="MOFU")
    assert "protagonist_auftreten" not in build_system_prompt()


def test_protagonist_auftreten_kommt_aus_dem_handwerk_call():
    """Auftreten gehoert zum Handwerks-Call — dort liegen auch energie und blickkontakt."""
    from services.analyst_eval import TEIL_FELDER, build_system_prompt, merge_teilergebnisse
    assert "protagonist_auftreten" in TEIL_FELDER["handwerk"]
    assert '\n  "protagonist_auftreten":' in build_system_prompt(teil="handwerk", ziel="MOFU")
    assert '\n  "protagonist_auftreten":' not in build_system_prompt(teil="eroeffnung", ziel="MOFU")
    handwerk = _eval_mit_scores()
    handwerk.protagonist_auftreten.beschreibung = "Spricht ruhig, Blick in der Linse."
    out = merge_teilergebnisse(_eval_mit_scores(), handwerk)
    assert out.protagonist_auftreten.beschreibung == "Spricht ruhig, Blick in der Linse."


def test_der_vertrag_verlangt_die_beschreibung_auch_ohne_score():
    """Ohne diesen Halbsatz liefert das Modell bei fehlenden Daten gar nichts — und der Fall
    „nur beschreiben" waere fuer den Nutzer unsichtbar."""
    from services.analyst_eval import build_system_prompt
    zeile = [z for z in build_system_prompt(ziel="MOFU").splitlines()
             if z.startswith('  "protagonist_auftreten":')][0]
    assert "null" in zeile
    assert "beschreibung" in zeile


def test_der_skill_bindet_den_score_an_daten_zur_person():
    """Scharf formuliert, sonst vergibt das Modell trotzdem einen Score — dieselbe Lektion wie
    bei `zielgruppen_relevanz`."""
    text = " ".join(_v3_text().split())
    assert "protagonist_auftreten" in text
    assert ("Liegen dir keine Angaben zur Person oder zur Marke vor, setzt du `score` auf null "
            "und beschreibst nur, was du siehst und hörst. Rate nicht.") in text


def test_der_skill_definiert_energie_als_ausdruckskraft_nicht_als_lautstaerke():
    """Vorgabe Chris: „Bei Energie meine ich, dass eine Person zum Beispiel eine starke
    Ausdruckskraft hat, eine starke gute Betonung, ein emotionales Statement auch wirklich gut
    emotional rueberbringen kann, energetisch." Ohne die Negativabgrenzung misst das Modell
    Lautstaerke und Tempo — beides ist nicht gemeint."""
    text = " ".join(_v3_text().split())
    assert "Ausdruckskraft" in text
    assert "nicht Lautstärke und nicht Tempo" in text


def test_der_skill_zieht_die_blickfuehrung_in_die_dimension():
    """Vorgabe Chris: „Darin kannst du auch die Blickrichtung zum Beispiel mit integrieren."
    Die Regeln aus KB 10.2 gelten unveraendert weiter, inklusive der Reaction-Ausnahme (KB 12)."""
    text = " ".join(_v3_text().split())
    start = text.index("## Auftreten des Protagonisten")
    abschnitt = text[start:text.index("## ", start + 5)]
    assert "Blick" in abschnitt
    assert "blickkontakt" in abschnitt


def test_protagonist_auftreten_hat_score_anker_ueber_fuenf_stufen():
    """Ohne Anker urteilt das Modell nachweislich zur Mitte (der 3er-Befund aus KB 8.5)."""
    block = _ankerblock("protagonist_auftreten.score")
    for stufe in ("**5**", "**4**", "**3**", "**2**", "**1**"):
        assert stufe in block, stufe


def test_die_anker_gelten_nur_bei_vorliegenden_daten():
    block = _ankerblock("protagonist_auftreten.score")
    assert "nur, wenn dir Angaben zur Person oder zur Marke vorliegen" in block


def test_staerken_duerfen_sich_auf_den_protagonisten_beziehen():
    """filtere_staerken ordnet ueber `betrifft` zu — fehlt der Name im Vertrag, kann ein starkes
    Auftreten nie gelobt werden."""
    from services.analyst_eval import build_system_prompt
    zeile = [z for z in build_system_prompt(teil="handwerk", ziel="MOFU").splitlines()
             if z.startswith('  "staerken":')][0]
    assert "protagonist_auftreten" in zeile


# --- Stufe 3: Skript, Einblendungen, Soundeffekte (Vorgabe Chris, 2026-09-13) -------------------

def test_die_drei_neuen_dimensionen_sind_gewichtet_und_zaehlen():
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL as G, KATEGORIEN
    from services.analyst_eval import dimensions_scores
    from models.analyst import AnalystEvaluationV2
    for d in ("skript", "einblendungen", "soundeffekte"):
        assert all(d in G[z] for z in G), d
        assert d in dimensions_scores(AnalystEvaluationV2()), d
    assert "skript" in KATEGORIEN["mittelteil"]
    assert "einblendungen" in KATEGORIEN["editing"]
    assert "soundeffekte" in KATEGORIEN["editing"]


def test_skript_wiegt_bei_mofu_und_bofu_mehr_als_bei_tofu():
    """KB 11: Bei TOFU traegt der Einstieg, bei MOFU/BOFU der Inhalt (Tiefe, Pitch)."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL as G
    assert G["MOFU"]["skript"] > G["TOFU"]["skript"]
    assert G["BOFU"]["skript"] > G["TOFU"]["skript"]


def test_einblendungen_wiegen_mehr_als_soundeffekte():
    """KB 5 nennt Einblendungen einen mehrfach wirkenden Hebel, Soundeffekte Feinschliff."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL as G
    for z in G:
        assert G[z]["einblendungen"] > G[z]["soundeffekte"], z


def test_editing_ist_nicht_laenger_die_kleinste_kategorie():
    """Mit drei Dimensionen waeren 10 von 100 Punkten zu wenig — die Kategorie waechst um 4."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL as G, KATEGORIEN
    for z, mindestens in (("TOFU", 14), ("MOFU", 13), ("BOFU", 12)):
        assert sum(G[z][d] for d in KATEGORIEN["editing"]) == mindestens, z


def test_der_v3_vertrag_kennt_die_drei_neuen_felder_der_v2_nicht():
    from services.analyst_eval import build_system_prompt
    h = build_system_prompt(teil="handwerk", ziel="MOFU")
    v2 = build_system_prompt()
    for feld in ('"einblendungen_eval"', '"soundeffekte"', '"skript"'):
        assert feld in h, feld
        assert feld not in v2, feld


def test_skript_ist_gegen_struktur_und_spannungsbogen_abgegrenzt():
    """Ohne Abgrenzung wandern dieselben Befunde zwischen den drei Dimensionen hin und her."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    assert "die FORM" in p and "der VERLAUF" in p and "der INHALT" in p


def test_soundeffekte_sind_gegen_audioqualitaet_abgegrenzt():
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    assert "ABGRENZUNG" in p and "AUFNAHME" in p and "GESTALTUNG" in p


def test_schnitt_pacing_traegt_die_einblendungen_nicht_mehr():
    """Extraktion: Was in einer eigenen Dimension bewertet wird, gehoert nicht mehr in die alte."""
    from services.analyst_eval import build_system_prompt
    p = build_system_prompt(teil="handwerk", ziel="MOFU")
    schnitt = p[p.index("## Schnitt & Pacing"):p.index("## Einblendungen")]
    assert "mehrfach wirkender Hebel" not in schnitt
    assert "einblendungen_eval" in schnitt      # Querverweis steht


# ---------- Stufe 4: Lob UND Kritik je Dimension (Vorgabe Chris, 2026-09-13) ----------
#
# Bis hierher hatte jede Dimension nur eine Textseite, und die war Kritik. Der Aufklapper im
# Frontend zeigt beide Seiten — dafuer braucht jede der sechzehn Dimensionen ein eigenes
# `positiv`. Die Kritikseite (`probleme`, `kommentar`, `grund`, `maengel`) bleibt, wie sie ist.


def test_jede_bewertete_dimension_hat_ein_positiv_feld():
    """Sechzehn Dimensionen, sechzehn Lob-Felder — `positiv_felder` und `dimensions_scores`
    muessen dieselben Namen kennen. Laeuft das auseinander, faellt eine Dimension still aus dem
    Aufklapper und aus dem Filter heraus."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import dimensions_scores, positiv_felder
    ev = AnalystEvaluationV2()
    assert set(positiv_felder(ev)) == set(dimensions_scores(ev))
    assert len(positiv_felder(ev)) == 16


def test_positiv_ist_ueberall_leer_vorbelegt():
    """Default "" — sonst laedt keiner der gespeicherten Altlaeufe mehr."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import positiv_felder
    ev = AnalystEvaluationV2()
    for name, (obj, attr) in positiv_felder(ev).items():
        assert getattr(obj, attr) == "", name


def test_die_drei_hook_ebenen_haben_eigene_lob_felder():
    """Sie liegen in EINER Klasse — ein gemeinsames `positiv` wuerde drei Urteile zu einem
    verschmelzen."""
    from models.analyst import HookEval
    h = HookEval()
    assert h.sprech_hook_positiv == ""
    assert h.text_hook_positiv == ""
    assert h.visuell_hook_positiv == ""


def test_untertitel_haben_zwei_lob_felder():
    """Eine Klasse, zwei Dimensionen: „gibt es sie" und „wie sind sie gemacht" sind zwei Urteile,
    genau wie `kommentar` und `maengel` auf der Kritikseite schon zwei sind."""
    from models.analyst import UntertitelEval
    u = UntertitelEval()
    assert u.positiv == ""
    assert u.gestaltung_positiv == ""


def test_v3_vertrag_verlangt_positiv_je_dimension():
    """Der Vertrag ist die Anweisung, der das Modell am zuverlaessigsten folgt — eine reine
    Skill-Bitte reicht nicht (dieselbe Lektion wie bei P2 in docs/offene-fixes-analyst.md)."""
    from services.analyst_eval import build_system_prompt
    e = build_system_prompt(teil="eroeffnung", ziel="MOFU")
    h = build_system_prompt(teil="handwerk", ziel="MOFU")
    for feld in ('"sprech_hook_positiv"', '"text_hook_positiv"', '"visuell_hook_positiv"'):
        assert feld in e, feld
    # Die uebrigen dreizehn liegen im Handwerk-Call und heissen alle schlicht "positiv" —
    # gezaehlt wird deshalb, statt auf einen Namen zu pruefen.
    assert h.count('"positiv"') == 12, h.count('"positiv"')
    assert '"gestaltung_positiv"' in h


def test_v2_vertrag_kennt_positiv_nicht():
    """V2 ist die eingefrorene Vergleichsbasis von 89 Laeufen."""
    from services.analyst_eval import build_system_prompt, OUTPUT_SCHEMA
    assert "positiv" not in OUTPUT_SCHEMA.replace("positive Aspekte", "")
    for teil in (None, "eroeffnung", "handwerk"):
        p = build_system_prompt(teil=teil)
        assert "_positiv" not in p
        assert '"positiv"' not in p


def test_v3_skill_bindet_die_menge_an_lob_an_den_score():
    """„Je geringer der Score, desto weniger positives Feedback" — pruefbar formuliert, nicht als
    Bitte. Der V2-Skill bleibt unangetastet."""
    from services.analyst_eval import SKILL_PATH_V3, load_skill_body
    v3 = load_skill_body(SKILL_PATH_V3)
    v2 = load_skill_body()
    assert "Lob und Kritik" in v3
    assert "Lob und Kritik" not in v2
    assert "positiv" in v3


def test_lob_wird_bei_score_1_und_2_verworfen():
    """Code-Schwelle statt Prompt-Bitte: Bei 1 oder 2 gibt es nichts ehrlich zu loben. Ohne diese
    Schwelle wird die Regel zu Boilerplate (P2 in docs/offene-fixes-analyst.md)."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import filtere_positiv
    ev = AnalystEvaluationV2()
    ev.schnitt_pacing.score = 2
    ev.schnitt_pacing.positiv = "Die Schnitte sitzen sauber."
    ev.hook.sprech_hook_score = 1
    ev.hook.sprech_hook_positiv = "Immerhin ist die Kamera an."
    filtere_positiv(ev, ziel="MOFU")
    assert ev.schnitt_pacing.positiv == ""
    assert ev.hook.sprech_hook_positiv == ""


def test_lob_bleibt_ab_score_3_stehen():
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import filtere_positiv
    ev = AnalystEvaluationV2()
    ev.spannungsbogen.score = 3
    ev.spannungsbogen.positiv = "Der Einstieg zieht."
    ev.visuelle_aesthetik.score = 5
    ev.visuelle_aesthetik.positiv = "Licht und Bildaufbau sitzen."
    filtere_positiv(ev, ziel="MOFU")
    assert ev.spannungsbogen.positiv == "Der Einstieg zieht."
    assert ev.visuelle_aesthetik.positiv == "Licht und Bildaufbau sitzen."


def test_nicht_bewertbare_dimension_behaelt_ihr_lob():
    """score None heisst „nicht bewertbar", nicht „schlecht" — ein Lob dazu ist nicht gedeckelt."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import filtere_positiv
    ev = AnalystEvaluationV2()
    ev.protagonist_auftreten.score = None
    ev.protagonist_auftreten.positiv = "Ruhige, klare Praesenz."
    filtere_positiv(ev, ziel="MOFU")
    assert ev.protagonist_auftreten.positiv == "Ruhige, klare Praesenz."


def test_ohne_ziel_wird_kein_lob_verworfen():
    """v2 und Altlaeufe: Ein gespeichertes Ergebnis darf beim erneuten Anzeigen nicht weniger
    enthalten als beim ersten Mal — dieselbe Regel wie bei filtere_staerken."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import filtere_positiv
    ev = AnalystEvaluationV2()
    ev.cta.score = 1
    ev.cta.positiv = "Der Abschluss ist freundlich."
    filtere_positiv(ev, ziel="")
    assert ev.cta.positiv == "Der Abschluss ist freundlich."


def test_nachbearbeiten_wendet_den_lob_filter_an():
    """Ende zu Ende — der Filter muss NACH den Score-Deckelungen laufen, sonst ueberlebt Lob zu
    einer Dimension, die danach abgesenkt wird."""
    from models.analyst import AnalystEvaluationV2, AnalystResult
    from services.analyst_eval import nachbearbeiten
    ev = AnalystEvaluationV2()
    ev.soundeffekte.score = 2
    ev.soundeffekte.positiv = "Die Musik passt zum Thema."
    result = AnalystResult(id="t", filename="t.mp4", duration_sec=30.0, scene_count=0,
                           scenes=[], gewaehltes_ziel="MOFU", gewaehltes_format="Talking Head")
    nachbearbeiten(ev, result)
    assert ev.soundeffekte.positiv == ""


def test_eine_spaetere_score_korrektur_zieht_das_lob_mit():
    """setze_untertitel_scores setzt den Score erst kurz vor Schluss auf 1 — das Modell konnte
    davon nichts wissen und hat sein Lob schon geschrieben. Genau dafuer steht filtere_positiv
    HINTER allen Score-Korrekturen und nicht davor."""
    from models.analyst import AnalystEvaluationV2, AnalystResult
    from services.analyst_eval import nachbearbeiten
    ev = AnalystEvaluationV2()
    ev.untertitel.vorhanden = False
    ev.untertitel.score = 4
    ev.untertitel.positiv = "Die Untertitel sitzen sauber unter dem Gesicht."
    result = AnalystResult(id="t", filename="t.mp4", duration_sec=30.0, scene_count=0, scenes=[],
                           transcript="Hier wird durchgehend gesprochen, aber ohne Untertitel.",
                           gewaehltes_ziel="MOFU", gewaehltes_format="Talking Head")
    nachbearbeiten(ev, result)
    assert ev.untertitel.score == 1
    assert ev.untertitel.positiv == ""


# ---------- Frontend: aus dem Hover-Tooltip wird ein Aufklapper ----------

def _appjsx():
    import pathlib
    return pathlib.Path("static/app.jsx").read_text(encoding="utf-8")


def test_scorechip_ist_ein_aufklapper_ohne_eigenes_javascript():
    """<details>/<summary> wie die Kategorie-Aufklapper: kein State, tastaturbedienbar."""
    import re
    block = re.search(r"function ScoreChip\(.*?\n\}\n", _appjsx(), re.S).group()
    assert "<details" in block and "<summary" in block
    assert "Das ist gut" in block
    assert "Das kannst du besser machen" in block
    assert "useState" not in block


def test_der_hover_tooltip_ist_weg():
    """Er war die Stelle, die ersetzt wird — bleibt er stehen, steht dieselbe Information zweimal."""
    quelle = _appjsx()
    assert "sc-tip" not in quelle
    assert "sc-info" not in quelle
    import pathlib
    css = pathlib.Path("static/styles.css").read_text(encoding="utf-8")
    assert ".sc-tip" not in css and ".sc-info" not in css


def test_kein_fuelltext_wo_nichts_zu_sagen_ist():
    """„Keine Auffaelligkeiten" war der Platzhalter des Tooltips. Im Aufklapper faellt die
    Ueberschrift ganz weg, wenn die Seite leer ist."""
    quelle = _appjsx()
    assert "Keine Auffälligkeiten" not in quelle
    assert "problemeDetail" not in quelle


def test_die_drei_neuen_dimensionen_haben_einen_chip():
    """skript, einblendungen und soundeffekte sind im Backend gebaut, im Frontend fehlten sie."""
    import re
    block = re.search(r"function kategorieChips\(.*?\n\}\n", _appjsx(), re.S).group()
    for feld in ("ev.skript", "ev.einblendungen_eval", "ev.soundeffekte"):
        assert feld in block, feld


def test_jeder_chip_bekommt_beide_seiten():
    """Lob und Kritik werden getrennt uebergeben — sonst kann der Aufklapper sie nicht trennen."""
    import re
    block = re.search(r"function kategorieChips\(.*?\n\}\n", _appjsx(), re.S).group()
    assert block.count("positiv:") >= 16
    assert block.count("kritik:") >= 16
    assert "detail:" not in block


def test_die_bestehenden_feedback_namen_bleiben():
    """An ihnen haengt die gesammelte Feedback-Historie."""
    import re
    block = re.search(r"function kategorieChips\(.*?\n\}\n", _appjsx(), re.S).group()
    for feld in ("hook.sprech", "hook.text", "hook.visuell", "spannungsbogen", "struktur",
                 "untertitel", "cta", "schnitt_pacing", "untertitel_gestaltung",
                 "sprechqualitaet", "visuelle_aesthetik", "audioqualitaet",
                 "protagonist_auftreten"):
        assert f'"{feld}"' in block, feld


def test_die_kategorie_zuordnung_kennt_alle_dimensionen():
    """Spiegel von KATEGORIEN in models/analyst.py — laeuft sie auseinander, landen Staerken zu
    den drei neuen Dimensionen in der Sammelgruppe ohne Ueberschrift."""
    import re
    from models.analyst import KATEGORIEN
    block = re.search(r"const DIMENSION_ZU_KATEGORIE = \{.*?\n\};", _appjsx(), re.S).group()
    for dims in KATEGORIEN.values():
        for d in dims:
            assert f"{d}:" in block, d


def test_betrifft_enum_kennt_alle_dimensionen():
    """Fehlte ein Name, hatte das Modell fuer diese Dimension keinen zulaessigen `betrifft`-Wert:
    Die Empfehlung bekam keine Dimension, die Sortierung nach Schwere griff nicht und Lob zu dieser
    Dimension fiel durch `filtere_staerken`. V2 bleibt bei seinen sieben Namen."""
    from models.analyst import SCORE_GEWICHTE_JE_ZIEL
    from services.analyst_eval import OUTPUT_SCHEMA, v3_schema

    schema = v3_schema()
    zeilen = [z for z in schema.splitlines() if '"betrifft"' in z]
    assert len(zeilen) == 2, "betrifft steht in empfehlungen UND staerken"
    for name in SCORE_GEWICHTE_JE_ZIEL["TOFU"]:
        for zeile in zeilen:
            assert name in zeile, f"{name} fehlt in: {zeile[:60]}"
    assert "protagonist_auftreten" not in OUTPUT_SCHEMA, "V2-Vertrag bleibt unberuehrt"
