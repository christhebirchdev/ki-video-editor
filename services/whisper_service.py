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

_model = None
WHISPER_MODEL_SIZE = "small"


def _get_model():
    """Lädt das Modell beim ersten Aufruf (lazy)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        print(f"  [WHISPER] Lade Modell '{WHISPER_MODEL_SIZE}' (erstes Mal kann 1-2 Min dauern, dann gecached)…")
        # CPU-Modus, int8 quantisiert → schnell + RAM-arm. GPU würde compute_type="float16" nutzen.
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        print(f"  [WHISPER] Modell bereit")
    return _model


def transcribe_with_word_timestamps(video_path: Path, language: str = "de") -> tuple[list[WhisperWord], str]:
    """
    Transkribiert ein Video mit Wort-Timestamps.

    Returns:
        (words, full_transcript_text)
        - words: Liste von WhisperWord (start/end/word in Sekunden)
        - full_transcript_text: kompletter Text als ein String

    faster-whisper akzeptiert Audio + Video direkt (nutzt ffmpeg intern).
    """
    model = _get_model()
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
