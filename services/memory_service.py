# services/memory_service.py
import json
from pathlib import Path
from datetime import datetime
from models.memory import StyleMemory, MemoryRule
from models.analysis import CutPlan
from config import MEMORY_PATH

MEMORY_FILE = MEMORY_PATH / "style_memory.json"


def load_memory() -> StyleMemory:
    if not MEMORY_FILE.exists():
        return StyleMemory()
    return StyleMemory(**json.loads(MEMORY_FILE.read_text()))


def save_memory(memory: StyleMemory):
    MEMORY_FILE.write_text(memory.model_dump_json(indent=2))


def add_rule(rule_text: str, reason: str) -> StyleMemory:
    memory = load_memory()
    for existing in memory.rules:
        if existing.rule.lower() == rule_text.lower():
            existing.video_count += 1
            save_memory(memory)
            return memory
    memory.rules.append(MemoryRule(rule=rule_text, reason=reason, confirmed_at=datetime.now()))
    save_memory(memory)
    return memory


def log_decision(project_id: str, cut_plan: CutPlan, user_override: bool = False):
    memory = load_memory()
    memory.decision_log.append({
        "project_id": project_id,
        "timestamp": datetime.now().isoformat(),
        "user_override": user_override,
        "decisions": [d.model_dump() for d in cut_plan.decisions],
        "claude_reasoning": cut_plan.claude_reasoning
    })
    save_memory(memory)


def learn_from_feedback(original_plan: CutPlan, user_corrections: list[dict]) -> StyleMemory:
    """Lernt aus User-Korrekturen und leitet neue Regeln ab."""
    memory = load_memory()
    for correction in user_corrections:
        original = next(
            (d for d in original_plan.decisions if d.take_id == correction["take_id"]), None
        )
        if original and original.keep != correction["user_keep"] and correction.get("user_reason"):
            add_rule(
                rule_text=correction["user_reason"],
                reason=f"Nutzer korrigierte Claude-Entscheidung für {correction['take_id']}"
            )
    return load_memory()
