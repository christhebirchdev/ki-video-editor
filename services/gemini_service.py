# services/gemini_service.py
import json
import re
import time
from pathlib import Path
import google.generativeai as genai
from config import settings
from models.analysis import TakeAnalysis, VideoAnalysis

genai.configure(api_key=settings.gemini_api_key)

ANALYSIS_PROMPT = """
Analysiere dieses Video als Videoeditor. Identifiziere alle Takes (Aufnahmen desselben Inhalts).

Für jeden Take / jedes Segment gib zurück:
- take_id: fortlaufend ("take_1", "take_2", ...)
- start_seconds: Startzeit in Sekunden (float)
- end_seconds: Endzeit in Sekunden (float)
- transcript: Gesprochener Text in diesem Segment
- energy: Energielevel des Sprechers ("hoch", "mittel", "niedrig")
- eye_contact: Blickkontakt mit der Kamera ("gut", "unterbrochen", "schlecht")
- voice_quality: Stimmqualität ("klar", "leise", "heiser", "zittrig")
- issues: Liste von Problemen, z.B. ["Versprecher bei Sekunde 12", "Schaut auf Bildschirm am Ende"]
- gemini_description: Deine neutrale Beschreibung was in diesem Segment passiert

Außerdem:
- full_transcript: Vollständiges Transkript des gesamten Videos
- total_duration_seconds: Gesamtlänge in Sekunden
- repeated_content: Liste von Textstellen die mehrfach gesagt wurden, mit den zugehörigen take_ids

Antworte NUR mit validem JSON ohne Markdown-Blöcke:
{
  "takes": [...],
  "full_transcript": "...",
  "total_duration_seconds": 0.0,
  "repeated_content": [
    {"text": "der wiederholte Satz", "take_ids": ["take_1", "take_3"]}
  ]
}
"""


def _upload_video_to_gemini(video_path: Path):
    """Lädt Video zur Gemini Files API hoch und wartet bis verarbeitet."""
    print(f"  Uploading {video_path.name} to Gemini...")
    video_file = genai.upload_file(path=str(video_path), display_name=video_path.name)
    while video_file.state.name == "PROCESSING":
        time.sleep(2)
        video_file = genai.get_file(video_file.name)
    if video_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini File Upload fehlgeschlagen: {video_file.state}")
    print(f"  Upload fertig: {video_file.uri}")
    return video_file


def analyse_video(project_id: str, video_paths: list[Path]) -> VideoAnalysis:
    """Analysiert alle Video-Dateien eines Projekts mit Gemini."""
    model = genai.GenerativeModel("gemini-2.5-flash")
    all_takes = []
    full_transcripts = []
    total_duration = 0.0
    repeated_content = []

    for i, video_path in enumerate(video_paths):
        video_file = _upload_video_to_gemini(video_path)
        response = model.generate_content([video_file, ANALYSIS_PROMPT])

        raw = response.text.strip()
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            raw = json_match.group()

        data = json.loads(raw)

        for take_data in data.get("takes", []):
            take_data["take_id"] = f"file{i+1}_{take_data['take_id']}"
            take_data["file"] = video_path.name
            all_takes.append(TakeAnalysis(**take_data))

        full_transcripts.append(data.get("full_transcript", ""))
        total_duration += data.get("total_duration_seconds", 0.0)
        repeated_content.extend(data.get("repeated_content", []))

    return VideoAnalysis(
        project_id=project_id,
        takes=all_takes,
        full_transcript="\n\n---\n\n".join(full_transcripts),
        total_duration_seconds=total_duration,
        repeated_content=repeated_content
    )
