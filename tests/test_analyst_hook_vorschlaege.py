"""Geplante Texthook und konkrete Hook-Vorschlaege (Vorgabe Chris, 2026-09-14).

Hintergrund: Viele bauen die Texthook erst kurz vor dem Upload ein und testen mehrere Varianten
ueber Test-Reels. Deshalb gibt es das Freifeld — und deshalb darf der Analyst nicht empfehlen,
eine Texthook einzubauen, wenn dort eine steht.
"""
from models.analyst import AnalystEvaluationV2, AnalystResult, SpeechStats


def _result(geplant="", ziel="MOFU", format="Talking Head"):
    """Mit Transkript und Sprachstatistik: Ohne gesprochenes Wort setzt
    `neutralisiere_stumme_scores` den Sprech-Hook auf null, und dann greift keine der Regeln, um
    die es hier geht."""
    return AnalystResult(
        id="x", filename="c.mp4", duration_sec=20.0, scene_count=0, scenes=[],
        gewaehltes_ziel=ziel, gewaehltes_format=format, geplante_texthook=geplant,
        transcript="Heute zeige ich dir, warum das so oft schiefgeht und was stattdessen hilft.",
        speech_stats=SpeechStats(wort_anzahl=60, sprech_dauer_sec=18.0, wpm=170, filler_count=0,
                                 filler_words=[], pausen_count=0, laengste_pause_sec=0.0,
                                 sprechbeginn_sec=0.0))


# --- 1) Die geplante Texthook IST die Texthook -----------------------------------------------

def test_geplante_texthook_gilt_als_vorhanden():
    """Das Modell sieht sie im Video nicht und meldet deshalb regelmaessig `vorhanden=false`.
    Der Nutzer weiss es besser — er hat sie eingetragen."""
    from services.analyst_eval import erzwinge_geplante_texthook
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": False, "text_hook_score": 0})
    e = erzwinge_geplante_texthook(e, _result(geplant="Mit über 46 nochmal Mutter"))
    assert e.hook.text_hook_vorhanden is True
    assert e.hook.text_hook_wortlaut == "Mit über 46 nochmal Mutter"


def test_score_null_wird_zu_nicht_bewertbar_statt_zu_einer_null():
    """0 heisst im Schema „fehlt komplett". Mit eingetragener Texthook ist das faktisch falsch und
    wuerde den Gesamtscore ueber die volle Gewichtung nach unten ziehen. null heisst „nicht
    bewertbar" — das System verteilt das Gewicht dann auf die uebrigen Dimensionen."""
    from services.analyst_eval import erzwinge_geplante_texthook
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": False, "text_hook_score": 0})
    e = erzwinge_geplante_texthook(e, _result(geplant="Mit über 46 nochmal Mutter"))
    assert e.hook.text_hook_score is None


def test_eine_echte_bewertung_des_wortlauts_bleibt_stehen():
    from services.analyst_eval import erzwinge_geplante_texthook
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": True, "text_hook_score": 4})
    e = erzwinge_geplante_texthook(e, _result(geplant="Mit über 46 nochmal Mutter"))
    assert e.hook.text_hook_score == 4


def test_gestaltungs_maengel_fallen_weg():
    """„Die Gestaltung der Texthook kann in dem Fall natuerlich nicht bewertet werden." Sie steht
    noch gar nicht im Video — Position, Groesse, Farbe, Lesbarkeit und Standdauer sind nicht
    beurteilbar. Was den WORTLAUT betrifft, bleibt."""
    from services.analyst_eval import erzwinge_geplante_texthook
    e = AnalystEvaluationV2(hook={"text_hook_score": 3},
                            texthook_maengel=["position", "groesse", "lesbarkeit", "dauer",
                                              "farbe", "wortlaut", "laenge"])
    e = erzwinge_geplante_texthook(e, _result(geplant="Mit über 46 nochmal Mutter"))
    assert e.texthook_maengel == ["wortlaut", "laenge"]


def test_ohne_freifeld_bleibt_alles_wie_es_war():
    from services.analyst_eval import erzwinge_geplante_texthook
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": False, "text_hook_score": 0},
                            texthook_maengel=["position"])
    e = erzwinge_geplante_texthook(e, _result(geplant=""))
    assert e.hook.text_hook_vorhanden is False
    assert e.hook.text_hook_score == 0
    assert e.texthook_maengel == ["position"]


def test_keine_empfehlung_eine_texthook_einzubauen():
    """Der Kern der Meldung: „dass die KI trotzdem eine Empfehlung gibt, dass eine Texthook
    eingefuegt werden soll. Das darf dann nicht mehr sein."""
    from services.analyst_eval import nachbearbeiten
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": False, "text_hook_score": 0,
                                  "sprech_hook_score": 4},
                            texthook_varianten=["Mit 46 nochmal Mutter — geht das?"])
    out = nachbearbeiten(e, _result(geplant="Mit über 46 nochmal Mutter"))
    texte = " ".join((s.anweisung or "") for s in out.action_steps + out.weitere_empfehlungen).lower()
    assert "blende" not in texte, texte
    assert "texthook ein" not in texte, texte


def test_bei_einer_reaction_schlaegt_das_freifeld_die_fremdtext_regel():
    """bereinige_fremd_texthook setzt den Score auf 0, WENN das Feld leer ist. Ist es gefuellt,
    greift die Regel schon heute nicht — dieser Test haelt das Zusammenspiel fest."""
    from services.analyst_eval import nachbearbeiten
    e = AnalystEvaluationV2(hook={"text_hook_vorhanden": True, "text_hook_score": 4,
                                  "text_hook_wortlaut": "Nur 3 Wörter",
                                  "text_hook_offene_frage": "Welche drei Wörter sind gemeint?",
                                  "text_hook_mechanik": "neugierluecke"})
    out = nachbearbeiten(e, _result(geplant="Nur 3 Wörter", format="Reaction"))
    assert out.hook.text_hook_score == 4


# --- 2) Konkrete Vorschlaege statt allgemeiner Aufforderungen ----------------------------------

def test_sprechhook_empfehlung_traegt_die_vorschlaege_des_modells():
    """„wenn eine Texthook oder eine Sprechhook stark bemaengelt wird, soll dort auch ein direkter
    Vorschlag kommen." Ein Satz zum Abschreiben schlaegt jede Aufforderung."""
    from services.analyst_eval import nachbearbeiten
    e = AnalystEvaluationV2(
        hook={"sprech_hook_score": 2, "text_hook_score": 4, "text_hook_vorhanden": True},
        sprechhook_varianten=["Ich habe drei Jahre lang alles falsch gemacht.",
                              "Das hier kostet dich jeden Monat Geld."])
    out = nachbearbeiten(e, _result())
    text = " ".join((s.anweisung or "") for s in out.action_steps + out.weitere_empfehlungen)
    assert "drei Jahre lang alles falsch" in text


def test_visuellhook_empfehlung_traegt_die_vorschlaege_des_modells():
    """„Genauso wie es auch bei der visuellen Hook ist … das soll auch ein kleiner Vorschlag
    kommen, wie man das praktisch umsetzen kann."""
    from services.analyst_eval import nachbearbeiten
    e = AnalystEvaluationV2(
        hook={"visuell_hook_score": 1, "sprech_hook_score": 4, "text_hook_score": 4,
              "text_hook_vorhanden": True},
        visuellhook_vorschlaege=["Starte mit einem langsamen Push-In auf dein Gesicht.",
                                 "Halte in Sekunde 1 das Produkt in die Kamera."])
    out = nachbearbeiten(e, _result())
    text = " ".join((s.anweisung or "") for s in out.action_steps + out.weitere_empfehlungen)
    assert "Push-In" in text


def test_bei_guter_hook_kommt_kein_vorschlag():
    """Dieselbe Disziplin wie bei texthook_varianten: Ein Vorschlag zu einer 5/5 ist Fuelltext."""
    from services.analyst_eval import nachbearbeiten
    e = AnalystEvaluationV2(
        hook={"sprech_hook_score": 5, "visuell_hook_score": 5, "text_hook_score": 5,
              "text_hook_vorhanden": True, "text_hook_wortlaut": "Mit 46 nochmal Mutter",
              # Ohne offene Frage und Mechanik deckelt deckle_hooks_ohne_haken auf 2 — dann
              # waere der Test nicht der, den er zu sein vorgibt.
              "sprech_hook_offene_frage": "Wie geht das in dem Alter?",
              "sprech_hook_mechanik": "neugierluecke",
              "text_hook_offene_frage": "Wie geht das in dem Alter?",
              "text_hook_mechanik": "neugierluecke"},
        sprechhook_varianten=["Sollte hier nicht auftauchen."],
        visuellhook_vorschlaege=["Sollte hier auch nicht auftauchen."])
    out = nachbearbeiten(e, _result())
    text = " ".join((s.anweisung or "") for s in out.action_steps + out.weitere_empfehlungen)
    assert "Sollte hier" not in text


# --- 3) Vertrag und Prompt ---------------------------------------------------------------------

def test_die_neuen_felder_stehen_nur_im_v3_vertrag():
    from services.analyst_eval import OUTPUT_SCHEMA, v3_schema
    s = v3_schema()
    for feld in ("sprechhook_varianten", "visuellhook_vorschlaege"):
        assert feld in s, feld
        assert feld not in OUTPUT_SCHEMA, f"{feld} darf den V2-Vertrag nicht veraendern"


def test_die_neuen_felder_gehoeren_in_den_eroeffnungs_call():
    """Dort werden die Hooks bewertet — im Handwerk-Teil waeren sie ohne Bezug."""
    from services.analyst_eval import _schema_fuer
    teil = _schema_fuer("eroeffnung", "MOFU")
    assert "sprechhook_varianten" in teil and "visuellhook_vorschlaege" in teil


def test_der_prompt_sagt_dass_die_gestaltung_nicht_zaehlt():
    from models.analyst import AnalystResult
    from services.analyst_gemini_eval import _texthook_instruction
    txt = _texthook_instruction(_result(geplant="Mit über 46 nochmal Mutter"))
    assert "gestaltung" in txt.lower()
    assert "Abzug" in txt
    assert "einzublenden" in txt, "die falsche Empfehlung muss ausdruecklich verboten sein"
