# services/cut_engine_v4.py
"""
V4 Hybrid-Engine — Lokale Audio-Wahrheit + Determinismus.

Baut auf V3 auf (gleicher Verständlichkeits-Prompt für die Sentence-Selection),
ändert aber drei Dinge — V1/V2/V3 bleiben unangetastet:

1. LOKALE AUDIO-WAHRHEIT statt Gemini-Audio-Filler:
   - Silero VAD findet Filler in Pausen ZWISCHEN Wörtern (war bisher unsichtbar)
   - RMS-Energie findet den echten Schnittpunkt in Long-Words ("Hand…äh",
     "jemand…äh" 1.33×) statt der reinen Zeichen-Schätzung
   - Gemini-Audio-Issues vom Typ "filler" werden in V4 KOMPLETT ignoriert
     (belegt nicht-deterministisch: 7/6/3 Filler bei identischem Input).
     voice_break/mispronunciation bleiben als Outtake-Zonen erhalten,
     Gemini-VISUAL bleibt voll aktiv (outtake_break-Garantie wie V2/V3).

2. SELECTION-CACHE: sha256(Modell + Prompt-Version + Input) → kept_ids werden
   pro Projekt eingefroren (v4_selection_cache.json). Gleicher Input = exakt
   gleicher Cut, Re-Plan ohne Claude-Call (schneller + kostenlos).
   Hintergrund: temperature ist bei claude-sonnet-4-6 deprecated → Determinismus
   muss code-seitig kommen.

3. CLIP-BUILDER V4: wie V2, zusätzlich
   - Split wenn die Pause zum nächsten Wort eine Pause-Filler-Zone enthält
     (auch bei Pausen <400ms, die V2 durchlaufen lässt)
   - Tail-Trim-Zonen kürzen Wörter am ECHTEN Energie-Einbruch; die
     V2-Zweistufen-Heuristik (1.4×/1.7×) bleibt als Fallback ohne Audio-Signal.

Code-Pfad funktioniert garantiert auch mit 0 erkannten Zonen (Audio-Analyse-
Fehler ⇒ leere Zonen ⇒ Verhalten wie V3 ohne Gemini-Audio).
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import anthropic

from config import settings, PROJECTS_PATH
from models.analysis import VideoAnalysis, CutPlan, CutClip, WhisperWord
from models.memory import StyleMemory

# Wiederverwendung der V2-Helpers + Konstanten (V2 bleibt unverändert)
from services.cut_engine_v2 import (
    group_into_sentences,
    assign_visual_quality,
    assign_position_labels,
    estimated_max_word_duration,
    _is_filler,
    Sentence,
    LONG_PAUSE_WITHIN_SENTENCE,
    LONG_PAUSE_NO_POSTPAD_SEC,
    PADDING_START_SEC,
    PADDING_END_SEC,
    MIN_CLIP_DURATION,
    FILLER_GAP_SAFETY_MS,
    OUTTAKE_SAFETY_MS,
    SUSPICIOUS_WORD_DURATION_SEC,
    HESITATION_FACTOR_HARD,
    HESITATION_FACTOR_SOFT,
    HESITATION_MIN_TRUNCATE_SEC,
    HESITATION_SOFT_RESERVE_SEC,
)
# Bewährter V3-Prompt wird 1:1 wiederverwendet (V3 bleibt unverändert)
from services.cut_engine_v3 import (
    SENTENCE_SELECTION_SYSTEM_V3,
    SENTENCE_SELECTION_USER_V3,
    _format_audio_issues,
    _format_rules,
)
from services.audio_truth import build_audio_truth

# Bei Prompt-/Logik-Änderungen hochzählen → Selection-Cache invalidiert sich selbst
V4_PROMPT_VERSION = "v4.1"

# Mindest-Überlappung damit eine Zone in einer Wort-Pause als "enthalten" gilt
GAP_ZONE_MIN_OVERLAP_SEC = 0.03
# Mindest-Pause damit der Gap-Filler-Check überhaupt greift (Whisper-Jitter)
GAP_CHECK_MIN_PAUSE_SEC = 0.12
# V4.2: Tail-Trim-Mindestrest 250ms (120ms-Stummel klangen abgeschnitten — Testreel 4.0)
TAIL_TRIM_MIN_WORD_REST_SEC = 0.25

# V4.2: Zonen-Safety nach Quelle getrennt.
# VAD/RMS sind auf ~20ms genau — die pauschale 100ms-Safety (für Gemini ±200-300ms
# gedacht) hat in Testreel 4.0 Nachbar-Wörter verschluckt ('der', 'dann', 'die', 'können').
VAD_ZONE_SAFETY_SEC = 0.02            # lokale Zonen (VAD-Pause-Filler): präzise
COARSE_ZONE_SAFETY_SEC = FILLER_GAP_SAFETY_MS / 1000.0   # Whisper-Text + Gemini: 100ms

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ===== Selection mit Cache =====

def _selection_cache_key(sentences_block: str, audio_block: str, rules: str) -> str:
    payload = "\x1f".join([
        V4_PROMPT_VERSION,
        settings.claude_model,
        sentences_block,
        audio_block,
        rules,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_sentences_cached(
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
    rules = _format_rules(memory)

    cache_key = _selection_cache_key(sentences_block, audio_block, rules)
    cache_path = PROJECTS_PATH / project_id / "v4_selection_cache.json"

    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}
        entry = cache.get(cache_key)
        if entry:
            print(f"  [V4] Selection-Cache-Hit — kein Claude-Call (deterministisch)")
            return entry["kept_ids"], entry["reasoning"] + " [cached]"
    else:
        cache = {}

    prompt = SENTENCE_SELECTION_USER_V3.format(
        project_id=project_id,
        platform=platform,
        duration_sec=f"{duration_sec:.1f}",
        sentences_block=sentences_block,
        audio_issues_block=audio_block,
        rules=rules,
    )

    print(f"  [V4] Claude-Call mit Modell '{settings.claude_model}' …")
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

    # Code-Garantie: nur existierende IDs, dedupliziert, sortiert (stabil)
    valid_ids = {s.id for s in sentences}
    kept_ids = sorted({int(x) for x in data.get("kept_sentence_ids", [])} & valid_ids)
    reasoning = data.get("reasoning", "")

    cache[cache_key] = {"kept_ids": kept_ids, "reasoning": reasoning}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"  [V4] WARN: Selection-Cache nicht schreibbar: {e}")

    return kept_ids, reasoning


# ===== Clip-Builder V4 =====

def _normalize_zones(zones, default_safety: float = VAD_ZONE_SAFETY_SEC) -> list[tuple[float, float, float]]:
    """Akzeptiert (start, end) oder (start, end, safety) → einheitlich 3-Tupel."""
    out = []
    for z in zones:
        if len(z) == 3:
            out.append((z[0], z[1], z[2]))
        else:
            out.append((z[0], z[1], default_safety))
    return out


def _zone_overlap(a_start: float, a_end: float, zones: list[tuple[float, float, float]]) -> bool:
    return any(min(a_end, ze) - max(a_start, zs) >= GAP_ZONE_MIN_OVERLAP_SEC for zs, ze, _ in zones)


def _sentence_to_clips_v4(
    sentence: Sentence,
    filler_zones: list,
    outtake_zones: list[tuple[float, float]],
    tail_trim_zones: list[tuple[float, float]],
    use_text_fallback: bool = True,
) -> list[CutClip]:
    """
    Wie V2 _sentence_to_clips, plus:
    - Gap-Filler-Split: Pause zum nächsten Wort enthält eine Filler-Zone
      → Split + kein Post-Padding (fängt Pause-Filler auch bei Pausen <400ms)
    - Tail-Trim: Wort wird am Energie-Einbruch gekürzt (Zone aus audio_truth)
    - filler_zones: (start, end) oder (start, end, safety) — V4.2: Safety pro
      Quelle (VAD 20ms, Whisper/Gemini 100ms), damit präzise VAD-Zonen keine
      Nachbar-Wörter mehr verschlucken
    - use_text_fallback=False (V4.2): wenn echte RMS-Daten vorliegen, wird die
      V2-Zeichen-Schätzung NICHT mehr angewandt — sie schnitt nachweislich
      mitten in stimmhaftes Audio ('und' 850ms, 'nutzt' 610ms in Testreel 4.0)
    """
    # 🛡️ CODE-GARANTIE wie V2: outtake_break-Sätze nie in den Cut
    if sentence.visual_quality == "outtake_break":
        return []

    filler_zones = _normalize_zones(filler_zones)

    clips: list[CutClip] = []
    buf: list[WhisperWord] = []
    outtake_safety = OUTTAKE_SAFETY_MS / 1000.0

    def in_outtake(w: WhisperWord) -> bool:
        mid = (w.start + w.end) / 2.0
        return any(s - outtake_safety <= mid <= e + outtake_safety for s, e in outtake_zones)

    def in_filler_zone(w: WhisperWord) -> bool:
        mid = (w.start + w.end) / 2.0
        return any(s - sf <= mid <= e + sf for s, e, sf in filler_zones)

    def tail_zone_start_for(w: WhisperWord) -> Optional[float]:
        """Beginn einer Tail-Trim-Zone innerhalb dieses Wortes (falls vorhanden)."""
        for zs, ze in tail_trim_zones:
            if w.start < zs < w.end - 0.02 and ze >= w.end - 0.05:
                return zs
        return None

    def flush(safe_pre_pad: bool, safe_post_pad: bool):
        nonlocal buf
        if not buf:
            return
        start = buf[0].start
        end = buf[-1].end
        if safe_pre_pad:
            start -= PADDING_START_SEC
        if safe_post_pad:
            end += PADDING_END_SEC
        start = max(0.0, start)
        if end - start >= MIN_CLIP_DURATION:
            clips.append(CutClip(
                start=start, end=end,
                reason=f"Satz #{sentence.id} ({sentence.visual_quality})"
            ))
        buf = []

    for w in sentence.words:
        if in_outtake(w):
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        if _is_filler(w.word) or in_filler_zone(w):
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        # 🆕 Gap-Filler-Split: Filler-Zone in der Pause vor diesem Wort?
        if buf:
            pause = w.start - buf[-1].end
            gap_filler = (
                pause > GAP_CHECK_MIN_PAUSE_SEC
                and _zone_overlap(buf[-1].end, w.start, filler_zones)
            )
            if gap_filler:
                # Filler liegt in der Pause → Split, hartes Ende ohne Post-Padding
                flush(safe_pre_pad=True, safe_post_pad=False)
            elif pause > LONG_PAUSE_WITHIN_SENTENCE:
                allow_postpad = pause <= LONG_PAUSE_NO_POSTPAD_SEC
                flush(safe_pre_pad=True, safe_post_pad=allow_postpad)

        # 🆕 Tail-Trim: echter Energie-Einbruch im Wort schlägt Zeichen-Schätzung
        tz_start = tail_zone_start_for(w)
        if tz_start is not None:
            target_end = max(w.start + TAIL_TRIM_MIN_WORD_REST_SEC, tz_start)
            buf.append(WhisperWord(word=w.word, start=w.start, end=min(target_end, w.end)))
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        # Fallback: V2-Zweistufen-Heuristik — NUR wenn keine RMS-Daten vorliegen.
        # Mit Audio-Wahrheit gilt: kein Energie-Einbruch = durchgehende Stimme =
        # gedehntes echtes Wort → bleibt ganz (Schnitt mitten in den Laut klingt kaputt).
        duration = w.end - w.start
        expected_max = estimated_max_word_duration(w.word)
        factor = duration / expected_max if expected_max > 0 else 0
        if use_text_fallback and duration > SUSPICIOUS_WORD_DURATION_SEC and factor >= HESITATION_FACTOR_SOFT:
            if factor >= HESITATION_FACTOR_HARD:
                target_end = w.start + max(HESITATION_MIN_TRUNCATE_SEC, expected_max)
            else:
                target_end = w.start + max(
                    HESITATION_MIN_TRUNCATE_SEC, expected_max + HESITATION_SOFT_RESERVE_SEC
                )
            buf.append(WhisperWord(word=w.word, start=w.start, end=min(target_end, w.end)))
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        buf.append(w)

    flush(safe_pre_pad=True, safe_post_pad=True)

    # Clip-Ränder gegen Filler-Zonen clampen (wie V2, defensive Sicherheit)
    # V4.2: Safety pro Zone (VAD 20ms / Whisper+Gemini 100ms)
    out: list[CutClip] = []
    for c in clips:
        start, end = c.start, c.end
        for fs, fe, sf in filler_zones:
            if end > fs - sf and end <= fe + sf:
                end = fs - sf
            if start >= fs - sf and start < fe + sf:
                start = fe + sf
        if end - start >= MIN_CLIP_DURATION:
            out.append(CutClip(start=start, end=end, reason=c.reason))
    return out


# ===== Hauptfunktion =====

def _find_raw_video(project_id: str) -> Optional[Path]:
    raw_dir = PROJECTS_PATH / project_id / "raw"
    if not raw_dir.exists():
        return None
    videos = sorted(
        f for f in raw_dir.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    return videos[0] if videos else None


def plan_cuts_v4(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    """V4: V3-Selection (gecacht) + lokale Audio-Wahrheit (VAD+RMS) + V4-Clip-Builder."""

    # 1) Wörter → Sätze (V2-Helper)
    sentences = group_into_sentences(analysis.whisper_words)
    print(f"  [V4] {len(sentences)} Sätze aus {len(analysis.whisper_words)} Wörtern")

    assign_visual_quality(sentences, analysis.visual_phases)
    assign_position_labels(sentences, analysis.duration_sec)

    if not sentences:
        return CutPlan(project_id=analysis.project_id, clips=[], claude_reasoning="V4: Keine Sätze erkannt")

    # 2) Sentence-Selection mit Cache (deterministisch ab dem 2. Lauf)
    audio_issues = getattr(analysis, "audio_issues", []) or []
    kept_ids, reasoning = _select_sentences_cached(
        sentences, analysis.project_id, platform, analysis.duration_sec, memory, audio_issues
    )
    kept_sentences = [s for s in sentences if s.id in kept_ids]
    print(f"  [V4] {len(kept_sentences)}/{len(sentences)} Sätze behalten")

    # 3) Lokale Audio-Wahrheit (gecacht; bei Fehler → 0 Zonen, Pipeline läuft weiter)
    video_path = _find_raw_video(analysis.project_id)
    if video_path is not None:
        truth = build_audio_truth(
            video_path,
            analysis.whisper_words,
            estimated_max_word_duration,
            cache_path=PROJECTS_PATH / analysis.project_id / "v4_audio_truth.json",
        )
    else:
        print(f"  [V4] WARN: kein Raw-Video gefunden — Audio-Wahrheit übersprungen")
        truth = {"pause_filler_zones": [], "tail_trim_zones": [], "vad_segment_count": 0}

    # Lag echte RMS/VAD-Analyse vor? Steuert ob die Text-Schätzung als Fallback läuft.
    truth_available = truth.get("vad_segment_count", 0) > 0

    # V4.2: Safety pro Quelle — VAD-Zonen sind präzise (20ms), Whisper/Gemini grob (100ms)
    pause_filler_zones = [(z[0], z[1], VAD_ZONE_SAFETY_SEC) for z in truth["pause_filler_zones"]]
    tail_trim_zones = [tuple(z) for z in truth["tail_trim_zones"]]

    # 4) Filler-Zonen aus drei Quellen:
    #    a) Whisper-textlich ("äh"/"ähm" im Transkript)
    whisper_filler_zones = [
        (w.start, w.end, COARSE_ZONE_SAFETY_SEC)
        for w in analysis.whisper_words if _is_filler(w.word)
    ]
    #    b) lokale VAD-Pause-Filler (präzise)
    #    c) V4.2: Gemini-Filler NUR mit confidence=="high" + Plausibilitäts-Cap.
    #       Begründung: VAD ist blind für Filler IN Wörtern / Mini-Pausen <200ms —
    #       in Testreel 4.0 blieben genau 3 solche Ähs drin (46.2s, 51.5s, 88.9s),
    #       alle 3 von Gemini conf=high gemeldet. Der Cap schützt weiterhin gegen
    #       Halluzination (103-Filler-Fall), low/medium-Meldungen bleiben draußen.
    gemini_filler_zones = [
        (ai.start_sec, ai.end_sec, COARSE_ZONE_SAFETY_SEC)
        for ai in audio_issues
        if ai.type == "filler" and ai.confidence == "high"
    ]
    max_plausible_fillers = max(5, int(analysis.duration_sec / 5))
    if len(gemini_filler_zones) > max_plausible_fillers:
        print(f"  [V4] ⚠️ Gemini lieferte {len(gemini_filler_zones)} high-conf-Filler "
              f"(Plausibilitätsgrenze: {max_plausible_fillers}) — ignoriere Gemini-Filler")
        gemini_filler_zones = []

    filler_zones = sorted(whisper_filler_zones + pause_filler_zones + gemini_filler_zones)

    # Outtake-Zonen wie V2/V3: Gemini-VISUAL bleibt aktiv + akustische Brüche
    outtake_zones = [
        (ph.start_sec, ph.end_sec) for ph in analysis.visual_phases
        if ph.visual_quality == "outtake_break"
    ]
    outtake_zones += [
        (ai.start_sec, ai.end_sec) for ai in audio_issues
        if ai.type in ("voice_break", "mispronunciation")
    ]

    print(
        f"  [V4] Zonen: {len(whisper_filler_zones)} Whisper-Filler, "
        f"{len(pause_filler_zones)} Pause-Filler (VAD), {len(gemini_filler_zones)} Gemini-high-conf, "
        f"{len(tail_trim_zones)} Tail-Trims (RMS), {len(outtake_zones)} Outtakes "
        f"(Text-Fallback: {'aus' if truth_available else 'an'})"
    )

    # 5) Clips bauen (deterministisch)
    clips: list[CutClip] = []
    for s in kept_sentences:
        clips.extend(_sentence_to_clips_v4(
            s, filler_zones, outtake_zones, tail_trim_zones,
            use_text_fallback=not truth_available,
        ))

    print(f"  [V4] {len(clips)} finale Clips")

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=f"V4 (lokale Audio-Wahrheit + Cache): {reasoning}",
    )
