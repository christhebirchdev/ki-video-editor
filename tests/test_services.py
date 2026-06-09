# tests/test_services.py — alle Service-Tests zusammen
import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# ── Gemini Service ──────────────────────────────────────────────────────────

MOCK_GEMINI_RESPONSE = """{
  "takes": [
    {
      "take_id": "take_1", "start_seconds": 0.0, "end_seconds": 30.5,
      "transcript": "Hallo, heute zeige ich euch wie man...",
      "energy": "hoch", "eye_contact": "gut", "voice_quality": "klar",
      "issues": [],
      "gemini_description": "Sprecher schaut direkt in die Kamera, energetisch."
    },
    {
      "take_id": "take_2", "start_seconds": 31.0, "end_seconds": 62.0,
      "transcript": "Hallo, heute zeige ich euch wie man...",
      "energy": "mittel", "eye_contact": "unterbrochen", "voice_quality": "klar",
      "issues": ["Schaut bei Sekunde 45 kurz auf Bildschirm"],
      "gemini_description": "Zweiter Versuch, kurzes Wegschauen."
    }
  ],
  "full_transcript": "Hallo, heute zeige ich euch wie man...",
  "total_duration_seconds": 62.0,
  "repeated_content": [
    {"text": "Hallo, heute zeige ich euch wie man...", "take_ids": ["take_1", "take_2"]}
  ]
}"""


@patch("services.gemini_service.genai.upload_file")
@patch("services.gemini_service.genai.GenerativeModel")
def test_gemini_analyse_returns_video_analysis(mock_model_class, mock_upload):
    from services.gemini_service import analyse_video
    from models.analysis import VideoAnalysis

    mock_file = MagicMock()
    mock_file.state.name = "ACTIVE"
    mock_upload.return_value = mock_file

    mock_model = MagicMock()
    mock_model.generate_content.return_value = MagicMock(text=MOCK_GEMINI_RESPONSE)
    mock_model_class.return_value = mock_model

    result = analyse_video("proj_test", [Path("fake.mp4")])
    assert isinstance(result, VideoAnalysis)
    assert len(result.takes) == 2
    assert result.takes[0].energy == "hoch"
    assert result.takes[1].issues == ["Schaut bei Sekunde 45 kurz auf Bildschirm"]


@patch("services.gemini_service.genai.upload_file")
@patch("services.gemini_service.genai.GenerativeModel")
def test_gemini_take_ids_unique_per_file(mock_model_class, mock_upload):
    from services.gemini_service import analyse_video

    mock_file = MagicMock()
    mock_file.state.name = "ACTIVE"
    mock_upload.return_value = mock_file
    mock_model = MagicMock()
    mock_model.generate_content.return_value = MagicMock(text=MOCK_GEMINI_RESPONSE)
    mock_model_class.return_value = mock_model

    result = analyse_video("proj_test", [Path("clip1.mp4"), Path("clip2.mp4")])
    take_ids = [t.take_id for t in result.takes]
    assert len(take_ids) == len(set(take_ids)), "Take-IDs müssen eindeutig sein"


# ── Claude Service ──────────────────────────────────────────────────────────

MOCK_CLAUDE_RESPONSE = """{
  "decisions": [
    {"take_id": "file1_take_1", "keep": true, "reason": "Gute Energie",
     "in_point": 0.5, "out_point": 29.8, "order": 0},
    {"take_id": "file1_take_2", "keep": false, "reason": "Wegschauen",
     "in_point": 31.0, "out_point": 62.0, "order": -1}
  ],
  "claude_reasoning": "Take 1 gewählt, Take 2 verworfen wegen Wegschauen."
}"""


def _make_analysis():
    from models.analysis import VideoAnalysis, TakeAnalysis
    return VideoAnalysis(
        project_id="proj_test",
        takes=[
            TakeAnalysis(take_id="file1_take_1", file="clip.mp4",
                         start_seconds=0.0, end_seconds=30.5, transcript="Test",
                         energy="hoch", eye_contact="gut", voice_quality="klar",
                         issues=[], gemini_description="Gut"),
            TakeAnalysis(take_id="file1_take_2", file="clip.mp4",
                         start_seconds=31.0, end_seconds=62.0, transcript="Test",
                         energy="mittel", eye_contact="unterbrochen", voice_quality="klar",
                         issues=["Wegschauen"], gemini_description="Wegschauen")
        ],
        full_transcript="Test", total_duration_seconds=62.0,
        repeated_content=[{"text": "Test", "take_ids": ["file1_take_1", "file1_take_2"]}]
    )


@patch("services.claude_service._get_client")
def test_claude_plan_cuts_returns_cut_plan(mock_get_client):
    from services.claude_service import plan_cuts
    from models.memory import StyleMemory, MemoryRule
    from models.analysis import CutPlan

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=MOCK_CLAUDE_RESPONSE)]
    )
    mock_get_client.return_value = mock_client

    memory = StyleMemory(rules=[
        MemoryRule(rule="Wegschauen = Outtake", reason="Nutzerregel", confirmed_at=datetime.now())
    ])
    result = plan_cuts(_make_analysis(), memory, "tiktok")

    assert isinstance(result, CutPlan)
    assert len([d for d in result.decisions if d.keep]) == 1
    assert result.confirmed_by_user is False


# ── FFmpeg Service ──────────────────────────────────────────────────────────

def test_ffmpeg_cut_segment_calls_ffmpeg(tmp_path):
    with patch("services.ffmpeg_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from services.ffmpeg_service import cut_segment
        cut_segment(tmp_path / "input.mp4", tmp_path / "output.mp4", 5.0, 25.0)
        args = mock_run.call_args[0][0]
        assert "ffmpeg" in args
        assert "20.0" in args  # duration = 25 - 5


def test_ffmpeg_execute_raises_if_no_keeps(tmp_path):
    from services.ffmpeg_service import execute_cut_plan
    from models.analysis import CutPlan, CutDecision
    plan = CutPlan(project_id="test", decisions=[
        CutDecision(take_id="file1_take_1", keep=False, reason="Schlecht",
                    in_point=0, out_point=10, order=0)
    ], claude_reasoning="Nichts behalten")
    with pytest.raises(ValueError, match="Kein Take"):
        execute_cut_plan(plan, tmp_path / "raw", tmp_path / "output")


# ── Subtitle Service ────────────────────────────────────────────────────────

MOCK_SRT = "1\n00:00:00,000 --> 00:00:03,500\nHallo Test\n"


def test_subtitle_save_srt(tmp_path):
    from services.subtitle_service import save_srt
    path = save_srt(MOCK_SRT, tmp_path / "test.srt")
    assert path.exists()
    assert path.read_text() == MOCK_SRT


def test_subtitle_burn_calls_ffmpeg(tmp_path):
    with patch("services.subtitle_service.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from services.subtitle_service import burn_subtitles
        burn_subtitles(tmp_path / "input.mp4", tmp_path / "subs.srt", tmp_path / "out.mp4")
        args = " ".join(mock_run.call_args[0][0])
        assert "ffmpeg" in args
        assert "subtitles=" in args


# ── Memory Service ──────────────────────────────────────────────────────────

def test_memory_save_and_load(tmp_path):
    from models.memory import StyleMemory, MemoryRule
    with patch("services.memory_service.MEMORY_FILE", tmp_path / "memory.json"):
        from services.memory_service import save_memory, load_memory
        memory = StyleMemory(rules=[
            MemoryRule(rule="Test Regel", reason="Test", confirmed_at=datetime.now())
        ])
        save_memory(memory)
        loaded = load_memory()
    assert loaded.rules[0].rule == "Test Regel"


def test_memory_add_rule_increments_duplicate(tmp_path):
    with patch("services.memory_service.MEMORY_FILE", tmp_path / "memory.json"):
        from services.memory_service import add_rule, load_memory
        add_rule("Wegschauen = Outtake", "Nutzerregel")
        add_rule("Wegschauen = Outtake", "Nochmal")  # Duplikat
        memory = load_memory()
    assert len(memory.rules) == 1
    assert memory.rules[0].video_count == 2


def test_memory_learn_from_feedback_creates_rule(tmp_path):
    from services.memory_service import learn_from_feedback, load_memory
    from models.analysis import CutPlan, CutDecision

    plan = CutPlan(project_id="test", decisions=[
        CutDecision(take_id="file1_take_1", keep=True, reason="Claude mag es",
                    in_point=0, out_point=30, order=0)
    ], claude_reasoning="OK")

    with patch("services.memory_service.MEMORY_FILE", tmp_path / "memory.json"):
        learn_from_feedback(plan, [
            {"take_id": "file1_take_1", "user_keep": False, "user_reason": "Stimme zu leise"}
        ])
        memory = load_memory()
    assert any("leise" in r.rule for r in memory.rules)
