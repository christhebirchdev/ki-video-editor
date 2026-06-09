# services/claude_service.py
import json
import re
import anthropic
from config import settings
from models.analysis import VideoAnalysis, CutDecision, CutPlan
from models.memory import StyleMemory

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client

TAKE_SELECTION_SYSTEM = """Du bist ein erfahrener Videocutter für Short-Form Social Media Content (TikTok, Reels, Shorts).
Du bekommst eine Analyse aller Takes eines Videos und Stil-Regeln des Nutzers.
Du entscheidest welche Takes behalten werden und wie das Video geschnitten wird.
Antworte NUR mit validem JSON, ohne Markdown-Blöcke."""

TAKE_SELECTION_PROMPT = """
# Video-Analyse
Projekt: {project_id}
Plattform: {platform}
Gesamtdauer: {duration:.1f} Sekunden

## Alle Takes
{takes_json}

## Wiederholungen (gleicher Inhalt)
{repeated_json}

## Stil-Regeln des Nutzers
{rules}

## Aufgabe
Entscheide für jeden Take: behalten oder verwerfen?
Für behaltene Takes: In-Punkt, Out-Punkt und Reihenfolge angeben.

Antworte im Format:
{{
  "decisions": [
    {{
      "take_id": "file1_take_1",
      "keep": true,
      "reason": "Gute Energie, kein Versprecher, Blickkontakt durchgehend",
      "in_point": 0.5,
      "out_point": 29.8,
      "order": 0
    }}
  ],
  "claude_reasoning": "Gesamtbegründung in 2-3 Sätzen"
}}
"""


def _format_rules(memory: StyleMemory) -> str:
    if not memory.rules:
        return "Noch keine Regeln gespeichert. Entscheide nach Standard-Qualitätskriterien."
    return "\n".join([f"- {r.rule} (Grund: {r.reason})" for r in memory.rules])


def plan_cuts(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    prompt = TAKE_SELECTION_PROMPT.format(
        project_id=analysis.project_id,
        platform=platform,
        duration=analysis.total_duration_seconds,
        takes_json=json.dumps([t.model_dump() for t in analysis.takes], ensure_ascii=False, indent=2),
        repeated_json=json.dumps(analysis.repeated_content, ensure_ascii=False, indent=2),
        rules=_format_rules(memory)
    )

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=TAKE_SELECTION_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # JSON aus Antwort extrahieren falls nötig
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()

    data = json.loads(raw)
    return CutPlan(
        project_id=analysis.project_id,
        decisions=[CutDecision(**d) for d in data["decisions"]],
        claude_reasoning=data["claude_reasoning"]
    )
