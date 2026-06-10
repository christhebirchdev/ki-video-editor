# api/feedback.py
"""
Feedback-Endpoints: User bestätigt den von Claude generierten Cut-Plan.

Mit der neuen Architektur (Whisper+Visual+Claude) entscheidet Claude direkt
über Clips — keine segment_id-Korrekturen mehr nötig. /confirm setzt nur
das confirmed-Flag.
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.projects import _get_project_path
from models.analysis import CutPlan

router = APIRouter()


class ConfirmPlan(BaseModel):
    confirmed: bool


@router.post("/{project_id}/confirm")
async def confirm_cut_plan(project_id: str, body: ConfirmPlan):
    """User bestätigt den Schnittplan, damit /execute-cut darauf zugreifen darf."""
    project_path = _get_project_path(project_id)
    cut_plan_path = project_path / "cut_plan.json"

    if not cut_plan_path.exists():
        raise HTTPException(status_code=400, detail="Kein Schnittplan gefunden")

    cut_plan = CutPlan(**json.loads(cut_plan_path.read_text()))
    cut_plan.confirmed_by_user = body.confirmed
    cut_plan_path.write_text(cut_plan.model_dump_json(indent=2))

    return {"confirmed": body.confirmed, "project_id": project_id}
