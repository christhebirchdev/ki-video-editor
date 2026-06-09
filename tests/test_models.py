# tests/test_models.py
from models.project import Project, ProjectCreate
from models.analysis import TakeAnalysis, CutDecision, CutPlan
from models.memory import MemoryRule, StyleMemory
from datetime import datetime


def test_project_create():
    p = ProjectCreate(name="Test Video", platform="tiktok")
    assert p.name == "Test Video"
    assert p.platform == "tiktok"


def test_take_analysis():
    take = TakeAnalysis(
        take_id="take_1", file="clip_01.mp4",
        start_seconds=0.0, end_seconds=45.2,
        transcript="Hallo, heute zeige ich euch...",
        energy="hoch", eye_contact="gut", voice_quality="klar",
        issues=[], gemini_description="Sprecher schaut direkt in die Kamera."
    )
    assert take.take_id == "take_1"
    assert take.issues == []


def test_cut_plan_unconfirmed_by_default():
    plan = CutPlan(project_id="proj_123", decisions=[], claude_reasoning="Test.")
    assert plan.confirmed_by_user is False


def test_memory_rule():
    rule = MemoryRule(rule="Wegschauen = Outtake", reason="Nutzerregel", confirmed_at=datetime.now())
    assert rule.video_count == 1
