# services/whisper_service.py
"""
Wort-genaue Transkription via faster-whisper.

faster-whisper ist 4-5× schneller als openai-whisper bei gleicher Qualität,
basiert auf CTranslate2, hat keine torch-Hard-Dependency und liefert nativ
word-level timestamps die wir hier brauchen.

Modell-Wahl:
- "small"  → ~470 MB, sehr schnell, gute deutsche Qualität (Default für MVP)
- "medium" → ~1.5 GB, langsamer, deutlich präziser bei Akzenten/leiser Stimme
- "large-v3" → ~3 GB, beste Qualität, ~5× langsamer
"""
import time
from pathlib import Path
from typing import Optional
from models.analysis import WhisperWord

# Mehrere Modelle parallel gecached (für A/B-Vergleich verschiedener Engines).
_models: dict = {}
DEFAULT_WHISPER_MODEL = "small"  # Default für v5.2 und ältere Engines


def _get_model(model_name: str):
    """Lädt ein Modell beim ersten Aufruf (lazy) und cached es pro Name.
    model_name kann eine Größe ("small"/"large-v3") ODER eine HF-Repo-ID eines
    CTranslate2-Modells sein (z.B. "nyrahealth/faster_CrisperWhisper")."""
    m = _models.get(model_name)
    if m is None:
        from faster_whisper import WhisperModel
        print(f"  [WHISPER] Lade Modell '{model_name}' (erstes Mal: Download + 1-2 Min, dann gecached)…")
        # CPU-Modus, int8 quantisiert → schnell + RAM-arm. GPU würde compute_type="float16" nutzen.
        m = WhisperModel(model_name, device="cpu", compute_type="int8")
        _models[model_name] = m
        print(f"  [WHISPER] Modell '{model_name}' bereit")
    return m


def transcribe_with_word_timestamps(
    video_path: Path, language: str = "de", model_name: Optional[str] = None,
) -> tuple[list[WhisperWord], str]:
    """
    Transkribiert ein Video mit Wort-Timestamps.

    Returns:
        (words, full_transcript_text)
        - words: Liste von WhisperWord (start/end/word in Sekunden)
        - full_transcript_text: kompletter Text als ein String

    faster-whisper akzeptiert Audio + Video direkt (nutzt ffmpeg intern).
    """
    model = _get_model(model_name or DEFAULT_WHISPER_MODEL)
    print(f"  [WHISPER] Transkribiere {video_path.name} (Sprache: {language})…")
    t0 = time.time()
    segments_iter, info = model.transcribe(
        str(video_path),
        language=language,
        word_timestamps=True,         # ← essentiell, sonst nur Segment-Level
        vad_filter=True,              # Voice Activity Detection — überspringt lange Stille
        beam_size=5,                  # höhere Genauigkeit
    )

    words: list[WhisperWord] = []
    full_text_parts: list[str] = []
    for seg in segments_iter:
        if seg.words:
            for w in seg.words:
                # word.word kann führende Leerzeichen haben — strippen
                cleaned = w.word.strip()
                if not cleaned:
                    continue
                words.append(WhisperWord(
                    start=float(w.start),
                    end=float(w.end),
                    word=cleaned,
                ))
        full_text_parts.append(seg.text.strip())

    full_transcript = " ".join(full_text_parts).strip()
    elapsed = time.time() - t0
    print(f"  [WHISPER] ✓ {len(words)} Wörter transkribiert in {elapsed:.1f}s "
          f"(Video-Dauer: {info.duration:.1f}s, Sprache: {info.language})")
    return words, full_transcript
