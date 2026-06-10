# services/ffmpeg_service.py
"""
FFmpeg-Schnitt via filter_complex (ein einziger Pass).

Bekommt CutClips (start/end in Sekunden) und rendert den finalen Cut
mit trim+atrim+concat + libx264 + faststart in einem ffmpeg-Call.

stdin=DEVNULL + -nostdin verhindern macOS TTY-Suspend bei nohup-uvicorn.
"""
import subprocess
import json
from pathlib import Path
from models.analysis import CutPlan

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def get_video_duration(video_path: Path) -> float:
    """Gibt die Länge des Videos in Sekunden zurück."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _build_filter_complex(clips: list[tuple[float, float]]) -> tuple[str, str, str]:
    """trim+atrim pro Clip, dann concat. Sekunden mit 3 Nachkommastellen."""
    parts = []
    concat_inputs = []
    for i, (start_s, end_s) in enumerate(clips):
        parts.append(f"[0:v]trim=start={start_s:.3f}:end={end_s:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={start_s:.3f}:end={end_s:.3f},asetpts=PTS-STARTPTS[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")
    concat_str = "".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=1[outv][outa]"
    filter_expr = ";".join(parts) + ";" + concat_str
    return filter_expr, "[outv]", "[outa]"


def execute_cut_plan(cut_plan: CutPlan, raw_dir: Path, output_dir: Path) -> Path:
    """Führt den Schnittplan aus: alle Clips in einem ffmpeg-Call schneiden + zusammenfügen."""
    clips = [(c.start, c.end) for c in cut_plan.clips if c.end > c.start]
    if not clips:
        raise ValueError("Kein Clip zum Schneiden im Cut-Plan.")

    video_files = sorted(
        f for f in raw_dir.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not video_files:
        raise ValueError(f"Keine Video-Datei in {raw_dir} gefunden.")
    input_path = video_files[0]

    filter_expr, v_out, a_out = _build_filter_complex(clips)

    final_out = output_dir / "rough_cut.mp4"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(input_path),
        "-filter_complex", filter_expr,
        "-map", v_out,
        "-map", a_out,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(final_out),
    ]

    print(f"  [FFMPEG] Cut: {len(clips)} Clips → {final_out.name}")
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        stderr_tail = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"FFmpeg-Cut fehlgeschlagen (rc={result.returncode}):\n{stderr_tail}")

    return final_out
