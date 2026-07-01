# services/verbatim_whisper.py
"""
V5-only: Verbatim-Whisper-Pass — der Gemini-Audio-Ersatz für In-Wort-Filler.

Erkenntnis aus V4.2 (Testreel 4.0): 3 von 5 hörbaren Ähs lagen IN Wörtern bzw.
Mini-Pausen <200ms — dort sind VAD (keine Pause) und der Standard-Whisper-Pass
(glättet Filler weg) beide blind. Nur Gemini fand sie.

Lokaler Ersatz: ein zweiter Whisper-Lauf, der auf Filler-Treue getrimmt ist:
- initial_prompt voller Füllwörter → Whisper traut sich, "äh"/"ähm" zu schreiben
- vad_filter=False → auch Laute in Pausen werden transkribiert
- Ergebnis: textliche Filler MIT Wort-Timestamps → Filler-Zonen

ANNAHME (am eigenen Material zu messen): Der Prompt-Trick erhöht die
Filler-Erkennung deutlich, garantiert sie aber nicht. Deshalb ist dieser Pass
EINE von mehreren Quellen (zusätzlich zu VAD-Pause-Fillern + RMS-Tail-Trims),
nicht die einzige. Liefert er 0 Filler, läuft die Pipeline normal weiter.

Laufzeit: ein zusätzlicher faster-whisper-small-Lauf (lokal), Ergebnis wird
pro Projekt gecacht (v5_verbatim_fillers.json) — Re-Plans kosten 0s.
"""
import json
import time
from pathlib import Path
from typing import Optional

# Gleiche Patterns wie cut_engine_v2 / post_cut_cleanup
FILLER_WORD_PATTERNS = {
    "äh", "ähm", "ähhm", "ähhh", "äääh",
    "uh", "uhm", "um",
    "öh", "öhm", "ehm",
    "mhm", "hm", "hmm",
}

# Filler-lastiger Kontext: macht Whisper "mutig", Füllwörter wörtlich zu transkribieren
VERBATIM_INITIAL_PROMPT = "Ähm, also äh, ich ähm, genau äh, ähm also."

VERBATIM_VERSION = 1   # bei Parameter-Änderungen hochzählen → Cache invalidiert


def _is_filler(word_str: str) -> bool:
    cleaned = word_str.lower().strip(".,!?;:'\"()[] ")
    return cleaned in FILLER_WORD_PATTERNS


def transcribe_verbatim_fillers(video_path: Path, language: str = "de") -> list[tuple[float, float]]:
    """Verbatim-Pass: liefert Filler-Zonen (start, end) in Sekunden."""
    from services.whisper_service import _get_model   # lazy: Modell wird geteilt/gecacht
    model = _get_model()
    print(f"  [VERBATIM] Filler-treuer Whisper-Pass auf {video_path.name} …")
    t0 = time.time()
    segments_iter, _info = model.transcribe(
        str(video_path),
        language=language,
        word_timestamps=True,
        vad_filter=False,              # Pausen NICHT überspringen — da sitzen die Filler
        beam_size=5,
        initial_prompt=VERBATIM_INITIAL_PROMPT,
    )
    zones: list[tuple[float, float]] = []
    total_words = 0
    for seg in segments_iter:
        if not seg.words:
            continue
        for w in seg.words:
            total_words += 1
            if _is_filler(w.word.strip()):
                zones.append((float(w.start), float(w.end)))
    print(f"  [VERBATIM] ✓ {len(zones)} Filler in {total_words} Wörtern ({time.time() - t0:.1f}s)")
    return zones


def get_verbatim_filler_zones(
    video_path: Path,
    cache_path: Optional[Path] = None,
) -> list[tuple[float, float]]:
    """Mit Datei-Cache (Größe+mtime+Version). Bei jedem Fehler: leere Liste."""
    try:
        stat = video_path.stat()
        cache_key = {
            "src_size": stat.st_size,
            "src_mtime": int(stat.st_mtime),
            "version": VERBATIM_VERSION,
        }
        if cache_path is not None and cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("cache_key") == cache_key:
                print(f"  [VERBATIM] Cache-Hit ({cache_path.name})")
                return [tuple(z) for z in cached["zones"]]

        zones = transcribe_verbatim_fillers(video_path)
        if cache_path is not None:
            cache_path.write_text(json.dumps(
                {"cache_key": cache_key, "zones": [list(z) for z in zones]}, indent=2
            ))
        return zones
    except Exception as e:
        print(f"  [VERBATIM] WARN: Pass fehlgeschlagen ({type(e).__name__}: {e}) — fahre mit 0 Zonen fort")
        return []
