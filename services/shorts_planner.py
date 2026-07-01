# services/shorts_planner.py
"""
MultiCut-Planner: findet im langen Video (Podcast) Stellen, die als
eigenständige Shorts für den gewählten Content-Typ (TOFU/MOFU) funktionieren.

Arbeitsteilung wie bei V2-V5 bewährt:
- Code gruppiert Whisper-Wörter zu Sätzen (Reuse: cut_engine_v2.group_into_sentences)
- Claude wählt Satz-RANGES (robuster als rohe Sekunden) + Titel + KPI-Score 1-5
- Code erzwingt hart: Dauer-Fenster, keine Überlappungen, Score-Clamp, Padding

Kein Gemini: bei Podcast-Länge ist Video-Upload unpraktikabel, Whisper-Text reicht
für die semantische Stellen-Suche.
"""
import json
import re

from config import settings
from models.analysis import VideoAnalysis
from models.shorts import ShortCandidate, ShortsPlan
from services.cut_engine_v2 import group_into_sentences, Sentence

_client = None

# Padding wie in den Cut-Engines: Wortgrenzen schützen
PAD_START_SEC = 0.08
PAD_END_SEC = 0.04

CONTENT_TYPE_RULES = {
    "tofu": {
        "label": "TOFU (Top of Funnel)",
        "min_sec": 3.0,
        "max_sec": 15.0,
        "max_candidates": 12,
        "prompt_rules": (
            "- Maximal 15 Sekunden! Lieber 5-12s. Einzelne Statements, keine Erklärungen.\n"
            "- Kontrovers, polarisierend, überraschend oder emotional — Stoppt-den-Daumen-Material.\n"
            "- MUSS ohne jeden Kontext verständlich sein (der Zuschauer kennt den Podcast nicht).\n"
            "- Starke erste Worte: das erste gesprochene Wort ist der Hook.\n"
            "- Ziel-KPIs: Views, Shares, Kommentare (Diskussion provozieren)."
        ),
    },
    "mofu": {
        "label": "MOFU (Middle of Funnel)",
        "min_sec": 25.0,
        "max_sec": 60.0,
        "max_candidates": 8,
        "prompt_rules": (
            "- 30-60 Sekunden. Insights, How-to-Passagen, kompakte Takeaways mit konkretem Mehrwert.\n"
            "- Ein abgeschlossener Gedanke: Aha-Moment, Framework, Beispiel oder klare Anleitung.\n"
            "- MUSS ohne Kontext verständlich sein und einen vollständigen Nutzwert liefern.\n"
            "- Ziel-KPIs: Watchtime, Saves, Profilbesuche (Vertrauen + Expertise aufbauen)."
        ),
    },
}

SHORTS_PLANNER_SYSTEM = """Du bist Senior Content-Stratege für virale Kurzvideos. Du bekommst das \
transkribierte Langvideo (z.B. Podcast) als nummerierte Sätze mit Timestamps und findest die Stellen, \
die als EIGENSTÄNDIGE Shorts stark performen.

# Content-Typ: {content_label}
{content_rules}

# Harte Regeln
- Jeder Vorschlag ist ein zusammenhängender Satz-Range: start_sentence_id bis end_sentence_id (inklusive).
- Ranges dürfen sich NICHT überlappen.
- Das Dauer-Fenster ({min_sec:.0f}-{max_sec:.0f}s) ist PFLICHT — prüfe die Timestamps nach.
- kpi_score: 1-5. 5 = maximale Erwartung bei den Ziel-KPIs, 1 = minimale. Sei streng: \
5 nur für außergewöhnliche Stellen, verteile realistisch.
- Maximal {max_candidates} Vorschläge. Qualität vor Quantität — lieber 4 starke als 10 mittelmäßige.
- title: 4-8 Wörter, deutsch, beschreibt den Inhalt zugespitzt.
- rationale: 1 Satz, warum diese Stelle für {content_label} funktioniert.

# Output (NUR dieses JSON, kein Markdown):
{{
  "shorts": [
    {{"title": "…", "start_sentence_id": 12, "end_sentence_id": 14, "kpi_score": 4, "rationale": "…"}}
  ],
  "reasoning": "2-3 Sätze: wie bist du vorgegangen, was hast du bewusst NICHT genommen?"
}}"""

SHORTS_PLANNER_PROMPT = """# Langvideo-Transkript ({duration_sec:.0f}s gesamt, {n_sentences} Sätze)

{sentences_block}

# Aufgabe
Finde die besten Stellen für {content_label}-Shorts. Gib NUR das JSON zurück."""


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_sentences_block(sentences: list[Sentence]) -> str:
    lines = []
    for s in sentences:
        lines.append(f"[{s.id}] ({s.start_sec:.1f}-{s.end_sec:.1f}s) {s.text}")
    return "\n".join(lines)


def validate_candidates(
    raw_shorts: list[dict],
    sentences: list[Sentence],
    content_type: str,
    duration_sec: float,
) -> list[ShortCandidate]:
    """Code-Garantie: Dauer-Fenster, Überlappungen, Score-Clamp — egal was Claude liefert.

    Pure Funktion, ohne API — direkt testbar.
    """
    rules = CONTENT_TYPE_RULES[content_type]
    by_id = {s.id: s for s in sentences}

    candidates: list[dict] = []
    for raw in raw_shorts:
        try:
            start_id = int(raw["start_sentence_id"])
            end_id = int(raw["end_sentence_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if start_id > end_id or start_id not in by_id or end_id not in by_id:
            continue

        # Zu lang? Sätze vom Ende wegnehmen statt komplett verwerfen (Kern bleibt).
        while end_id > start_id and (by_id[end_id].end_sec - by_id[start_id].start_sec) > rules["max_sec"]:
            end_id -= 1

        dur = by_id[end_id].end_sec - by_id[start_id].start_sec
        if dur > rules["max_sec"] or dur < rules["min_sec"]:
            continue

        score = raw.get("kpi_score", 3)
        try:
            score = max(1, min(5, int(score)))
        except (TypeError, ValueError):
            score = 3

        candidates.append({
            "title": str(raw.get("title", "")).strip() or f"Short ab {by_id[start_id].start_sec:.0f}s",
            "start_id": start_id,
            "end_id": end_id,
            "start_sec": by_id[start_id].start_sec,
            "end_sec": by_id[end_id].end_sec,
            "kpi_score": score,
            "rationale": str(raw.get("rationale", "")).strip(),
        })

    # Überlappungen auflösen: höherer Score gewinnt, bei Gleichstand der frühere.
    candidates.sort(key=lambda c: (-c["kpi_score"], c["start_sec"]))
    kept: list[dict] = []
    for c in candidates:
        if any(c["start_sec"] < k["end_sec"] and c["end_sec"] > k["start_sec"] for k in kept):
            continue
        kept.append(c)
        if len(kept) >= rules["max_candidates"]:
            break

    kept.sort(key=lambda c: c["start_sec"])

    result: list[ShortCandidate] = []
    for i, c in enumerate(kept, start=1):
        start = max(0.0, c["start_sec"] - PAD_START_SEC)
        end = min(duration_sec, c["end_sec"] + PAD_END_SEC)
        transcript = " ".join(by_id[sid].text for sid in range(c["start_id"], c["end_id"] + 1) if sid in by_id)
        result.append(ShortCandidate(
            id=f"short_{i:02d}",
            index=i,
            title=c["title"],
            start_sec=round(start, 3),
            end_sec=round(end, 3),
            duration_sec=round(end - start, 3),
            transcript=transcript,
            kpi_score=c["kpi_score"],
            rationale=c["rationale"],
        ))
    return result


def plan_shorts(analysis: VideoAnalysis, content_type: str) -> ShortsPlan:
    """Hauptfunktion: Whisper-Sätze → Claude-Auswahl → Code-Validierung → ShortsPlan."""
    if content_type not in CONTENT_TYPE_RULES:
        raise ValueError(f"Unbekannter content_type '{content_type}' (erlaubt: tofu, mofu)")

    rules = CONTENT_TYPE_RULES[content_type]
    sentences = group_into_sentences(analysis.whisper_words)
    print(f"  [MULTICUT] {len(sentences)} Sätze aus {len(analysis.whisper_words)} Wörtern")
    if not sentences:
        return ShortsPlan(project_id=analysis.project_id, content_type=content_type,
                          claude_reasoning="Keine Sätze erkannt — ist das Video transkribierbar?")

    system = SHORTS_PLANNER_SYSTEM.format(
        content_label=rules["label"],
        content_rules=rules["prompt_rules"],
        min_sec=rules["min_sec"],
        max_sec=rules["max_sec"],
        max_candidates=rules["max_candidates"],
    )
    prompt = SHORTS_PLANNER_PROMPT.format(
        duration_sec=analysis.duration_sec,
        n_sentences=len(sentences),
        sentences_block=_build_sentences_block(sentences),
        content_label=rules["label"],
    )

    print(f"  [MULTICUT] Claude-Call ({settings.claude_model}) für {content_type}-Kandidaten …")
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    shorts = validate_candidates(
        data.get("shorts", []), sentences, content_type, analysis.duration_sec
    )
    print(f"  [MULTICUT] {len(data.get('shorts', []))} Vorschläge → {len(shorts)} nach Code-Validierung")

    return ShortsPlan(
        project_id=analysis.project_id,
        content_type=content_type,
        shorts=shorts,
        claude_reasoning=data.get("reasoning", ""),
    )
