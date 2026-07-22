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
    words = [_w("Hallo", 0.0, 0.4), _w("ähm", 0.5, 0.8), _w("Welt", 1.5, 1.9), _w("heute", 2.0, 2.4)]
    st = compute_speech_stats(words)
    assert st.wort_anzahl == 4
    assert st.filler_count == 1 and st.filler_words == ["ähm"]
    assert st.pausen_count == 1 and st.laengste_pause_sec == pytest.approx(0.7)


def test_speech_stats_no_pause_no_filler():
    st = compute_speech_stats([_w("eins", 0.0, 0.3), _w("zwei", 0.35, 0.6)])
    assert st.filler_count == 0 and st.pausen_count == 0


def test_speech_stats_pausen_behalten_ihre_position():
    """Regression: Die Position der Pause darf nicht wegaggregiert werden — ohne sie
    ordnet das Modell die gemessene Dauer einer geratenen Stelle zu (Run a2808a62)."""
    words = [_w("a", 0.0, 1.0), _w("b", 4.1, 4.5), _w("c", 5.2, 5.6)]
    st = compute_speech_stats(words)
    assert st.pausen_count == 2
    assert [(p.start_sec, p.end_sec, p.dauer_sec) for p in st.pausen] == [
        (1.0, 4.1, 3.1),   # die lange Pause ist bei Sek. 1–4.1, nicht "irgendwo"
        (4.5, 5.2, 0.7),
    ]
    assert st.laengste_pause_sec == pytest.approx(3.1)


def test_pausen_txt_rendert_position_in_den_prompt():
    from services.analyst_eval import pausen_txt
    st = compute_speech_stats([_w("a", 0.0, 1.0), _w("b", 4.1, 4.5)])
    assert pausen_txt(st) == "3.1s @ 1.0–4.1s"
    assert pausen_txt(compute_speech_stats([_w("a", 0.0, 0.3), _w("b", 0.4, 0.7)])) == "keine Pausen >0.5s gemessen"


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
