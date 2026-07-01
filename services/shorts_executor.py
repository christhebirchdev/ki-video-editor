# services/shorts_executor.py
"""
MultiCut-Batch-Executor: schneidet alle gewählten Shorts sequentiell aus dem Langvideo.

Pro Short:
1. FFmpeg-Extraktion mit 9:16-Center-Crop (Re-Encode — Stream-Copy wäre an
   Nicht-Keyframes unsauber) → output/shorts/<short_id>.mp4
2. Optional (post_processing="v52"): V5.2-Feinschnitt auf dem Segment.
   Trick: Pseudo-Projektverzeichnis shorts_work/<short_id>/ mit raw/segment.mp4 —
   plan_cuts_v52 findet Video + Caches über den composite project_id von selbst.
   Whisper-Wörter werden aus der Podcast-Analyse zeitlich gesliced (Offset-Korrektur),
   KEINE Neu-Transkription.

Status-Updates landen atomar in shorts.json (tmp + os.replace) — das Frontend
pollt diese Datei und zeigt Fortschritt pro Kachel.
Fehler bei einem Short stoppen den Batch NICHT (status="error", weiter).
"""
import os
import json
import shutil
import subprocess
import threading
from pathlib import Path

from config import PROJECTS_PATH
from models.analysis import VideoAnalysis, WhisperWord
from models.shorts import ShortsPlan, ShortCandidate

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}

# Wörter, die minimal über den Segment-Rand ragen, noch mitnehmen (Padding-Toleranz)
SLICE_TOLERANCE_SEC = 0.10

# Ein Batch pro Projekt — In-Memory-Lock (nach Server-Neustart automatisch frei)
_running_projects: set[str] = set()
_lock = threading.Lock()


def shorts_plan_path(project_path: Path) -> Path:
    return project_path / "shorts.json"


def load_shorts_plan(project_path: Path) -> ShortsPlan | None:
    p = shorts_plan_path(project_path)
    if not p.exists():
        return None
    return ShortsPlan(**json.loads(p.read_text()))


def save_shorts_plan(project_path: Path, plan: ShortsPlan) -> None:
    """Atomar schreiben — Frontend-Polling darf nie halbe JSONs sehen."""
    target = shorts_plan_path(project_path)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2))
    os.replace(tmp, target)


def is_batch_running(project_id: str) -> bool:
    with _lock:
        return project_id in _running_projects


def slice_words_for_segment(
    words: list[WhisperWord], start_sec: float, end_sec: float
) -> list[WhisperWord]:
    """Whisper-Wörter des Langvideos → segment-lokale Wörter mit Offset 0.

    Pure Funktion — direkt testbar.
    """
    sliced: list[WhisperWord] = []
    for w in words:
        if w.start >= start_sec - SLICE_TOLERANCE_SEC and w.end <= end_sec + SLICE_TOLERANCE_SEC:
            sliced.append(WhisperWord(
                start=max(0.0, round(w.start - start_sec, 3)),
                end=max(0.0, round(w.end - start_sec, 3)),
                word=w.word,
            ))
    return sliced


def _find_source_video(project_path: Path) -> Path:
    raw = project_path / "raw"
    videos = sorted(
        f for f in raw.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        raise RuntimeError(f"Kein Quellvideo in {raw}")
    return videos[0]


def extract_segment(
    input_path: Path, out_path: Path, start_sec: float, end_sec: float, crop_9_16: bool = True
) -> None:
    """Segment ausschneiden + optional 9:16-Center-Crop. Re-Encode für saubere Kanten."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = end_sec - start_sec
    cmd = ["ffmpeg", "-y", "-nostdin", "-ss", f"{start_sec:.3f}", "-t", f"{dur:.3f}",
           "-i", str(input_path)]
    if crop_9_16:
        # min(iw, ih*9/16): croppt nur wenn das Video breiter als 9:16 ist
        cmd += ["-vf", "crop='min(iw,ih*9/16)':ih"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(f"FFmpeg-Extraktion fehlgeschlagen (rc={result.returncode}):\n{tail}")


def _refine_with_v52(
    project_id: str,
    project_path: Path,
    short: ShortCandidate,
    segment_path: Path,
    analysis: VideoAnalysis,
) -> Path:
    """V5.2-Feinschnitt auf dem extrahierten Segment. Gibt Pfad des Ergebnisses zurück."""
    # Lazy Imports — heavy deps (numpy, faster-whisper) nur wenn wirklich gebraucht
    from services.cut_engine_v52 import plan_cuts_v52
    from services.ffmpeg_service import execute_cut_plan
    from services.memory_service import load_memory

    work_id = f"{project_id}/shorts_work/{short.id}"
    work_dir = PROJECTS_PATH / work_id
    work_raw = work_dir / "raw"
    work_out = work_dir / "output"
    work_raw.mkdir(parents=True, exist_ok=True)
    work_out.mkdir(parents=True, exist_ok=True)

    # Segment als "raw" des Pseudo-Projekts (move, nicht copy — spart Platz)
    seg_in_work = work_raw / "segment.mp4"
    shutil.move(str(segment_path), seg_in_work)

    seg_words = slice_words_for_segment(analysis.whisper_words, short.start_sec, short.end_sec)
    seg_analysis = VideoAnalysis(
        project_id=work_id,
        duration_sec=round(short.end_sec - short.start_sec, 3),
        whisper_words=seg_words,
        visual_phases=[],
        audio_issues=[],
        full_transcript=" ".join(w.word for w in seg_words),
    )

    cut_plan = plan_cuts_v52(seg_analysis, load_memory(), platform="short")
    if not cut_plan.clips:
        print(f"  [MULTICUT] WARN: V5.2 lieferte 0 Clips für {short.id} — Segment bleibt ungeschnitten")
        return seg_in_work

    return execute_cut_plan(cut_plan, work_raw, work_out, audio_fade_ms=20)


def _update_short(project_path: Path, short_id: str, **fields) -> ShortsPlan:
    """Shorts-Plan laden, ein Short patchen, atomar speichern."""
    plan = load_shorts_plan(project_path)
    for s in plan.shorts:
        if s.id == short_id:
            for k, v in fields.items():
                setattr(s, k, v)
            break
    save_shorts_plan(project_path, plan)
    return plan


def run_batch(project_id: str, post_processing: str = "segment") -> None:
    """Batch-Lauf: alle gewählten, noch nicht fertigen Shorts sequentiell schneiden.

    Läuft als FastAPI-BackgroundTask. Wiederholbar: 'done' wird übersprungen,
    'error' wird zurückgesetzt und neu versucht.
    """
    with _lock:
        if project_id in _running_projects:
            print(f"  [MULTICUT] Batch für {project_id} läuft bereits — Abbruch")
            return
        _running_projects.add(project_id)

    project_path = PROJECTS_PATH / project_id
    try:
        plan = load_shorts_plan(project_path)
        if plan is None:
            print(f"  [MULTICUT] Kein shorts.json in {project_id} — nichts zu tun")
            return

        analysis = VideoAnalysis(**json.loads((project_path / "analysis.json").read_text()))
        source = _find_source_video(project_path)
        shorts_out = project_path / "output" / "shorts"
        shorts_out.mkdir(parents=True, exist_ok=True)

        plan.batch_status = "running"
        plan.post_processing = post_processing if post_processing in ("segment", "v52") else "segment"
        # Fehlversuche zurücksetzen — neuer Lauf, neue Chance
        for s in plan.shorts:
            if s.selected and s.status == "error":
                s.status, s.error = "pending", ""
        save_shorts_plan(project_path, plan)

        todo = [s for s in plan.shorts if s.selected and s.status != "done"]
        print(f"  [MULTICUT] Batch-Start: {len(todo)} Shorts, Modus '{plan.post_processing}'")

        for short in todo:
            _update_short(project_path, short.id, status="cutting")
            final_path = shorts_out / f"{short.id}.mp4"
            try:
                if plan.post_processing == "v52":
                    tmp_segment = shorts_out / f"{short.id}_segment_tmp.mp4"
                    extract_segment(source, tmp_segment, short.start_sec, short.end_sec)
                    result = _refine_with_v52(project_id, project_path, short, tmp_segment, analysis)
                    shutil.move(str(result), final_path)
                else:
                    extract_segment(source, final_path, short.start_sec, short.end_sec)

                _update_short(project_path, short.id,
                              status="done", output_file=f"shorts/{short.id}.mp4", error="")
                print(f"  [MULTICUT] ✓ {short.id} fertig ({short.duration_sec:.1f}s)")
            except Exception as e:
                print(f"  [MULTICUT] ✗ {short.id} fehlgeschlagen: {e}")
                _update_short(project_path, short.id, status="error", error=str(e)[:500])

        plan = load_shorts_plan(project_path)
        plan.batch_status = "done"
        save_shorts_plan(project_path, plan)

        # Projekt-Status für die Projektliste (Single-Pipeline-Statuses bleiben unberührt)
        meta_file = project_path / "meta.json"
        meta = json.loads(meta_file.read_text())
        meta["status"] = "shorts_done"
        meta_file.write_text(json.dumps(meta, indent=2, default=str))

        n_done = sum(1 for s in plan.shorts if s.status == "done")
        print(f"  [MULTICUT] Batch fertig: {n_done}/{len(plan.shorts)} Shorts erfolgreich")
    finally:
        with _lock:
            _running_projects.discard(project_id)
