"""Tests für den AI Video Analyst (Whole-Video via Gemini)."""
import json
import pytest

from models.analysis import WhisperWord
from services.analyst_speech import compute_speech_stats
from services import analyst_vlm


# ---------- compute_speech_stats (pure) ----------

def _w(word, start, end):
    return WhisperWord(word=word, start=start, end=end)


def test_speech_stats_empty():
    assert compute_speech_stats([]) is None


def test_speech_stats_fillers_und_pausen():
    words = [_w("Hallo", 0.0, 0.4), _w("ähm", 0.5, 0.8), _w("Welt", 1.9, 2.3), _w("heute", 2.4, 2.8)]
    st = compute_speech_stats(words)
    assert st.wort_anzahl == 4
    assert st.filler_count == 1 and st.filler_words == ["ähm"]
    assert st.pausen_count == 1 and st.laengste_pause_sec == pytest.approx(1.1)


def test_kurze_luecken_werden_nicht_als_pause_gemeldet():
    """P5: Lücken unter 0.8s nimmt der Zuschauer nicht wahr — sie dürfen keine Schnitt-Empfehlung
    auslösen. Vorher lag die Schwelle bei 0.5s; Chris zu Run 25b8b2f6: „die sprechpause bei sek 3
    und sek 7 in ordnung. das hätte ich nicht als handlungsempfehlung gegeben.\""""
    from services.analyst_speech import PAUSE_THRESHOLD_SEC
    assert PAUSE_THRESHOLD_SEC == 0.8
    # 0.7s Lücke = früher gemeldet, heute nicht mehr
    st = compute_speech_stats([_w("a", 0.0, 1.0), _w("b", 1.7, 2.0)])
    assert st.pausen_count == 0 and st.pausen == []
    # 0.9s Lücke = weiterhin gemeldet
    st2 = compute_speech_stats([_w("a", 0.0, 1.0), _w("b", 1.9, 2.2)])
    assert st2.pausen_count == 1


def test_speech_stats_no_pause_no_filler():
    st = compute_speech_stats([_w("eins", 0.0, 0.3), _w("zwei", 0.35, 0.6)])
    assert st.filler_count == 0 and st.pausen_count == 0


def test_speech_stats_pausen_behalten_ihre_position():
    """Regression: Die Position der Pause darf nicht wegaggregiert werden — ohne sie
    ordnet das Modell die gemessene Dauer einer geratenen Stelle zu (Run a2808a62)."""
    words = [_w("a", 0.0, 1.0), _w("b", 4.1, 4.5), _w("c", 5.6, 6.0)]
    st = compute_speech_stats(words)
    assert st.pausen_count == 2
    assert [(p.start_sec, p.end_sec, p.dauer_sec) for p in st.pausen] == [
        (1.0, 4.1, 3.1),   # die lange Pause ist bei Sek. 1–4.1, nicht "irgendwo"
        (4.5, 5.6, 1.1),
    ]
    assert st.laengste_pause_sec == pytest.approx(3.1)


def test_pausen_txt_rendert_position_und_schwelle_in_den_prompt():
    from services.analyst_eval import pausen_txt
    st = compute_speech_stats([_w("a", 0.0, 1.0), _w("b", 4.1, 4.5)])
    assert pausen_txt(st).startswith("3.1s @ 1.0–4.1s")
    # Die Schwelle muss im Prompt stehen, sonst rät das Modell an Stellen herum, die gar nicht
    # gemeldet wurden ("da war doch bestimmt eine Pause").
    assert "Messschwelle: 0.8s" in pausen_txt(st)
    leer = pausen_txt(compute_speech_stats([_w("a", 0.0, 0.3), _w("b", 0.4, 0.7)]))
    assert "keine Pausen >0.8s gemessen" in leer


# ---------- Modell-Antwort mit null statt Zahl ----------

def test_protagonist_ab_sek_null_bricht_den_lauf_nicht_ab():
    """Läufe 102130ce, 3dce018c und d5ff1737 sind hart abgebrochen: Gemini liefert für
    `protagonist_ab_sek` ein `null`, wenn es keinen Sprechbeginn erkennt (alle drei Format
    „Andere"). Pydantic warf `float_type`, und die gesamte Analyse war verloren — 3 von 70
    gespeicherten Läufen. Kein erkannter Sprechbeginn wird wie „ab Sekunde 0" behandelt:
    das ist der Pydantic-Default des Feldes und derselbe Wert, den jeder Altlauf ohne das
    Feld bekommt."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2.model_validate({"protagonist_ab_sek": None})
    assert ev.protagonist_ab_sek == 0.0


def test_protagonist_ab_sek_zahl_bleibt_unveraendert():
    from models.analyst import AnalystEvaluationV2
    assert AnalystEvaluationV2.model_validate({"protagonist_ab_sek": 4.2}).protagonist_ab_sek == 4.2


# ---------- Format-Auswahl (Befund 0: bindend statt Modell-Rateversuch) ----------

def _result(**kw):
    from models.analyst import AnalystResult
    return AnalystResult(id="x", filename="v.mp4", duration_sec=43.1, scene_count=0, scenes=[], **kw)


def test_nutzer_format_ueberschreibt_modell_urteil():
    """Run a2808a62: Das Modell riet 'Talking-Head' bei einem Reaction-Video. Die Nutzerauswahl gewinnt."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import erzwinge_nutzer_format
    parsed = erzwinge_nutzer_format(
        AnalystEvaluationV2(format="Talking-Head"), _result(gewaehltes_format="Reaction")
    )
    assert parsed.format == "Reaction"


def test_altlauf_ohne_format_behaelt_modell_urteil():
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import erzwinge_nutzer_format
    parsed = erzwinge_nutzer_format(AnalystEvaluationV2(format="Sketch"), _result())
    assert parsed.format == "Sketch"


def test_reaction_prompt_warnt_vor_fremdem_audio():
    """Bei Reaction MUSS der Prompt sagen, dass der Transkript-Anfang nicht der Sprech-Hook ist."""
    from services.analyst_gemini_eval import _format_instruction
    txt = _format_instruction(_result(gewaehltes_format="Reaction"))
    assert "Reaction" in txt and "FREMDVIDEO" in txt
    assert "protagonist_ab_sek" in txt
    assert "NICHT der erste Satz im Transkript" in txt.replace("\n", " ").replace("  ", " ")


def test_ohne_reaction_keine_fremdvideo_warnung():
    from services.analyst_gemini_eval import _format_instruction
    txt = _format_instruction(_result(gewaehltes_format="Talking Head"))
    assert "FREMDVIDEO" not in txt
    assert "Talking Head" in txt and "protagonist_ab_sek" in txt
    assert "Texthook des Protagonisten" not in txt  # Fremdvideo-Texthook-Regel nur bei Reaction


def _ev_hook(score):
    from models.analyst import AnalystEvaluationV2, HookEval
    return AnalystEvaluationV2(hook=HookEval(
        text_hook_vorhanden=True, text_hook_score=score, text_hook_grund="Die Frage weckt Neugier."))


def test_reaction_ohne_eigenes_feld_wird_auf_0_geklemmt():
    """Kern des Fixes: Reaction + leeres 'Geplante Texthook'-Feld → der sichtbare Text stammt aus dem
    reagierten Video → Score hart 0. Real: Modell gab über 5 Läufe stabil Score 3, weil es den
    eingebrannten Fremdvideo-Text visuell nicht als fremd erkennt."""
    from services.analyst_eval import bereinige_fremd_texthook
    ev = bereinige_fremd_texthook(_ev_hook(3), _result(gewaehltes_format="Reaction", geplante_texthook=""))
    assert ev.hook.text_hook_score == 0 and ev.hook.text_hook_vorhanden is False
    assert "Fremdvideo" in ev.hook.text_hook_grund and "3 Varianten" in ev.hook.text_hook_grund


def test_reaction_mit_eigener_texthook_bleibt_unberuehrt():
    """Feld ausgefüllt = eigene Texthook → normale Bewertung, keine Klemme."""
    from services.analyst_eval import bereinige_fremd_texthook
    ev = bereinige_fremd_texthook(_ev_hook(4), _result(gewaehltes_format="Reaction",
                                                       geplante_texthook="Mit 46 nochmal Vater"))
    assert ev.hook.text_hook_score == 4 and ev.hook.text_hook_vorhanden is True


def test_nicht_reaction_wird_nicht_geklemmt():
    from services.analyst_eval import bereinige_fremd_texthook
    ev = bereinige_fremd_texthook(_ev_hook(3), _result(gewaehltes_format="Talking Head", geplante_texthook=""))
    assert ev.hook.text_hook_score == 3  # nur Reaction ist betroffen


# ---------- verteile_empfehlungen (Befund 3: Sortieren/Bündeln/Splitten deterministisch im Code) ----------

def _ev(*empfehlungen):
    from models.analyst import AnalystEvaluationV2
    return AnalystEvaluationV2(empfehlungen=[
        {"zeitpunkt_sek": t, "anweisung": a, "gruppe": g} for t, a, g in empfehlungen
    ])


def test_die_drei_fruehesten_werden_top_3():
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev(
        (40.0, "Versprecher am Ende rausschneiden", "versprecher"),
        (0.0, "Texthook einbauen", "texthook"),
        (17.0, "Symbolbild einblenden", "symbolbild"),
        (3.0, "Woosh-Ton einfügen", "woosh"),
    ))
    assert [s.zeitpunkt for s in ev.action_steps] == ["ca. Sek. 0", "ca. Sek. 3", "ca. Sek. 17"]
    assert [s.zeitpunkt for s in ev.weitere_empfehlungen] == ["ca. Sek. 40"]


def test_gleiche_gruppe_wird_zu_einem_schritt_mit_allen_zeitpunkten():
    """Die Bündelungs-Regel stand im Prompt und war ohne Zeitstempel unerfüllbar (Befund 1+3)."""
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev(
        (15.2, "Sprechpause rausschneiden", "sprechpausen"),
        (3.4, "Sprechpause rausschneiden", "sprechpausen"),
        (24.0, "Sprechpause rausschneiden", "sprechpausen"),
        (0.0, "Texthook einbauen", "texthook"),
    ))
    assert len(ev.action_steps) == 2  # 3 Pausen → EIN Schritt, nicht drei fast gleiche
    assert ev.action_steps[0].zeitpunkt == "ca. Sek. 0"
    assert ev.action_steps[1].zeitpunkt == "ca. Sek. 3, 15 und 24"
    assert ev.weitere_empfehlungen == []


def test_gleiches_label_aber_andere_handlung_wird_NICHT_gemergt():
    """Regression Lauf 702f9c11: Gehirn @18s, Telefon @28s, Folgen-Knopf @41s hatten alle das Label
    'einblendung' und wurden zu „Gehirn @18,28,41" verschmolzen. Label = Kategorie ≠ dieselbe Handlung.
    Merge NUR bei gleichem Label UND gleichem Text."""
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev(
        (18.0, "Blende ein Gehirn-Symbol ein", "einblendung"),
        (28.0, "Zeige einen Telefonhörer", "einblendung"),
        (41.0, "Blende einen Folgen-Knopf ein", "einblendung"),
    ))
    alle = ev.action_steps + ev.weitere_empfehlungen
    assert len(alle) == 3, "verschiedene Einblendungen dürfen NICHT zu einem Schritt werden"
    assert {s.anweisung for s in alle} == {
        "Blende ein Gehirn-Symbol ein", "Zeige einen Telefonhörer", "Blende einen Folgen-Knopf ein"}


def test_gleiches_label_und_gleicher_text_wird_gemergt():
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev(
        (3.0, "Sprechpause rausschneiden", "sprechpausen"),
        (15.0, "Sprechpause rausschneiden", "sprechpausen"),
        (24.0, "sprechpause  rausschneiden", "sprechpausen"),  # Groß/Whitespace egal
    ))
    assert len(ev.action_steps) == 1
    assert ev.action_steps[0].zeitpunkt == "ca. Sek. 3, 15 und 24"


def test_gruppe_zaehlt_ab_ihrem_fruehesten_vorkommen():
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev(
        (5.0, "A", "a"), (6.0, "B", "b"), (7.0, "C", "c"),
        (30.0, "Pause raus", "pausen"), (1.0, "Pause raus", "pausen"),
    ))
    # Die Gruppe hat einen Eintrag bei Sek. 1 → sie ist die früheste, trotz des Ausreißers bei 30.
    assert ev.action_steps[0].zeitpunkt == "ca. Sek. 1 und 30"


def test_ohne_gruppen_label_bleibt_jede_empfehlung_eigenstaendig():
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(_ev((1.0, "A", ""), (2.0, "B", ""), (3.0, "C", ""), (4.0, "D", "")))
    assert len(ev.action_steps) == 3 and len(ev.weitere_empfehlungen) == 1


def test_altes_schema_ohne_empfehlungen_bleibt_unveraendert():
    """Altläufe/Fallback: Liefert das Modell keine `empfehlungen`, bleiben geparste action_steps stehen."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import verteile_empfehlungen
    ev = verteile_empfehlungen(AnalystEvaluationV2(
        action_steps=[{"zeitpunkt": "0:03", "anweisung": "alt"}]
    ))
    assert [s.anweisung for s in ev.action_steps] == ["alt"]


# ---------- P3: Empfehlungen müssen etwas verändern ----------

def test_bestaetigungen_fliegen_aus_den_empfehlungen():
    """Feedback Run 10ff4193: „handlungsempfehlungen sollen immer zur veränderung beitragen und nicht
    das bestehende gut reden." Die Sätze unten sind wörtlich aus echten Läufen."""
    from services.analyst_eval import entferne_bestaetigungen
    ev = entferne_bestaetigungen(_ev(
        (30.0, "Die Atempause bei Sekunde 30 ist dramaturgisch super – diese Pause unbedingt im Schnitt behalten.", ""),
        (18.0, "Die Sprechpause von einer halben Sekunde bewusst im Video lassen, damit die Botschaft wirkt.", ""),
        (3.0, "Schneide den Versprecher raus.", ""),
    ))
    assert [e.zeitpunkt_sek for e in ev.empfehlungen] == [3.0]


def test_filter_trifft_keine_echten_handlungen():
    """Gegenprobe: Der Filter darf keine Handlung wegwerfen, nur weil „lassen" darin vorkommt."""
    from services.analyst_eval import entferne_bestaetigungen
    ev = entferne_bestaetigungen(_ev(
        (2.0, "Lass den Zuschauer hier einen Moment raten, bevor du auflöst.", ""),
        (5.0, "Blende eine Grafik ein, die das Wort Vertrauen verstärkt.", ""),
        (9.0, "Kürze die lange Pause auf eine halbe Sekunde.", ""),
    ))
    assert len(ev.empfehlungen) == 3


# ---------- P6: Anlauf am Anfang wegschneiden (Messwert statt Prompt-Bitte) ----------

def _stats(sprechbeginn):
    from models.analyst import SpeechStats
    return SpeechStats(wort_anzahl=10, sprech_dauer_sec=20.0, wpm=120.0, filler_count=0,
                       filler_words=[], pausen_count=0, laengste_pause_sec=0.0,
                       sprechbeginn_sec=sprechbeginn)


def test_verzoegerter_sprechbeginn_erzwingt_anlauf_schnitt():
    """Feedback Run a4fbb8ae (sprechbeginn 0.98s): „die sprechpause am anfang wurde nicht empfohlen
    rauszuschneiden. das hätte ich mir hier gewünscht." Regel stand im Skill, feuerte nicht."""
    from services.analyst_eval import erzwinge_anlauf_schnitt
    ev = erzwinge_anlauf_schnitt(_ev((23.0, "Foto einblenden", "foto")),
                                 _result(gewaehltes_format="Talking Head", speech_stats=_stats(0.98)))
    assert ev.empfehlungen[0].zeitpunkt_sek == 0.0
    assert ev.empfehlungen[0].gruppe == "anlauf" and "Sekunde 1." in ev.empfehlungen[0].anweisung


def test_eigene_anlauf_empfehlung_des_modells_wird_ersetzt_nicht_ergaenzt():
    """Feedback Run 61d39035: „2 mal derselbe tipp." Das Modell lieferte `gruppe='anlauf_weg'` bei
    0.98s — der Label-Vergleich griff nicht, beide Schritte landeten im Output."""
    from services.analyst_eval import erzwinge_anlauf_schnitt
    ev = erzwinge_anlauf_schnitt(
        _ev((0.98, "Schneide das Atmen und Zögern vor deinem ersten Wort weg.", "anlauf_weg"),
            (0.0, "Ersetze die Texthook am Anfang durch eine kürzere Variante.", ""),
            (20.0, "Schneide die lange Pause heraus.", "pause_weg")),
        _result(gewaehltes_format="Talking Head", speech_stats=_stats(0.98)),
    )
    anlauf = [e for e in ev.empfehlungen if "Anlauf" in e.anweisung or "Atmen" in e.anweisung]
    assert len(anlauf) == 1 and anlauf[0].gruppe == "anlauf"
    # Die Texthook-Empfehlung bei Sekunde 0 schneidet nichts und bleibt unangetastet
    assert any("Texthook" in e.anweisung for e in ev.empfehlungen)
    # Die Pause bei Sek. 20 liegt außerhalb des Fensters
    assert any(e.zeitpunkt_sek == 20.0 for e in ev.empfehlungen)


def test_reaction_bekommt_keinen_anlauf_schnitt():
    """Bei Reaction misst sprechbeginn das Fremdvideo — die Übergangspause trägt den Formatwechsel."""
    from services.analyst_eval import erzwinge_anlauf_schnitt
    ev = erzwinge_anlauf_schnitt(_ev((23.0, "Foto einblenden", "foto")),
                                 _result(gewaehltes_format="Reaction", speech_stats=_stats(18.0)))
    assert all(e.gruppe != "anlauf" for e in ev.empfehlungen)


def test_sofortiger_sprechbeginn_erzwingt_nichts():
    from services.analyst_eval import erzwinge_anlauf_schnitt
    ev = erzwinge_anlauf_schnitt(_ev((23.0, "Foto einblenden", "foto")),
                                 _result(gewaehltes_format="Talking Head", speech_stats=_stats(0.2)))
    assert len(ev.empfehlungen) == 1


# ---------- Kritik ohne Handlung: Hook-Empfehlungen erzwingen ----------

def _ev_scores(sprech=None, text=None, *empfehlungen):
    from models.analyst import AnalystEvaluationV2, HookEval
    return AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=sprech, text_hook_score=text,
                      text_hook_vorhanden=bool(text)),
        empfehlungen=[{"zeitpunkt_sek": t, "anweisung": a, "gruppe": g} for t, a, g in empfehlungen],
    )


def test_schwache_sprechhook_erzwingt_eine_empfehlung():
    """Feedback Run f2312dc9: „hier hätte noch ein tipp zur sprechhook ergänzt werden können. das wird
    ja unten in der bewertung auch kritisiert." Score war 3 — Kritik unten, keine Handlung oben."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(_ev_scores(3, 4, (33.0, "Grafik einblenden", "")))
    assert ev.empfehlungen[0].gruppe == "sprechhook" and ev.empfehlungen[0].zeitpunkt_sek == 0.0


def test_starke_sprechhook_erzwingt_nichts():
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(_ev_scores(4, 4, (33.0, "Grafik einblenden", "")))
    assert all(e.gruppe != "sprechhook" for e in ev.empfehlungen)


def test_vorhandene_hook_empfehlung_wird_nicht_verdoppelt():
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(_ev_scores(
        2, 0, (0.0, "Ersetze die Texthook am Anfang durch eine kürzere Variante.", ""),
        (1.0, "Formuliere deinen ersten Satz um, damit er neugierig macht.", "")))
    assert len(ev.empfehlungen) == 2  # nichts dazugekommen


def _ev_betrifft(sprech, text, *empfehlungen, vorhanden=None, maengel=None):
    """Wie _ev_scores, aber die Empfehlungen tragen ein `betrifft`-Label."""
    from models.analyst import AnalystEvaluationV2, HookEval
    return AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=sprech, text_hook_score=text,
                      text_hook_vorhanden=bool(text) if vorhanden is None else vorhanden),
        texthook_maengel=maengel or [],
        empfehlungen=[{"zeitpunkt_sek": t, "anweisung": a, "gruppe": "", "betrifft": b}
                      for t, a, b in empfehlungen],
    )


def test_eigene_texthook_empfehlung_ueberlebt_statt_template():
    """Vorgabe Chris: „in den Empfehlungsfeldern sollen immer individuelle Handlungsempfehlungen
    stehen … nicht die Templates, die im Code generiert werden."

    Bisher löschte erzwinge_hook_empfehlungen JEDE Empfehlung, die das Texthook-Thema traf, und
    setzte den Baustein-Text ein — auch wenn das Modell die konkrete Schwäche schon benannt hatte.
    Über 10 Läufe vom 2026-08-06 stand deshalb 9× ein wortgleicher Template-Satz in den Top 3."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    eigene = "Färbe die Texthook statt Neongelb in ein ruhiges Weiß, das zum Look des Videos passt."
    ev = erzwinge_hook_empfehlungen(_ev_betrifft(4, 3, (0.0, eigene, "text_hook")))
    texthook = [e for e in ev.empfehlungen if "Texthook" in e.anweisung or "texthook" in e.anweisung]
    assert len(texthook) == 1, "Modell-Empfehlung und Template dürfen nicht beide dastehen"
    assert texthook[0].anweisung == eigene


def test_template_greift_weiter_wenn_das_modell_nichts_liefert():
    """Gegenprobe: Ohne eigene Texthook-Empfehlung bleibt der Baustein die Rückfallebene."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(
        _ev_betrifft(4, 3, (12.0, "Blende bei Sek. 12 ein Symbol ein.", ""), maengel=["laenge"]))
    assert any(e.gruppe == "texthook" for e in ev.empfehlungen)


def test_geklemmter_score_ersetzt_die_modell_empfehlung_weiterhin():
    """Wurde der Score erst im Code auf 0 gesetzt (Fremd-Texthook), kannte das Modell den Grund
    nicht — seine Empfehlung beschreibt dann die falsche Sache und muss ersetzt werden."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = _ev_betrifft(4, 0, (0.0, "Mach die Texthook etwas größer.", "text_hook"), vorhanden=False)
    ev.hook.text_hook_score_geklemmt = True
    ev = erzwinge_hook_empfehlungen(ev)
    assert any(e.gruppe == "texthook" for e in ev.empfehlungen)
    assert all("etwas größer" not in e.anweisung for e in ev.empfehlungen)


def test_texthook_score_0_erzwingt_eine_empfehlung():
    """Run e7fdf99d: text_hook_score 0 (im Code geklemmt), aber kein Handlungsschritt dazu — das
    Modell hielt die Fremdvideo-Texthook für vorhanden und hatte keinen Grund, einen zu schreiben."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(_ev_scores(4, 0, (10.0, "Fragezeichen einblenden", "")))
    assert ev.empfehlungen[0].gruppe == "texthook" and "9 Wörter" in ev.empfehlungen[0].anweisung


def test_stummes_video_erzwingt_keine_sprechhook_empfehlung():
    """sprech_hook_score ist dort None — None darf nicht wie 'schwach' behandelt werden."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    ev = erzwinge_hook_empfehlungen(_ev_scores(None, 4, (5.0, "Sound einfügen", "")))
    assert all(e.gruppe != "sprechhook" for e in ev.empfehlungen)


def test_reihenfolge_der_erzwungenen_schritte():
    """Alle drei liegen auf Sekunde 0 — die Einfüge-Reihenfolge entscheidet: Anlauf, Texthook, Sprechhook."""
    from services.analyst_eval import nachbearbeiten
    ev = nachbearbeiten(_ev_scores(2, 0, (25.0, "Foto einblenden", "foto")),
                        _result(gewaehltes_format="Talking Head", speech_stats=_stats(1.5)))
    assert [s.anweisung[:20] for s in ev.action_steps] == [
        "Schneide den Anlauf ", "Blende in den ersten", "Formuliere deinen er"]


# ---------- P4: Sprechpausen als Urteil, Schritt aus dem Code ----------

def _ev_pausen(*urteile, **kw):
    from models.analyst import AnalystEvaluationV2
    return AnalystEvaluationV2(
        pausen_urteile=[{"start_sec": s, "urteil": u} for s, u in urteile],
        empfehlungen=[{"zeitpunkt_sek": t, "anweisung": a, "gruppe": g}
                      for t, a, g in kw.get("empfehlungen", [])],
    )


def test_raus_urteile_werden_zu_einem_gebuendelten_schritt():
    """Chris zu Run 25b8b2f6: „sprechpausen sollten gebündelt empfohlen werden." Über Freitext war das
    unmöglich, weil verteile_empfehlungen() identischen Text zum Bündeln braucht."""
    from services.analyst_eval import baue_pausen_schritt, verteile_empfehlungen
    ev = verteile_empfehlungen(baue_pausen_schritt(
        _ev_pausen((3.1, "lassen"), (12.0, "raus"), (24.0, "raus"), (47.0, "raus"), (60.0, "unklar"))))
    pausen = [s for s in ev.action_steps + ev.weitere_empfehlungen if "Sprechpause" in s.anweisung]
    assert len(pausen) == 1
    assert pausen[0].zeitpunkt == "ca. Sek. 12, 24 und 47"   # „lassen" und „unklar" sind nicht dabei


def test_eigene_pausen_empfehlungen_des_modells_werden_ersetzt():
    """Sonst stünde der gebündelte Schritt neben den Einzelsätzen — derselbe Doppel-Fehler wie beim Anlauf."""
    from services.analyst_eval import baue_pausen_schritt
    ev = baue_pausen_schritt(_ev_pausen(
        (12.0, "raus"),
        empfehlungen=[(12.0, "Schneide die Pause von 0,9 Sekunden hier komplett heraus.", "pause_weg"),
                      (20.0, "Kürze die lange Pause vor dem Schlusssatz.", ""),
                      (30.0, "Blende ein Foto ein.", "foto")]))
    texte = [e.anweisung for e in ev.empfehlungen]
    assert sum("pause" in t.lower() for t in texte) == 1  # nur der gebaute Schritt
    assert any("Foto" in t for t in texte)                # unbeteiligte Empfehlung bleibt


def test_altlauf_ohne_pausen_urteile_behaelt_seine_empfehlungen():
    """Ein Lauf mit altem Schema darf seine Pausen-Empfehlungen nicht ersatzlos verlieren."""
    from services.analyst_eval import baue_pausen_schritt
    ev = baue_pausen_schritt(_ev_pausen(
        empfehlungen=[(12.0, "Schneide die Pause hier heraus.", "pause_weg")]))
    assert len(ev.empfehlungen) == 1


# ---------- P16: Texthook-Varianten prüft der Code ----------

def test_zu_lange_texthook_varianten_werden_verworfen():
    """Run f2312dc9 lieferte eine Variante mit 13 Wörtern, obwohl die 9-Wörter-Regel im Prompt steht.
    Wortzählen ist Arithmetik und gehört deshalb in den Code."""
    from services.analyst_eval import gueltige_texthook_varianten
    gueltig = gueltige_texthook_varianten([
        "Warum du keine Kunden gewinnst (und was Markus Baulig damit zu tun hat)",  # 13
        "Der größte Fehler im Verkauf",                                             # 5
        "Verkaufst du zu bedürftig?",                                               # 4
    ])
    assert gueltig == ["Der größte Fehler im Verkauf", "Verkaufst du zu bedürftig?"]


def test_varianten_landen_in_der_texthook_empfehlung():
    from services.analyst_eval import erzwinge_hook_empfehlungen
    from models.analyst import AnalystEvaluationV2, HookEval
    ev = erzwinge_hook_empfehlungen(AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=4, text_hook_score=2, text_hook_vorhanden=True),
        texthook_varianten=["Der größte Fehler im Verkauf", "Zu viel zu lang " * 4],
        empfehlungen=[{"zeitpunkt_sek": 0.0, "anweisung": "Ersetze die Texthook durch etwas Besseres.",
                       "gruppe": ""}],
    ))
    texthook = [e for e in ev.empfehlungen if e.gruppe == "texthook"]
    assert len(texthook) == 1                                    # die Modell-Fassung wurde ersetzt
    assert "Der größte Fehler im Verkauf" in texthook[0].anweisung
    assert "Zu viel zu lang" not in texthook[0].anweisung        # zu lange Variante fliegt raus


def test_starke_texthook_bekommt_keine_empfehlung_mehr():
    """Feedback b98c88b1 (Score 5): „die texthook empfehlung bei 1 ist unnötig, da ja eine schon sehr
    gute texthook vorhanden ist." Das Modell liefert Varianten trotzdem — die Schranke muss im Code sein."""
    from services.analyst_eval import erzwinge_hook_empfehlungen
    from models.analyst import AnalystEvaluationV2, HookEval
    for score in (4, 5):
        ev = erzwinge_hook_empfehlungen(AnalystEvaluationV2(
            hook=HookEval(sprech_hook_score=5, text_hook_score=score, text_hook_vorhanden=True),
            texthook_varianten=["Der größte Fehler im Verkauf"],
            empfehlungen=[{"zeitpunkt_sek": 5.0, "anweisung": "Sound einfügen", "gruppe": ""}]))
        assert all(e.gruppe != "texthook" for e in ev.empfehlungen), f"Score {score} löst noch aus"
    # bei 3 kommt sie weiterhin
    ev3 = erzwinge_hook_empfehlungen(AnalystEvaluationV2(
        hook=HookEval(sprech_hook_score=5, text_hook_score=3, text_hook_vorhanden=True),
        texthook_varianten=["Der größte Fehler im Verkauf"]))
    assert any(e.gruppe == "texthook" for e in ev3.empfehlungen)


# ---------- Einblendungen: ein Sammelschritt statt mehrerer Einzeltipps ----------

def test_einblendungen_werden_zu_einem_schritt_gebuendelt():
    """Feedback 3185d209: „den tipp mit den grafiken kann man auch zusammenfassen. maximal an 3
    stellen empfehlen. gerne auch statt bildgrafik auch optional eine b-roll aufnahme."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import baue_einblendungs_schritt
    ev = baue_einblendungs_schritt(AnalystEvaluationV2(
        einblendungen=[{"zeitpunkt_sek": 40.0, "verstaerkt": "Kinder"},
                       {"zeitpunkt_sek": 23.0, "verstaerkt": "Hof"},
                       {"zeitpunkt_sek": 8.0, "verstaerkt": "Zweifel"},
                       {"zeitpunkt_sek": 55.0, "verstaerkt": "Glück"}],
        empfehlungen=[{"zeitpunkt_sek": 23.0, "anweisung": "Blende hier eine kleine Grafik zum Hof ein.",
                       "gruppe": "bild"},
                      {"zeitpunkt_sek": 56.0, "anweisung": "Blende einen Folgen-Knopf ein.", "gruppe": "cta"}]))
    schritte = [e for e in ev.empfehlungen if e.gruppe == "einblendungen"]
    assert len(schritte) == 1
    text = schritte[0].anweisung
    assert "Sek. 8" in text and "Sek. 23" in text and "Sek. 40" in text
    assert "Sek. 55" not in text            # höchstens 3 Stellen
    assert "B-Roll" in text and "Emoji" in text
    assert schritte[0].zeitpunkt_sek == 8.0  # sortiert ab der frühesten Stelle
    # die Modell-Empfehlung an derselben Stelle ist ersetzt, der CTA am Ende bleibt
    assert not any("Grafik zum Hof" in (e.anweisung or "") for e in ev.empfehlungen)
    assert any("Folgen-Knopf" in (e.anweisung or "") for e in ev.empfehlungen)


def test_ohne_einblendungs_feld_bleibt_alles_stehen():
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import baue_einblendungs_schritt
    ev = baue_einblendungs_schritt(AnalystEvaluationV2(
        empfehlungen=[{"zeitpunkt_sek": 23.0, "anweisung": "Blende eine Grafik ein.", "gruppe": "bild"}]))
    assert len(ev.empfehlungen) == 1


# ---------- P11: performance_score aus den Einzel-Scores ----------

def _ev_voll(sprech, text, struktur, sprechq, schnitt, spannung, aesthetik):
    from models.analyst import AnalystEvaluationV2
    return AnalystEvaluationV2(**{
        "hook": {"sprech_hook_score": sprech, "text_hook_score": text, "text_hook_vorhanden": True},
        "struktur": {"score": struktur}, "sprechqualitaet": {"score": sprechq},
        "schnitt_pacing": {"score": schnitt}, "spannungsbogen": {"score": spannung},
        "visuelle_aesthetik": {"score": aesthetik},
    })


def test_score_wird_aus_den_dimensionen_berechnet():
    """Gegenprobe an echten Läufen: 25b8b2f6 bekam vom Modell 62, Chris: „score sollte schlechter sein".
    56748c94 (fast alles 5er) bekam 92 — der Wert soll oben bleiben."""
    from services.analyst_eval import berechne_performance_score
    schwach = berechne_performance_score(_ev_voll(3, 3, 3, 3, 2, 2, 3)).performance_score
    stark = berechne_performance_score(_ev_voll(5, 4, 5, 5, 5, 5, 5)).performance_score
    assert schwach < 55 and stark > 90
    assert berechne_performance_score(_ev_voll(5, 5, 5, 5, 5, 5, 5)).performance_score == 100
    assert berechne_performance_score(_ev_voll(1, 0, 1, 1, 1, 1, 1)).performance_score == 0


def test_hooks_und_qualitaet_wiegen_schwerer_als_der_rest():
    """Chris' Vorgabe: Sprechhook, Texthook, Audio und Bildqualität am stärksten."""
    from services.analyst_eval import SCORE_GEWICHTE
    stark = ("sprech_hook", "text_hook", "sprechqualitaet", "visuelle_aesthetik")
    schwach = ("spannungsbogen", "struktur", "schnitt_pacing")
    assert min(SCORE_GEWICHTE[k] for k in stark) > max(SCORE_GEWICHTE[k] for k in schwach)
    assert sum(SCORE_GEWICHTE.values()) == 100


def test_nicht_bewertbare_dimensionen_verteilen_ihr_gewicht_um():
    """Stummes Video: Sprech-Hook und Sprechqualität sind None — das darf den Score nicht drücken."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import berechne_performance_score
    ev = AnalystEvaluationV2(**{
        "hook": {"sprech_hook_score": None, "text_hook_score": 4, "text_hook_vorhanden": True},
        "struktur": {"score": 4}, "sprechqualitaet": {"score": None},
        "schnitt_pacing": {"score": 4}, "spannungsbogen": {"score": 4},
        "visuelle_aesthetik": {"score": 4},
    })
    assert berechne_performance_score(ev).performance_score >= 75


# ---------- P9: Videos ohne gesprochenes Wort ----------

def test_stummes_video_bekommt_keine_sprech_scores():
    """Feedback Run 08e908d7: sprech_hook 1/5 und sprechqualitaet 1/5 für ein Video, in dem bewusst
    nicht gesprochen wird. null heißt „nicht bewertbar", 1 hieße „schlecht gemacht"."""
    from models.analyst import AnalystEvaluationV2, HookEval, ScoreProbleme
    from services.analyst_eval import neutralisiere_stumme_scores
    ev = neutralisiere_stumme_scores(
        AnalystEvaluationV2(hook=HookEval(sprech_hook_score=1, sprech_hook_grund="Kein Text gesprochen."),
                            sprechqualitaet=ScoreProbleme(score=1, probleme=["Kein Ton."])),
        _result(transcript="", speech_stats=None),
    )
    assert ev.hook.sprech_hook_score is None and ev.sprechqualitaet.score is None
    assert ev.sprechqualitaet.probleme == [] and "kein Fehler" in ev.hook.sprech_hook_grund


def test_video_mit_sprache_behaelt_seine_scores():
    from models.analyst import AnalystEvaluationV2, HookEval, ScoreProbleme
    from services.analyst_eval import neutralisiere_stumme_scores
    ev = neutralisiere_stumme_scores(
        AnalystEvaluationV2(hook=HookEval(sprech_hook_score=4), sprechqualitaet=ScoreProbleme(score=3)),
        _result(transcript="Hallo Welt", speech_stats=_stats(0.1)),
    )
    assert ev.hook.sprech_hook_score == 4 and ev.sprechqualitaet.score == 3


# ---------- Nachbearbeitung als Kette ----------

def test_nachbearbeiten_laeuft_in_der_richtigen_reihenfolge():
    """Integration: Bestätigung fliegt raus, Anlauf kommt rein, und erst danach wird sortiert —
    sonst landet der erzwungene Schritt nicht auf Platz 1."""
    from services.analyst_eval import nachbearbeiten
    ev = nachbearbeiten(
        _ev((30.0, "Die Pause unbedingt behalten.", ""),
            (12.0, "Blende ein Foto ein.", "foto"),
            (40.0, "Kürze das Ende.", "ende")),
        _result(gewaehltes_format="Talking Head", speech_stats=_stats(1.4)),
    )
    assert ev.action_steps[0].zeitpunkt == "ca. Sek. 0"        # Anlauf zuerst
    assert "Anlauf" in ev.action_steps[0].anweisung
    assert len(ev.action_steps) == 3 and ev.weitere_empfehlungen == []
    assert all("behalten" not in s.anweisung for s in ev.action_steps)


# ---------- Safe Zone, Licht, Ton (Vorgaben Chris 2026-08-07) ----------

def test_safezone_steht_genau_einmal_mit_prozentwerten():
    """Chris hat die Werte vorgegeben (konservative Faustregel für Instagram UND TikTok).
    Prozent statt Pixel, weil das Modell keine Pixel im gerenderten Video ausmessen kann.
    Genau einmal, damit nicht wieder zwei Fassungen auseinanderlaufen."""
    from services.analyst_eval import load_skill_body
    skill = " ".join(load_skill_body().split())
    # Die vier Randwerte stehen als eine zusammenhängende Zeile — genau einmal. Einzelne Prozentzahlen
    # kommen anderswo legitim vor (Kopfraum „10–15 %"), deshalb wird die ganze Zeile geprüft.
    zeile = "oben 13 %, unten 21 %, rechts 15 %, links 4 %"
    assert skill.count(zeile) == 1, f"Safe-Zone-Werte stehen {skill.count(zeile)}× statt einmal"
    assert "SAFE ZONE" in skill


def test_texthook_position_verweist_auf_die_safezone():
    """Die alte Fassung („oberes Drittel, Abstand zum oberen Rand") nannte eigene Werte — mit der
    Safe Zone wären das zwei Regeln für dieselbe Sache."""
    from services.analyst_eval import load_skill_body
    skill = " ".join(load_skill_body().split())
    assert "oberes Drittel, mit deutlichem Abstand zum oberen Rand" not in skill


def test_sprechqualitaet_prueft_verstaendlichkeit_und_musikbalance():
    """Chris: „ob man jedes Wort ohne Anstrengung versteht, das in die Sprechqualität mit einfließen
    lassen" und „Hintergrundmusik subtil, nicht auf derselben Lautstärke wie das gesprochene Wort".
    """
    from services.analyst_eval import load_skill_body
    block = load_skill_body().split("## Sprechqualität")[1].split("\n## ")[0]
    block = " ".join(block.split())
    assert "ohne Anstrengung" in block
    assert "Musik" in block


def test_musik_darf_ohne_sprache_laut_sein():
    """Gegenstück: In einem Video ohne gesprochenes Wort trägt die Musik das Video — dort ist
    normale Lautstärke richtig und kein Mangel."""
    from services.analyst_eval import load_skill_body
    skill = " ".join(load_skill_body().split())
    assert "Wird nicht gesprochen, darf die Musik" in skill


def test_aesthetik_fragt_ob_das_gesicht_erkennbar_ist():
    """Chris: „einmal eher die Frage stellen: Erkennt man das Gesicht klar?" — als Leitfrage vor
    der Detailprüfung, damit nicht wieder jede Kleinigkeit zum Mangel wird."""
    from services.analyst_eval import load_skill_body
    block = load_skill_body().split("## Visuelle Ästhetik")[1].split("\n## ")[0]
    assert "Erkennt man das Gesicht klar" in " ".join(block.split())


# ---------- V1.2: Prompt-Split auf zwei Calls ----------

def test_jeder_abschnitt_ist_einem_call_zugeordnet():
    """Ohne vollständige Zuordnung fällt ein Abschnitt beim Split still weg — der teuerste Fehler
    dieser Architektur, weil er im Output nur als schlechtere Bewertung sichtbar wird."""
    import re
    from services.analyst_eval import ABSCHNITT_ZUORDNUNG, load_skill_body
    ueberschriften = {m.group(1) for m in re.finditer(r'^## (.+)$', load_skill_body(), re.M)}
    fehlend = ueberschriften - set(ABSCHNITT_ZUORDNUNG)
    assert not fehlend, f"nicht zugeordnet: {fehlend}"
    verwaist = set(ABSCHNITT_ZUORDNUNG) - ueberschriften
    assert not verwaist, f"Zuordnung zeigt auf gelöschte Abschnitte: {verwaist}"


def test_split_prompts_sind_deutlich_kleiner_als_der_ganze():
    """Der Zweck des Splits: weniger gleichzeitig geltende Regeln pro Call (IFScale-Befund
    Primacy-Bias). Bleibt ein Teil fast so groß wie das Ganze, ist nichts gewonnen."""
    from services.analyst_eval import build_system_prompt
    ganz = len(build_system_prompt())
    eroeffnung = len(build_system_prompt(teil="eroeffnung"))
    handwerk = len(build_system_prompt(teil="handwerk"))
    assert eroeffnung < ganz * 0.75
    assert handwerk < ganz * 0.75


def test_hook_regeln_nur_im_eroeffnungs_call():
    from services.analyst_eval import build_system_prompt
    assert "Zählt es als Hook" in build_system_prompt(teil="eroeffnung")
    assert "Zählt es als Hook" not in build_system_prompt(teil="handwerk")


def test_handwerk_regeln_nicht_im_eroeffnungs_call():
    from services.analyst_eval import build_system_prompt
    hw = build_system_prompt(teil="handwerk")
    assert "Sprechpausen" in hw and "Untertitel" in hw and "Dynamik" in hw
    er = build_system_prompt(teil="eroeffnung")
    assert "nach FUNKTION beurteilen" not in er


def test_gemeinsame_basis_steht_in_beiden_calls():
    """Laiensprache, Empfehlungsregel und Ausgabeformat gelten für beide — fehlen sie in einem,
    liefert dieser Call unbrauchbare Freitexte."""
    from services.analyst_eval import build_system_prompt
    for teil in ("eroeffnung", "handwerk"):
        p = build_system_prompt(teil=teil)
        assert "Laiensprache" in p or "LAIENSPRACHE" in p
        assert "kanonische Regel" in p
        assert "empfehlungen" in p


def test_teilschemas_decken_zusammen_alle_felder_ab():
    """Kein Bewertungsfeld darf zwischen den beiden Calls verloren gehen."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import TEIL_FELDER
    pflicht = set(AnalystEvaluationV2.model_fields) - {
        "action_steps", "weitere_empfehlungen", "performance_score"}
    abgedeckt = set(TEIL_FELDER["eroeffnung"]) | set(TEIL_FELDER["handwerk"])
    assert pflicht <= abgedeckt, f"fehlt in beiden Teilen: {pflicht - abgedeckt}"


def test_merge_fuehrt_beide_teilergebnisse_zusammen():
    from models.analyst import AnalystEvaluationV2, HookEval, ScoreKommentar
    from services.analyst_eval import merge_teilergebnisse
    a = AnalystEvaluationV2(zielgruppe="Gründer.", hook=HookEval(sprech_hook_score=4),
                            empfehlungen=[{"zeitpunkt_sek": 0.0, "anweisung": "Hook schärfen"}])
    b = AnalystEvaluationV2(spannungsbogen=ScoreKommentar(score=3, kommentar="Plätschert."),
                            staerken=["Klare Sprache"],
                            empfehlungen=[{"zeitpunkt_sek": 20.0, "anweisung": "Pause kürzen"}])
    m = merge_teilergebnisse(a, b)
    assert m.zielgruppe == "Gründer." and m.hook.sprech_hook_score == 4
    assert m.spannungsbogen.score == 3 and m.staerken == ["Klare Sprache"]
    assert len(m.empfehlungen) == 2


def test_merge_wirft_doppelte_empfehlungen_raus():
    """Der Split macht Doppler WAHRSCHEINLICHER als ein einzelner Call: Beide Teile sehen dasselbe
    Video und können dieselbe Beobachtung melden — Call 1 kennt den Bildtext, Call 2 die
    Untertitel, beide könnten „Text kürzen" schreiben. In V1.1 fiel das dem Modell selbst auf,
    weil alles in einer Antwort stand. Chris hat doppelte Tipps 5× bemängelt."""
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import merge_teilergebnisse
    doppelt = "Kürze den Text auf dem Startbildschirm."
    a = AnalystEvaluationV2(empfehlungen=[{"zeitpunkt_sek": 0.0, "anweisung": doppelt}])
    b = AnalystEvaluationV2(empfehlungen=[
        {"zeitpunkt_sek": 0.0, "anweisung": doppelt},
        {"zeitpunkt_sek": 9.0, "anweisung": "Schneide die Pause bei Sek. 9."}])
    m = merge_teilergebnisse(a, b)
    assert len(m.empfehlungen) == 2
    assert [e.anweisung for e in m.empfehlungen].count(doppelt) == 1


def test_split_liefert_dasselbe_ergebnis_wie_ein_call():
    """Der Split darf nur ÄNDERN, wie das Modell gefragt wird — nicht, was der Code daraus macht.
    Gegenprobe an einem echten Lauf: dieselben Rohdaten einmal als Ganzes, einmal in zwei Teile
    zerlegt und wieder zusammengeführt. Score und Schritte müssen identisch sein."""
    import json
    from pathlib import Path
    from models.analyst import AnalystEvaluationV2
    from services.analyst_eval import TEIL_FELDER, merge_teilergebnisse, nachbearbeiten
    lauf = Path("analyst_runs/870b3d6c/analysis.json")
    if not lauf.exists():
        pytest.skip("Referenzlauf nicht vorhanden")
    ev = json.loads(lauf.read_text())["evaluation"]
    roh = {k: v for k, v in ev.items() if k not in ("action_steps", "weitere_empfehlungen")}
    res = _result(gewaehltes_format=ev.get("format", ""))
    ganz = nachbearbeiten(AnalystEvaluationV2(**roh), res)
    t1 = AnalystEvaluationV2(**{k: v for k, v in roh.items() if k in TEIL_FELDER["eroeffnung"]})
    t2 = AnalystEvaluationV2(**{k: v for k, v in roh.items() if k in TEIL_FELDER["handwerk"]})
    geteilt = nachbearbeiten(merge_teilergebnisse(t1, t2), res)
    assert geteilt.performance_score == ganz.performance_score
    assert [s.anweisung for s in geteilt.action_steps] == [s.anweisung for s in ganz.action_steps]


def test_teilschemas_bleiben_valides_json_geruest():
    """Beim Filtern der Schema-Zeilen dürfen keine offenen Klammern zurückbleiben — sonst bekommt
    das Modell einen kaputten Vertrag und antwortet frei."""
    from services.analyst_eval import _schema_fuer
    for teil in ("eroeffnung", "handwerk"):
        s = _schema_fuer(teil)
        kern = s[s.find("{"):s.rfind("}") + 1]
        assert kern.count("{") == kern.count("}"), teil
        assert kern.count("[") == kern.count("]"), teil


def test_zweiter_call_bekommt_das_ergebnis_des_ersten():
    """Chris: „wichtig wäre, dass der zweite Run den Kontext auch vom ersten Run bekommt und auch
    die Bewertung." Ohne den Hook beurteilt Call 2 den Spannungsbogen blind."""
    from models.analyst import AnalystEvaluationV2, HookEval
    from services.analyst_gemini_eval import eroeffnungs_kontext
    txt = eroeffnungs_kontext(AnalystEvaluationV2(
        zielgruppe="Gründer im B2B.",
        hook=HookEval(sprech_hook_score=2, text_hook_score=4,
                      text_hook_wortlaut="3 Fehler beim Lead-Kontakt")))
    assert "3 Fehler beim Lead-Kontakt" in txt
    assert "2" in txt and "Gründer im B2B." in txt


# ---------- Neue Bewertungsfelder (Runde 4, Vorgaben Chris 2026-08-06) ----------

def test_untertitel_haben_ein_eigenes_feld():
    """Chris: „die Untertitelbewertung, auch die dynamischen Untertitel und auch die Position der
    Untertitel, kam in den Bewertungen noch gar nicht vor. Das möchte ich gerne mit aufnehmen."
    Bisher landeten sie als Freitext in schnitt_pacing.kommentar — unauffindbar und ohne Schritt."""
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    ev = AnalystEvaluationV2()
    assert isinstance(ev.untertitel, UntertitelEval)
    assert ev.untertitel.vorhanden is False
    assert ev.untertitel.maengel == []


def test_untertitel_maengel_erzeugen_eine_empfehlung():
    """Wie bei der Texthook: Der Code baut die Empfehlung aus GEMELDETEN Mängeln, nicht auf Verdacht."""
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    from services.analyst_eval import erzwinge_untertitel_empfehlung
    ev = erzwinge_untertitel_empfehlung(AnalystEvaluationV2(
        untertitel=UntertitelEval(vorhanden=True, maengel=["position", "statisch"])))
    schritte = [e for e in ev.empfehlungen if e.gruppe == "untertitel"]
    assert len(schritte) == 1
    assert "Kinn" in schritte[0].anweisung          # Baustein „position"
    assert "2 bis 4 Wörter" in schritte[0].anweisung  # Baustein „statisch"


def test_untertitel_ohne_maengel_erzeugen_nichts():
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    from services.analyst_eval import erzwinge_untertitel_empfehlung
    ev = erzwinge_untertitel_empfehlung(AnalystEvaluationV2(
        untertitel=UntertitelEval(vorhanden=True, maengel=[])))
    assert ev.empfehlungen == []


def test_fehlende_untertitel_sind_ein_mangel_wenn_gesprochen_wird():
    """Vorgabe Chris (2026-08-06), Korrektur der bisherigen Regel: „ein Untertitel, der mitläuft,
    ist im Video absolut essentiell und muss immer dazugehören. Wenn im Video gesprochen wird und
    es ein Transkript gibt, muss quasi jedes gesprochene Wort auch als Untertitel lesbar sein."

    Vorher galt das Fehlen als Formatentscheidung — genau umgekehrt."""
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    from services.analyst_eval import erzwinge_untertitel_empfehlung
    ev = erzwinge_untertitel_empfehlung(
        AnalystEvaluationV2(untertitel=UntertitelEval(vorhanden=False)),
        _result(transcript="Heute zeige ich dir drei Fehler beim Lead-Kontakt."))
    schritte = [e for e in ev.empfehlungen if e.gruppe == "untertitel"]
    assert len(schritte) == 1
    assert "Untertitel" in schritte[0].anweisung


def test_ohne_gesprochenes_wort_keine_untertitel_pflicht():
    """Ein stummes Video braucht keine Untertitel — sonst bekäme jedes reine Bild-Ton-Format
    einen Tipp, der nichts zu untertiteln hätte."""
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    from services.analyst_eval import erzwinge_untertitel_empfehlung
    ev = erzwinge_untertitel_empfehlung(
        AnalystEvaluationV2(untertitel=UntertitelEval(vorhanden=False)), _result(transcript=""))
    assert ev.empfehlungen == []


def test_vereinzelt_fehlende_woerter_sind_kein_mangel():
    """Chris: „manchmal gibt es bei Untertiteln auch mal vereinzelt Wörter, die vielleicht nicht
    direkt angezeigt werden, aber an sich sollten Untertitel mitlaufen." Laufen sie mit und ist
    nichts gemeldet, entsteht kein Schritt."""
    from models.analyst import AnalystEvaluationV2, UntertitelEval
    from services.analyst_eval import erzwinge_untertitel_empfehlung
    ev = erzwinge_untertitel_empfehlung(
        AnalystEvaluationV2(untertitel=UntertitelEval(vorhanden=True, maengel=[])),
        _result(transcript="Ein gesprochener Satz."))
    assert ev.empfehlungen == []


def test_skill_macht_untertitel_zur_pflicht_bei_sprache():
    from services.analyst_eval import load_skill_body
    skill = " ".join(load_skill_body().split())
    assert "Formatentscheidung und KEIN Mangel" not in skill
    assert "jedes gesprochene Wort" in skill


def test_visuelle_hook_ist_die_dritte_ebene():
    """Chris: „bei der Hook am Anfang wird keine visuelle Hook mit aufgeführt. Also zum Beispiel eine
    Bewegung, ein Bild, Bewegung der Person, ein Zoom-In-Effekt."
    Die Referenz kennt die Ebene seit jeher (S1 „Hook auf 3 Ebenen"), das Schema nicht."""
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2()
    assert ev.hook.visuell_hook_score is None
    assert ev.hook.visuell_hook_grund == ""


def test_visuelle_hook_zaehlt_im_performance_score():
    """Drei Ebenen teilen sich das Hook-Gewicht — sonst bliebe die neue Ebene folgenlos."""
    from services.analyst_eval import SCORE_GEWICHTE
    assert "visuell_hook" in SCORE_GEWICHTE
    assert sum(SCORE_GEWICHTE.values()) == 100


def test_energie_hat_ein_eigenes_feld():
    """Chris: „was auch nicht bewertet wird, ist die Energy des Protagonisten … diese Energy hat
    einen krassen Einfluss." Bisher in sprechqualitaet eingeschmolzen (Skill: „Tempo, Energie,
    Deutlichkeit zu EINEM Score") — man sah nie, woran eine 3 lag."""
    from models.analyst import AnalystEvaluationV2, EnergieEval
    ev = AnalystEvaluationV2()
    assert isinstance(ev.energie, EnergieEval)
    assert ev.energie.urteil == ""


# ---------- Effekte: nur bei geringer Dynamik oder als Feinschliff, nie in den Top 3 ----------

def _ev_effekte(dynamik, *effekte, andere=()):
    from models.analyst import AnalystEvaluationV2, DynamikEval
    return AnalystEvaluationV2(
        dynamik=DynamikEval(urteil=dynamik),
        effekt_vorschlaege=[{"zeitpunkt_sek": t, "art": a, "zweck": z} for t, a, z in effekte],
        empfehlungen=[{"zeitpunkt_sek": t, "anweisung": a, "gruppe": ""} for t, a in andere],
    )


def test_geringe_dynamik_erzeugt_einen_effekt_schritt():
    """Chris: „wenn das Video an sich wenig Bewegung beinhaltet … können durch solche Effekte mehr
    Abwechslung erzeugt werden, was zu einer höheren Watch Time führt." Als Indikator nennt er die
    allgemeine Dynamik, z. B. nur ein Kamerawinkel."""
    from services.analyst_eval import baue_effekt_schritt
    ev = baue_effekt_schritt(_ev_effekte(
        "gering", (3.0, "sound", "Übergang zur Aufzählung"), (12.0, "visuell", "Zahl erscheint"),
        andere=[(0.0, "Texthook überarbeiten"), (5.0, "Pause rausschneiden"),
                (9.0, "Blick in die Linse"), (20.0, "Symbol einblenden")]))
    schritte = [e for e in ev.empfehlungen if e.gruppe == "effekte"]
    assert len(schritte) == 1
    assert "Sek. 3" in schritte[0].anweisung and "Sek. 12" in schritte[0].anweisung


def test_hohe_dynamik_und_viele_empfehlungen_erzeugen_nichts():
    """Ein Video mit viel Bewegung UND offenen Baustellen braucht keinen Effekt-Feinschliff —
    sonst wird der Tipp zum nächsten Dauerläufer wie vorher die Einblendungen."""
    from services.analyst_eval import baue_effekt_schritt
    ev = baue_effekt_schritt(_ev_effekte(
        "hoch", (3.0, "sound", "Übergang"),
        andere=[(0.0, "a"), (1.0, "b"), (2.0, "c"), (3.0, "d")]))
    assert all(e.gruppe != "effekte" for e in ev.empfehlungen)


def test_starkes_video_bekommt_effekte_als_feinschliff():
    """Chris: „wenn das Video schon sehr, sehr gut ist, fast keine Handlungsempfehlung mehr gibt,
    kann damit nochmal Feintuning betrieben werden." Hohe Dynamik, aber kaum offene Punkte."""
    from services.analyst_eval import baue_effekt_schritt
    ev = baue_effekt_schritt(_ev_effekte(
        "hoch", (7.0, "visuell", "Produkt erscheint"), andere=[(0.0, "Einziger offener Punkt")]))
    assert any(e.gruppe == "effekte" for e in ev.empfehlungen)


def test_effekt_schritt_landet_nie_in_den_top_3():
    """Vorgabe Chris: „ich würde nicht sagen, dass der Effekt-Tipp in die Top 3 kommen muss. Das ist
    eher eine ergänzende Empfehlung." Der Schritt liegt auf einer frühen Sekunde und würde ohne
    Sonderbehandlung nach vorn sortiert."""
    from services.analyst_eval import baue_effekt_schritt, verteile_empfehlungen
    ev = baue_effekt_schritt(_ev_effekte(
        "gering", (1.0, "sound", "Einstieg"),
        andere=[(30.0, "Späterer Schritt A"), (40.0, "Späterer Schritt B")]))
    ev = verteile_empfehlungen(ev)
    assert all("Soundeffekt" not in s.anweisung for s in ev.action_steps)
    assert any("Soundeffekt" in s.anweisung for s in ev.weitere_empfehlungen)


def test_ohne_effekt_vorschlaege_passiert_nichts():
    from services.analyst_eval import baue_effekt_schritt
    ev = baue_effekt_schritt(_ev_effekte("gering"))
    assert ev.empfehlungen == []


# ---------- Blickrichtung: eigenes Urteil, kein Score ----------

def test_blickkontakt_hat_ein_eigenes_feld_ohne_score():
    """Vorgabe Chris: Die Blickrichtung soll eine eigene Bewertung sein und NICHT in ein Kriterium
    mit Scoring einfließen. Bisher lief sie über `visuelle_aesthetik.probleme` — dort zählte sie
    für den Score-Deckel und damit über SCORE_GEWICHTE (17 Punkte) in den Performance-Score."""
    from models.analyst import AnalystEvaluationV2, BlickEval
    ev = AnalystEvaluationV2()
    assert isinstance(ev.blickkontakt, BlickEval)
    assert not hasattr(ev.blickkontakt, "score"), "Blickrichtung darf keinen Score tragen"
    assert ev.blickkontakt.urteil == ""


def test_abgelesener_blick_erzwingt_eine_empfehlung():
    """Chris: „falls die Blickrichtung negativ auffallen sollte, dass sie auch definitiv als
    Handlungsempfehlung mit reinkommt … sie hat schon einen starken Einfluss auf die Wirkung.\""""
    from models.analyst import AnalystEvaluationV2, BlickEval
    from services.analyst_eval import erzwinge_blick_empfehlung
    ev = erzwinge_blick_empfehlung(AnalystEvaluationV2(
        blickkontakt=BlickEval(urteil="abgelesen", kommentar="Blick wandert zeilenweise nach unten.")))
    blick = [e for e in ev.empfehlungen if e.gruppe == "blick"]
    assert len(blick) == 1
    assert blick[0].betrifft == "", "darf keine Score-Dimension referenzieren"


def test_blick_in_der_linse_erzwingt_nichts():
    from models.analyst import AnalystEvaluationV2, BlickEval
    from services.analyst_eval import erzwinge_blick_empfehlung
    ev = erzwinge_blick_empfehlung(AnalystEvaluationV2(blickkontakt=BlickEval(urteil="in_der_linse")))
    assert ev.empfehlungen == []


def test_blick_erzeugt_keine_zweite_empfehlung():
    """Hat das Modell schon selbst einen Blick-Schritt geschrieben, kommt keiner dazu."""
    from models.analyst import AnalystEvaluationV2, BlickEval, Empfehlung
    from services.analyst_eval import erzwinge_blick_empfehlung
    ev = erzwinge_blick_empfehlung(AnalystEvaluationV2(
        blickkontakt=BlickEval(urteil="abgelesen"),
        empfehlungen=[Empfehlung(zeitpunkt_sek=3.0, gruppe="blick", anweisung="Schau in die Linse.")]))
    assert len(ev.empfehlungen) == 1


def test_blick_zaehlt_nicht_mehr_zur_visuellen_aesthetik():
    """Der Skill wies die Blickrichtung ausdrücklich `visuelle_aesthetik` zu — das ist die Stelle,
    über die sie in den Score lief."""
    from services.analyst_eval import load_skill_body
    skill = " ".join(load_skill_body().split())
    assert "**BLICKRICHTUNG** zählt hier ebenfalls mit" not in skill
    assert "blickkontakt" in skill.lower()


# ---------- Prompt-Regeln: vorhanden, eindeutig, nicht doppelt ----------

def test_user_message_macht_wortgleichheit_nicht_zum_alleinkriterium():
    """Widerspruch zwischen Skill und User-Message (Befund Chris, 2026-08-06).

    Der Skill verlangt für Untertitel ZWEI Merkmale (wortgleich UND laufend neue Blöcke) und stellt
    ausdrücklich klar, dass Wortgleichheit allein eine Texthook nicht entwertet. Die User-Message
    strich Merkmal 2 und machte Wortgleichheit hinreichend — inklusive „auch wenn der Text statisch
    stehen bleibt oder oben im Bild steht". Chris: eine echte Texthook KANN wortgleich mitgesprochen
    werden, während parallel Untertitel mitlaufen. Die User-Message steht näher am Modell und gewinnt
    im Zweifel gegen den Skill — deshalb muss dort dasselbe Zwei-Merkmale-Kriterium stehen."""
    from models.analyst import AnalystResult
    from services.analyst_gemini_eval import _user_message
    res = AnalystResult(id="x", filename="a.mp4", duration_sec=30.0, scene_count=0, scenes=[],
                        transcript="Test", gewaehltes_format="Talking Head")
    um = _user_message(res, "hybrid")
    assert "auch wenn der Text statisch stehen bleibt" not in um, \
        "macht Wortgleichheit zum hinreichenden Untertitel-Kriterium"
    assert "BEIDE" in um, "das Zwei-Merkmale-Kriterium muss auch hier gelten"


def test_texthook_wird_an_der_persistenz_erkannt():
    """Positives Erkennungsmerkmal (Vorgabe Chris): Eine Texthook steht durchgehend an derselben
    Position ODER verschwindet und taucht danach nicht wieder auf. Untertitel werden dagegen laufend
    durch neue Blöcke ersetzt. Bisher war nur die negative Fassung da („läuft nicht weiter")."""
    from services.analyst_eval import load_skill_body
    # Zeilenumbrüche glätten — die Regel ist im Markdown umbrochen, geprüft wird der Inhalt.
    skill = " ".join(load_skill_body().split())
    assert "durchgehend an derselben Position" in skill
    assert "taucht danach nicht wieder auf" in skill


def test_schema_koppelt_texthook_nicht_an_nicht_gesprochen():
    """Dritte Stelle desselben Widerspruchs: Das Ausgabeschema definierte Score 0 über „kein
    nicht-gesprochener Bildtext". Damit fällt jede mitgesprochene Texthook auf 0 — dieselbe
    Fehlklassifikation, die Skill und User-Message eigentlich verhindern sollen."""
    from services.analyst_eval import OUTPUT_SCHEMA
    assert "nicht-gesprochener Bildtext" not in OUTPUT_SCHEMA


def test_untertitel_regel_entscheidet_am_wortlaut_nicht_an_der_darstellung():
    """P1, Feedback Run 25b8b2f6: Statische Untertitel-Blöcke wurden als Texthook bewertet. Die alte
    Regel hing an „wechselt mit der Sprache" — genau das Merkmal fehlte dort."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "ZWEI Merkmalen, die BEIDE zutreffen müssen" in skill
    assert "Untertitel müssen weder wechseln noch unten sitzen" in skill
    # Untertitelspur darf nie Auslöser einer Texthook-Empfehlung sein
    assert "nie Gegenstand einer Texthook-Empfehlung" in skill
    # die alte, zu enge Definition ist raus
    assert "wechselt mit der Sprache" not in skill
    assert "wechselnde Zeile in der unteren Bildhälfte" not in skill


def test_einzelner_textblock_ist_texthook_kein_untertitel():
    """Regression aus der ersten Runde: Weil die Texthook fast wortgleich mit dem Gesprochenen war,
    stufte das Modell sie als Untertitel ein und gab Score 0 (Run 3185d209, Chris: „nicht richtig.
    texthook ist vorhanden."). Wortgleichheit allein reicht als Kriterium nicht."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "Trifft nur Punkt 1 zu, ist es eine TEXT-HOOK" in skill
    assert "sondern REDUNDANT" in skill and "nicht Score 0" in skill


def test_texthook_laengenregel_gilt_auch_fuer_die_bewertung():
    """P13: Die 9-Wörter-Regel galt nur für Vorschläge. Chris zu Run 25b8b2f6: „die texthook oben ist
    etwas zu lang. sie sollte im besten fall nie länger als 7-9 wörter sein.\""""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "gilt für die BEWERTUNG der vorhandenen genauso wie für jeden VORSCHLAG" in skill
    assert "höchstens text_hook_score 3" in skill


def test_laengenregel_steht_nur_einmal():
    """Prompt-Hygiene: Die Regel stand zusätzlich als „HARTE REGEL Texthook-Länge" im V2-Override.
    Zwei Fassungen derselben Regel driften auseinander und erzeugen Varianten im Output."""
    from services.analyst_eval import load_skill_body
    from services.analyst_gemini_eval import _user_message
    override = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "HARTE REGEL Texthook-Länge" not in override
    assert load_skill_body().count("LÄNGE der Text-Hook") == 1


def test_reaction_blick_auf_laptop_ist_kein_ablesen():
    """P7, Feedback Run 093dc5a7: „er schaut sich das fremdvideo am laptop an" — der Pflicht-Blickcheck
    hatte keine Reaction-Ausnahme und überschrieb die anderen Reaction-Regeln."""
    from services.analyst_gemini_eval import _user_message
    txt = _user_message(_result(gewaehltes_format="Reaction"), "hybrid")
    assert "Laptop, Handy oder einen zweiten Bildschirm" in txt and "FUNKTIONAL" in txt
    assert "Die unten stehende Blickkontakt-Pflicht gilt hier NUR" in txt
    # Talking Head behält den Pflicht-Check ohne Ausnahme
    th = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "PFLICHT Blickkontakt" in th and "FUNKTIONAL" not in th


def test_einblendungen_gehen_ins_eigene_feld():
    """P8 + Feedback 3185d209: Motiv bleibt Sache des Nutzers, und mehrere Stellen werden zu EINEM
    Schritt gebündelt — beides steuert jetzt das Feld `einblendungen`."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "gehören ins Feld `einblendungen`" in skill
    assert "HÖCHSTENS 3\n  Stellen" in skill
    assert "Folgen-Knopf" in skill   # Abgrenzung: CTA-Einblendungen bleiben normale Empfehlungen


def test_empfehlungsregeln_stehen_nur_an_einer_stelle():
    """Prompt-Hygiene: Die Empfehlungs-Regeln standen 3× fast wortgleich (Skill, V2-Override,
    JSON-Vertrag). Redundanz erzeugt Varianten — die kanonische Fassung lebt jetzt im Skill."""
    from services.analyst_eval import OUTPUT_SCHEMA, load_skill_body
    from services.analyst_gemini_eval import _user_message
    skill = load_skill_body()
    override = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "Empfehlungen — die kanonische Regel" in skill
    # Override und Schema verweisen nur noch
    assert "kanonische Regel" in override and "kanonische Regel" in OUTPUT_SCHEMA
    for wiederholung in ("KEINE Kategorie", "Im Zweifel eigenes Label", "Sortieren, Priorisieren"):
        assert wiederholung not in override, f"{wiederholung!r} steht doppelt im Override"


def test_stumme_videos_regel_ersetzt_die_alte_score_0_regel():
    """P9: Die alte Zeile „Keine Sprache erkannt → score 0" hat den Fehler selbst produziert."""
    from services.analyst_eval import OUTPUT_SCHEMA, load_skill_body
    skill = load_skill_body()
    assert "## Videos ohne gesprochenes Wort" in skill
    assert "Keine Sprache erkannt → score 0" not in skill
    assert "Empfiehl NICHT,\netwas einzusprechen" in skill
    assert "oder null wenn im Video niemand spricht" in OUTPUT_SCHEMA


def test_sprechhook_hat_eigene_massstaebe():
    """P12, Chris: „Eine gute sprechhook ist in der regel deutlich länger als eine texthook." Ohne
    diesen Satz überträgt das Modell die 9-Wörter-Grenze auf den gesprochenen Einstieg."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "Die 9-Wörter-Grenze gilt für ihn\nNICHT" in skill
    # Formulierung am 2026-07-30 geschärft: „im Zweifel die TEXT-HOOK" war unbestimmt und stand in
    # Widerspruch zum Sprech-Hook-Abzug fürs Vorlesen. Jetzt zwei getrennte Folgen einer Regel.
    assert "Für die Doppelung zahlt die TEXT-HOOK" in skill


def test_aesthetik_referenz_gilt_nur_fuer_talking_head():
    """P14: Chris' Framing-Standard beschreibt ausdrücklich nur Talking-Head-Videos."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "dieser Standard gilt NUR für Talking Head" in skill
    # „Untertitel-Platzierung" stand hier bis 2026-08-06 als Punkt 4 — sie ist in den eigenen
    # `untertitel`-Block umgezogen, weil sie dort sonst zusätzlich den Ästhetik-Score gedeckelt hätte.
    for punkt in ("Kopfraum", "Augenhöhe", "1080p"):
        assert punkt in skill, f"{punkt!r} fehlt in der Ästhetik-Referenz"
    aesthetik = skill.split("## Visuelle Ästhetik")[1].split("\n## ")[0]
    assert "Abzug, wenn sie das Gesicht" not in aesthetik


def test_score_regel_verweist_aufs_system_statt_auf_den_funnel():
    """P11: Der funnel-abhängige Gewichtungsblock ist raus — der Code rechnet jetzt."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "berechnet das SYSTEM, nicht du" in skill
    assert "funnel-abhängig gewichten" not in skill
    assert "## Funnel" in skill  # die Einordnung selbst bleibt, sie ist für den Nutzer relevant


def test_pausen_und_varianten_werden_nicht_doppelt_erklaert():
    """Prompt-Hygiene: Beide neuen Felder werden im Skill definiert, Override und Schema verweisen nur."""
    from services.analyst_eval import OUTPUT_SCHEMA
    from services.analyst_gemini_eval import _user_message
    override = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "pausen_urteile" in override      # der Override nennt das Feld …
    # … buchstabiert die Urteils-Kategorien aber nicht neu aus

    for wiederholung in ("Stockung/Denkpause", "Dramaturgische Pause", "Übergangspause"):
        assert wiederholung not in override, f"{wiederholung!r} steht doppelt im Override"
    # Varianten-Regel steht nur im Skill; Schema nennt nur das Format
    assert "andere Mechanik" in OUTPUT_SCHEMA and "Provokation" not in OUTPUT_SCHEMA


# ---------- Lautheit: empirischer Bereich statt geratenem Richtwert ----------

def test_metrics_guide_nutzt_empirische_lautheitsgrenzen():
    """Der geratene Streaming-Richtwert (-14/-20) hat leises Material fälschlich als 'zu leise'
    markiert. Ersetzt durch Chris' gemessene Referenz. METRICS_GUIDE MUSS die Konstanten spiegeln."""
    from services.analyst_eval import METRICS_GUIDE
    from services.analyst_quality import LOUDNESS_OPTIMAL_LOW, LOUDNESS_OPTIMAL_HIGH
    assert f"{LOUDNESS_OPTIMAL_LOW}" in METRICS_GUIDE and f"{LOUDNESS_OPTIMAL_HIGH}" in METRICS_GUIDE
    assert "-14 LUFS" not in METRICS_GUIDE and "-20 LUFS" not in METRICS_GUIDE  # alter Richtwert weg
    # LeopoldSchultz (-18.8) liegt ÜBER der Obergrenze → darf nicht mehr als „zu leise" gelten:
    assert -18.8 > LOUDNESS_OPTIMAL_HIGH


def test_lautheitsgrenzen_sind_plausibel():
    from services.analyst_quality import LOUDNESS_OPTIMAL_LOW, LOUDNESS_OPTIMAL_HIGH, LOUDNESS_TOO_QUIET
    assert LOUDNESS_OPTIMAL_LOW < LOUDNESS_OPTIMAL_HIGH < 0
    assert LOUDNESS_TOO_QUIET < LOUDNESS_OPTIMAL_LOW  # „zu leise" liegt unter der Untergrenze


# ---------- Whisper-Determinismus (Input-Drift) ----------

def test_whisper_parameter_sind_deterministisch_gepinnt():
    """Regression: temperature/condition_on_previous_text NICHT zu setzen heißt, die stochastische
    Fallback-Leiter von faster-whisper zu aktivieren (Default [0.0, 0.2 … 1.0]). Real beobachtet:
    99 vs. 111 Wörter bei byte-identischer Datei — nicht erkannte Wörter sehen in der Pausenmessung
    wie Stille aus."""
    import inspect
    from services import whisper_service
    quelle = inspect.getsource(whisper_service.transcribe_with_word_timestamps)
    assert "temperature=0.0" in quelle, "temperature muss auf 0.0 gepinnt sein (Float, nicht Liste!)"
    assert "condition_on_previous_text=False" in quelle, "sonst kaskadiert eine frühe Abweichung"


def test_transkript_hash_ist_stabil_und_kurz():
    from services.whisper_service import transkript_hash
    h = transkript_hash("Der Kunde wird überzeugt sein.")
    assert h == transkript_hash("Der Kunde wird überzeugt sein.") and len(h) == 12
    assert h != transkript_hash("Der Kunde wird überzeugt sein!")


# ---------- is_available (Gemini-Key) ----------

def test_is_available_no_key(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "")
    ok, msg = analyst_vlm.is_available()
    assert ok is False and "GEMINI" in msg.upper()


def test_is_available_ok(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    assert analyst_vlm.is_available() == (True, "")


# ---------- _parse_array + _desc_fields (Frame-basiert) ----------

def test_parse_array_full_with_texte_list():
    # neues Format: mehrere unabhängige Texte, jeder mit eigener Darstellung
    raw = json.dumps([
        {"handlung": "Mann im Auto", "personen": "1 Mann",
         "texte": [{"wortlaut": "TITEL OBEN", "darstellung": "oben, statisch"},
                   {"wortlaut": "untertitel", "darstellung": "unten, dynamisch"}],
         "kamera": "Nah", "bild_fakten": {"komposition": "zentriert", "licht": "hell", "hintergrund": "Auto"},
         "effekte": "Zoom-In"},
    ])
    f = analyst_vlm._desc_fields(analyst_vlm._parse_array(raw)[0])
    assert f["effekte"] == "Zoom-In" and f["bild_fakten"]["licht"] == "hell"
    assert len(f["texte"]) == 2
    assert f["texte"][0] == {"wortlaut": "TITEL OBEN", "darstellung": "oben, statisch"}
    assert f["text_overlays"] == "TITEL OBEN | untertitel"  # abgeleiteter Join


def test_desc_fields_fallback_old_flat_format():
    f = analyst_vlm._desc_fields({"handlung": "x", "text_overlays": "GELD", "text_darstellung": "unten"})
    assert f["texte"] == [{"wortlaut": "GELD", "darstellung": "unten"}]
    assert f["text_overlays"] == "GELD"


def test_parse_array_markdown_and_wrapped_dict():
    raw = "```json\n" + json.dumps({"frames": [{"handlung": "x"}]}) + "\n```"
    objs = analyst_vlm._parse_array(raw)
    assert len(objs) == 1 and objs[0]["handlung"] == "x"
    f = analyst_vlm._desc_fields(objs[0])
    assert f["bild_fakten"] == {"komposition": "", "licht": "", "hintergrund": ""}


def test_frames_mad():
    import numpy as np
    from services.analyst_frames import _mad
    a, b = np.zeros((32, 32), "float32"), np.full((32, 32), 200, "float32")
    assert _mad(a, a) == 0.0 and _mad(a, b) == 200.0


# ---------- SceneDescription ----------

def test_scene_description_accepts_bild_fakten_dict():
    from models.analyst import SceneDescription
    s = SceneDescription(index=0, start=0.0, end=2.0, handlung="h",
                         bild_fakten={"komposition": "links", "licht": "dunkel", "hintergrund": "Wand"})
    assert s.bild_fakten.licht == "dunkel"


# ---------- AnalystEvaluationV2 ----------

def test_evaluation_v2_parses_full():
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(**{
        "zielgruppe": "Anfänger.", "format": "Talking-Head", "performance_score": 78, "funnel": "TOFU",
        "hook": {"sprech_hook_score": 4, "sprech_hook_grund": "Frage", "text_hook_vorhanden": True,
                 "text_hook_score": 3, "text_hook_grund": "generisch"},
        "struktur": {"score": 4, "elemente": {"hook": True, "bridge": True, "mid": True, "peak": True, "cta": False}, "kommentar": "ok"},
        "sprechqualitaet": {"score": 3, "probleme": ["Füllwörter"]},
        "schnitt_pacing": {"score": 4, "kommentar": "knapp"},
        "spannungsbogen": {"score": 3, "kommentar": "fällt ab"},
        "visuelle_aesthetik": {"score": 4, "probleme": []},
        "top_tipps": ["CTA", "kürzen"],
    })
    assert ev.hook.text_hook_vorhanden and ev.struktur.elemente.peak and ev.top_tipps == ["CTA", "kürzen"]


def test_evaluation_v2_tolerates_missing_blocks():
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2(performance_score=50)
    assert ev.hook.sprech_hook_score == 0 and ev.sprechqualitaet.probleme == []


# ---------- analyst_eval: System-Prompt + User-Message ----------

def test_build_system_prompt_strips_frontmatter_and_appends_schema():
    from services import analyst_eval
    sys = analyst_eval.build_system_prompt()
    assert not sys.lstrip().startswith("---")
    assert "Leitprinzip" in sys and '"performance_score"' in sys


def test_build_user_message_marks_hook_and_segments():
    from models.analyst import AnalystResult, SceneDescription, SpeechStats
    from services import analyst_eval
    res = AnalystResult(
        id="x", filename="reel.mp4", duration_sec=12.0, scene_count=2,
        scenes=[
            SceneDescription(index=0, start=0.0, end=3.0, handlung="Person spricht", kamera="Nah",
                             texte=[{"wortlaut": "3 FEHLER", "darstellung": "oben, statisch"}],
                             text_overlays="3 FEHLER", gesprochener_text="Diese drei Fehler",
                             bild_fakten={"komposition": "zentriert", "licht": "hell", "hintergrund": "Büro"}),
            SceneDescription(index=1, start=3.0, end=12.0, handlung="Demo"),
        ],
        transcript="Diese drei Fehler kosten dich Reichweite.",
        speech_stats=SpeechStats(wort_anzahl=7, sprech_dauer_sec=5.0, wpm=84.0,
                                 filler_count=0, filler_words=[], pausen_count=0, laengste_pause_sec=0.0),
    )
    msg = analyst_eval.build_user_message(res)
    assert "Segment 1" in msg and "[ERÖFFNUNG]" in msg
    assert "TEXT-HOOK-KANDIDAT" in msg and "3 FEHLER" in msg
    assert "(oben, statisch)" in msg          # Text + Darstellung verknüpft
    assert "Tesseract" not in msg             # OCR-Pfad entfernt


# ---------- Qualitäts-Messwerte (Audio bleibt, Frames optional leer) ----------

def test_audio_metrics_sine_and_silence(tmp_path):
    import subprocess
    from services.analyst_quality import audio_metrics
    vid = tmp_path / "tone.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=64x64:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
                    "-shortest", "-pix_fmt", "yuv420p", str(vid)], check=True, capture_output=True)
    m = audio_metrics(vid)
    assert m["lufs_integrated"] is not None and -60 < m["lufs_integrated"] < 0


def test_frame_metrics_empty_list_is_zero():
    from services.analyst_quality import frame_metrics
    assert frame_metrics([])["schaerfe_avg"] == 0.0


# ---------- API-Smoke (Upload → Start → Get), Engine + Gemini gemockt ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import services.analyst_engine as eng
    import api.analyst as api_analyst
    from main import app
    monkeypatch.setattr(eng, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(api_analyst, "ANALYST_PATH", tmp_path)
    return TestClient(app)


def test_upload_start_get_roundtrip(client, monkeypatch):
    import api.analyst as api_analyst
    r = client.post("/api/analyst/upload", files={"file": ("mein reel.mp4", b"\x00\x01\x02", "video/mp4")})
    assert r.status_code == 200
    run_id = r.json()["id"]

    monkeypatch.setattr(api_analyst.analyst_vlm, "is_available", lambda: (False, "GEMINI_API_KEY fehlt in der .env."))
    r = client.post(f"/api/analyst/{run_id}/start?format=Talking+Head")
    assert r.status_code == 503 and "GEMINI" in r.json()["detail"].upper()

    calls = []
    monkeypatch.setattr(api_analyst.analyst_vlm, "is_available", lambda: (True, ""))
    monkeypatch.setattr(api_analyst, "run_analysis", lambda rid: calls.append(rid))
    r = client.post(f"/api/analyst/{run_id}/start?skip_eval=true&format=Reaction")
    assert r.status_code == 200 and calls == [run_id]
    assert r.json()["format"] == "Reaction"
    assert client.get(f"/api/analyst/{run_id}").status_code == 200


def test_start_ohne_format_wird_abgelehnt(client, monkeypatch):
    """Format ist Pflicht — ohne Auswahl darf die Analyse gar nicht erst starten."""
    import api.analyst as api_analyst
    monkeypatch.setattr(api_analyst.analyst_vlm, "is_available", lambda: (True, ""))
    run_id = client.post(
        "/api/analyst/upload", files={"file": ("r.mp4", b"\x00", "video/mp4")}
    ).json()["id"]

    r = client.post(f"/api/analyst/{run_id}/start")
    assert r.status_code == 422 and "Format" in r.json()["detail"]

    r = client.post(f"/api/analyst/{run_id}/start?format=Podcast")
    assert r.status_code == 422 and "Podcast" in r.json()["detail"]


def test_get_unknown_run_404(client):
    assert client.get("/api/analyst/gibtsnicht").status_code == 404


# ---------- Admin-/Feedback-Ansicht ----------

def test_admin_verify_prueft_passwort_serverseitig(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "admin_password", "geheim123")
    assert client.post("/api/analyst/admin/verify", json={"password": "falsch"}).status_code == 401
    assert client.post("/api/analyst/admin/verify", json={"password": ""}).status_code == 401
    r = client.post("/api/analyst/admin/verify", json={"password": "geheim123"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ohne_gesetztes_passwort_ist_admin_gesperrt(client, monkeypatch):
    """Fail closed: leeres ADMIN_PASSWORD (z.B. .env vergessen) darf NICHTS freischalten —
    das Repo ist oeffentlich, ein Default im Code waere fuer jeden lesbar."""
    from config import settings
    monkeypatch.setattr(settings, "admin_password", "")
    assert client.post("/api/analyst/admin/verify", json={"password": ""}).status_code == 401
    assert client.post("/api/analyst/admin/verify", json={"password": "feedback"}).status_code == 401


def test_feedback_nur_mit_passwort_und_landet_in_jsonl(client, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "admin_password", "geheim123")
    run_id = client.post(
        "/api/analyst/upload", files={"file": ("v.mp4", b"\x00", "video/mp4")}
    ).json()["id"]

    # ohne/mit falschem Passwort: abgelehnt — sonst könnte jeder den Datensatz vollmüllen
    r = client.post(f"/api/analyst/{run_id}/feedback",
                    json={"password": "falsch", "field_id": "hook.sprech", "verdict": "down"})
    assert r.status_code == 401

    ok = {"password": settings.admin_password, "field_id": "hook.sprech",
          "verdict": "down", "text": "zu generisch"}
    assert client.post(f"/api/analyst/{run_id}/feedback", json=ok).status_code == 200

    # zweiter Eintrag zum selben Feld wird ANGEHÄNGT (Urteilsänderung bleibt sichtbar),
    # beim Auslesen gewinnt der letzte
    ok2 = {**ok, "verdict": "up", "text": "doch gut"}
    assert client.post(f"/api/analyst/{run_id}/feedback", json=ok2).status_code == 200

    zeilen = (tmp_path / run_id / "feedback.jsonl").read_text().strip().split("\n")
    assert len(zeilen) == 2
    erster = json.loads(zeilen[0])
    assert erster["field_id"] == "hook.sprech" and erster["verdict"] == "down"
    assert erster["prompt_version"]      # Versionsstempel muss dran sein
    assert erster["run_id"] == run_id

    gelesen = client.get(f"/api/analyst/{run_id}/feedback").json()
    assert gelesen["hook.sprech"] == {"verdict": "up", "text": "doch gut"}


def test_feedback_ohne_field_id_abgelehnt(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "admin_password", "geheim123")
    run_id = client.post(
        "/api/analyst/upload", files={"file": ("v.mp4", b"\x00", "video/mp4")}
    ).json()["id"]
    r = client.post(f"/api/analyst/{run_id}/feedback",
                    json={"password": settings.admin_password, "field_id": "  "})
    assert r.status_code == 422


# ---------- Redundanz Text-Hook / Sprech-Hook (Feedback 5502bb37) ----------

def _hook_lauf(wortlaut: str, transkript: str, text_score: int = 4, sprech_score: int = 4):
    """Minimaler Lauf zum Prüfen der Redundanz-Klemme."""
    from models.analyst import AnalystEvaluationV2, AnalystResult, HookEval
    ev = AnalystEvaluationV2(
        hook=HookEval(
            sprech_hook_score=sprech_score, sprech_hook_grund="Guter Einstieg.",
            text_hook_vorhanden=True, text_hook_score=text_score,
            text_hook_wortlaut=wortlaut, text_hook_grund="Zieht Blicke an.",
        ),
    )
    res = AnalystResult(id="x", filename="v.mp4", duration_sec=30.0, scene_count=0,
                        scenes=[], transcript=transkript, gewaehltes_format="Andere")
    return ev, res


def test_vorgelesene_texthook_klemmt_beide_hook_scores():
    """Eine ECHTE Text-Hook, die zu Beginn mitgesprochen wird: Doppelung zahlt die Text-Hook,
    die verschenkten Eröffnungssekunden zahlt der Sprech-Hook. Die Regel stand im Skill und griff
    im Lauf 5502bb37 nicht — ob zwei Textstellen übereinstimmen, ist aber Stringvergleich."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Du verlierst tausende Euro Umsatz",
                         "Du verlierst tausende Euro Umsatz, wenn du beide Leads gleich behandelst.")
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 2
    assert n.hook.sprech_hook_score == 3
    assert n.hook.text_hook_score_geklemmt is True


def test_grafik_text_klemmt_nur_den_sprech_hook(monkeypatch):
    """Fall 5502bb37 nach der neuen Regel: „Inbound vs. Outbound" ist grafischer Inhalt, das Modell
    meldet text_hook_vorhanden=false mit Score 0 — trotzdem MUSS der Sprech-Hook den Abzug fürs
    Vorlesen bekommen. Genau das ginge verloren, wäre die Prüfung an text_hook_vorhanden gekoppelt."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Inbound vs. Outbound",
                         "Inbound vs. Outbound, wer beide gleich behandelt, verliert am Ende beide.")
    ev.hook.text_hook_vorhanden = False          # grafischer Inhalt
    ev.hook.text_hook_score = 0
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 0           # 0 ist schon das Minimum, nicht angehoben
    assert n.hook.sprech_hook_score == 3         # der eigentliche Punkt


def test_geklemmter_score_erzwingt_beide_hook_empfehlungen():
    """Der eigentliche Schaden im Lauf: Score 4 → Varianten unterdrückt → KEINE Texthook-Empfehlung.
    Nach der Klemme müssen beide Empfehlungen stehen, obwohl das Modell keine Varianten lieferte."""
    from models.analyst import AnalystResult
    from services.analyst_eval import nachbearbeiten
    ev, res = _hook_lauf("Du verlierst tausende Euro Umsatz",
                         "Du verlierst tausende Euro Umsatz, wenn du beide Leads gleich behandelst.")
    assert ev.texthook_varianten == []          # so kam es aus dem Modell
    n = nachbearbeiten(ev, res)
    texte = " ".join(s.anweisung.lower() for s in n.action_steps + n.weitere_empfehlungen)
    assert "texthook" in texte
    assert "ersten gesprochenen satz" in texte


def test_begruendung_wird_ersetzt_und_widerspricht_sich_nicht():
    """Angehängt statt ersetzt läse der Kunde „zieht Blicke an … verschenkst eine Hook-Ebene"."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Inbound vs. Outbound",
                         "Inbound vs. Outbound, wer beide gleich behandelt, verliert beide.")
    n = bereinige_redundante_texthook(ev, res)
    assert "Zieht Blicke an" not in (n.hook.text_hook_grund or "")
    assert "Guter Einstieg" not in (n.hook.sprech_hook_grund or "")


def test_ohne_redundanz_bleiben_die_scores_stehen():
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Du verlierst tausende Euro",
                         "Inbound und Outbound sind zwei völlig verschiedene Welten.")
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 4
    assert n.hook.sprech_hook_score == 4
    assert n.hook.text_hook_score_geklemmt is False


def test_ohne_zitierten_wortlaut_wird_nicht_geraten():
    """Kein Zitat → kein Check. Ein Treffer auf gut Glück wäre schlimmer als keiner."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("", "Inbound vs. Outbound, wer beide gleich behandelt, verliert beide.")
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 4


def test_einzelwort_loest_keine_klemme_aus():
    """Ein einzelnes Wort trifft im Transkript zu leicht zufällig zu."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Outbound", "Inbound vs. Outbound, wer beide gleich behandelt, verliert.")
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 4


def test_klemme_erhoeht_nie_einen_score():
    """Eine schwach bewertete Hook darf durch die Klemme nicht besser werden."""
    from services.analyst_eval import bereinige_redundante_texthook
    ev, res = _hook_lauf("Inbound vs. Outbound",
                         "Inbound vs. Outbound, wer beide gleich behandelt, verliert beide.",
                         text_score=1, sprech_score=2)
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 1
    assert n.hook.sprech_hook_score == 2


def test_redundanz_nur_in_der_eroeffnung():
    """Steht der Bildtext erst spät im Transkript, ist die Hook nicht verschenkt."""
    from services.analyst_eval import bereinige_redundante_texthook
    spaet = " ".join(["füllwort"] * 30) + " Inbound vs. Outbound"
    ev, res = _hook_lauf("Inbound vs. Outbound", spaet)
    n = bereinige_redundante_texthook(ev, res)
    assert n.hook.text_hook_score == 4


def test_skill_verbietet_lesbarkeit_als_hook_begruendung():
    """Das Modell begründete Score 4 mit „ohne Ton verständlich" und „zieht Blicke an" — das ist
    Lesbarkeit, nicht Hook-Wirkung. Ohne diese Regel im Skill darf es das."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body().lower()
    assert "ohne ton verständlich" in s
    assert "zieht blicke an" in s
    # Der Trennstrich ist „Teil des grafischen Inhalts" (Chris), nicht eine Liste verbotener
    # Textsorten — eine Liste bleibt immer lückenhaft, die Regel generalisiert.
    assert "spaltenüberschrift einer vergleichsgrafik" in s


def test_skill_verlangt_den_zitierten_wortlaut():
    from services.analyst_eval import OUTPUT_SCHEMA
    assert "text_hook_wortlaut" in OUTPUT_SCHEMA


# ---------- Prompt-Hygiene: Hook-Regeln stehen genau einmal ----------

def _skill_abschnitte() -> list[str]:
    """Skill grob in Absätze zerlegen — Absatz = Block zwischen Leerzeilen."""
    from services.analyst_eval import load_skill_body
    return [a for a in load_skill_body().split("\n\n") if a.strip()]


def test_redundanz_regel_steht_genau_einmal():
    """CLAUDE.md: „jede Regel genau einmal". Am 2026-07-30 stand die Zuweisung des Abzugs in ZWEI
    Abschnitten mit gegenläufiger Aussage („Abzug bekommt die TEXT-HOOK" vs. Abzug beim Sprech-Hook).
    Der vorhandene Hygiene-Test deckte nur Pausen und Varianten ab — deshalb fiel es nicht auf."""
    treffer = [a for a in _skill_abschnitte() if "zahlt die TEXT-HOOK" in a or "zahlt der SPRECH-HOOK" in a]
    assert len(treffer) == 1, f"Redundanz-Zuweisung in {len(treffer)} Abschnitten statt einem"
    assert "KANONISCHE Fassung" in treffer[0]


def test_redundanz_regel_weist_den_abzug_widerspruchsfrei_zu():
    """Beide Folgen müssen im selben Block stehen UND getrennt benannt sein: Doppelung → Text-Hook,
    verschenkte Eröffnungssekunden → Sprech-Hook. Ohne die Trennung liest das Modell zwei Regeln."""
    block = next(a for a in _skill_abschnitte() if "KANONISCHE Fassung" in a)
    assert "Für die Doppelung zahlt die TEXT-HOOK" in block
    assert "Für verschenkte Eröffnungssekunden zahlt der SPRECH-HOOK" in block
    assert "nicht für die Doppelung" in block


def test_fehl_kriterien_stehen_genau_einmal():
    treffer = [a for a in _skill_abschnitte() if "zieht Blicke an" in a]
    assert len(treffer) == 1, "Fehl-Kriterien mehrfach im Skill"


def test_grafik_pruefschritt_geht_den_anderen_regeln_vor():
    """Ohne ausdrückliche Vorrangregel bleibt unbestimmt, was bei Text gilt, der Grafik-Inhalt UND
    mitgesprochen ist — genau der Fall aus Lauf 5502bb37."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "geht allen anderen Text-Hook-Regeln VOR" in s
    assert "grafischen Inhalt" in s


def test_score_0_hat_zwei_begruendungs_fassungen():
    """„Es gibt keine Text-Hook im Bild" wäre bei Grafik-Text faktisch falsch — Text WAR sichtbar."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "Es gibt keine Text-Hook im Bild" in s
    assert "gehört zu deiner Grafik" in s


def test_sprechhook_abschnitt_wiederholt_die_redundanz_regel_nicht():
    """Der Halbsatz „Am stärksten ERGÄNZT er die Text-Hook, statt sie zu wiederholen" war die dritte
    Nennung derselben Idee. Er verweist jetzt nur noch."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "Am stärksten ERGÄNZT er die Text-Hook" not in s
    assert "siehe die Redundanz-Regel unten" in s


# ---------- Fixes aus Lauf c12db030 ----------

def test_genullte_bildwerte_gelten_nicht_als_messung():
    """Kern-Bug c12db030: v2_hybrid zieht keine Frames, Schärfe/Helligkeit/Kontrast bleiben 0.0.
    Die als Fakt zu senden — direkt gefolgt von „verlässlicher als dein Seheindruck" — hat die
    Bildkritik komplett unterdrückt."""
    from models.analyst import QualityMetrics
    from services.analyst_eval import bildwerte_belastbar
    assert bildwerte_belastbar(QualityMetrics(lufs_integrated=-14.5)) is False
    assert bildwerte_belastbar(QualityMetrics(schaerfe_avg=120.0, helligkeit_avg=110.0,
                                             kontrast_avg=45.0)) is True
    assert bildwerte_belastbar(None) is False


def test_prompt_nennt_keine_bildzahlen_wenn_nicht_gemessen():
    from models.analyst import QualityMetrics
    from services.analyst_eval import metrics_txt
    txt = metrics_txt(QualityMetrics(lufs_integrated=-14.5, loudness_range=3.5, true_peak_db=-0.5))
    assert "Schärfe avg" not in txt
    assert "KEINE Messwerte vor" in txt
    assert "-14.5 LUFS" in txt                     # Audio bleibt, das ist echt gemessen
    assert "Laplacian" not in txt                  # auch die Bild-Richtwerte sind sinnlos


def test_prompt_nennt_bildzahlen_wenn_gemessen():
    from models.analyst import QualityMetrics
    from services.analyst_eval import metrics_txt
    txt = metrics_txt(QualityMetrics(schaerfe_avg=120.0, schaerfe_min=80.0, helligkeit_avg=110.0,
                                     kontrast_avg=45.0, lufs_integrated=-30.0))
    assert "Schärfe avg 120.0" in txt and "KEINE Messwerte" not in txt


def test_v2_override_macht_kopfraum_und_bildqualitaet_zur_pflicht():
    """Die Kopfraum-Regel stand im Skill und feuerte nicht — beim Blickkontakt gibt es eine
    ausdrückliche Pflicht, hier fehlte sie (Feedback c12db030)."""
    from services.analyst_gemini_eval import _user_message
    o = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "PFLICHT Bildaufbau und Bildqualität" in o
    assert "KOPFRAUM" in o and "BILDQUALITÄT" in o
    # Der Zielwert steht nur im Skill, der Override verweist — keine zweite Ausformulierung
    assert "10–15" not in o


def test_texthook_empfehlung_ist_optimierung_wenn_eine_existiert():
    """c12db030: text_hook_vorhanden=true und trotzdem „Blende eine Texthook ein" auf Platz 1."""
    from services.analyst_eval import _texthook_anweisung
    assert "Blende" in _texthook_anweisung([], vorhanden=False)
    opt = _texthook_anweisung([], vorhanden=True)
    assert "Blende" not in opt and "Überarbeite" in opt


def test_texthook_empfehlung_nennt_nur_die_gemeldeten_maengel():
    """Feedback 4e56336e: „bei tipp 2 hätte nur die textinhaltsanpassung gereicht. optisch ist die
    texthook in ordnung … er soll aber dynamisch sein und von fall zu fall unterschiedlich
    formuliert sein." Vorher listete der feste Text ALLE Gestaltungspunkte."""
    from services.analyst_eval import _texthook_anweisung
    nur_schrift = _texthook_anweisung([], vorhanden=True, maengel=["lesbarkeit"])
    assert "lesbare Schrift" in nur_schrift
    for fremd in ("5 Sekunden", "Drittel", "Farbe", "Wörter"):
        assert fremd not in nur_schrift, f"{fremd!r} gehört hier nicht hin"

    nur_inhalt = _texthook_anweisung([], vorhanden=True, maengel=["wortlaut"])
    assert "zugespitzter" in nur_inhalt and "Schrift" not in nur_inhalt

    zwei = _texthook_anweisung([], vorhanden=True, maengel=["groesse", "dauer"])
    assert "kleiner" in zwei and "5 Sekunden" in zwei and "Farbe" not in zwei


def test_texthook_ohne_gemeldete_maengel_erfindet_keine_kritik():
    from services.analyst_eval import _texthook_anweisung
    t = _texthook_anweisung([], vorhanden=True, maengel=[])
    assert "Überarbeite" in t
    for fremd in ("5 Sekunden", "Schrift", "Farbe", "kleiner"):
        assert fremd not in t


def test_doppelte_texthook_empfehlung_wird_erkannt_ohne_das_wort_texthook():
    """Der Doppel-Schritt in c12db030 hieß „Ändere den Text auf dem Bildschirm so, dass …" —
    er enthielt keines der Hook-Wörter und rutschte durch den Filter."""
    from services.analyst_eval import _TEXTHOOK_THEMA
    assert _TEXTHOOK_THEMA.search("Ändere den Text auf dem Bildschirm so, dass er neu ist.")
    assert _TEXTHOOK_THEMA.search("Kürze die Texthook.")
    assert not _TEXTHOOK_THEMA.search("Schneide die Sprechpause bei Sekunde 3 raus.")


def _ev_aesthetik(score, probleme=None):
    from models.analyst import AnalystEvaluationV2, ScoreProbleme
    return AnalystEvaluationV2(
        visuelle_aesthetik=ScoreProbleme(score=score, probleme=probleme or []))


def _ev_dim(**scores):
    """Bewertung mit gezielt gesetzten Dimensions-Scores."""
    from models.analyst import (AnalystEvaluationV2, ScoreKommentar, ScoreProbleme, StrukturEval)
    ev = AnalystEvaluationV2()
    for name, wert in scores.items():
        if isinstance(wert, tuple):
            score, text = wert
        else:
            score, text = wert, None
        block = getattr(ev, name)
        block.score = score
        if text is not None:
            if hasattr(block, "probleme"):
                block.probleme = [text]
            else:
                block.kommentar = text
    return ev


def test_score_3_erzwingt_eine_handlungsempfehlung():
    """Vorgabe Chris (3c9a8d95): „überall wo der score eine 3 oder schlechter ist, sollte eine
    handlungsempfehlung oben beschrieben werden". Im Lauf hatte spannungsbogen 3 mit benannter
    Schwäche und keinen Schritt."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = erzwinge_empfehlungen_bei_schwachen_scores(
        _ev_dim(spannungsbogen=(3, "Es plätschert im Mittelteil")))
    assert len(ev.empfehlungen) == 1
    assert ev.empfehlungen[0].gruppe == "spannungsbogen"
    assert ev.empfehlungen[0].zeitpunkt_sek == 0.0
    assert "plätschert im Mittelteil" in ev.empfehlungen[0].anweisung


def test_score_4_erzwingt_nichts():
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    assert erzwinge_empfehlungen_bei_schwachen_scores(_ev_dim(spannungsbogen=4)).empfehlungen == []


def test_score_0_ist_kein_urteil():
    """0 ist der Pydantic-Default bei Altläufen und Teil-Antworten, keine schwache Bewertung —
    dieselbe Falle wie beim Sprech-Hook. Dieser Test hat den Fehler beim Bauen gefangen."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    assert erzwinge_empfehlungen_bei_schwachen_scores(
        _ev_dim(spannungsbogen=0, visuelle_aesthetik=0, struktur=0)).empfehlungen == []


def test_aesthetik_bei_score_3_erzwingt_jetzt_auch(monkeypatch):
    """Geänderte Vorgabe: vorher „unter 3", jetzt „3 oder schlechter". Genau der Fall aus
    e35a568b — Ästhetik 3 mit zwei benannten Mängeln und keinem Schritt."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=3)
    ev.visuelle_aesthetik.probleme = ["Über dem Kopf ist zu viel leerer Raum",
                                      "Der Hintergrund wirkt unruhig"]
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert len(n.empfehlungen) == 1                     # EIN gebündelter Schritt
    assert "zu viel leerer Raum" in n.empfehlungen[0].anweisung
    assert "Hintergrund wirkt unruhig" in n.empfehlungen[0].anweisung


def test_reihenfolge_folgt_den_score_gewichten():
    """Vorgabe Chris: Hooks zuerst, dann nach Gewicht. Ästhetik (17) muss vor Spannungsbogen (10)
    stehen, obwohl der Spannungsbogen den schlechteren Score hat."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = erzwinge_empfehlungen_bei_schwachen_scores(
        _ev_dim(spannungsbogen=1, visuelle_aesthetik=3, schnitt_pacing=2))
    assert [e.gruppe for e in ev.empfehlungen] == ["aesthetik", "spannungsbogen", "schnitt"]


def test_hooks_stehen_vor_den_dimensions_tipps():
    """Hook- und Anlauf-Schritte werden vorne eingefügt, die Dimensions-Tipps angehängt —
    so entscheidet die Einfügereihenfolge, ohne die Zeit-Sortierung anzufassen."""
    from models.analyst import AnalystResult, HookEval
    from services.analyst_eval import nachbearbeiten
    ev = _ev_dim(visuelle_aesthetik=3, spannungsbogen=3)
    ev.hook = HookEval(sprech_hook_score=2, text_hook_vorhanden=False, text_hook_score=0)
    res = AnalystResult(id="x", filename="v.mp4", duration_sec=30.0, scene_count=0, scenes=[],
                        transcript="Text.", gewaehltes_format="Talking Head")
    n = nachbearbeiten(ev, res)
    erste = n.action_steps[0].anweisung.lower()
    assert "texthook" in erste or "gesprochenen satz" in erste
    # Die Dimensions-Tipps kommen danach — hier landen sie in den erweiterten Empfehlungen
    alle = " ".join(s.anweisung for s in n.action_steps + n.weitere_empfehlungen).lower()
    assert "kopf" in alle or "bild in ordnung" in alle


def test_nur_das_gruppen_label_verhindert_eine_doppelung():
    """Gleiches Label → kein zweiter Schritt."""
    from models.analyst import Empfehlung
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=2)
    ev.empfehlungen.append(Empfehlung(zeitpunkt_sek=5.0, gruppe="aesthetik", anweisung="Schon da."))
    assert len(erzwinge_empfehlungen_bei_schwachen_scores(ev).empfehlungen) == 1


def test_stichwort_im_modelltext_unterdrueckt_den_pflichttipp_nicht():
    """Drei Fehlalarme in Folge: `bild` traf „Text im Bild", `sprech` traf „Sprechhook",
    `hintergrund` traf eine Empfehlung zur Textfarbe — jedes Mal fiel der Pflicht-Tipp aus.
    Genau der Fall aus e35a568b: Ästhetik 3, aber die Farb-Empfehlung erwähnte „Hintergrund"."""
    from models.analyst import Empfehlung
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=3)
    ev.empfehlungen.append(Empfehlung(
        zeitpunkt_sek=0.0, gruppe="",
        anweisung="Ändere die Farbe des Text-Overlays in ein Marken-Grün mit dunklem Hintergrund."))
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert any(e.gruppe == "aesthetik" for e in n.empfehlungen), "Pflicht-Tipp wurde unterdrückt"


def test_ohne_benannte_schwaeche_wird_nichts_erfunden():
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = erzwinge_empfehlungen_bei_schwachen_scores(_ev_dim(visuelle_aesthetik=2))
    assert len(ev.empfehlungen) == 1
    assert "10–15" in ev.empfehlungen[0].anweisung    # nur der Bereich, keine erfundene Beobachtung


def test_skill_bewertet_die_gestaltung_der_texthook():
    """Der Skill prüfte nur Wortlaut, Länge und Redundanz — nicht Größe, Farbe, Dauer, Position."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "GESTALTUNG der Text-Hook" in s
    assert "mindestens 5 Sekunden" in s
    # Position: seit 2026-08-07 über die SAFE ZONE statt über eigene Angaben („oberes Drittel").
    gestaltung = s.split("GESTALTUNG der Text-Hook")[1].split("VORSCHLÄGE")[0]
    assert "SAFE ZONE" in gestaltung
    assert "oberes Drittel" not in gestaltung


def test_aesthetik_skala_hat_anker_fuer_1_und_2():
    """Über 35 gespeicherte Läufe wurde visuelle_aesthetik NIE unter 3 bewertet — 3 war faktisch
    die Untergrenze. Ohne Anker für 1 und 2 bleibt Chris' Top-3-Regel („unter 3") wirkungslos,
    weil der Auslöser nie eintritt."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "ANKER für `visuelle_aesthetik.score`" in s
    assert "Zwei DEUTLICHE Mängel sind eine 2" in s
    assert "Zwei Hinweise sind keine 2" in s


def test_kopfraum_regel_nennt_auch_zu_viel_luft():
    """Chris: „der platz zwischen kopf und oberen rand ist zu groß". Die alte Regel nannte nur
    den Zielwert, nicht die Abweichung nach oben."""
    from services.analyst_eval import load_skill_body
    assert "Deutlich MEHR Luft" in load_skill_body()


def test_keine_schriftgroessen_werte_irgendwo():
    """Ein Zielwert (12–14) war kurz in den Empfehlungen und wurde auf Wunsch wieder entfernt.
    Das Modell darf ihn auch nicht selbst erfinden: Im gerenderten Video ist ein App-Wert nicht
    ablesbar, und eine ungeprüfte Zahl ist erfunden."""
    from services.analyst_eval import (TEXTHOOK_EMPFEHLUNG, _texthook_anweisung, load_skill_body)
    for text in (TEXTHOOK_EMPFEHLUNG, _texthook_anweisung([], vorhanden=True),
                 _texthook_anweisung([], vorhanden=False)):
        assert "12–14" not in text
        assert "Schriftgröße" not in text
    skill = load_skill_body()
    assert "KEINE Schriftgrößen-Werte" in skill
    # Für die Bewertung bleibt ein im Bild sichtbarer Anhaltspunkt
    assert "nicht höher sein als der Kopf des Sprechers" in skill


def test_gestaltungs_bausteine_bleiben_konkret():
    """Die Bausteine selbst müssen konkret sein — Dauer, Position und Größe sind Chris' Vorgaben."""
    from services.analyst_eval import _TEXTHOOK_BAUSTEINE
    assert "5 Sekunden" in _TEXTHOOK_BAUSTEINE["dauer"]
    assert "obere Drittel" in _TEXTHOOK_BAUSTEINE["position"]
    assert "nicht höher als der Kopf" in _TEXTHOOK_BAUSTEINE["groesse"]


# ---------- Fixes Runde 3 (Läufe 26a1adbf, 041770c1) ----------

def test_kein_vorspann_mehr_in_den_erzwungenen_tipps():
    """Feedback 26a1adbf: „Diese Formulierung ist unnötig und sollte nicht mit drinstehen:
    Diese Punkte fallen sofort auf und gehören zuerst behoben" — sie stand in zwei Tipps
    untereinander."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=2)
    ev.visuelle_aesthetik.probleme = ["Der Kopfraum ist zu groß"]
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert "fallen sofort auf" not in n.empfehlungen[0].anweisung
    assert n.empfehlungen[0].anweisung.startswith("Der Kopfraum")


def test_fast_wortgleiches_problem_erzeugt_nur_einen_tipp():
    """Sicherheitsnetz im Code: Fast identischer Text wird nicht zweimal zum Tipp."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(sprechqualitaet=3, visuelle_aesthetik=3)
    ev.sprechqualitaet.probleme = ["Dein Blick wandert häufig nach unten oder zur Seite"]
    ev.visuelle_aesthetik.probleme = ["Dein Blick wandert häufig nach unten oder zur Seite"]
    assert len(erzwinge_empfehlungen_bei_schwachen_scores(ev).empfehlungen) == 1


def test_paraphrasen_verhindert_der_prompt_nicht_der_code():
    """Lauf 26a1adbf: Der Blickkontakt stand zweimal, aber als PARAPHRASE — gemeinsam genau ein
    Inhaltswort. Kein Wortmaß erkennt das, ohne bei echten Mängeln falsch zusammenzulegen; eine
    Fehl-Zusammenlegung unterschlägt einen Mangel und ist damit schlimmer als eine Doppelung.
    Deshalb steht die Regel im Prompt, wo das Modell weiß, was es schon geschrieben hat."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "JEDE BEOBACHTUNG NUR EINMAL" in s
    assert "Paraphrasen nicht zuverlässig erkennen" in s


def test_verschiedene_beobachtungen_bleiben_beide():
    """Der Doppler-Filter darf nicht zu scharf sein — zwei echte Mängel bleiben zwei Tipps."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(sprechqualitaet=3, visuelle_aesthetik=3)
    ev.sprechqualitaet.probleme = ["Das Sprechtempo ist durchgehend sehr langsam"]
    ev.visuelle_aesthetik.probleme = ["Der Hintergrund ist unruhig und lenkt ab"]
    assert len(erzwinge_empfehlungen_bei_schwachen_scores(ev).empfehlungen) == 2


def test_benannte_probleme_deckeln_den_score():
    """Lauf 041770c1: aesthetik 4 UND zwei benannte Probleme. Die Score-Anker im Skill haben das
    zweimal nicht verhindert — deshalb im Code."""
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _ev_dim(visuelle_aesthetik=4, sprechqualitaet=5)
    ev.visuelle_aesthetik.probleme = ["Kopfraum zu groß", "Bildausschnitt unruhig"]
    ev.sprechqualitaet.probleme = ["Etwas monotone Betonung"]
    n = deckle_score_auf_probleme(ev)
    assert n.visuelle_aesthetik.score == 3      # zwei Probleme
    assert n.sprechqualitaet.score == 4         # ein Problem


def test_deckel_hebt_keinen_score_an():
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _ev_dim(visuelle_aesthetik=1)
    ev.visuelle_aesthetik.probleme = ["Ein Problem"]
    assert deckle_score_auf_probleme(ev).visuelle_aesthetik.score == 1


def test_ohne_probleme_kein_deckel():
    from services.analyst_eval import deckle_score_auf_probleme
    assert deckle_score_auf_probleme(_ev_dim(visuelle_aesthetik=5)).visuelle_aesthetik.score == 5


def test_kommentar_allein_deckelt_nicht():
    """Nur Dimensionen mit echter probleme-Liste. Ein Kommentar ist nicht zwingend ein Mangel —
    daraus einen Abzug zu machen wäre erfunden."""
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _ev_dim(spannungsbogen=(5, "Die Spannung bleibt durchgehend hoch"))
    assert deckle_score_auf_probleme(ev).spannungsbogen.score == 5


def test_hoechstens_zwei_sammel_tipps_in_den_top3():
    """Lauf 26a1adbf: Anlauf plus zwei erzwungene Tipps belegten alle drei Plätze, die konkreten
    Stellen-Tipps (Sek. 10, 15, 20) rutschten komplett nach unten."""
    from models.analyst import AnalystEvaluationV2, Empfehlung
    from services.analyst_eval import SAMMEL_GRUPPEN, verteile_empfehlungen
    ev = AnalystEvaluationV2(empfehlungen=[
        Empfehlung(zeitpunkt_sek=0.0, gruppe="sprechqualitaet", anweisung="Sammel A"),
        Empfehlung(zeitpunkt_sek=0.0, gruppe="aesthetik", anweisung="Sammel B"),
        Empfehlung(zeitpunkt_sek=0.0, gruppe="spannungsbogen", anweisung="Sammel C"),
        Empfehlung(zeitpunkt_sek=10.0, gruppe="einblendungen", anweisung="Konkret bei Sek 10"),
    ])
    n = verteile_empfehlungen(ev)
    assert len(n.action_steps) == 3
    assert n.action_steps[2].anweisung == "Konkret bei Sek 10", "kein Platz für den konkreten Tipp"
    assert "Sammel C" in n.weitere_empfehlungen[0].anweisung


def test_hooks_zaehlen_nicht_zum_sammel_deckel():
    """Hook- und Anlauf-Schritte betreffen die ersten Sekunden und behalten Vorrang."""
    from models.analyst import AnalystEvaluationV2, Empfehlung
    from services.analyst_eval import verteile_empfehlungen
    ev = AnalystEvaluationV2(empfehlungen=[
        Empfehlung(zeitpunkt_sek=0.0, gruppe="anlauf", anweisung="Anlauf weg"),
        Empfehlung(zeitpunkt_sek=0.0, gruppe="texthook", anweisung="Texthook"),
        Empfehlung(zeitpunkt_sek=0.0, gruppe="sprechqualitaet", anweisung="Sammel A"),
        Empfehlung(zeitpunkt_sek=5.0, gruppe="", anweisung="Konkret"),
    ])
    n = verteile_empfehlungen(ev)
    assert [s.anweisung for s in n.action_steps] == ["Anlauf weg", "Texthook", "Sammel A"]


def test_untertitel_regel_steht_nur_im_untertitel_block():
    """Die Untertitel-Bewertung ist aus `schnitt_pacing` in den eigenen Block umgezogen (2026-08-06).
    Bleibt die alte Fassung stehen, gilt dieselbe Regel an zwei Orten — der Fehler, an dem sich
    doppelte Tipps im Repo schon mehrfach entzündet haben.

    Ursprünglicher Anlass der Regel bleibt gültig (Feedback 26a1adbf: „es fehlt die kritik an den
    untertiteln … zu viele wörter pro textblock. eher auf 2-4 reduzieren.") — nur der Ort ändert sich.
    """
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "UNTERTITEL gehören zum Pacing" not in s
    # Die Wortzahl-Regel steht jetzt ausschließlich im Untertitel-Abschnitt
    block = s.split("## Untertitel")[1].split("\n## ")[0]
    assert "4 Wörter" in block
    pacing = s.split("## Schnitt & Pacing")[1].split("\n## ")[0]
    assert "Wörter pro Block" not in pacing


def test_override_macht_untertitel_zur_pflicht():
    from services.analyst_gemini_eval import _user_message
    o = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "PFLICHT Untertitel" in o
    assert "2–4" not in o          # Zielwert steht nur im Skill, keine zweite Ausformulierung


def test_sprechhook_ohne_haken_ist_hoechstens_2():
    """Feedback 041770c1: Score 3 vergeben, Chris: „es hookt fast garnicht", eher 2."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "Kein Haken in den ersten zwei Sätzen = höchstens 2" in s
    assert "keine Hook-Kriterien" in s


def test_weitschweifigkeit_ist_ein_struktur_mangel():
    """Feedback 041770c1: „an vielen stellen könnte man text reduzieren.\""""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "WEITSCHWEIFIGKEIT ist ein Struktur-Mangel" in s
    assert "nicht als pauschales" in s


# ---------- Konsolidierung (Läufe 0c68aa58, e9f69518, 4e56336e, 30d6b472, 82bda700) ----------

def test_betrifft_verhindert_den_doppelten_tipp():
    """Kern der Konsolidierung. In 4 von 5 Läufen wiederholte Tipp 3 die Tipps 1/2, weil beide aus
    denselben `probleme` entstanden — einmal vom Modell, einmal vom erzwungenen Sammeltipp.
    Vier Versuche, das über Textmuster zu erkennen, sind gescheitert; das Feld ist exakt."""
    from models.analyst import Empfehlung
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=2)
    ev.visuelle_aesthetik.probleme = ["Der Fernseher im Hintergrund lenkt ab"]
    ev.empfehlungen.append(Empfehlung(
        zeitpunkt_sek=0.0, betrifft="visuelle_aesthetik",
        anweisung="Wähle einen anderen Hintergrund ohne den Fernseher."))
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert len(n.empfehlungen) == 1, "Sammeltipp trotz vorhandener Modell-Empfehlung"


def test_betrifft_einer_anderen_dimension_blockiert_nicht():
    """Der Filter darf nur die passende Dimension überspringen, nicht alle."""
    from models.analyst import Empfehlung
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=2)
    ev.visuelle_aesthetik.probleme = ["Bild ist unscharf"]
    ev.empfehlungen.append(Empfehlung(
        zeitpunkt_sek=0.0, betrifft="spannungsbogen", anweisung="Setz einen Haken im Mittelteil."))
    assert len(erzwinge_empfehlungen_bei_schwachen_scores(ev).empfehlungen) == 2


def test_erzwungene_schritte_tragen_ihre_dimension():
    """Damit der Filter auch gegen die eigenen Schritte greift und nichts doppelt entsteht."""
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=2)
    ev.visuelle_aesthetik.probleme = ["Bild ist unscharf"]
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert n.empfehlungen[0].betrifft == "visuelle_aesthetik"
    # zweiter Durchlauf darf nichts hinzufügen
    assert len(erzwinge_empfehlungen_bei_schwachen_scores(n).empfehlungen) == 1


def test_hinweise_deckeln_den_score_nicht():
    """Läufe 30d6b472/82bda700: Die Pflicht-Beurteilung erzeugte immer zwei Einträge, der Deckel
    machte daraus in 5 von 5 Läufen exakt eine 3. Chris: „bildausschnitt ist gut", „der raum
    zwischen kopf und rand ist nahezu perfekt groß"."""
    from services.analyst_eval import deckle_score_auf_probleme
    ev = _ev_dim(visuelle_aesthetik=4)
    ev.visuelle_aesthetik.hinweise = ["Hintergrund ist schlicht", "Textbox nah an den Haaren"]
    assert deckle_score_auf_probleme(ev).visuelle_aesthetik.score == 4


def test_hinweise_erzeugen_keine_empfehlung():
    from services.analyst_eval import erzwinge_empfehlungen_bei_schwachen_scores
    ev = _ev_dim(visuelle_aesthetik=3)
    ev.visuelle_aesthetik.hinweise = ["Hintergrund ist schlicht"]
    n = erzwinge_empfehlungen_bei_schwachen_scores(ev)
    assert "schlicht" not in n.empfehlungen[0].anweisung


def test_schema_verlangt_betrifft_hinweise_und_maengel():
    from services.analyst_eval import OUTPUT_SCHEMA
    for feld in ("betrifft", "hinweise", "texthook_maengel"):
        assert feld in OUTPUT_SCHEMA, f"{feld} fehlt im Schema-Vertrag"


def test_pflicht_heisst_pruefen_nicht_kritisieren():
    """Die alte Formulierung („Ist eines auffällig, MUSS es in probleme stehen") hat die
    Über-Kritik erzeugt."""
    from services.analyst_gemini_eval import _user_message
    o = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "PRÜFEN heißt nicht KRITISIEREN" in o
    assert "SAG DAS als Stärke" in o
    assert "MUSS es in visuelle_aesthetik.probleme stehen" not in o


def test_toleranzbereich_steht_im_skill():
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "DEUTLICHER MANGEL vs. HINWEIS" in s
    assert "Natürlich gefilmt ist NICHT" in s
    assert "kann sogar\nDynamik erzeugen" in s


def test_gestaltung_ist_teil_der_score_definition():
    """e9f69518: text_hook 4, Begründung nur über den Inhalt, obwohl die Hook laut Chris
    „grottig aussieht". Die Gestaltungsregel stand zu weit von der Score-Vergabe entfernt."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "bewertet Inhalt UND\nGESTALTUNG" in s
    assert "müssen BEIDE Seiten tragen" in s


def test_handlung_zuerst_steht_im_empfehlungs_kanon():
    """4e56336e: „kürze die textblöcke so das die handlungsempfehlung vordergründig ist"."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "HANDLUNG ZUERST, Begründung knapp" in s


# ---------- V1 abgeschafft: der Prompt beschreibt den echten Modus ----------

def test_v1_ist_nicht_mehr_waehlbar():
    """V1 bewertete OHNE Video und nutzte denselben build_system_prompt(). Der Skill ist jetzt für
    „du siehst das Video" geschrieben — wäre V1 noch aufrufbar, bekäme er einen falschen Prompt.
    Der API-Default stand auf 'v1', also hätte JEDER Aufruf ohne engine-Parameter dort gelandet."""
    import inspect
    from api.analyst import ENGINES, start_analysis
    assert "v1" not in ENGINES
    assert inspect.signature(start_analysis).parameters["engine"].default == "v2_hybrid"


def test_skill_behauptet_nicht_mehr_das_video_nicht_zu_sehen():
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    assert "nie gesehen" not in s
    assert "Du bekommst das VIDEO selbst" in s
    for altlast in ("Szenenliste", "Bild-Fakten", "Gemma", "dedizierter Gemini-Pass"):
        assert altlast not in s, f"{altlast!r} setzt den abgeschafften V1-Modus voraus"


def test_override_widerruft_den_systemprompt_nicht_mehr():
    """Der Widerruf lief bei JEDEM Lauf und schwächte jede Regel im Prompt — das Modell musste bei
    jeder Aussage mitentscheiden, ob sie noch gilt."""
    from services.analyst_gemini_eval import _user_message
    o = _user_message(_result(gewaehltes_format="Talking Head"), "hybrid")
    assert "abweichend vom System-Prompt" not in o
    assert "Ignoriere daher" not in o
    assert "Du erhältst das VIDEO direkt" in o      # die Ansage selbst bleibt


def test_score_farbe_ist_immer_gruen():
    """Vorgabe Chris: Die Score-Farbe darf nicht demotivieren — jedes veröffentlichte Video ist
    besser als keines. Die Zahl und der Einordnungstext differenzieren weiterhin, nur das
    Farbsignal ist neutralisiert."""
    import re, pathlib
    quelle = pathlib.Path("static/app.jsx").read_text(encoding="utf-8")
    block = re.search(r"function scoreEinordnung\(score\) \{.*?\n\}", quelle, re.S).group()
    assert "var(--err-ink)" not in block, "rote Score-Farbe ist wieder drin"
    assert "var(--gold-deep)" not in block
    assert block.count("var(--ok-ink)") >= 1


# ---------- Hook-Regeln: Struktur A→B→C→D + Pflichtfelder (Runde „Texthook triggert nicht") ----------

def _hook_ev(**felder):
    from models.analyst import AnalystEvaluationV2
    ev = AnalystEvaluationV2()
    for k, v in felder.items():
        setattr(ev.hook, k, v)
    return ev


def test_hook_abschnitt_hat_entscheidungsreihenfolge():
    """Chris: „wie sollten wir die hook regeln optimal strukturieren". Der Score darf erst fallen,
    nachdem geklärt ist, was da ist und ob es überhaupt zählt."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    for ueberschrift in ("### A — Was ist da", "### B — Zählt es als Hook",
                         "### C — Wie stark ist der Hook", "### D — Was empfiehlst du"):
        assert ueberschrift in s, ueberschrift
    assert s.index("### A") < s.index("### B") < s.index("### C") < s.index("### D")


def test_hook_regeln_stehen_nur_einmal():
    """Chris: „wichtig ist das der prompt nie doppelte regeln irgendwo enthält"."""
    from services.analyst_eval import load_skill_body
    s = load_skill_body()
    for satz in ("Kein Haken in den ersten zwei Sätzen = höchstens 2",
                 "müssen BEIDE Seiten tragen",
                 "3–9 Wörter (ideal 3–6)",
                 "REDUNDANZ Sprech-Hook = Text-Hook ist eine SCHWÄCHE"):
        assert s.count(satz) == 1, f"{satz!r} steht {s.count(satz)}x"


def test_pflichtfelder_offene_frage_und_mechanik_stehen_im_prompt():
    from services.analyst_eval import load_skill_body, OUTPUT_SCHEMA
    s = load_skill_body()
    assert "PFLICHT `*_offene_frage`" in s
    assert "PFLICHT `*_mechanik`" in s
    for feld in ("sprech_hook_offene_frage", "sprech_hook_mechanik",
                 "text_hook_offene_frage", "text_hook_mechanik"):
        assert feld in OUTPUT_SCHEMA, feld


def test_hook_ohne_offene_frage_wird_auf_2_gedeckelt():
    """Kernfall: sauber formulierter Einstieg, aber keine Lücke — das ist eine Aussage, kein Hook."""
    from services.analyst_eval import deckle_hooks_ohne_haken
    ev = deckle_hooks_ohne_haken(_hook_ev(
        sprech_hook_score=4, sprech_hook_offene_frage="", sprech_hook_mechanik="neugierluecke"))
    assert ev.hook.sprech_hook_score == 2


def test_hook_ohne_mechanik_wird_auf_2_gedeckelt():
    from services.analyst_eval import deckle_hooks_ohne_haken
    ev = deckle_hooks_ohne_haken(_hook_ev(
        sprech_hook_score=5, sprech_hook_offene_frage="Was ist der dritte Fehler?",
        sprech_hook_mechanik="keine"))
    assert ev.hook.sprech_hook_score == 2


def test_hook_mit_haken_bleibt_unberuehrt():
    from services.analyst_eval import deckle_hooks_ohne_haken
    ev = deckle_hooks_ohne_haken(_hook_ev(
        sprech_hook_score=5, sprech_hook_offene_frage="Welcher Fehler kostet am meisten Geld?",
        sprech_hook_mechanik="neugierluecke"))
    assert ev.hook.sprech_hook_score == 5


def test_fehlende_texthook_wird_nicht_gedeckelt():
    """Score 0 heißt „gar keine Text-Hook" — daraus darf kein 2 werden."""
    from services.analyst_eval import deckle_hooks_ohne_haken
    ev = deckle_hooks_ohne_haken(_hook_ev(
        text_hook_vorhanden=False, text_hook_score=0,
        text_hook_offene_frage="", text_hook_mechanik=""))
    assert ev.hook.text_hook_score == 0


def test_texthook_deckel_wird_markiert():
    from services.analyst_eval import deckle_hooks_ohne_haken
    ev = deckle_hooks_ohne_haken(_hook_ev(
        text_hook_vorhanden=True, text_hook_score=4,
        text_hook_offene_frage="", text_hook_mechanik="zahl"))
    assert ev.hook.text_hook_score == 2
    assert ev.hook.text_hook_score_geklemmt is True


# ---------- Phasenzeiten: Entscheidungsgrundlage für ANALYST_MAX_CONCURRENT ----------

def test_messe_phase_schreibt_die_dauer_unter_ihren_namen():
    """Ohne Phasenzeiten steht nur `elapsed_sec` für den Gesamtlauf in der analysis.json
    (59 v2_hybrid-Läufe: Median 80 s, Spanne 30–120 s). Die Frage, ob mehr als ein Lauf
    parallel etwas bringt, hängt aber genau an der Aufteilung: Whisper belegt die CPU,
    der Gemini-Call wartet nur auf Netz. Ohne diese zwei Zahlen ist jede Prognose geraten."""
    import time as _t
    from services.analyst_engine import messe_phase
    phasen = {}
    with messe_phase(phasen, "transkript"):
        _t.sleep(0.05)
    assert "transkript" in phasen
    assert phasen["transkript"] >= 0.05


def test_messe_phase_haelt_die_zeit_auch_bei_einem_fehler_fest():
    """Ein abgebrochener Lauf ist der teuerste — dann will man erst recht wissen,
    wie weit er gekommen ist und wie lange das gedauert hat."""
    from services.analyst_engine import messe_phase
    phasen = {}
    with pytest.raises(RuntimeError):
        with messe_phase(phasen, "bewertung"):
            raise RuntimeError("Gemini 429")
    assert "bewertung" in phasen


def test_altlauf_ohne_phasenzeiten_bleibt_ladbar():
    from models.analyst import AnalystResult
    r = AnalystResult(id="x", filename="v.mp4", duration_sec=1.0, scene_count=0, scenes=[])
    assert r.phasen_sek == {}


def test_whisper_modellcache_laedt_nur_einmal(monkeypatch):
    """Zwei kalt startende Läufe dürfen nicht zwei Modelle in den RAM legen.

    Ab ANALYST_MAX_CONCURRENT=2 ist das kein theoretischer Fall mehr: Nach jedem Redeploy ist
    der Cache kalt, und zwei gleichzeitig gestartete Analysen treffen genau darauf.
    """
    import threading
    from services import whisper_service

    ladevorgaenge = []

    class LangsamesModell:
        def __init__(self, name, **kw):
            ladevorgaenge.append(name)
            threading.Event().wait(0.2)   # Ladefenster aufreißen, in dem der zweite Thread ankommt

    monkeypatch.setattr(whisper_service, "_models", {})
    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("M", (), {"WhisperModel": LangsamesModell}),
    )

    ergebnisse = []
    threads = [
        threading.Thread(target=lambda: ergebnisse.append(whisper_service._get_model("small")))
        for _ in range(2)
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    assert ladevorgaenge == ["small"], f"Modell mehrfach geladen: {ladevorgaenge}"
    assert ergebnisse[0] is ergebnisse[1], "Threads bekamen verschiedene Instanzen"


def test_reaper_markiert_haengende_laeufe(tmp_path, monkeypatch):
    """Nach einem Neustart existiert zu einem 'laufenden' Status kein Thread mehr."""
    import json as _json
    from services import analyst_engine

    monkeypatch.setattr(analyst_engine, "ANALYST_PATH", tmp_path)
    haengt = tmp_path / "aaa11111"; haengt.mkdir()
    analyst_engine.write_status(haengt, "evaluate", "Analyse läuft…")
    fertig = tmp_path / "bbb22222"; fertig.mkdir()
    analyst_engine.write_status(fertig, "done", "fertig", done=True)

    assert analyst_engine.markiere_abgebrochene_laeufe() == 1
    assert _json.loads((haengt / "status.json").read_text())["phase"] == "error"
    assert _json.loads((fertig / "status.json").read_text())["phase"] == "done"


def test_letztes_modell_ist_thread_lokal(monkeypatch):
    """Ab ANALYST_MAX_CONCURRENT=2 laufen zwei Analysen als zwei Threads im selben Prozess.
    Jeder Lauf muss über `letztes_modell()` SEIN EIGENES Modell sehen — sonst landet im
    prompt_log.md das Modell des jeweils ANDEREN Laufs. Besonders tückisch: Solange beide Läufe
    auf dem Primärmodell landen, ist der Wert zufällig richtig; falsch wird er genau dann, wenn
    einer der beiden auf das Fallback-Modell ausweicht — im einzigen Fall, für den das Feld
    überhaupt gebaut wurde.

    Deshalb prüft dieser Test den ECHTEN Codepfad (`_generate`) statt nur ein Detail der
    Speicherung: Zwei Threads rufen `_generate` auf, Thread A landet direkt beim Primärmodell,
    Thread B fällt auf das erste Fallback-Modell zurück (die Fake-`client`-Antwort hängt vom
    aufrufenden Thread ab). `_call_with_retry` wird durch einen Direktaufruf ersetzt, damit
    weder Netzwerk noch echte Wartezeiten ins Spiel kommen. Eine `Barrier` zwingt beide Threads,
    nach ihrem `_generate`-Aufruf aufeinander zu warten, bevor sie `letztes_modell()` lesen —
    nur so ist bewiesen, dass beide Werte GLEICHZEITIG existieren müssen. Bei einem geteilten
    Modul-Global könnte höchstens einer der beiden stimmen; ein `sleep` würde das nur raten statt
    beweisen.
    """
    import threading
    from types import SimpleNamespace
    from services import analyst_vlm

    monkeypatch.setattr(analyst_vlm, "_call_with_retry", lambda label, fn: fn())

    class FakeModels:
        def generate_content(self, model, contents, config):
            thread = threading.current_thread().name
            if thread == "A":
                if model == analyst_vlm.GEMINI_MODEL:
                    return SimpleNamespace(text="antwort-a")
                raise AssertionError("Thread A sollte das Primärmodell nie verlassen")
            if thread == "B":
                if model == analyst_vlm.GEMINI_MODEL:
                    raise RuntimeError("Primärmodell down (simuliert)")
                if model == analyst_vlm.GEMINI_FALLBACK_MODELS[0]:
                    return SimpleNamespace(text="antwort-b")
                raise AssertionError(f"unerwartetes Modell für Thread B: {model}")
            raise AssertionError(f"unerwarteter Thread: {thread}")

    monkeypatch.setattr(analyst_vlm, "client", SimpleNamespace(models=FakeModels()))

    barrier = threading.Barrier(2)
    ergebnisse = {}
    fehler = []

    def lauf(name):
        try:
            antwort = analyst_vlm._generate(["dummy-content"], None, "test")
            # Erst NACH dem _generate-Aufruf synchronisieren, VOR dem Lesen von letztes_modell():
            # beweist, dass beide thread-lokalen Werte gleichzeitig gültig nebeneinander stehen.
            barrier.wait(timeout=5)
            ergebnisse[name] = (antwort.text, analyst_vlm.letztes_modell())
        except Exception as e:
            fehler.append(e)
            try:
                barrier.wait(timeout=5)  # den anderen Thread nicht ewig blockieren lassen
            except Exception:
                pass

    t_a = threading.Thread(target=lauf, args=("A",), name="A")
    t_b = threading.Thread(target=lauf, args=("B",), name="B")
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert not fehler, f"Fehler in den Threads: {fehler}"
    assert ergebnisse["A"] == ("antwort-a", analyst_vlm.GEMINI_MODEL)
    assert ergebnisse["B"] == ("antwort-b", analyst_vlm.GEMINI_FALLBACK_MODELS[0])
