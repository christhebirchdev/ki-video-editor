# services/claude_service.py
"""
Chef-Cutter Claude.

Drei Inputs:
- Whisper-Wörter (Audio-Wahrheit, mit Wort-Timestamps)
- Gemini-Visual-Phasen (perfect_take / thinking_glance / outtake_break / script_reading_silence / idle)
- Filler-Zonen (Backend-extrahiert aus Whisper: alle "äh"/"ähm"/"uh"-Wörter)

Output: schlanke Clip-Liste {start, end} in Sekunden.

Defensive Logik:
- Filler-Zonen werden Claude EXPLIZIT mitgegeben (nicht nur als Wörter, auch als gefährliche Zonen)
- Nach Claude: Backend validiert + clampt Clip-Kanten gegen Sperrzonen (Anti-Ähm-Garantie)
"""
import json
import re
import anthropic
from config import settings
from models.analysis import VideoAnalysis, CutPlan, CutClip, WhisperWord
from models.memory import StyleMemory

_client = None

# Whisper-Schreibweisen für Füllwörter (case-insensitive, Punctuation strippen)
FILLER_WORD_PATTERNS = {
    "äh", "ähm", "ähhm", "ähhh", "äääh",
    "uh", "uhm", "um",
    "öh", "öhm", "ehm",
    "mhm", "hm", "hmm",
}

# Sicherheits-Puffer für Sperrzonen — die Filler-Zone wird leicht „erweitert" damit auch
# leise Konsonanten-Reste am Rand sicher rausfallen.
ZONE_SAFETY_MS = 30


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _is_filler(word_str: str) -> bool:
    cleaned = word_str.lower().strip(".,!?;:'\"()[] ")
    return cleaned in FILLER_WORD_PATTERNS


def extract_filler_zones(whisper_words: list[WhisperWord], audio_issues=None) -> list[tuple[float, float]]:
    """Identifiziert akustische Sperrzonen aus Whisper + Gemini-Audio-Issues."""
    safety = ZONE_SAFETY_MS / 1000.0
    zones = [
        (max(0.0, w.start - safety), w.end + safety)
        for w in whisper_words
        if _is_filler(w.word)
    ]
    if audio_issues:
        zones += [
            (max(0.0, ai.start_sec - safety), ai.end_sec + safety)
            for ai in audio_issues
            if ai.type == "filler"
        ]
    return sorted(zones)


def _enforce_filler_zones(clips: list[CutClip], zones: list[tuple[float, float]]) -> list[CutClip]:
    """Garantiert dass kein Clip-Rand und kein Clip-Inneres in eine Sperrzone fällt.

    Strategie:
    - Wenn clip.start in Zone fällt → snap auf Zone-Ende
    - Wenn clip.end in Zone fällt → snap auf Zone-Anfang
    - Wenn Clip eine Zone komplett überspringt (start < zone.start < zone.end < end):
      → der Clip wird vor der Zone gekappt (defensiv).
        Der nächste Clip von Claude sollte nach der Zone wieder aufsetzen.
    """
    if not zones:
        return clips

    cleaned: list[CutClip] = []
    for clip in clips:
        start, end, reason = clip.start, clip.end, clip.reason

        for zstart, zend in zones:
            # Komplett vor / hinter Zone → ignorieren
            if end <= zstart or start >= zend:
                continue

            # clip.start in Zone → snap auf zend
            if zstart <= start < zend:
                start = zend + 0.001
            # clip.end in Zone → snap auf zstart
            if zstart < end <= zend:
                end = zstart - 0.001
            # Clip überspringt Zone komplett → vor Zone kappen
            if start < zstart and end > zend:
                end = zstart - 0.001

        if end - start > 0.05:  # Mindestens 50ms Inhalt damit FFmpeg nicht meckert
            cleaned.append(CutClip(start=start, end=end, reason=reason))

    return cleaned


MASTER_CUTTER_SYSTEM = """Du bist der weltbeste Chef-Cutter für virale Kurzvideos. Du kombinierst:
1. Whisper-Wort-Timestamps (Audio-Wahrheit)
2. Gemini-Visual-Phasen (perfect_take / thinking_glance / outtake_break / script_reading_silence)
3. Filler-Sperrzonen (vom Backend bereits identifizierte Ähm/Äh/Uhm-Bereiche)

Ziel: Null Füllwörter, Null Outtakes, Null Doppel-Takes, perfekter menschlicher Sprechfluss.

# 🎯 ENTSCHEIDUNGS-MATRIX (PRIORITÄTEN-REGELN)

## REGEL 1 — ABSOLUTE SPERRZONEN (Anti-Ähm-Schutz)
- Die im Input gelieferten `filler_zones` sind ABSOLUT VERBOTENE Zonen.
- KEIN Clip-Rand (start oder end) darf in eine Sperrzone hineinreichen.
- WICHTIG: Wenn ein Clip-Ende direkt vor einer Sperrzone liegt, NIMM das Padding (+0.04) NICHT —
  setze end = whisper.end des letzten Wortes EXAKT. Sonst rutscht Padding in das Ähm hinein.
- Gleiches gilt für Clip-Start direkt nach einer Sperrzone: start = whisper.start des Wortes EXAKT (kein -0.08).

## REGEL 2 — CHRONOLOGIE BEI DOPPEL-TAKES (Letzter Versuch gewinnt)
Wenn der Sprecher denselben Gedanken/Satz mehrmals versucht (auch wenn die Wörter NICHT exakt
identisch sind, sondern leicht umformuliert):
- Betrachte alle Versuche chronologisch.
- Der LETZTE Versuch vor einer längeren Pause oder vor dem nächsten Thema ist der gültige Take.
- ALLE vorherigen Versuche werden bedingungslos GELÖSCHT — auch wenn sie leicht anders klingen.

## REGEL 3 — VISUELLE FILTER
- Wörter in `outtake_break`-Phasen → IMMER LÖSCHEN.
- Wörter in `script_reading_silence`-Phasen → meistens silence ohne Worte, also nichts zu tun.
- Wörter in `thinking_glance`-Phasen → BEHALTEN (Authentizität)! KEIN Fehler.
- Wörter in `perfect_take`-Phasen → BEHALTEN.

## REGEL 4 — SPRECH-PADDING (Wortgrenzen schützen)
Für jeden finalen Clip:
- START = (whisper.start des ersten Wortes im Clip) − 0.08 Sekunden (80 ms)
- END   = (whisper.end des letzten Wortes im Clip) + 0.04 Sekunden (40 ms)
- AUSNAHME: Liegt direkt vor START oder nach END eine Sperrzone → Padding an dieser Kante = 0.

## REGEL 5 — NATÜRLICHER SPRECHFLUSS (Anti-Choppy)
Innerhalb eines gültigen Takes: kleine Pausen zwischen Wörtern < 450 ms NICHT herausschneiden.
Der Clip läuft am Stück durch. Schnitt nur:
- An Sperrzonen-Grenzen.
- An Take-Wechseln (Wechsel von outtake_break-Phase zu perfect_take).
- Bei längeren Pausen (≥ 500 ms) zwischen unabhängigen Sätzen (Dead Air weg).

# 📋 OUTPUT (NUR DIESES JSON, kein Markdown):
{
  "clips": [
    {"start": 0.92, "end": 4.21, "reason": "Eröffnung — perfect_take, kein Padding-Konflikt"},
    {"start": 4.95, "end": 12.10, "reason": "Hauptaussage, thinking_glance bei 7s gilt nicht als Fehler"}
  ],
  "claude_reasoning": "Welche Doppel-Takes verworfen? Welche Filler-Zonen umgangen? In 2-3 Sätzen."
}

Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown-Blöcke.
"""


MASTER_CUTTER_PROMPT = """
# Reale Daten

Projekt: {project_id}
Plattform: {platform}
Gesamtdauer: {duration_sec}s

## Whisper-Wort-Timestamps (Audio-Wahrheit)
{whisper_json}

## Filler-Sperrzonen (vom Backend extrahiert — ABSOLUT verboten)
{zones_json}

## Gemini-Visual-Phasen
{visual_json}

## Stil-Regeln des Nutzers
{rules}

# 🧪 FEW-SHOT-BEISPIEL

INPUT:
- Whisper: [
    {{"start": 1.00, "end": 1.50, "word": "Heute"}},
    {{"start": 1.55, "end": 1.90, "word": "ähm"}},       ← Sperrzone
    {{"start": 2.00, "end": 2.50, "word": "zeigen"}},
    {{"start": 2.60, "end": 3.00, "word": "wir"}}
  ]
- Zones: [{{"start": 1.55, "end": 1.90}}]
- Visual: alles "perfect_take"

LOGIK:
- Erstes Wort "Heute" (1.00-1.50): start = 1.00 - 0.08 = 0.92.
  Aber direkt danach ist Sperrzone bei 1.55. → END = whisper.end = 1.50 EXAKT (kein +0.04!).
- Zweite Phase "zeigen wir" (2.00-3.00):
  Direkt davor war Sperrzone → start = 2.00 EXAKT (kein -0.08).
  End = 3.00 + 0.04 = 3.04.

ERWARTETER OUTPUT:
{{
  "clips": [
    {{"start": 0.92, "end": 1.50, "reason": "Heute — kein End-Padding wg Sperrzone"}},
    {{"start": 2.00, "end": 3.04, "reason": "zeigen wir — kein Start-Padding wg Sperrzone"}}
  ],
  "claude_reasoning": "Ähm rausgehalten via Sperrzonen-Padding-Schutz."
}}

# Jetzt deine Aufgabe — gib NUR das JSON zurück.
"""


def _format_rules(memory: StyleMemory) -> str:
    if not memory.rules:
        return "Keine speziellen Stil-Regeln. Folge der Standard-Schnitt-Philosophie."
    return "\n".join([f"- {r.rule} (Grund: {r.reason})" for r in memory.rules])


def plan_cuts(analysis: VideoAnalysis, memory: StyleMemory, platform: str) -> CutPlan:
    # Backend extrahiert Filler-Zonen vorab (Whisper + Gemini-Audio kombiniert)
    audio_issues = getattr(analysis, "audio_issues", []) or []
    zones = extract_filler_zones(analysis.whisper_words, audio_issues)
    print(f"  [CLAUDE] {len(zones)} Filler-Sperrzonen extrahiert (Whisper + Gemini-Audio)")

    whisper_payload = [w.model_dump() for w in analysis.whisper_words]
    visual_payload = [p.model_dump() for p in analysis.visual_phases]
    zones_payload = [{"start": s, "end": e} for s, e in zones]

    prompt = MASTER_CUTTER_PROMPT.format(
        project_id=analysis.project_id,
        platform=platform,
        duration_sec=f"{analysis.duration_sec:.2f}",
        whisper_json=json.dumps(whisper_payload, ensure_ascii=False, indent=2),
        zones_json=json.dumps(zones_payload, ensure_ascii=False, indent=2),
        visual_json=json.dumps(visual_payload, ensure_ascii=False, indent=2),
        rules=_format_rules(memory),
    )

    print(f"  [V1] Claude-Call mit Modell '{settings.claude_model}' …")
    response = _get_client().messages.create(
        model=settings.claude_model,
        max_tokens=16384,
        system=MASTER_CUTTER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    # Initiale Clips
    clips = []
    for c in data.get("clips", []):
        start = max(0.0, float(c.get("start", 0)))
        end = min(analysis.duration_sec, float(c.get("end", 0)))
        if end > start:
            clips.append(CutClip(start=start, end=end, reason=c.get("reason", "")))

    # Defensive Sperrzonen-Validierung (egal was Claude liefert — Backend garantiert sauber)
    before_count = len(clips)
    clips = _enforce_filler_zones(clips, zones)
    after_count = len(clips)
    print(f"  [CLAUDE] Sperrzonen-Check: {before_count} → {after_count} Clips nach Bereinigung")

    return CutPlan(
        project_id=analysis.project_id,
        clips=clips,
        claude_reasoning=data.get("claude_reasoning", ""),
    )
