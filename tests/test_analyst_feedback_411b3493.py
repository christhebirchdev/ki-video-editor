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


# --- 4) Die Lautheit wird gar nicht mehr bewertet ------------------------------------------------

def test_die_lautheit_deckelt_die_audioqualitaet_nicht_mehr():
    """„ich habe das video nochmal angehoert und die lautstaerke ist wirklich super." Gemessen
    waren -35,8 LUFS — die Zahl stimmt, das Urteil daraus war trotzdem falsch."""
    from services.analyst_eval import pruefe_audio_bewertbar
    e = AnalystEvaluationV2(audioqualitaet={"score": 5})
    e = pruefe_audio_bewertbar(e, _result(lufs=-35.8, peak=-18.0))
    assert e.audioqualitaet.score == 5
    assert e.audioqualitaet.probleme == []


# --- 5) Toneffekte: der Suchauftrag steht im Prompt, nicht nur als Hinweis ----------------------

def test_der_toneffekt_suchauftrag_steht_in_der_v3_user_message():
    """„sie liegen auf der musik. subtil aber hoerbar." Der Block steht in der USER-Message, weil
    sie zuletzt kommt und am zuverlaessigsten befolgt wird — und NUR bei gesetztem Ziel, damit die
    V2-Vergleichsbasis byte-identisch bleibt."""
    from models.analyst import AnalystResult
    from services.analyst_gemini_eval import _evaluate_teil  # noqa: F401  (Import-Smoke)
    from services.analyst_gemini_eval import _PFLICHT_TONEFFEKTE, _user_message

    r = AnalystResult(id="x", filename="c.mp4", duration_sec=10.0, scene_count=0, scenes=[])
    assert "Toneffekte" not in _user_message(r, "hybrid"), "V2 bleibt unberuehrt"
    for wort in ("Whoosh", "Viertelsekunde", "unter", "unsicher"):
        assert wort in _PFLICHT_TONEFFEKTE, wort


def test_der_soundeffekt_kommentar_verlangt_den_befund_an_den_schnitten():
    """Ein Pflichtfeld, das eine FRAGE stellt, wird zuverlaessiger beantwortet als eine Regel im
    Fliesstext — das Modell muss den Satz schreiben und hoert dafuer hin."""
    from services.analyst_eval import OUTPUT_SCHEMA, v3_schema
    zeile = [l for l in v3_schema().splitlines() if l.strip().startswith('"soundeffekte"')][0]
    assert "SCHNITTSTELLEN" in zeile
    assert "SCHNITTSTELLEN" not in OUTPUT_SCHEMA, "V2-Vertrag bleibt unberuehrt"


# --- 6) `null` darf NIE einen bezahlten Lauf abbrechen -------------------------------------------

def test_jedes_score_feld_vertraegt_null():
    """Abbruch eines bezahlten Laufs, gemeldet von Chris:

        1 validation error for AnalystEvaluationV2
        cta.score  Input should be a valid integer [input_value=None]

    Ursache: Der V3-Vertrag erlaubt seit der CTA-Entscheidung `null`, `ScoreKommentar.score` war
    aber `int`. Dieselbe Fehlerklasse wie bei `top_tipps` — das Modell haelt sich an den Vertrag,
    das Modell-Schema nicht. `null` heisst ueberall in diesem Schema „nicht bewertbar" und ist
    damit fuer JEDE Dimension ein zulaessiger Wert; eine Dimension, die es nicht anbietet, bekommt
    ihn nie, aber ein Tippfehler im Vertrag darf keinen Lauf kosten.
    """
    from models.analyst import AnalystEvaluationV2

    e = AnalystEvaluationV2(**{
        "hook": {"sprech_hook_score": None, "text_hook_score": None, "visuell_hook_score": None},
        "struktur": {"score": None},
        "spannungsbogen": {"score": None},
        "skript": {"score": None},
        "untertitel": {"score": None, "gestaltung_score": None},
        "cta": {"score": None},
        "schnitt_pacing": {"score": None},
        "einblendungen_eval": {"score": None},
        "soundeffekte": {"score": None},
        "sprechqualitaet": {"score": None},
        "visuelle_aesthetik": {"score": None},
        "audioqualitaet": {"score": None},
        "protagonist_auftreten": {"score": None},
    })
    assert e.cta.score is None


def test_die_nachbearbeitung_ueberlebt_lauter_nullen():
    """Nicht nur parsen — der ganze Weg bis zum Performance-Score."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import nachbearbeiten

    e = AnalystEvaluationV2(**{k: {"score": None} for k in (
        "struktur", "spannungsbogen", "skript", "cta", "schnitt_pacing",
        "einblendungen_eval", "soundeffekte", "sprechqualitaet", "visuelle_aesthetik",
        "audioqualitaet", "protagonist_auftreten")})
    out = nachbearbeiten(e, _result())
    assert isinstance(out.performance_score, int)


# --- 7) Ein einzelnes kaputtes Feld darf nie die ganze Analyse kosten ----------------------------

def test_ein_ungueltiges_feld_wird_verworfen_statt_den_lauf_abzubrechen():
    """Zweimal ist jetzt ein BEZAHLTER Lauf an der Schema-Strenge gestorben: einmal an `top_tipps`
    als Objektliste, einmal an `cta.score: null`. Beide Male war die Analyse fertig und das Geld
    ausgegeben — und das Ergebnis trotzdem weg, wegen EINEM Feld.

    Also parst der Code ab jetzt nachsichtig: Was nicht ins Schema passt, faellt auf seinen
    Default zurueck, der Rest ueberlebt. Ein fehlendes Feld ist immer besser als kein Ergebnis."""
    from services.analyst_eval import parse_evaluation

    e, verworfen = parse_evaluation({
        "performance_score": 77,
        "cta": {"score": "fuenf", "kommentar": "Da ist einer."},
        "top_tipps": ["Mach das Ende konkreter."],
    })
    assert e.performance_score == 77, "der gute Teil ueberlebt"
    assert e.top_tipps == ["Mach das Ende konkreter."]
    assert "cta" in " ".join(verworfen)


def test_sauberer_output_wird_unveraendert_geparst():
    from services.analyst_eval import parse_evaluation
    e, verworfen = parse_evaluation({"performance_score": 80, "cta": {"score": 4}})
    assert verworfen == []
    assert e.cta.score == 4
