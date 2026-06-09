# services/ffmpeg_service.py
import subprocess
import json
from pathlib import Path
from models.analysis import CutPlan


def get_video_duration(video_path: Path) -> float:
    """Gibt die Länge des Videos in Sekunden zurück."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def cut_segment(input_path: Path, output_path: Path, start: float, end: float) -> Path:
    """Schneidet ein Segment aus einer Video-Datei."""
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(input_path),
        "-t", str(end - start),
        "-c", "copy",
        str(output_path)
    ], check=True, capture_output=True)
    return output_path


def concatenate_videos(segment_paths: list[Path], output_path: Path) -> Path:
    """Fügt mehrere Video-Segmente zusammen."""
    list_file = output_path.parent / "concat_list.txt"
    list_file.write_text("\n".join([f"file '{p.resolve()}'" for p in segment_paths]))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(output_path)
    ], check=True, capture_output=True)
    list_file.unlink()
    return output_path


def execute_cut_plan(cut_plan: CutPlan, raw_dir: Path, output_dir: Path) -> Path:
    """Führt den Schnittplan aus: schneidet alle behaltenen Takes und fügt sie zusammen."""
    kept = sorted([d for d in cut_plan.decisions if d.keep], key=lambda d: d.order)
    if not kept:
        raise ValueError("Kein Take zum Behalten im Schnittplan.")

    video_files = sorted(
        list(raw_dir.glob("*.mp4")) + list(raw_dir.glob("*.mov")) + list(raw_dir.glob("*.MP4"))
    )
    segments = []
    for i, decision in enumerate(kept):
        # take_id Format: "fileN_take_X" → file-Index aus N ableiten
        file_index = int(decision.take_id.split("_")[0].replace("file", "")) - 1
        input_path = video_files[file_index]
        segment_out = output_dir / f"segment_{i:03d}.mp4"
        cut_segment(input_path, segment_out, decision.in_point, decision.out_point)
        segments.append(segment_out)

    final_out = output_dir / "rough_cut.mp4"
    if len(segments) == 1:
        segments[0].rename(final_out)
    else:
        concatenate_videos(segments, final_out)
        for s in segments:
            if s.exists():
                s.unlink()
    return final_out
