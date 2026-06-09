# api/feedback.py
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.projects import _load_project, _save_project, _get_project_path
from services.memory_service import log_decision, learn_from_feedback

router = APIRouter()


class ConfirmPlan(BaseModel):
    confirmed: bool


class UserCorrection(BaseModel):
    take_id: str
    user_keep: bool
    user_reason: str = ""


class FeedbackRequest(BaseModel):
    corrections: list[UserCorrection]


@router.post("/{project_id}/confirm")
async def confirm_cut_plan(project_id: str, body: ConfirmPlan):
    """User bestätigt den Schnittplan ohne Korrekturen."""
    project_path = _get_project_path(project_id)
    cut_plan_path = project_path / "cut_plan.json"

    if not cut_plan_path.exists():
        raise HTTPException(status_code=400, detail="Kein Schnittplan gefunden")

    from models.analysis import CutPlan
    cut_plan = CutPlan(**json.loads(cut_plan_path.read_text()))
    cut_plan.confirmed_by_user = body.confirmed
    cut_plan_path.write_text(cut_plan.model_dump_json(indent=2))

    if body.confirmed:
        log_decision(project_id, cut_plan, user_override=False)

    return {"confirmed": body.confirmed, "project_id": project_id}


@router.post("/{project_id}/corrections")
async def submit_corrections(project_id: str, body: FeedbackRequest):
    """User korrigiert Claudes Entscheidungen — neue Regeln werden ins Gedächtnis gespeichert."""
    project_path = _get_project_path(project_id)
    cut_plan_path = project_path / "cut_plan.json"

    if not cut_plan_path.exists():
        raise HTTPException(status_code=400, detail="Kein Schnittplan gefunden")

    from models.analysis import CutPlan
    cut_plan = CutPlan(**json.loads(cut_plan_path.read_text()))

    correction_map = {c.take_id: c for c in body.corrections}
    for decision in cut_plan.decisions:
        if decision.take_id in correction_map:
            c = correction_map[decision.take_id]
            decision.keep = c.user_keep
            decision.reason = f"[User korrigiert] {c.user_reason}"

    cut_plan.confirmed_by_user = True
    cut_plan_path.write_text(cut_plan.model_dump_json(indent=2))

    learn_from_feedback(cut_plan, [c.model_dump() for c in body.corrections])
    log_decision(project_id, cut_plan, user_override=True)

    return {"corrections_applied": len(body.corrections), "memory_updated": True}
