"""Feedback aus Lauf 411b3493 (Reel 04, MOFU, prompt_version 2026-09-13f).

Chris' Rueckmeldungen, jeweils woertlich in der Docstring der zugehoerigen Regel.
"""
import pytest

from models.analyst import AnalystEvaluationV2, AnalystResult, QualityMetrics, SpeechStats


def _result(ziel="MOFU", duration=24.92, sprech_dauer=24.68, sprechbeginn=0.0,
            lufs=-14.0, peak=-2.0):
    return AnalystResult(
        id="x", filename="c.mp4", duration_sec=duration, scene_count=0, scenes=[],
        gewaehltes_ziel=ziel,
        speech_stats=SpeechStats(wort_anzahl=80, sprech_dauer_sec=sprech_dauer, wpm=180,
                                 filler_count=0, filler_words=[], pausen_count=0,
                                 laengste_pause_sec=0.0, sprechbeginn_sec=sprechbeginn),
        quality_metrics=QualityMetrics(lufs_integrated=lufs, true_peak_db=peak),
    )


# --- 1) Nachlauf: kein Mangel, solange er unter 2 Sekunden bleibt -------------------------------

def test_kurzer_nachlauf_erzeugt_keinen_schritt():
    """„das video soll nach dem peak oder cta direkt enden ohne nachlauf. wenn aber 1-2 sekunden
    nachlauf waere ist es kein grund das als mangel zu sehen."" Gemessen waren 0,24 s."""
    from services.analyst_eval import baue_videoende_schritt
    e = baue_videoende_schritt(AnalystEvaluationV2(), _result())
    assert e.empfehlungen == []


def test_kein_nachlauf_erzeugt_keinen_schritt():
    from services.analyst_eval import baue_videoende_schritt
    e = baue_videoende_schritt(AnalystEvaluationV2(), _result(duration=24.68))
    assert e.empfehlungen == []


def test_langer_nachlauf_erzeugt_weiterhin_einen_schritt():
    """„gebe nur eine handlungsempfehlung, wenn das video wirklich laenger als 1-2 sekunden
    inhaltslosen nachlauf hat."""
    from services.analyst_eval import baue_videoende_schritt
    e = baue_videoende_schritt(AnalystEvaluationV2(), _result(duration=30.0))
    assert len(e.empfehlungen) == 1
    assert "kürze" in e.empfehlungen[0].anweisung.lower()


# --- 2) Lob nur, wo der Score der GENANNTEN Dimension es deckt -----------------------------------

def test_lob_zu_einer_schwachen_dimension_faellt_raus():
    """Lauf 411b3493: `audioqualitaet.score` war 2, und daneben stand die Staerke „Die
    Audioqualitaet ist auf absolutem Studio-Niveau." Sie ueberlebte, weil der Filter die
    KATEGORIE geprueft hat (sprechqualitaet 5 in derselben Kategorie) statt die Dimension."""
    from services.analyst_eval import filtere_staerken
    e = AnalystEvaluationV2(
        audioqualitaet={"score": 2}, sprechqualitaet={"score": 5},
        staerken=[{"text": "Studio-Niveau.", "betrifft": "audioqualitaet"},
                  {"text": "Klar gesprochen.", "betrifft": "sprechqualitaet"}],
    )
    e = filtere_staerken(e, ziel="MOFU")
    assert [s.betrifft for s in e.staerken] == ["sprechqualitaet"]


# --- 3) Die Kritikseite ist Kritik, nicht noch ein Lob -------------------------------------------

def test_bei_score_5_bleibt_die_kritikseite_leer():
    """„der tipp ist kein tipp sondern ein weiteres Lob. das ist falsch. in dem fall sollte es dann
    auch kein vorschlag zur verbesserung geben, da der score ja bei 5/5 liegt."""
    from services.analyst_eval import filtere_verbesserung
    e = AnalystEvaluationV2(
        schnitt_pacing={"score": 5, "verbesserung": "Die Schnitte sind hervorragend getaktet."},
        hook={"sprech_hook_score": 5, "sprech_hook_verbesserung": "Die Frage zieht sofort hinein."},
    )
    e = filtere_verbesserung(e, ziel="MOFU")
    assert e.schnitt_pacing.verbesserung == ""
    assert e.hook.sprech_hook_verbesserung == ""


def test_bei_score_4_bleibt_die_kritikseite_stehen():
    from services.analyst_eval import filtere_verbesserung
    e = AnalystEvaluationV2(
        schnitt_pacing={"score": 4, "verbesserung": "Eine Stelle zieht sich kurz."})
    e = filtere_verbesserung(e, ziel="MOFU")
    assert e.schnitt_pacing.verbesserung == "Eine Stelle zieht sich kurz."


def test_bei_score_4_ohne_kritik_springt_der_notnagel_ein():
    """„hier fehlt ein verbesserungsvorschlag. da der score eine 4/5 ist muss ja noch was besser
    gehen." Der Text kommt aus dem, was das Modell selbst geschrieben hat — erst wenn da nichts
    steht, setzt der Code seinen eigenen Satz."""
    from services.analyst_eval import filtere_verbesserung
    e = AnalystEvaluationV2(
        skript={"score": 4, "probleme": [],
                "hinweise": ["Der letzte Satz bleibt vage."]},
        soundeffekte={"score": 4, "verbesserung": ""},
    )
    e = filtere_verbesserung(e, ziel="MOFU")
    # skript hat schon einen Hinweis — der Code laesst ihn in Ruhe und erfindet nichts daneben.
    assert e.skript.hinweise == ["Der letzte Satz bleibt vage."]
    # soundeffekte hat gar nichts: hier springt der Notnagel ein.
    assert e.soundeffekte.verbesserung, "leer waere hier ein Loch im Aufklapper"


def test_scoreprobleme_dimension_ohne_jeden_befund_bekommt_den_notnagel():
    from services.analyst_eval import filtere_verbesserung
    e = AnalystEvaluationV2(sprechqualitaet={"score": 4, "probleme": [], "hinweise": []})
    e = filtere_verbesserung(e, ziel="MOFU")
    assert e.sprechqualitaet.hinweise, "bei 4/5 muss etwas dastehen"


def test_v2_laeufe_behalten_ihre_kommentare():
    from services.analyst_eval import filtere_verbesserung
    e = AnalystEvaluationV2(schnitt_pacing={"score": 5, "verbesserung": "Hervorragend."})
    e = filtere_verbesserung(e, ziel="")
    assert e.schnitt_pacing.verbesserung == "Hervorragend."


# --- 4) Der Lautheits-Deckel sagt, warum er deckelt ----------------------------------------------

def test_gedeckelte_audioqualitaet_nennt_den_messwert():
    """„mir fehlt das feedback und die handlungsaufforderung komplett." Im Lauf stand
    `audioqualitaet`: score 2, probleme [], hinweise [], positiv "" — der Aufklapper war leer."""
    from services.analyst_eval import deckle_audioqualitaet
    e = AnalystEvaluationV2(audioqualitaet={"score": 5})
    e = deckle_audioqualitaet(e, _result(lufs=-35.8, peak=-18.0))
    assert e.audioqualitaet.score < 5
    text = " ".join(e.audioqualitaet.probleme)
    assert "-35.8" in text or "−35,8" in text or "35,8" in text
    assert "14" in text, "der Zielwert gehoert dazu, sonst weiss der Nutzer nicht, wohin"


def test_gute_lautheit_erzeugt_keinen_zusatztext():
    from services.analyst_eval import deckle_audioqualitaet
    e = AnalystEvaluationV2(audioqualitaet={"score": 5})
    e = deckle_audioqualitaet(e, _result(lufs=-14.0, peak=-2.0))
    assert e.audioqualitaet.score == 5
    assert e.audioqualitaet.probleme == []
