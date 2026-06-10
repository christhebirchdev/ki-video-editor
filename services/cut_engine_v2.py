# services/cut_engine_v2.py
"""
V2 Hybrid-Engine: Code macht Heavy-Lifting, Claude macht nur die semantische Sentence-Selection.

Pipeline:
1. Code gruppiert Whisper-Wörter zu Sätzen (Pausen >500ms + Punctuation als Trenner)
2. Code mappt jedem Satz seine dominante Visual-Phase zu
3. Claude bekommt nummerierte Sätze + Visual-Tag → wählt nur kept-IDs
4. Code baut Clips: nimmt nur kept-Sätze, schneidet INNERHALB jedes Satzes
   alle Ähms, lange Pausen und outtake_break-Wörter heraus → Sub-Clips
5. Padding -80ms/+40ms wird vom Code berechnet, mit Sperrzonen-Garantie

Vorteile gegenüber V1:
- Claude muss nicht mehr rechnen → deterministisches Padding
- Code garantiert dass keine Filler/Outtakes durchkommen
- Hook-Wiederholungen mit unterschiedlichem Wortlaut werden über Sentence-IDs auflösbar
- Versprecher in outtake_break-Phasen werden auch innerhalb behaltener Sätze gefiltert
"""
import json
import re
import anthropic
from dataclasses import dataclass, field
from typing import Optional
from config import settings
from models.analysis import VideoAnalysis, CutPlan, CutClip, WhisperWord, VisualPhase
from models.memory import StyleMemory


# ===== Konfiguration =====

SENTENCE_PAUSE_THRESHOLD_SEC = 0.5    # Pause >500ms zwischen Wörtern = neue Satzgrenze
LONG_PAUSE_WITHIN_SENTENCE = 0.4      # Pause >400ms innerhalb Satz → wird übersprungen (Sub-Clip-Split)
LONG_PAUSE_NO_POSTPAD_SEC = 0.6       # Pause >600ms = Filler-Verdacht → kein post-padding (würde sonst Filler einschließen)
PADDING_START_SEC = 0.12              # -120ms vor Wort-Anfang (schützt leise Konsonanten H/F/Sch)
PADDING_END_SEC = 0.08                # +80ms nach Wort-Ende (schützt auslaufende Konsonanten)
MIN_CLIP_DURATION = 0.15              # Sub-Clips kürzer als 150ms verwerfen
FILLER_GAP_SAFETY_MS = 100            # 100ms Sicherheit um Filler-Zonen (Gemini-Zeitstempel sind ungenau ±200ms)
OUTTAKE_SAFETY_MS = 300               # 300ms Sicherheit um Outtake-Zonen (visuelle Phasen-Grenzen sind unscharf)
SUSPICIOUS_WORD_DURATION_SEC = 0.8    # Mindest-Absolutdauer (unter 800ms gilt nie als Hesitation)
HESITATION_FACTOR_HARD = 1.7          # Ab 1.7× = eindeutige Hesitation → hart auf erwartete Dauer kürzen
HESITATION_FACTOR_SOFT = 1.4          # 1.4-1.7× = wahrscheinlich Filler hinten dran → sanft kürzen (mehr Wort-Reserve)
HESITATION_MIN_TRUNCATE_SEC = 0.55    # Mindest-Restlänge nach Kürzung
HESITATION_SOFT_RESERVE_SEC = 0.20    # Bei Soft-Kürzung: zusätzliche Reserve auf erwartete Dauer drauf (schützt echte Wörter)
WORD_DURATION_PER_CHAR_SEC = 0.08     # Deutsche Sprechgeschwindigkeit: ~80ms pro Buchstabe
WORD_DURATION_BASE_SEC = 0.15         # Grundlast pro Wort (Konsonanten-An- und -Auslaut)


def estimated_max_word_duration(word: str) -> float:
    """
    Geschätzte natürliche Maximaldauer für ein deutsches Wort.

    Beispiele:
      - "ist"               (3 Zeichen) → 390ms
      - "Hand"              (4 Zeichen) → 470ms
      - "Vorsorgeuntersuchung" (20 Zeichen) → 1750ms
      - "selbstverständlich" (18 Zeichen) → 1590ms

    Echte lange Komposita sind damit safe — sie überschreiten diese natürliche
    Dauer nicht signifikant. Kurze Wörter mit Filler-Anhang (z. B. "Hand...äh")
    sprengen sie hingegen deutlich.
    """
    # Nur Buchstaben zählen (Satzzeichen ignorieren)
    chars = sum(1 for c in word if c.isalpha())
    chars = max(1, chars)
    return chars * WORD_DURATION_PER_CHAR_SEC + WORD_DURATION_BASE_SEC

FILLER_WORD_PATTERNS = {
    "äh", "ähm", "ähhm", "ähhh", "äääh",
    "uh", "uhm", "um",
    "öh", "öhm", "ehm",
    "mhm", "hm", "hmm",
}

SENTENCE_END_CHARS = {".", "!", "?"}

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _is_filler(word_str: str) -> bool:
    cleaned = word_str.lower().strip(".,!?;:'\"()[] ")
    return cleaned in FILLER_WORD_PATTERNS


# ===== Datenstrukturen =====

@dataclass
class Sentence:
    id: int
    words: list[WhisperWord]
    start_sec: float
    end_sec: float
    visual_quality: str = "unknown"   # dominantes Gemini-Label
    position_label: str = ""           # "Anfang" / "Mitte" / "Ende"

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)


# ===== Schritt 1: Wörter → Sätze =====

def group_into_sentences(words: list[WhisperWord]) -> list[Sentence]:
    """Wörter zu Sätzen gruppieren via Pause-Heuristik + Punctuation."""
    sentences: list[Sentence] = []
    current: list[WhisperWord] = []
    next_id = 0

    def flush():
        nonlocal next_id, current
        if not current:
            return
        sentences.append(Sentence(
            id=next_id,
            words=current[:],
            start_sec=current[0].start,
            end_sec=current[-1].end,
        ))
        next_id += 1
        current = []

    for w in words:
        if current:
            pause = w.start - current[-1].end
            prev_ends_sentence = any(
                current[-1].word.rstrip().endswith(c) for c in SENTENCE_END_CHARS
            )
            if pause > SENTENCE_PAUSE_THRESHOLD_SEC or prev_ends_sentence:
                flush()
        current.append(w)
    flush()
    return sentences


# ===== Schritt 2: Visual-Phasen pro Satz =====

def assign_visual_quality(sentences: list[Sentence], phases: list[VisualPhase]) -> None:
    """Setzt sentence.visual_quality auf das dominante Label (längste Überlappung)."""
    for s in sentences:
        overlaps: dict[str, float] = {}
        for ph in phases:
            ov_start = max(s.start_sec, ph.start_sec)
            ov_end = min(s.end_sec, ph.end_sec)
            if ov_end > ov_start:
                overlaps[ph.visual_quality] = overlaps.get(ph.visual_quality, 0.0) + (ov_end - ov_start)
        if overlaps:
            s.visual_quality = max(overlaps.items(), key=lambda kv: kv[1])[0]


def assign_position_labels(sentences: list[Sentence], total_duration: float) -> None:
    """Label 'Anfang'/'Mitte'/'Ende' für Hook-Erkennung."""
    if not sentences:
        return
    for s in sentences:
        rel = s.start_sec / max(total_duration, 0.01)
        if rel < 0.20:
            s.position_label = "Anfang (Hook-Zone)"
        elif rel > 0.80:
            s.position_label = "Ende"
        else:
            s.position_label = "Mitte"


# ===== Schritt 3: Claude wählt Sätze =====

SENTENCE_SELECTION_SYSTEM = """Du bist ein eiskalter Text-Editor für Videoschnitt-Skripte.
Du bekommst eine nummerierte Liste von Sätzen aus einem Rohvideo.
Pro Satz weißt du: Text, Visual-Qualität, Position im Video, Dauer.

Deine Aufgabe: Entscheide für JEDEN Satz, ob er im finalen Cut bleibt.

# 🎯 ENTSCHEIDUNGS-REGELN

## REGEL 1 — Hook-Wiederholungen (kritisch!)
Wenn am Anfang des Videos (Position "Anfang" oder "Hook-Zone") mehrere Sätze inhaltlich
DASSELBE Konzept einleiten — auch wenn sie textlich völlig unterschiedlich formuliert sind
(z. B. "Hey Leute, heute zeige ich…", "In diesem Video sehen wir…", "Willkommen, heute geht es um…"):
→ ALLE Versuche außer dem LETZTEN werden RAUSGEWORFEN.
Der letzte Hook-Versuch ist die finale Version, weil der Sprecher davor mehrfach neu angesetzt hat.

## REGEL 2 — Outtakes komplett raus
Sätze mit visual_quality = "outtake_break" → IMMER keep=false.
Sätze mit visual_quality = "script_reading_silence" → IMMER keep=false (nur Stille / Notizen lesen).

## REGEL 3 — thinking_glance ist kein Fehler
Sätze mit visual_quality = "thinking_glance" → IMMER keep=true wenn inhaltlich relevant.
Authentisches Nachdenken bei flüssiger Stimme ist ERWÜNSCHT.

## REGEL 4 — Inhaltliche Wiederholungen mitten im Video
Wenn ein Satz inhaltlich das wiederholt was 1-2 Sätze davor schon gesagt wurde
(auch in anderen Worten): nur die spätere/bessere Variante behalten.

## REGEL 5 — Filler-Sätze
Sätze die NUR aus Füllwörtern bestehen ("ähm", "äh", "ja so") → keep=false.

# 📋 OUTPUT (NUR JSON, kein Markdown)
{
  "kept_sentence_ids": [0, 2, 5, 6, 8],
  "reasoning": "Sätze 1, 3, 4 als Hook-Wiederholungen verworfen — Satz 5 ist die finale Version. Satz 7 war outtake_break."
}
"""

SENTENCE_SELECTION_USER = """
Projekt: {project_id} ({platform})
Gesamtdauer: {duration_sec}s

## Sätze (nummeriert mit Visual-Qualität + Position):
{sentences_block}

Wähle aus welche Sentence-IDs im finalen Cut bleiben.
"""


def _select_sentences_with_claude(
    sentences: list[Sentence],
    project_id: str,
    platform: str,
    duration_sec: float,
    memory: StyleMemory,
) -> tuple[list[int], str]:
    """Claude entscheidet welche Sätze behalten werden."""
    lines = []
    for s in sentences:
        dur = s.end_sec - s.start_sec
        lines.append(
            f'  {s.id}: ({s.visual_quality}, {s.position_label}, {dur:.1f}s) "{s.text}"'
        )
    sentences_block = "\n".join(lines)

    prompt = SENTENCE_SELECTION_USER.format(
        project_id=project_id,
        platform=platform,
        duration_sec=f"{duration_sec:.1f}",
        sentences_block=sentences_block,
    )

    print(f"  [V2] Claude-Call mit Modell '{settings.claude_model}' …")
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=4096,
        system=SENTENCE_SELECTION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    kept_ids = [int(x) for x in data.get("kept_sentence_ids", [])]
    reasoning = data.get("reasoning", "")
    return kept_ids, reasoning


# ===== Schritt 4: Sub-Clips aus behaltenen Sätzen =====

def _next_filler_start_after(t: float, fillers: list[tuple[float, float]]) -> Optional[float]:
    """Erste Filler-Zone die nach Zeit t beginnt."""
    for fs, fe in fillers:
        if fs > t:
            return fs
    return None


def _prev_filler_end_before(t: float, fillers: list[tuple[float, float]]) -> Optional[float]:
    """Letzte Filler-Zone die vor Zeit t endet."""
    last = None
    for fs, fe in fillers:
        if fe < t:
            last = fe
        else:
            break
    return last


def _sentence_to_clips(
    sentence: Sentence,
    filler_zones: list[tuple[float, float]],
    outtake_zones: list[tuple[float, float]],
) -> list[CutClip]:
    """
    Aus einem behaltenen Satz Sub-Clips bilden — Filler, lange Pausen und Outtake-Wörter raus.

    Algorithmus: Wir akkumulieren "gute" Wörter. Bei Filler / outtake_break-Phase / lange Pause /
    Wort dessen Zeit in einer Filler-Zone (Gemini-Audio) liegt: aktuellen Buffer als Sub-Clip
    flushen, weitermachen mit neuem Buffer.
    """
    # 🛡️ CODE-GARANTIE: outtake_break-Sätze kommen NIE in den finalen Cut,
    # auch wenn Claude (V3-wohlwollend) sie behalten will.
    if sentence.visual_quality == "outtake_break":
        return []

    clips: list[CutClip] = []
    buf: list[WhisperWord] = []
    outtake_safety = OUTTAKE_SAFETY_MS / 1000.0
    filler_safety = FILLER_GAP_SAFETY_MS / 1000.0

    def in_outtake(w: WhisperWord) -> bool:
        mid = (w.start + w.end) / 2.0
        # Outtake-Zonen mit Sicherheits-Puffer (Gemini-Phasen-Grenzen sind unscharf)
        return any(s - outtake_safety <= mid <= e + outtake_safety for s, e in outtake_zones)

    def in_filler_zone(w: WhisperWord) -> bool:
        """Wort liegt zeitlich in einer Filler-Zone (Gemini hört ein Ähm, auch wenn Whisper
        das Wort anders transkribiert hat — z. B. 'habe' statt 'ähm').
        Mit Safety-Puffer weil Gemini-Audio-Zeitstempel ±200ms ungenau sein können."""
        mid = (w.start + w.end) / 2.0
        return any(s - filler_safety <= mid <= e + filler_safety for s, e in filler_zones)

    def flush(safe_pre_pad: bool, safe_post_pad: bool):
        nonlocal buf
        if not buf:
            return
        start = buf[0].start
        end = buf[-1].end
        # Padding nur wenn keine Sperrzone direkt angrenzt
        if safe_pre_pad:
            start -= PADDING_START_SEC
        if safe_post_pad:
            end += PADDING_END_SEC
        start = max(0.0, start)
        if end - start >= MIN_CLIP_DURATION:
            clips.append(CutClip(
                start=start, end=end,
                reason=f"Satz #{sentence.id} ({sentence.visual_quality})"
            ))
        buf = []

    safety = FILLER_GAP_SAFETY_MS / 1000.0

    for w in sentence.words:
        # Outtake-Wort → flush aktuelle Buf
        if in_outtake(w):
            # Pre-Pad OK (kein Filler davor), Post-Pad NICHT (Outtake folgt)
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        # Textlicher Filler (Whisper schrieb "äh"/"ähm") ODER
        # zeitlicher Filler (Gemini-Audio hört dort ein Ähm, auch wenn Whisper was anderes schrieb)
        if _is_filler(w.word) or in_filler_zone(w):
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        # 🆕 Long-Word-Heuristik mit zweistufiger Schwelle
        #
        # Whisper merged Filler ins vorherige Wort. Erkennung über Wortdauer vs.
        # erwartete Dauer (80ms/Zeichen + 150ms Grundlast).
        #
        # Zweistufig damit echte gedehnte Wörter im Grenzfall nicht zu hart gekürzt werden:
        #   - Faktor ≥1.7 (hart): eindeutige Hesitation → auf erwartete Dauer kürzen
        #   - Faktor 1.4-1.7 (sanft): vermutlich Filler hinten dran → erwartete Dauer + 200ms Reserve
        #   - Faktor <1.4: echtes Wort, unangetastet
        #
        # Echte Komposita ("Vorsorgeuntersuchung" 1.14×, "selbstverständlich" 0.94×) sind safe.
        # Filler-Anhang in kurzen Wörtern ("Hand...äh" 2.9×, "produziert...äh" 1.43×) wird gefangen.
        duration = w.end - w.start
        expected_max = estimated_max_word_duration(w.word)
        factor = duration / expected_max if expected_max > 0 else 0

        if duration > SUSPICIOUS_WORD_DURATION_SEC and factor >= HESITATION_FACTOR_SOFT:
            if factor >= HESITATION_FACTOR_HARD:
                # Hart kürzen: auf erwartete natürliche Dauer
                target_end = w.start + max(HESITATION_MIN_TRUNCATE_SEC, expected_max)
            else:
                # Sanft kürzen: erwartete Dauer + Reserve (schützt Wort-Substanz)
                target_end = w.start + max(HESITATION_MIN_TRUNCATE_SEC, expected_max + HESITATION_SOFT_RESERVE_SEC)
            truncated = WhisperWord(
                word=w.word,
                start=w.start,
                end=min(target_end, w.end),
            )
            buf.append(truncated)
            flush(safe_pre_pad=True, safe_post_pad=False)
            continue

        # Lange Pause vor diesem Wort → flush vorherigen Buf
        if buf:
            pause = w.start - buf[-1].end
            if pause > LONG_PAUSE_WITHIN_SENTENCE:
                # Bei sehr langen Pausen (>600ms): kein post-padding (Filler in der Pause sehr wahrscheinlich)
                allow_postpad = pause <= LONG_PAUSE_NO_POSTPAD_SEC
                flush(safe_pre_pad=True, safe_post_pad=allow_postpad)

        buf.append(w)

    # Flush rest — am Ende der Buf: pre und post Padding OK
    flush(safe_pre_pad=True, safe_post_pad=True)

    # Padding gegen Filler-Zonen clampen (defensive Sicherheit)
    out: list[CutClip] = []
    for c in clips:
        start, end = c.start, c.end
        for fs, fe in filler_zones:
            # Wenn unser end in eine Filler-Zone reicht (oder zu nah) → snap zurück
            if end > fs - safety and end <= fe + safety:
                end = fs - safety
            # Wenn unser start in einer Filler-Zone liegt → snap nach vorn
            if start >= fs - safety and start < fe + safety:
                start = fe + safety
        if end - start >= MIN_CLIP_DURATION:
            out.append(CutClip(start=start, end=end, reason=c.reason))
    return out


# ===== Hauptfunktion =====

def plan_cuts_v2(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    """Hybrid-Pipeline V2: Sätze → Claude wählt IDs → Code baut deterministische Clips."""

    # 1) Wörter zu Sätzen
    sentences = group_into_sentences(analysis.whisper_words)
    print(f"  [V2] {len(sentences)} Sätze aus {len(analysis.whisper_words)} Wörtern gruppiert")

    # 2) Visual-Mapping + Position
    assign_visual_quality(sentences, analysis.visual_phases)
    assign_position_labels(sentences, analysis.duration_sec)

    # 3) Claude wählt nur IDs
    if not sentences:
        return CutPlan(project_id=analysis.project_id, clips=[], claude_reasoning="Keine Sätze erkannt")

    kept_ids, reasoning = _select_sentences_with_claude(
        sentences, analysis.project_id, platform, analysis.duration_sec, memory
    )
    kept_sentences = [s for s in sentences if s.id in kept_ids]
    print(f"  [V2] Claude wählt {len(kept_sentences)}/{len(sentences)} Sätze")

    # 4) Pre-compute Filler- und Outtake-Zonen aus den Analyse-Daten
    # Quelle 1: Whisper-Wörter die textlich als Filler erkannt sind
    whisper_filler_zones = [(w.start, w.end) for w in analysis.whisper_words if _is_filler(w.word)]
    # Quelle 2: Gemini's Audio-Issues vom Typ "filler" — fängt Filler die Whisper textlich
    # nicht als solche erkannt hat (z. B. "ähm" als "ich" transkribiert)
    gemini_filler_zones = [
        (ai.start_sec, ai.end_sec) for ai in analysis.audio_issues if ai.type == "filler"
    ]

    # 🛡️ Plausibilitäts-Check: Realistisch max. 1 Filler pro 5s Video-Länge.
    # Wenn Gemini deutlich mehr liefert (z.B. 103 in 93s = halluzinierend),
    # komplett ignorieren — Whisper+Long-Word reicht aus.
    max_plausible_fillers = max(5, int(analysis.duration_sec / 5))
    if len(gemini_filler_zones) > max_plausible_fillers:
        print(f"  [V2] ⚠️ Gemini lieferte {len(gemini_filler_zones)} Filler (Plausibilitätsgrenze: {max_plausible_fillers}) — ignoriere Gemini-Audio-Filler")
        gemini_filler_zones = []

    # Zusammenführen + dedupen (verhindert dass nahe Zonen doppelt zählen)
    filler_zones = whisper_filler_zones + gemini_filler_zones
    filler_zones.sort()

    # Outtake-Zonen: Gemini-Visual (outtake_break) + Gemini-Audio (voice_break / mispronunciation)
    outtake_zones = [(ph.start_sec, ph.end_sec) for ph in analysis.visual_phases if ph.visual_quality == "outtake_break"]
    outtake_zones += [
        (ai.start_sec, ai.end_sec) for ai in analysis.audio_issues
        if ai.type in ("voice_break", "mispronunciation")
    ]

    print(f"  [V2] Filler-Zonen: {len(whisper_filler_zones)} aus Whisper + {len(gemini_filler_zones)} aus Gemini-Audio")
    print(f"  [V2] Outtake-Zonen: {len(outtake_zones)} (Visual+Audio kombiniert)")

    # 5) Sub-Clips bilden — Filler und Outtake-Wörter rausschneiden
    clips: list[CutClip] = []
    for s in kept_sentences:
        clips.extend(_sentence_to_clips(s, filler_zones, outtake_zones))

    print(f"  [V2] {len(clips)} finale Clips")

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=f"V2: {reasoning}",
    )
