# tests/test_subtitle_engine.py
"""Tests für die Untertitel-Engine V1 (Bold Pop) — ohne Whisper, ohne ffmpeg."""
import os

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

from models.analysis import WhisperWord  # noqa: E402
from services.subtitle_engine import (  # noqa: E402
    group_words_into_phrases,
    pick_keywords,
    build_ass,
    _hex_to_ass_bgr,
    AVAILABLE_STYLES,
    DEFAULT_POSITION,
)


def _w(word, start, end):
    return WhisperWord(word=word, start=start, end=end)


def test_phrases_max_five_words():
    words = [_w(f"wort{i}", i * 0.3, i * 0.3 + 0.25) for i in range(10)]
    phrases = group_words_into_phrases(words)
    assert all(1 <= len(p["words"]) <= 5 for p in phrases)
    assert sum(len(p["words"]) for p in phrases) == 10


def test_phrase_breaks_at_pause_and_punctuation():
    words = [
        _w("Das", 0.0, 0.2), _w("ist", 0.25, 0.4), _w("wichtig.", 0.45, 0.9),
        _w("Und", 2.0, 2.2),  # 1.1s Pause + Satzende davor
        _w("jetzt", 2.25, 2.5),
    ]
    phrases = group_words_into_phrases(words)
    assert len(phrases) == 2
    assert phrases[0]["words"][-1]["text"] == "wichtig"


def test_display_end_holds_until_next_phrase():
    words = [_w("Eins", 0.0, 0.4), _w("Zwei.", 0.5, 0.9), _w("Drei", 1.0, 1.4)]
    phrases = group_words_into_phrases(words)
    assert phrases[0]["display_end"] <= phrases[1]["start"] + 0.001


def test_keyword_max_one_per_phrase_skips_stopwords():
    words = [
        _w("wir", 0.0, 0.2), _w("müssen", 0.25, 0.5),         # Stopword trotz Länge
        _w("Dopamin", 0.55, 1.0), _w("verstehen.", 1.05, 1.6),
    ]
    phrases = group_words_into_phrases(words)
    pick_keywords(phrases)
    highlights = [w["text"] for p in phrases for w in p["words"] if w["highlight"]]
    assert "müssen" not in highlights                      # Stopword nie Highlight
    assert len(highlights) >= 1                             # Inhaltswort gewinnt
    for p in phrases:                                       # max 1 pro Phrase
        assert sum(1 for w in p["words"] if w["highlight"]) <= 1


def test_phrase_char_limit_and_line_break():
    """Lange Phrase bleibt zusammen (34-Zeichen-Limit) und bekommt einen Zeilenumbruch."""
    words = [
        _w("Vorsorgeuntersuchung", 0.0, 1.5),
        _w("ist", 1.6, 1.8), _w("wichtig", 1.85, 2.3),
    ]
    phrases = group_words_into_phrases(words)
    assert len(phrases) == 1                                # 32 Zeichen ≤ 34 → eine Phrase
    assert phrases[0]["line_break_after"] == 0              # Umbruch nach dem langen Wort
    from services.subtitle_engine import longest_line_chars
    assert longest_line_chars(phrases[0]) == 20             # Zeile 1 = das 20-Zeichen-Wort


def test_line_break_balanced():
    """Umbruch minimiert die längere Zeile (balanciert)."""
    from services.subtitle_engine import compute_line_break
    words = [{"text": t} for t in ["DAS", "WIRD", "RICHTIG", "GROSSARTIG"]]  # 27 Zeichen
    lb = compute_line_break(words)
    assert lb == 2  # "DAS WIRD RICHTIG" (16) / "GROSSARTIG" (10) → max 16 ist Minimum


def test_short_phrase_no_line_break():
    from services.subtitle_engine import compute_line_break
    assert compute_line_break([{"text": "KI"}, {"text": "GEWINNT"}]) is None


def test_build_ass_line_break():
    """ASS enthält \\N sobald Wörter der 2. Zeile sichtbar sind."""
    words = [_w("Vorsorgeuntersuchung", 0.0, 1.5), _w("ist", 1.6, 1.8), _w("wichtig.", 1.85, 2.3)]
    phrases = group_words_into_phrases(words)
    doc = {
        "style_def": AVAILABLE_STYLES["bold_pop_cyan"],
        "position": dict(DEFAULT_POSITION),
        "font_size": 16,
        "phrases": phrases,
    }
    ass = build_ass(doc, 1080, 1920)
    assert "\\N" in ass
    # Event 1 (nur Wort 1 sichtbar) hat noch KEINEN Umbruch
    first_event = [l for l in ass.split("\n") if l.startswith("Dialogue:")][0]
    assert "\\N" not in first_event


def test_hex_to_ass_bgr():
    # #00D2FF (Cyan): R=00 G=D2 B=FF → ASS BGR &H00FFD200
    assert _hex_to_ass_bgr("#00D2FF") == "&H00FFD200"
    assert _hex_to_ass_bgr("#FFFFFF") == "&H00FFFFFF"


def test_build_ass_word_by_word_and_popin():
    words = [_w("KI", 1.0, 1.3), _w("gewinnt", 1.35, 1.9), _w("immer.", 1.95, 2.4)]  # 17 Zeichen = 1 Phrase
    phrases = group_words_into_phrases(words)
    pick_keywords(phrases)
    assert len(phrases) == 1
    doc = {
        "style_def": AVAILABLE_STYLES["bold_pop_cyan"],
        "position": dict(DEFAULT_POSITION),
        "font_size": 16,
        "phrases": phrases,
    }
    ass = build_ass(doc, 1080, 1920)
    # Pro Wort ein Event (3 Wörter → 3 Dialogue-Zeilen)
    assert ass.count("Dialogue:") == 3
    # ALL CAPS
    assert "GEWINNT" in ass
    # Pop-In nur am Phrasen-Anfang
    assert ass.count("\\t(0,110,\\fscx100\\fscy100)") == 1
    # Cyan-Highlight (BGR) vorhanden
    assert "&H00FFD200" in ass
    # Position: zentriert (an5) bei 65% Höhe von 1920 = 1248
    assert "\\pos(540,1248)" in ass
    # Größe 16 @1920p → Basis 64px; Phrase 17 Zeichen passt in 80% von 1080 → \fs64
    assert "\\fs64" in ass


def test_build_ass_respects_safe_width():
    """Rand-Garantie Stufe 2: lange Phrase wird per \\fs verkleinert, nie breiter als 80%."""
    from services.subtitle_engine import effective_font_px, CHAR_WIDTH_FACTOR, SAFE_WIDTH_PCT
    # 20-Zeichen-Wort bei Größe 16 @1080 Breite: 64px Basis wäre 0.68*64*20 = 870px > 864px (80%)
    eff = effective_font_px(64, 20, 1080)
    assert eff < 64
    assert CHAR_WIDTH_FACTOR * eff * 20 <= 1080 * SAFE_WIDTH_PCT + 1  # nie über Safe-Width

    words = [_w("Vorsorgeuntersuchung", 1.0, 2.5)]
    phrases = group_words_into_phrases(words)
    doc = {
        "style_def": AVAILABLE_STYLES["bold_pop_cyan"],
        "position": dict(DEFAULT_POSITION),
        "font_size": 16,
        "phrases": phrases,
    }
    ass = build_ass(doc, 1080, 1920)
    assert f"\\fs{eff}" in ass


def test_font_size_setting_scales():
    """Größen-Setting wirkt: 32 statt 16 → doppelte Pixel (bei kurzer Phrase)."""
    words = [_w("Top.", 1.0, 1.4)]
    phrases = group_words_into_phrases(words)
    base = {
        "style_def": AVAILABLE_STYLES["bold_pop_cyan"],
        "position": dict(DEFAULT_POSITION),
        "phrases": phrases,
    }
    ass16 = build_ass({**base, "font_size": 16}, 1080, 1920)
    ass32 = build_ass({**base, "font_size": 32}, 1080, 1920)
    assert "\\fs64" in ass16 and "\\fs128" in ass32


def test_ffmpeg_capability_check():
    """Capability-Check erkennt libass-fähiges ffmpeg und lehnt unfähige Binaries ab."""
    from services.subtitle_engine import _ffmpeg_supports_ass, find_ffmpeg_with_ass
    # /bin/true liefert rc=0 aber keine Filter-Liste → kein ass
    assert _ffmpeg_supports_ass("/bin/true") is False
    assert _ffmpeg_supports_ass("/nonexistent/ffmpeg") is False
    # Auf Systemen mit libass-ffmpeg muss die Suche ein Binary liefern
    import shutil as _sh
    if _sh.which("ffmpeg"):
        assert find_ffmpeg_with_ass()


def test_build_ass_empty_phrases_safe():
    doc = {
        "style_def": AVAILABLE_STYLES["bold_pop_cyan"],
        "position": dict(DEFAULT_POSITION),
        "phrases": [],
    }
    ass = build_ass(doc, 1080, 1920)
    assert "[Events]" in ass and "Dialogue:" not in ass
