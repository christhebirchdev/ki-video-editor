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
    r = client.post(f"/api/analyst/{run_id}/start")
    assert r.status_code == 503 and "GEMINI" in r.json()["detail"].upper()

    calls = []
    monkeypatch.setattr(api_analyst.analyst_vlm, "is_available", lambda: (True, ""))
    monkeypatch.setattr(api_analyst, "run_analysis", lambda rid: calls.append(rid))
    r = client.post(f"/api/analyst/{run_id}/start?skip_eval=true")
    assert r.status_code == 200 and calls == [run_id]
    assert client.get(f"/api/analyst/{run_id}").status_code == 200


def test_get_unknown_run_404(client):
    assert client.get("/api/analyst/gibtsnicht").status_code == 404
