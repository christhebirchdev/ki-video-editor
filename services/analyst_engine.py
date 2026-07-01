# services/analyst_engine.py
"""
Orchestrierung des AI Video Analyst (Whole-Video, ein Gemini-Lauf).

Ablauf:
1. Whisper-Transkript (lokal) + Sprachstatistik (Code)
2. Gemini beschreibt das GANZE Video in einem Lauf (eigene Timestamps, nur Beschreibung)
3. Audio-Messwerte (ffmpeg ebur128, deterministisch)
4. Bewertung via Claude (übersprungen, wenn skip_eval=true)

Status-Updates landen in status.json (Frontend pollt alle 2s).
"""
import json
import threading
import traceback
from pathlib import Path

from config import settings
from models.analyst import AnalystResult, SceneDescription
from services import analyst_quality, analyst_vlm
from services.analyst_speech import compute_speech_stats

ANALYST_PATH = Path("analyst_runs")
ANALYST_PATH.mkdir(exist_ok=True)

# Begrenzt, wie viele Analysen GLEICHZEITIG die schwere Pipeline (Whisper/Gemini/ffmpeg)
# durchlaufen. Default 1 → Jobs warten in der Schlange und laufen seriell, damit ein
# geteilter VPS (inkl. parallel laufendem n8n) nicht überlastet wird.
# ponytail: einfacher Semaphore statt echter Job-Queue/Redis — reicht für ~3 Nutzer.
# Mehr Durchsatz nötig → ANALYST_MAX_CONCURRENT hochsetzen (nur bei genug CPU/RAM).
_SLOTS = threading.BoundedSemaphore(max(1, settings.analyst_max_concurrent))


def write_status(run_dir: Path, phase: str, detail: str = "", done: bool = False, error: str = ""):
    (run_dir / "status.json").write_text(json.dumps(
        {"phase": phase, "detail": detail, "done": done, "error": error},
        ensure_ascii=False, indent=2,
    ))


def _find_video(run_dir: Path) -> Path:
    files = [f for f in (run_dir / "raw").iterdir() if f.is_file()]
    if not files:
        raise RuntimeError("Kein Video im Lauf gefunden")
    return files[0]


def _speech_in_window(words, start: float, end: float) -> str:
    """Gesprochener Wortlaut im Segmentfenster (deterministisch aus Whisper, keine Bild-OCR)."""
    return " ".join(w.word.strip() for w in words if start <= w.start < end).strip()


def run_analysis(run_id: str) -> None:
    """BackgroundTask-Einstieg. Schreibt analysis.json + status.json.

    Holt sich vorher einen Slot aus dem Semaphore. Ist keiner frei, zeigt der Status
    'In Warteschlange' und der Job wartet blockierend, bis ein laufender fertig ist.
    """
    run_dir = ANALYST_PATH / run_id
    if not _SLOTS.acquire(blocking=False):
        write_status(run_dir, "queued", "In Warteschlange – wartet auf einen freien Platz…")
        _SLOTS.acquire()  # blockiert, bis ein laufender Job fertig ist
    try:
        _run(run_id, run_dir)
    except Exception as e:
        traceback.print_exc()
        write_status(run_dir, phase="error", error=str(e))
    finally:
        _SLOTS.release()


def _run(run_id: str, run_dir: Path) -> None:
    from services.whisper_service import transcribe_with_word_timestamps

    video = _find_video(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())

    write_status(run_dir, "transcribe", "Transkription läuft (Whisper, lokal)…")
    words, transcript = transcribe_with_word_timestamps(video, model_name=settings.whisper_model)
    speech_stats = compute_speech_stats(words)

    write_status(run_dir, "describe", "Frames werden beschrieben (Gemini: Bild + Audio + Blick)…")
    segments, duration, audio_overview, gaze_overview = analyst_vlm.describe_video(video, run_dir / "frames")
    descriptions = [
        SceneDescription(
            index=i,
            gesprochener_text=_speech_in_window(words, seg["start"], seg["end"]),
            **seg,
        )
        for i, seg in enumerate(segments)
    ]

    write_status(run_dir, "quality", "Audio-Messwerte werden erhoben…")
    try:
        quality = analyst_quality.measure(video, [])  # nur Audio (LUFS); keine Frames mehr
    except Exception as e:
        print(f"  [ANALYST] Qualitäts-Messung fehlgeschlagen: {e}")
        quality = None

    result = AnalystResult(
        id=run_id,
        filename=meta.get("filename", video.name),
        duration_sec=duration,
        scene_count=len(descriptions),
        scenes=descriptions,
        audio_overview=audio_overview,
        gaze_overview=gaze_overview,
        transcript=transcript,
        speech_stats=speech_stats,
        quality_metrics=quality,
    )

    if meta.get("skip_eval"):
        print("  [ANALYST] skip_eval=true → Claude-Bewertung übersprungen")
    else:
        write_status(run_dir, "evaluate", "Bewertung läuft (Claude)…")
        try:
            from services import analyst_eval
            result.evaluation = analyst_eval.evaluate(result)
        except Exception as e:
            print(f"  [ANALYST] Claude-Bewertung fehlgeschlagen: {e}")

    (run_dir / "analysis.json").write_text(result.model_dump_json(indent=2))
    write_status(run_dir, "done", "Analyse abgeschlossen", done=True)
