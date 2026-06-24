# services/outtake_detector.py
"""
V5-only: Lokale Outtake-Erkennung — der Gemini-Visual-Ersatz.

Geminis outtake_break-Label basiert laut eigenem Prompt primär auf dem
"Stimmen-Test" (Stimme bricht ab → Outtake). Das lässt sich größtenteils
aus Whisper-Text + Timing rekonstruieren:

1. RESTART: Sprecher setzt neu an — zwei aufeinanderfolgende Sätze beginnen
   sehr ähnlich. Der frühere Versuch ist der Outtake.
   - Unvollständiger Satz (kein .!?) + Präfix-Ähnlichkeit ≥ 0.60 → sicher raus
   - Vollständiger Satz + GESAMT-Ähnlichkeit ≥ 0.80 → Doppel-Take, Flag für
     Claude (nur der LLM kann entscheiden, welche Version besser ist)
   Schutz gegen rhetorische Anaphern ("Wir machen X. Wir machen Y."):
   vollständige Sätze brauchen die hohe Gesamt-Ähnlichkeit, nicht nur den Anfang.

2. ABBRUCH: kurzer Satz (≤4 Wörter) ohne Satzende + lange Pause danach (≥700ms)
   → abgebrochener Ansatz, sicher raus.

3. FRAGMENT: Whisper-Wörter mit "-"-Endung ("Kund-") → Wort-Zone raus.

Alles deterministisch (difflib, stdlib). Kein API-Call, kein Audio nötig.
Funktioniert garantiert auch bei 0 Funden (leere Ergebnisse).
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from models.analysis import WhisperWord

# --- Schwellen (zentral, benannt) ---
RESTART_COMPARE_CANDIDATES = 2     # gegen die nächsten 2 NICHT-abgebrochenen Sätze vergleichen
RESTART_MAX_AHEAD = 5              # … innerhalb der nächsten 5 Sätze (Eval-Erkenntnis:
                                   # zwischen Ansatz und Neustart liegen oft 2-3 Abbruch-Fragmente)
RESTART_PREFIX_TOKENS = 5          # Anfangs-Vergleich über die ersten 5 Tokens
RESTART_SIM_INCOMPLETE = 0.60      # unvollständiger Satz: Präfix-Ähnlichkeit reicht
DUPLICATE_SIM_COMPLETE = 0.80      # vollständiger Satz: hohe GESAMT-Ähnlichkeit nötig
ABORT_MAX_WORDS = 4                # Abbruch-Kandidat: max. 4 Wörter
ABORT_MIN_PAUSE_SEC = 0.70         # … und ≥700ms Pause danach
SENTENCE_END_CHARS = (".", "!", "?")


@dataclass
class OuttakeFlag:
    sentence_id: int
    kind: str          # "restart" | "duplicate" | "aborted"
    reason: str
    enforced: bool     # True = Code-Garantie (fliegt immer raus), False = nur Flag für Claude


def _norm_tokens(text: str) -> list[str]:
    return [t for t in re.sub(r"[^\wäöüß ]", " ", text.lower()).split() if t]


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _ends_complete(sentence) -> bool:
    if not sentence.words:
        return False
    return sentence.words[-1].word.rstrip().endswith(SENTENCE_END_CHARS)


def detect_outtake_sentences(sentences) -> list[OuttakeFlag]:
    """
    Erwartet Sentence-Objekte (cut_engine_v2.Sentence: .id, .words, .start_sec,
    .end_sec, .text). Liefert Flags, sortiert nach sentence_id.

    Zwei Pässe (Eval-Erkenntnis vom 2026-06-10): zwischen einem abgebrochenen
    Ansatz und seinem Neustart liegen oft 2-3 Abbruch-Fragmente. Deshalb werden
    zuerst Abbrüche erkannt, und der Restart-Vergleich überspringt sie.
    """
    flags: dict[int, OuttakeFlag] = {}

    # --- Pass 1: Abbrüche (kurz + unvollständig + lange Pause danach) ---
    for i, s in enumerate(sentences):
        if not _norm_tokens(s.text):
            continue
        if not _ends_complete(s) and len(s.words) <= ABORT_MAX_WORDS:
            nxt = sentences[i + 1] if i + 1 < len(sentences) else None
            pause = (nxt.start_sec - s.end_sec) if nxt else 99.0
            if pause >= ABORT_MIN_PAUSE_SEC:
                flags[s.id] = OuttakeFlag(
                    sentence_id=s.id, kind="aborted",
                    reason=f"Abgebrochener Ansatz ({len(s.words)} Wörter, {pause:.2f}s Pause danach)",
                    enforced=True,
                )
    aborted_ids = set(flags.keys())

    # --- Pass 2: Restart / Doppel-Take — Abbruch-Fragmente werden übersprungen ---
    for i, s in enumerate(sentences):
        if s.id in flags:
            continue
        s_tokens = _norm_tokens(s.text)
        if not s_tokens:
            continue
        complete = _ends_complete(s)
        s_prefix = " ".join(s_tokens[:RESTART_PREFIX_TOKENS])
        s_full = " ".join(s_tokens)

        compared = 0
        for j in range(i + 1, min(i + 1 + RESTART_MAX_AHEAD, len(sentences))):
            o = sentences[j]
            if o.id in aborted_ids:
                continue  # Trümmer zwischen Ansatz und Neustart überspringen
            o_tokens = _norm_tokens(o.text)
            if not o_tokens:
                continue
            compared += 1
            if compared > RESTART_COMPARE_CANDIDATES:
                break
            o_prefix = " ".join(o_tokens[:RESTART_PREFIX_TOKENS])
            o_full = " ".join(o_tokens)

            if not complete:
                prefix_sim = _sim(s_prefix, o_prefix)
                if prefix_sim >= RESTART_SIM_INCOMPLETE:
                    flags[s.id] = OuttakeFlag(
                        sentence_id=s.id, kind="restart",
                        reason=f"Neustart von Satz {o.id} (Anfangs-Ähnlichkeit {prefix_sim:.2f}, Satz unvollständig)",
                        enforced=True,
                    )
                    break
            else:
                full_sim = _sim(s_full, o_full)
                if full_sim >= DUPLICATE_SIM_COMPLETE:
                    flags[s.id] = OuttakeFlag(
                        sentence_id=s.id, kind="duplicate",
                        reason=f"Doppel-Take zu Satz {o.id} (Gesamt-Ähnlichkeit {full_sim:.2f})",
                        enforced=False,  # Claude entscheidet welche Version besser ist
                    )
                    break

    return sorted(flags.values(), key=lambda f: f.sentence_id)


def detect_fragment_zones(words: list[WhisperWord]) -> list[tuple[float, float]]:
    """Wort-Fragmente ('Kund-') → Zonen. Whisper markiert Abbrüche oft mit '-'."""
    zones = []
    for w in words:
        stripped = w.word.rstrip(".,!?;: ")
        if len(stripped) >= 2 and stripped.endswith("-"):
            zones.append((w.start, w.end))
    return zones


# --- Wort-Dopplungen / Stotterer (z.B. "hinzu hinzu") -----------------------
# Verbatim-Modelle (CrisperWhisper, v5.4) erfassen direkt wiederholte Wörter
# treu — die wollen wir auf EINE Kopie reduzieren.
DUP_MAX_GAP_SEC = 0.40   # gilt als Dopplung, wenn ≤400ms zwischen den gleichen Wörtern
DUP_MIN_LEN = 3          # sehr kurze Partikel ("ja","so") ignorieren
# Wörter, deren Verdopplung oft ABSICHT ist (Betonung) → NIE schneiden:
INTENTIONAL_DOUBLE = {
    "sehr", "ganz", "ja", "nein", "nie", "na", "so", "oh", "viel", "mega",
    "total", "echt", "voll", "mehr", "immer", "wieder", "gut", "doch", "klar",
}


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-zäöüß]", "", w.lower())


def detect_duplicate_word_zones(words: list[WhisperWord]) -> list[tuple[float, float]]:
    """Direkt wiederholte Einzelwörter (Stotterer wie 'hinzu hinzu') → die früheren
    Vorkommen als Schnitt-Zonen liefern; das LETZTE bleibt stehen.

    Konservativ: nur direkt benachbarte, identische Wörter (Lücke ≤ DUP_MAX_GAP_SEC),
    mit Mindestlänge, und NICHT bei typischen Betonungs-Dopplungen (sehr sehr, ja ja).
    """
    zones: list[tuple[float, float]] = []
    n = len(words)
    i = 0
    while i < n - 1:
        cur = _norm_word(words[i].word)
        if len(cur) < DUP_MIN_LEN or cur in INTENTIONAL_DOUBLE:
            i += 1
            continue
        j = i  # Lauf direkt benachbarter, gleicher Wörter sammeln
        while (j + 1 < n
               and _norm_word(words[j + 1].word) == cur
               and (words[j + 1].start - words[j].end) <= DUP_MAX_GAP_SEC):
            j += 1
        if j > i:
            for k in range(i, j):          # alle außer der letzten Kopie rausschneiden
                zones.append((words[k].start, words[k].end))
            i = j + 1
        else:
            i += 1
    return zones
