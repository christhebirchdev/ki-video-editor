# services/cut_engine_v5.py
"""
V5 Local-First-Engine — komplett OHNE Gemini.

Spart pro Analyse die kompletten Gemini-Kosten (gemessen am 320-MB-Testreel:
114,6s Upload + 39,0s Processing + 32,5s Inference = 186s) und alle Quota-
Risiken. Einziger API-Call der gesamten Pipeline: 1× Claude-Selection (gecacht).

Gemini-Ersatz (alles lokal + deterministisch):
- outtake_break        → outtake_detector.py (Restart/Abbruch via Text+Timing,
                          Fragmente via Whisper-"-"-Endungen)
- Audio-Filler         → audio_truth.py (VAD-Pause-Filler + RMS-Tail-Trims, aus V4.2)
                          + verbatim_whisper.py (Filler-treuer 2. Whisper-Pass für
                          In-Wort-Filler — die Lücke, die V4.2 aufgedeckt hat)
- thinking_glance      → entfällt (war ohnehin nur ein "nichts tun"-Signal)
- script_reading_silence → strukturell abgedeckt (Clips entstehen nur aus Wörtern)

Selection: V3-Verständlichkeits-Hierarchie ohne Visual-Ebene, plus die
deterministischen Code-Flags des Outtake-Detektors im Prompt. Cache wie V4.

Code-Garantien:
- enforced-Outtakes (Restart unvollständig / Abbruch) fliegen IMMER raus,
  unabhängig von Claudes Entscheidung (Philosophie der bewährten
  outtake_break-Garantie, jetzt ohne Gemini)
- Clip-Builder ist der V4.2-Builder (per-Zone-Safety, Tail-Trim mit Release,
  kein Text-Fallback wenn RMS-Daten vorliegen)

V1–V4 bleiben unberührt. Funktioniert garantiert auch wenn alle lokalen
Detektoren 0 Funde liefern.
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import anthropic

from config import settings, PROJECTS_PATH
from models.analysis import VideoAnalysis, CutPlan, CutClip
from models.memory import StyleMemory

# V2-Helpers (unverändert wiederverwendet)
from services.cut_engine_v2 import (
    group_into_sentences,
    assign_position_labels,
    estimated_max_word_duration,
    _is_filler,
    Sentence,
)
# V4.2-Builder + Zonen-Logik (unverändert wiederverwendet)
from services.cut_engine_v4 import (
    _sentence_to_clips_v4,
    _find_raw_video,
    VAD_ZONE_SAFETY_SEC,
    COARSE_ZONE_SAFETY_SEC,
)
from services.cut_engine_v3 import _format_rules
from services.audio_truth import build_audio_truth
from services.verbatim_whisper import get_verbatim_filler_zones
from services.outtake_detector import detect_outtake_sentences, detect_fragment_zones, detect_duplicate_word_zones

V5_PROMPT_VERSION = "v5.1"

# Verbatim-Whisper-Timestamps sind genauer als Gemini, ungenauer als VAD
VERBATIM_ZONE_SAFETY_SEC = 0.06

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ===== Selection (V3-Hierarchie ohne Visual, mit Code-Flags) =====

SENTENCE_SELECTION_SYSTEM_V5 = """Du bist ein Premium-Cutter für virale Kurzvideos. Du bekommst das Roh-Skript
des Sprechers als nummerierte Liste von Sätzen. Ein deterministischer Code-Detektor
hat bereits Neustarts und Doppel-Takes markiert — diese Flags sind verlässlich.

# 🎯 ENTSCHEIDUNGS-HIERARCHIE (STRIKT EINHALTEN)

## PRIORITÄT 1 — SKRIPT-SINN (höchste Gewichtung)
Stell dir vor du wärst die Person die das Video später sieht. Welche Version macht den
Inhalt am verständlichsten?
- Bei Wiederholungen oder ähnlichen Aussagen: die Version mit klarerem Wortlaut, vollständigerem Satz, präziserer Aussage gewinnt.
- Bei Hook-Wiederholungen (Sprecher fängt mehrfach an): IMMER die letzte oder die inhaltlich beste Version. Niemals zwei Hooks behalten.

## PRIORITÄT 2 — CODE-FLAGS (verlässliche Detektor-Hinweise)
- Flag [WIRD VOM CODE ENTFERNT]: Dieser Satz fliegt garantiert raus (Neustart/Abbruch).
  Plane den Cut so, als gäbe es ihn nicht. Wähle ihn NICHT.
- Flag [DOPPEL-TAKE zu Satz X]: Dieser Satz und Satz X sagen dasselbe.
  Behalte GENAU EINEN von beiden — den verständlicheren.

## PRIORITÄT 3 — AUDIO-PERFORMANCE (Tie-Breaker)
NUR wenn Skript-Qualität gleichwertig ist: flüssige Sätze schlagen stockende.
Einzelne Filler sind KEIN Grund einen inhaltlich besseren Satz zu verwerfen —
der Code schneidet Filler später chirurgisch raus.

# 📋 OUTPUT (NUR JSON, kein Markdown)
{
  "kept_sentence_ids": [3, 7, 10, 12, 15],
  "reasoning": "Sätze 1-2 als schwächere Hook-Versuche verworfen — Satz 3 ist die klarste Version. Satz 8 war Doppel-Take zu 10, 10 war präziser."
}
"""

SENTENCE_SELECTION_USER_V5 = """
Projekt: {project_id} ({platform})
Gesamtdauer: {duration_sec}s

## Das vollständige Skript als Sätze
Jeder Satz hat: ID, Position im Video, Dauer, ggf. Code-Flags.
Lies das gesamte Skript ZUERST als Text — verstehe was der Sprecher sagen will.
Dann wende die Entscheidungs-Hierarchie an.

{sentences_block}

## Stil-Regeln des Nutzers (optional)
{rules}

Wähle aus welche Sentence-IDs im finalen Cut bleiben.
"""


def _build_sentences_block(sentences: list[Sentence], flags_by_id: dict) -> str:
    lines = []
    for s in sentences:
        dur = s.end_sec - s.start_sec
        flag = flags_by_id.get(s.id)
        if flag is None:
            tag = ""
        elif flag.enforced:
            tag = " [WIRD VOM CODE ENTFERNT: " + flag.reason + "]"
        else:
            tag = " [" + flag.reason.upper().replace("DOPPEL-TAKE ZU SATZ", "DOPPEL-TAKE zu Satz") + "]"
        lines.append(f'  {s.id}: ({s.position_label}, {dur:.1f}s){tag} "{s.text}"')
    return "\n".join(lines)


def _selection_cache_key_v5(sentences_block: str, rules: str) -> str:
    payload = "\x1f".join([V5_PROMPT_VERSION, settings.claude_model, sentences_block, rules])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_sentences_cached_v5(
    sentences: list[Sentence],
    flags_by_id: dict,
    project_id: str,
    platform: str,
    duration_sec: float,
    memory: StyleMemory,
) -> tuple[list[int], str]:
    sentences_block = _build_sentences_block(sentences, flags_by_id)
    rules = _format_rules(memory)

    cache_key = _selection_cache_key_v5(sentences_block, rules)
    cache_path = PROJECTS_PATH / project_id / "v5_selection_cache.json"

    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}
        entry = cache.get(cache_key)
        if entry:
            print(f"  [V5] Selection-Cache-Hit — kein Claude-Call (deterministisch)")
            return entry["kept_ids"], entry["reasoning"] + " [cached]"

    prompt = SENTENCE_SELECTION_USER_V5.format(
        project_id=project_id,
        platform=platform,
        duration_sec=f"{duration_sec:.1f}",
        sentences_block=sentences_block,
        rules=rules,
    )

    print(f"  [V5] Claude-Call mit Modell '{settings.claude_model}' …")
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        system=SENTENCE_SELECTION_SYSTEM_V5,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    valid_ids = {s.id for s in sentences}
    kept_ids = sorted({int(x) for x in data.get("kept_sentence_ids", [])} & valid_ids)
    reasoning = data.get("reasoning", "")

    cache[cache_key] = {"kept_ids": kept_ids, "reasoning": reasoning}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"  [V5] WARN: Selection-Cache nicht schreibbar: {e}")

    return kept_ids, reasoning


# ===== Hauptfunktion =====

def plan_cuts_v5(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    """V5: komplett ohne Gemini — lokale Detektoren + Claude-Selection (gecacht)."""

    # 1) Wörter → Sätze + Position (KEIN Visual-Mapping — V5 ignoriert visual_phases
    #    bewusst auch wenn vorhanden, damit alte und neue Projekte identisch laufen)
    sentences = group_into_sentences(analysis.whisper_words)
    assign_position_labels(sentences, analysis.duration_sec)
    print(f"  [V5] {len(sentences)} Sätze aus {len(analysis.whisper_words)} Wörtern (Gemini: nicht verwendet)")

    if not sentences:
        return CutPlan(project_id=analysis.project_id, clips=[], claude_reasoning="V5: Keine Sätze erkannt")

    # 2) Lokale Outtake-Erkennung (Gemini-Visual-Ersatz)
    flags = detect_outtake_sentences(sentences)
    flags_by_id = {f.sentence_id: f for f in flags}
    enforced_out = {f.sentence_id for f in flags if f.enforced}
    for f in flags:
        marker = "🛡️ Code-Garantie" if f.enforced else "→ Flag für Claude"
        print(f"  [V5] Outtake-Detektor: Satz {f.sentence_id} ({f.kind}) — {f.reason} {marker}")

    # 3) Claude-Selection mit Flags (gecacht)
    kept_ids, reasoning = _select_sentences_cached_v5(
        sentences, flags_by_id, analysis.project_id, platform, analysis.duration_sec, memory
    )

    # 🛡️ CODE-GARANTIE: enforced-Outtakes fliegen immer raus, egal was Claude sagt
    kept_ids = [i for i in kept_ids if i not in enforced_out]
    kept_sentences = [s for s in sentences if s.id in kept_ids]
    print(f"  [V5] {len(kept_sentences)}/{len(sentences)} Sätze behalten ({len(enforced_out)} per Code-Garantie raus)")

    # 4) Lokale Audio-Wahrheit (V4.2-Logik, geteilter Cache) + Verbatim-Pass
    video_path = _find_raw_video(analysis.project_id)
    if video_path is not None:
        truth = build_audio_truth(
            video_path,
            analysis.whisper_words,
            estimated_max_word_duration,
            cache_path=PROJECTS_PATH / analysis.project_id / "v4_audio_truth.json",
        )
        verbatim_zones = get_verbatim_filler_zones(
            video_path,
            cache_path=PROJECTS_PATH / analysis.project_id / "v5_verbatim_fillers.json",
        )
    else:
        print(f"  [V5] WARN: kein Raw-Video gefunden — Audio-Analysen übersprungen")
        truth = {"pause_filler_zones": [], "tail_trim_zones": [], "vad_segment_count": 0}
        verbatim_zones = []

    truth_available = truth.get("vad_segment_count", 0) > 0

    # 5) Filler-Zonen aus drei LOKALEN Quellen (Safety pro Quelle, V4.2-Prinzip)
    whisper_filler_zones = [
        (w.start, w.end, COARSE_ZONE_SAFETY_SEC)
        for w in analysis.whisper_words if _is_filler(w.word)
    ]
    pause_filler_zones = [(z[0], z[1], VAD_ZONE_SAFETY_SEC) for z in truth["pause_filler_zones"]]
    verbatim_filler_zones = [(zs, ze, VERBATIM_ZONE_SAFETY_SEC) for zs, ze in verbatim_zones]
    filler_zones = sorted(whisper_filler_zones + pause_filler_zones + verbatim_filler_zones)

    tail_trim_zones = [tuple(z) for z in truth["tail_trim_zones"]]

    # Outtake-Zonen auf Wort-Ebene: nur Fragmente ("Kund-")
    outtake_zones = detect_fragment_zones(analysis.whisper_words)
    _dup_zones = detect_duplicate_word_zones(analysis.whisper_words)
    if _dup_zones:
        print(f"  [V5] {len(_dup_zones)} doppelte Wörter (Stotterer) entfernt: "
              + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in _dup_zones))
    outtake_zones = sorted(outtake_zones + _dup_zones)

    print(
        f"  [V5] Zonen: {len(whisper_filler_zones)} Whisper-Filler, "
        f"{len(pause_filler_zones)} Pause-Filler (VAD), {len(verbatim_filler_zones)} Verbatim-Filler, "
        f"{len(tail_trim_zones)} Tail-Trims (RMS), {len(outtake_zones)} Fragmente "
        f"(Text-Fallback: {'aus' if truth_available else 'an'})"
    )

    # 6) Clips bauen — V4.2-Builder, unverändert
    clips: list[CutClip] = []
    for s in kept_sentences:
        clips.extend(_sentence_to_clips_v4(
            s, filler_zones, outtake_zones, tail_trim_zones,
            use_text_fallback=not truth_available,
        ))

    print(f"  [V5] {len(clips)} finale Clips")

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=f"V5 (Local-First, ohne Gemini): {reasoning}",
    )
