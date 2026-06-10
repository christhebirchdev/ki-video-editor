# services/cut_engine_v3.py
"""
V3 Hybrid-Engine — Verständlichkeits-priorisiert.

Selbe Architektur wie V2 (Code macht Heavy-Lifting, Claude wählt Sentence-IDs),
aber Claude bekommt einen ANDEREN Prompt mit klarer Hierarchie:

1. SKRIPT-SINN (höchste Priorität)
   → Welche Version macht den Punkt am verständlichsten?
   → Bei Wiederholungen: nimm die verständlichere Version
2. Audio-Probleme (Tie-Breaker)
3. Visuelle Probleme (Tie-Breaker bei sonst gleichwertigem Inhalt)

Code-Logik (Sentence-Grouping, Sub-Schnitte, Padding, Sperrzonen) bleibt identisch zu V2.
"""
import json
import re
import anthropic
from typing import Optional
from config import settings
from models.analysis import VideoAnalysis, CutPlan, CutClip
from models.memory import StyleMemory

# Wiederverwendung der V2-Helpers (Sentence-Grouping, Visual-Mapping, Sub-Clips)
from services.cut_engine_v2 import (
    group_into_sentences,
    assign_visual_quality,
    assign_position_labels,
    _sentence_to_clips,
    _is_filler,
    Sentence,
)


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


SENTENCE_SELECTION_SYSTEM_V3 = """Du bist ein Premium-Cutter für virale Kurzvideos. Du bekommst das Roh-Skript
des Sprechers als nummerierte Liste von Sätzen mit visuellen und akustischen Performance-Tags.

# 🎯 ENTSCHEIDUNGS-HIERARCHIE (STRIKT EINHALTEN)

Deine Entscheidungen folgen IMMER dieser Reihenfolge:

## PRIORITÄT 1 — SKRIPT-SINN (höchste Gewichtung)
Stell dir vor du wärst die Person die das Video später sieht. Welche Version macht den
Inhalt am verständlichsten?
- Bei Wiederholungen oder ähnlichen Aussagen: die Version mit klarerem Wortlaut, vollständigerem Satz, präziserer Aussage gewinnt.
- Wenn ein Take inhaltlich schwächer formuliert ist als der andere — auch wenn er visuell besser aussieht — verwirfst du den schlechteren.
- Bei Hook-Wiederholungen (Sprecher fängt mehrfach an): IMMER die letzte oder die inhaltlich beste Version. Niemals zwei Hooks behalten.

## PRIORITÄT 2 — AUDIO-PERFORMANCE (Tie-Breaker)
NUR wenn Skript-Qualität gleichwertig ist:
- Stabile Stimme schlägt brüchige
- Klare Aussprache schlägt nuschelnde
- Keine Filler/Stocker schlägt Filler/Stocker

## PRIORITÄT 3 — VISUELLE PERFORMANCE (letzter Tie-Breaker)
NUR wenn Skript und Audio gleichwertig sind:
- "perfect_take" gewinnt
- "thinking_glance" ist OK (nicht abwerten!)
- "outtake_break" verlieren (außer Skript ist außergewöhnlich besser)

# 🔑 KERNREGELN

- **Verständlichkeit schlägt Visual:** wenn ein "thinking_glance"-Take inhaltlich besser ist als ein "perfect_take", nimm den thinking_glance. Authentizität > Perfektion.
- **Verständlichkeit schlägt Audio-Imperfektion:** wenn ein leicht stockender Take inhaltlich klarer ist, nimm den stockenden. Code räumt die Stocker später raus.
- **outtake_break-Sätze:** IMMER raus, ausnahmslos. Auch wenn der Inhalt scheinbar wichtig ist — der Sprecher hat ihn in einem späteren sauberen Take wiederholt. Code schneidet outtake-Sätze garantiert raus.
- **script_reading_silence-Sätze:** IMMER raus (Sprecher liest nur Notizen, kein Content).
- **Hook-Zone (Anfang):** rücksichtslos auf einen einzigen besten Hook reduzieren. Hookt der Sprecher 3-4 mal an: lösche alle bis auf den verständlichsten/finalsten Versuch.
- **Inhaltliche Doppelungen mitten im Video:** auch raus — die spätere/bessere Version gewinnt.

# 📋 OUTPUT (NUR JSON, kein Markdown)
{
  "kept_sentence_ids": [3, 7, 10, 12, 15],
  "reasoning": "Sätze 1-2 als schwächere Hook-Versuche verworfen — Satz 3 ist die klarste Version. Satz 5 hat outtake_break aber Inhalt ist in Satz 7 besser formuliert. Sätze 8-9 sagten dasselbe wie 10, aber 10 war präziser."
}
"""

SENTENCE_SELECTION_USER_V3 = """
Projekt: {project_id} ({platform})
Gesamtdauer: {duration_sec}s

## Das vollständige Skript als Sätze
Jeder Satz hat: ID, Visual-Performance, Position im Video, Dauer.
Lies das gesamte Skript ZUERST als Text — verstehe was der Sprecher sagen will.
Dann wende die Entscheidungs-Hierarchie an.

{sentences_block}

## Audio-Issues (vom System erkannt — als Kontext)
{audio_issues_block}

## Stil-Regeln des Nutzers (optional)
{rules}

Wähle aus welche Sentence-IDs im finalen Cut bleiben.
"""


def _format_audio_issues(audio_issues) -> str:
    if not audio_issues:
        return "Keine Audio-Issues vom System gemeldet."
    lines = []
    for ai in audio_issues:
        lines.append(
            f"  - {ai.start_sec:.2f}s–{ai.end_sec:.2f}s: {ai.type} (\"{ai.text_heard}\", conf={ai.confidence})"
        )
    return "\n".join(lines)


def _format_rules(memory: StyleMemory) -> str:
    if not memory.rules:
        return "Keine speziellen Stil-Regeln."
    return "\n".join([f"- {r.rule}" for r in memory.rules])


def _select_sentences_with_claude_v3(
    sentences: list[Sentence],
    project_id: str,
    platform: str,
    duration_sec: float,
    memory: StyleMemory,
    audio_issues,
) -> tuple[list[int], str]:
    lines = []
    for s in sentences:
        dur = s.end_sec - s.start_sec
        lines.append(
            f'  {s.id}: ({s.visual_quality}, {s.position_label}, {dur:.1f}s) "{s.text}"'
        )
    sentences_block = "\n".join(lines)
    audio_block = _format_audio_issues(audio_issues)

    prompt = SENTENCE_SELECTION_USER_V3.format(
        project_id=project_id,
        platform=platform,
        duration_sec=f"{duration_sec:.1f}",
        sentences_block=sentences_block,
        audio_issues_block=audio_block,
        rules=_format_rules(memory),
    )

    print(f"  [V3] Claude-Call mit Modell '{settings.claude_model}' …")
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        system=SENTENCE_SELECTION_SYSTEM_V3,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    kept_ids = [int(x) for x in data.get("kept_sentence_ids", [])]
    reasoning = data.get("reasoning", "")
    return kept_ids, reasoning


def plan_cuts_v3(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    """V3 Pipeline: Wie V2, aber mit Verständlichkeits-priorisiertem Sentence-Selection-Prompt."""

    # 1) Wörter zu Sätzen (V2-Helper)
    sentences = group_into_sentences(analysis.whisper_words)
    print(f"  [V3] {len(sentences)} Sätze aus {len(analysis.whisper_words)} Wörtern")

    # 2) Visual-Mapping + Position
    assign_visual_quality(sentences, analysis.visual_phases)
    assign_position_labels(sentences, analysis.duration_sec)

    if not sentences:
        return CutPlan(project_id=analysis.project_id, clips=[], claude_reasoning="V3: Keine Sätze erkannt")

    # 3) Claude mit V3-Prompt (Verständlichkeit zuerst)
    audio_issues = getattr(analysis, "audio_issues", []) or []
    kept_ids, reasoning = _select_sentences_with_claude_v3(
        sentences, analysis.project_id, platform, analysis.duration_sec, memory, audio_issues
    )
    kept_sentences = [s for s in sentences if s.id in kept_ids]
    print(f"  [V3] Claude wählt {len(kept_sentences)}/{len(sentences)} Sätze nach Verständlichkeits-Hierarchie")

    # 4) Filler- und Outtake-Zonen kombiniert (Whisper + Gemini-Audio + Visual-Outtake)
    whisper_filler_zones = [(w.start, w.end) for w in analysis.whisper_words if _is_filler(w.word)]
    gemini_filler_zones = [(ai.start_sec, ai.end_sec) for ai in audio_issues if ai.type == "filler"]

    # 🛡️ Plausibilitäts-Check: Realistisch sind max. 1 Filler pro 5s Video-Länge.
    # Wenn Gemini deutlich mehr liefert (z.B. 103 in 93s mit Lite-Modell), halluziniert
    # das Modell — Filler-Zonen komplett ignorieren, Whisper+Long-Word reicht.
    max_plausible_fillers = max(5, int(analysis.duration_sec / 5))
    if len(gemini_filler_zones) > max_plausible_fillers:
        print(f"  [V3] ⚠️ Gemini lieferte {len(gemini_filler_zones)} Filler (Plausibilitätsgrenze: {max_plausible_fillers}) — ignoriere Gemini-Audio-Filler, nutze nur Whisper + Long-Word")
        gemini_filler_zones = []

    filler_zones = sorted(whisper_filler_zones + gemini_filler_zones)

    outtake_zones = [(ph.start_sec, ph.end_sec) for ph in analysis.visual_phases if ph.visual_quality == "outtake_break"]
    outtake_zones += [
        (ai.start_sec, ai.end_sec) for ai in audio_issues
        if ai.type in ("voice_break", "mispronunciation")
    ]
    print(f"  [V3] {len(filler_zones)} Filler-Zonen ({len(whisper_filler_zones)}+{len(gemini_filler_zones)}), {len(outtake_zones)} Outtake-Zonen")

    # 5) Sub-Clips bilden (V2-Helper, deterministisch)
    clips: list[CutClip] = []
    for s in kept_sentences:
        clips.extend(_sentence_to_clips(s, filler_zones, outtake_zones))

    print(f"  [V3] {len(clips)} finale Clips")

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=f"V3 (Verständlichkeits-Hierarchie): {reasoning}",
    )
