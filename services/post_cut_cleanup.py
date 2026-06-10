# services/post_cut_cleanup.py
"""
Auto-Post-Cut-Cleanup: Whisper-Selbstkontrolle.

Nachdem der erste Cut gerendert ist:
1. Whisper läuft erneut auf dem rough_cut.mp4
2. Findet Filler-Wörter die durchgerutscht sind (z. B. weil Whisper sie im
   Original-Pass anders transkribiert hatte oder das Sub-Clip-System sie übersehen hat)
3. Mappt Filler-Zeit im Cut → Original-Zeit
4. Splittet die Clips im cut_plan an diesen Stellen
5. Re-render

Eine Iteration reicht in 99% der Fälle. Wir machen max 2 Durchgänge.
"""
from pathlib import Path
from services.whisper_service import transcribe_with_word_timestamps
from services.ffmpeg_service import execute_cut_plan
from models.analysis import CutPlan, CutClip, WhisperWord
from typing import Optional

# Gleiche Filler-Patterns wie in cut_engine_v2
FILLER_WORD_PATTERNS = {
    "äh", "ähm", "ähhm", "ähhh", "äääh",
    "uh", "uhm", "um",
    "öh", "öhm", "ehm",
    "mhm", "hm", "hmm",
}

POST_CLEANUP_SAFETY_MS = 30          # +30ms Sicherheits-Puffer beim Cut
MAX_CLEANUP_ITERATIONS = 2           # max 2 Durchläufe


def _is_filler_word(word_str: str) -> bool:
    cleaned = word_str.lower().strip(".,!?;:'\"()[] ")
    return cleaned in FILLER_WORD_PATTERNS


def _find_fillers_in_cut(cut_video_path: Path) -> list[tuple[float, float, str]]:
    """Whisper-Re-Run findet alle Filler-Wörter im fertigen Cut."""
    words, _ = transcribe_with_word_timestamps(cut_video_path, language="de")
    fillers = []
    safety = POST_CLEANUP_SAFETY_MS / 1000.0
    for w in words:
        if _is_filler_word(w.word):
            # Sicherheits-Puffer hinzufügen damit auch leise Reste rausfallen
            fillers.append((max(0, w.start - safety), w.end + safety, w.word))
    return fillers


def _map_cut_time_to_original(cut_time_sec: float, clips: list[CutClip]) -> Optional[tuple[int, float]]:
    """Konvertiert Cut-Zeit → (clip_index, original_time_sec)."""
    cumulative = 0.0
    for i, c in enumerate(clips):
        dur = c.end - c.start
        if cumulative + dur >= cut_time_sec:
            offset = cut_time_sec - cumulative
            return (i, c.start + offset)
        cumulative += dur
    return None


def _remove_fillers_from_clips(
    clips: list[CutClip], cut_fillers: list[tuple[float, float, str]]
) -> tuple[list[CutClip], int]:
    """Splittet die Clips an Filler-Positionen.

    Returns: (neue_clip_liste, anzahl_entfernter_filler)
    """
    if not cut_fillers:
        return clips, 0

    # Konvertiere Cut-Zeit-Filler → Original-Zeit-Filler pro Clip
    per_clip_fillers: dict[int, list[tuple[float, float, str]]] = {}
    skipped = 0
    for f_start, f_end, word in cut_fillers:
        s_map = _map_cut_time_to_original(f_start, clips)
        e_map = _map_cut_time_to_original(f_end, clips)
        if s_map is None or e_map is None:
            skipped += 1
            continue
        if s_map[0] != e_map[0]:
            # Filler-Zone spannt über Clip-Grenze → ignorieren (sehr selten)
            skipped += 1
            continue
        ci = s_map[0]
        per_clip_fillers.setdefault(ci, []).append((s_map[1], e_map[1], word))

    if not per_clip_fillers:
        return clips, 0

    # Splitte Clips
    new_clips: list[CutClip] = []
    removed = 0
    for i, c in enumerate(clips):
        fillers_here = sorted(per_clip_fillers.get(i, []))
        if not fillers_here:
            new_clips.append(c)
            continue
        cursor = c.start
        for fs, fe, word in fillers_here:
            # Clamp Filler-Zone auf Clip-Grenzen
            fs = max(fs, c.start)
            fe = min(fe, c.end)
            if fs <= cursor:
                # Filler beginnt vor unserem aktuellen Position — Cursor weiter
                cursor = max(cursor, fe)
                removed += 1
                continue
            # Pre-Filler-Segment
            if fs > cursor + 0.05:
                new_clips.append(CutClip(start=cursor, end=fs, reason=c.reason + f" [vor '{word}']"))
            cursor = fe
            removed += 1
        # Post-Filler-Rest
        if cursor < c.end - 0.05:
            new_clips.append(CutClip(start=cursor, end=c.end, reason=c.reason + " [post-cleanup]"))

    return new_clips, removed


def post_cut_cleanup(
    rough_cut_path: Path,
    cut_plan: CutPlan,
    raw_dir: Path,
    output_dir: Path,
) -> tuple[CutPlan, Path, dict]:
    """Iterative Whisper-Selbstkontrolle. Returns (neuer_plan, neuer_output, stats).

    stats = {"iterations": int, "total_fillers_removed": int, "fillers_per_iteration": [int, ...]}
    """
    stats = {"iterations": 0, "total_fillers_removed": 0, "fillers_per_iteration": []}
    current_plan = cut_plan
    current_output = rough_cut_path

    for iteration in range(MAX_CLEANUP_ITERATIONS):
        print(f"  [POST-CLEANUP] Iteration {iteration + 1}: Whisper-Re-Run auf {current_output.name}...")
        cut_fillers = _find_fillers_in_cut(current_output)
        if not cut_fillers:
            print(f"  [POST-CLEANUP] ✓ Keine Filler im Cut gefunden — fertig")
            break

        print(f"  [POST-CLEANUP] {len(cut_fillers)} Filler im Cut gefunden: {[f[2] for f in cut_fillers]}")
        new_clips, removed = _remove_fillers_from_clips(current_plan.clips, cut_fillers)
        if removed == 0:
            print(f"  [POST-CLEANUP] Keine Filler in Original-Zeit zuordenbar — Abbruch")
            break

        # Re-render
        current_plan = CutPlan(
            project_id=current_plan.project_id,
            clips=new_clips,
            claude_reasoning=current_plan.claude_reasoning + f"\n[Cleanup-{iteration+1}: {removed} Filler entfernt]",
            confirmed_by_user=current_plan.confirmed_by_user,
        )
        print(f"  [POST-CLEANUP] Re-render mit {len(new_clips)} Clips ({removed} Filler entfernt)…")
        current_output = execute_cut_plan(current_plan, raw_dir, output_dir)

        stats["iterations"] += 1
        stats["total_fillers_removed"] += removed
        stats["fillers_per_iteration"].append(removed)

    return current_plan, current_output, stats
