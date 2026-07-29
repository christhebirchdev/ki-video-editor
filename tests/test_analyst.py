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


# ---------- Prompt-Regeln: vorhanden, eindeutig, nicht doppelt ----------

def test_untertitel_regel_entscheidet_am_wortlaut_nicht_an_der_darstellung():
    """P1, Feedback Run 25b8b2f6: Statische Untertitel-Blöcke wurden als Texthook bewertet. Die alte
    Regel hing an „wechselt mit der Sprache" — genau das Merkmal fehlte dort."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "Kommt ein Bildtext (nahezu) genauso im TRANSKRIPT vor, sind das UNTERTITEL" in skill
    assert "Untertitel müssen\nweder wechseln noch unten stehen" in skill
    # Untertitel dürfen nie Auslöser einer Texthook-Empfehlung sein
    assert "NIE zum Gegenstand einer\nTexthook-Empfehlung" in skill
    # die alte, zu enge Definition ist raus
    assert "wechselt mit der Sprache" not in skill
    assert "wechselnde Zeile in der unteren Bildhälfte" not in skill


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


def test_einblendungen_geben_den_zweck_vor_nicht_das_motiv():
    """P8, Feedback Run 093dc5a7: „ich verbinde selbstbewusstsein nicht mit einem gehirn symbol"."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "den ZWECK vorgeben, nicht das Motiv" in skill
    assert "bis zu 3 Motiv-Optionen" in skill


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
    assert "Den Abzug bekommt im Zweifel die TEXT-HOOK" in skill


def test_aesthetik_referenz_gilt_nur_fuer_talking_head():
    """P14: Chris' Framing-Standard beschreibt ausdrücklich nur Talking-Head-Videos."""
    from services.analyst_eval import load_skill_body
    skill = load_skill_body()
    assert "dieser Standard gilt NUR für Talking Head" in skill
    for punkt in ("Kopfraum", "Augenhöhe", "Untertitel-Platzierung", "1080p"):
        assert punkt in skill, f"{punkt!r} fehlt in der Ästhetik-Referenz"


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
