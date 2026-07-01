# tests/test_cut_engine_v5.py
"""
Mini-Tests für die V5-Bausteine — ohne API-Calls, ohne Audio.

1. Outtake-Detektor: Restart / Doppel-Take / Abbruch / Anaphern-Schutz / Fragmente
2. Code-Garantie-Logik (enforced-Flags)
3. V5-Cache-Key
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

from models.analysis import WhisperWord  # noqa: E402
from services.cut_engine_v2 import Sentence  # noqa: E402
from services.outtake_detector import (  # noqa: E402
    detect_outtake_sentences,
    detect_fragment_zones,
)
from services.cut_engine_v5 import _selection_cache_key_v5, _build_sentences_block  # noqa: E402


def _sentence(sid, text, start, end):
    """Sentence aus Text bauen — Wörter gleichmäßig über die Zeit verteilt."""
    tokens = text.split()
    dur = (end - start) / max(len(tokens), 1)
    words = [
        WhisperWord(word=t, start=start + i * dur, end=start + (i + 1) * dur)
        for i, t in enumerate(tokens)
    ]
    return Sentence(id=sid, words=words, start_sec=start, end_sec=end)


# ===== 1. Restart-Erkennung =====

def test_incomplete_restart_detected_and_enforced():
    """Klassischer Neustart: unvollständiger Ansatz (>4 Wörter) + ähnlicher Folge-Satz → Code-Garantie."""
    s = [
        _sentence(0, "Die meisten Leute da draußen fragen", 0.0, 1.8),       # 6 Wörter, kein Punkt
        _sentence(1, "Die meisten Leute da draußen fragen mich, wie ich das mache.", 2.5, 6.0),
    ]
    flags = detect_outtake_sentences(s)
    assert len(flags) == 1
    assert flags[0].sentence_id == 0
    assert flags[0].kind == "restart"
    assert flags[0].enforced is True


def test_short_incomplete_start_enforced_as_aborted():
    """Kurzer Ansatz (≤4 Wörter) vor ähnlichem Satz → als 'aborted' erzwungen (Pass 1 greift zuerst)."""
    s = [
        _sentence(0, "Die meisten Leute fragen", 0.0, 1.5),
        _sentence(1, "Die meisten Leute fragen mich, wie ich das mache.", 2.2, 5.0),
    ]
    flags = detect_outtake_sentences(s)
    assert len(flags) == 1
    assert flags[0].enforced is True
    assert flags[0].kind in ("aborted", "restart")


def test_restart_found_across_aborted_fragments():
    """Eval-Erkenntnis: Neustart erst 3 Sätze später, dazwischen Abbruch-Trümmer → trotzdem gefunden."""
    s = [
        _sentence(0, "Wir kommen heute nicht mehr davon weg,", 15.0, 17.0),   # Ansatz, 7 Wörter
        _sentence(1, "mit KI zu arbeiten,", 22.6, 23.6),                       # Trümmer (aborted)
        _sentence(2, "weil jeder,", 24.5, 25.0),                               # Trümmer (aborted)
        _sentence(3, "Wir kommen heute nicht mehr darum herum, mit KI zu arbeiten, denn es lohnt sich.", 27.1, 32.0),
    ]
    flags = detect_outtake_sentences(s)
    by_id = {f.sentence_id: f for f in flags}
    assert 0 in by_id and by_id[0].kind == "restart" and by_id[0].enforced
    assert 1 in by_id and 2 in by_id  # Trümmer als aborted


def test_complete_duplicate_flagged_not_enforced():
    """Zwei fast identische VOLLSTÄNDIGE Sätze → Doppel-Take-Flag, Claude entscheidet."""
    s = [
        _sentence(0, "Wir arbeiten jeden Tag mit künstlicher Intelligenz.", 0.0, 3.0),
        _sentence(1, "Wir arbeiten jeden Tag mit künstlicher Intelligenz zusammen.", 4.0, 7.0),
    ]
    flags = detect_outtake_sentences(s)
    assert len(flags) == 1
    assert flags[0].kind == "duplicate"
    assert flags[0].enforced is False


def test_anaphora_not_flagged():
    """Rhetorische Anapher (gleicher Anfang, anderer Inhalt, vollständige Sätze) → KEIN Flag.

    Schutz: vollständige Sätze brauchen hohe GESAMT-Ähnlichkeit, nicht nur den Anfang.
    """
    s = [
        _sentence(0, "Wir machen heute die komplette Recherche mit modernen Tools.", 0.0, 3.0),
        _sentence(1, "Wir machen morgen einen ganz anderen spannenden Themenbereich auf.", 4.0, 7.0),
    ]
    assert detect_outtake_sentences(s) == []


def test_aborted_short_sentence_with_pause():
    """Kurzer Ansatz ohne Satzende + lange Pause → aborted, Code-Garantie."""
    s = [
        _sentence(0, "Also wenn man", 0.0, 1.0),       # 3 Wörter, kein Punkt
        _sentence(1, "Heute zeige ich euch etwas komplett Neues.", 2.5, 5.0),  # Pause 1.5s
    ]
    flags = detect_outtake_sentences(s)
    assert len(flags) == 1
    assert flags[0].kind == "aborted"
    assert flags[0].enforced is True


def test_complete_short_sentence_not_aborted():
    """Kurzer aber VOLLSTÄNDIGER Satz ('Genau.') → kein Abbruch-Flag."""
    s = [
        _sentence(0, "Genau.", 0.0, 0.5),
        _sentence(1, "Und deshalb machen wir das jetzt so.", 2.0, 4.0),
    ]
    assert detect_outtake_sentences(s) == []


def test_no_flags_on_clean_script():
    """Sauberes Skript ohne Wiederholungen → 0 Flags (0-Funde-Pfad)."""
    s = [
        _sentence(0, "Heute geht es um künstliche Intelligenz.", 0.0, 2.0),
        _sentence(1, "Viele Firmen nutzen sie bereits täglich.", 2.3, 4.5),
        _sentence(2, "Ich zeige dir drei konkrete Beispiele.", 4.8, 7.0),
    ]
    assert detect_outtake_sentences(s) == []


# ===== 2. Fragmente =====

def test_fragment_zones():
    words = [
        WhisperWord(word="Kund-", start=1.0, end=1.4),
        WhisperWord(word="Kunden", start=2.0, end=2.5),
        WhisperWord(word="so-", start=3.0, end=3.2),
    ]
    zones = detect_fragment_zones(words)
    assert zones == [(1.0, 1.4), (3.0, 3.2)]


# ===== 3. Prompt-Block + Cache-Key =====

def test_sentences_block_contains_flags():
    s = [
        _sentence(0, "Die meisten Leute fragen", 0.0, 1.5),
        _sentence(1, "Die meisten Leute fragen mich, wie ich das mache.", 2.2, 5.0),
    ]
    flags = {f.sentence_id: f for f in detect_outtake_sentences(s)}
    block = _build_sentences_block(s, flags)
    assert "[WIRD VOM CODE ENTFERNT" in block
    assert "Die meisten Leute fragen mich" in block


def test_v5_cache_key_stable_and_input_sensitive():
    k1 = _selection_cache_key_v5("block A", "regeln")
    k2 = _selection_cache_key_v5("block A", "regeln")
    k3 = _selection_cache_key_v5("block B", "regeln")
    assert k1 == k2
    assert k1 != k3
