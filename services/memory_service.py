# services/memory_service.py
"""
Style-Memory: Stil-Regeln aus User-Feedback ableiten und persistieren.

Mit der neuen Architektur (Whisper+Visual+Claude) erzeugt Claude direkt
Clips — keine segment_id-Korrekturen mehr nötig. learn_from_feedback ist
für zukünftige UI-Korrekturen vorgesehen (aktuell ungenutzt).
"""
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
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
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
    """Loggt einen Schnitt-Entscheid für Audit-Trail."""
    memory = load_memory()
    memory.decision_log.append({
        "project_id": project_id,
        "timestamp": datetime.now().isoformat(),
        "user_override": user_override,
        "clip_count": len(cut_plan.clips),
        "claude_reasoning": cut_plan.claude_reasoning,
    })
    save_memory(memory)
