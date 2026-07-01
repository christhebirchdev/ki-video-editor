# tests/test_multicut.py
"""MultiCut: Planner-Validierung (Code-Garantien) + Executor-Helpers."""
import json
import pytest

from models.analysis import WhisperWord
from models.shorts import ShortsPlan, ShortCandidate
from services.cut_engine_v2 import Sentence
from services.shorts_planner import validate_candidates, CONTENT_TYPE_RULES
from services.shorts_executor import (
    slice_words_for_segment, save_shorts_plan, load_shorts_plan,
)


def _w(start, end, word):
    return WhisperWord(start=start, end=end, word=word)


def _sentence(sid, start, end, text="bla"):
    words = [_w(start, end, text)]
    return Sentence(id=sid, words=words, start_sec=start, end_sec=end)


# ===== validate_candidates =====

def test_tofu_innerhalb_fenster_wird_akzeptiert():
    sentences = [_sentence(0, 10.0, 14.0), _sentence(1, 14.5, 18.0)]
    raw = [{"title": "Hot Take", "start_sentence_id": 0, "end_sentence_id": 1, "kpi_score": 4, "rationale": "polarisiert"}]
    result = validate_candidates(raw, sentences, "tofu", 100.0)
    assert len(result) == 1
    s = result[0]
    assert s.id == "short_01"
    assert s.kpi_score == 4
    # Padding: -0.08 / +0.04
    assert s.start_sec == pytest.approx(10.0 - 0.08, abs=0.001)
    assert s.end_sec == pytest.approx(18.0 + 0.04, abs=0.001)


def test_tofu_zu_lang_wird_vom_ende_getrimmt():
    # 0→20s wäre zu lang (>15s) — Trim auf Satz 0+1 (0→12s) muss greifen
    sentences = [_sentence(0, 0.0, 8.0), _sentence(1, 8.5, 12.0), _sentence(2, 12.5, 20.0)]
    raw = [{"title": "x", "start_sentence_id": 0, "end_sentence_id": 2, "kpi_score": 3}]
    result = validate_candidates(raw, sentences, "tofu", 100.0)
    assert len(result) == 1
    assert result[0].end_sec == pytest.approx(12.0 + 0.04, abs=0.001)


def test_tofu_zu_kurz_wird_verworfen():
    sentences = [_sentence(0, 5.0, 6.0)]  # 1s < min 3s
    raw = [{"title": "x", "start_sentence_id": 0, "end_sentence_id": 0, "kpi_score": 5}]
    assert validate_candidates(raw, sentences, "tofu", 100.0) == []


def test_mofu_fenster_25_bis_60s():
    sentences = [_sentence(0, 0.0, 10.0), _sentence(1, 10.5, 40.0), _sentence(2, 40.5, 45.0)]
    raw = [
        {"title": "ok", "start_sentence_id": 0, "end_sentence_id": 2, "kpi_score": 4},     # 45s ✓
        {"title": "zu kurz", "start_sentence_id": 2, "end_sentence_id": 2, "kpi_score": 5}, # 4.5s ✗
    ]
    result = validate_candidates(raw, sentences, "mofu", 100.0)
    assert [r.title for r in result] == ["ok"]


def test_ueberlappung_hoeherer_score_gewinnt():
    sentences = [_sentence(i, i * 5.0, i * 5.0 + 4.0) for i in range(6)]
    raw = [
        {"title": "schwach", "start_sentence_id": 0, "end_sentence_id": 1, "kpi_score": 2},
        {"title": "stark", "start_sentence_id": 1, "end_sentence_id": 2, "kpi_score": 5},  # überlappt 'schwach'
        {"title": "solo", "start_sentence_id": 4, "end_sentence_id": 5, "kpi_score": 3},
    ]
    result = validate_candidates(raw, sentences, "tofu", 100.0)
    titles = [r.title for r in result]
    assert "stark" in titles and "solo" in titles and "schwach" not in titles
    # chronologisch sortiert + IDs neu vergeben
    assert [r.id for r in result] == ["short_01", "short_02"]
    assert result[0].start_sec < result[1].start_sec


def test_kpi_score_wird_geclampt_und_defaults():
    sentences = [_sentence(0, 0.0, 10.0), _sentence(1, 20.0, 30.0), _sentence(2, 40.0, 50.0)]
    raw = [
        {"title": "a", "start_sentence_id": 0, "end_sentence_id": 0, "kpi_score": 99},
        {"title": "b", "start_sentence_id": 1, "end_sentence_id": 1, "kpi_score": -3},
        {"title": "c", "start_sentence_id": 2, "end_sentence_id": 2, "kpi_score": "kaputt"},
    ]
    result = validate_candidates(raw, sentences, "tofu", 100.0)
    scores = {r.title: r.kpi_score for r in result}
    assert scores == {"a": 5, "b": 1, "c": 3}


def test_unbekannte_satz_ids_werden_verworfen():
    sentences = [_sentence(0, 0.0, 10.0)]
    raw = [
        {"title": "x", "start_sentence_id": 7, "end_sentence_id": 9, "kpi_score": 4},
        {"title": "y", "start_sentence_id": 0, "end_sentence_id": "abc", "kpi_score": 4},
        {"start_sentence_id": 0},  # end fehlt
    ]
    assert validate_candidates(raw, sentences, "tofu", 100.0) == []


def test_max_candidates_cap():
    cap = CONTENT_TYPE_RULES["tofu"]["max_candidates"]
    sentences = [_sentence(i, i * 20.0, i * 20.0 + 10.0) for i in range(cap + 5)]
    raw = [{"title": f"s{i}", "start_sentence_id": i, "end_sentence_id": i, "kpi_score": 3}
           for i in range(cap + 5)]
    result = validate_candidates(raw, sentences, "tofu", 10000.0)
    assert len(result) == cap


def test_padding_clampt_an_videoraendern():
    sentences = [_sentence(0, 0.02, 10.0)]
    raw = [{"title": "x", "start_sentence_id": 0, "end_sentence_id": 0, "kpi_score": 3}]
    result = validate_candidates(raw, sentences, "tofu", 10.01)
    assert result[0].start_sec == 0.0          # nicht negativ
    assert result[0].end_sec == 10.01          # nicht über Videolänge


def test_transcript_kommt_aus_dem_range():
    sentences = [
        Sentence(id=0, words=[_w(0, 1, "Hallo"), _w(1, 2, "Welt.")], start_sec=0.0, end_sec=2.0),
        Sentence(id=1, words=[_w(3, 4, "Zweiter"), _w(4, 5, "Satz.")], start_sec=3.0, end_sec=5.0),
    ]
    raw = [{"title": "x", "start_sentence_id": 0, "end_sentence_id": 1, "kpi_score": 3}]
    result = validate_candidates(raw, sentences, "tofu", 100.0)
    assert result[0].transcript == "Hallo Welt. Zweiter Satz."


# ===== slice_words_for_segment =====

def test_slice_offset_und_grenzen():
    words = [_w(10.0, 10.5, "vor"), _w(20.0, 20.6, "drin"), _w(25.0, 25.4, "auch"), _w(40.0, 40.5, "nach")]
    sliced = slice_words_for_segment(words, 19.9, 26.0)
    assert [w.word for w in sliced] == ["drin", "auch"]
    assert sliced[0].start == pytest.approx(0.1, abs=0.001)   # 20.0 - 19.9
    assert sliced[0].end == pytest.approx(0.7, abs=0.001)


def test_slice_toleranz_nimmt_randwoerter_mit():
    words = [_w(9.95, 10.4, "rand")]  # beginnt 50ms vor Segment-Start
    sliced = slice_words_for_segment(words, 10.0, 15.0)
    assert len(sliced) == 1
    assert sliced[0].start == 0.0  # geclampt, nie negativ


# ===== shorts.json Roundtrip =====

def test_shorts_plan_save_load_roundtrip(tmp_path):
    plan = ShortsPlan(
        project_id="abc123",
        content_type="tofu",
        shorts=[ShortCandidate(
            id="short_01", index=1, title="Test", start_sec=1.0, end_sec=11.0,
            duration_sec=10.0, transcript="t", kpi_score=4,
        )],
    )
    save_shorts_plan(tmp_path, plan)
    loaded = load_shorts_plan(tmp_path)
    assert loaded.project_id == "abc123"
    assert loaded.shorts[0].selected is True       # default an
    assert loaded.shorts[0].status == "pending"
    assert loaded.batch_status == "idle"
    # atomar: keine tmp-Datei übrig
    assert not (tmp_path / "shorts.json.tmp").exists()
    # JSON ist valide
    json.loads((tmp_path / "shorts.json").read_text())


def test_load_ohne_datei_gibt_none(tmp_path):
    assert load_shorts_plan(tmp_path) is None
