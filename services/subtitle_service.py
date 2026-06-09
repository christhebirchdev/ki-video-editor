# services/subtitle_service.py
import assemblyai as aai
import subprocess
from pathlib import Path
from config import settings

aai.settings.api_key = settings.assemblyai_api_key


def transcribe_and_get_srt(video_path: Path) -> str:
    """Transkribiert das Video mit AssemblyAI und gibt SRT-Inhalt zurück."""
    config = aai.TranscriptionConfig(language_code="de")
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(str(video_path))

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transkription fehlgeschlagen: {transcript.error}")

    return transcript.export_subtitles_srt(chars_per_caption=40)


def save_srt(srt_content: str, output_path: Path) -> Path:
    output_path.write_text(srt_content, encoding="utf-8")
    return output_path


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path,
                   font_size: int = 24) -> Path:
    """Brennt Untertitel in das Video ein via FFmpeg."""
    subtitle_filter = (
        f"subtitles={srt_path}:force_style="
        f"'FontSize={font_size},FontName=Arial,PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,Outline=1,Alignment=2'"
    )
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", subtitle_filter, "-c:a", "copy", str(output_path)
    ], check=True, capture_output=True)
    return output_path


def add_subtitles_to_video(video_path: Path, project_path: Path) -> dict:
    """Vollständiger Untertitel-Workflow: Transkribieren → SRT speichern → Einbrennen."""
    srt_path = project_path / "subtitles.srt"
    output_path = project_path / "output" / "with_subtitles.mp4"

    srt_content = transcribe_and_get_srt(video_path)
    save_srt(srt_content, srt_path)
    burn_subtitles(video_path, srt_path, output_path)

    return {"srt_path": str(srt_path), "output_path": str(output_path)}
