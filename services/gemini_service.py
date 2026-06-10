# services/gemini_service.py
"""
Visuelle Phasen-Analyse via Gemini (multimodal).

Gemini macht hier KEINE Audio-Segmentierung mehr (das macht Whisper),
sondern bewertet ausschließlich die visuelle Performance: Blickkontakt,
natürliche Blickwechsel, sichtbare Outtakes.

Mit Retry und Modell-Fallback bei 503-Storms.
"""
import json
import re
import time
from pathlib import Path
from google import genai
from google.genai import errors as genai_errors
from config import settings
from models.analysis import VisualPhase, AudioIssue

client = genai.Client(api_key=settings.gemini_api_key)

GEMINI_MODEL = "gemini-2.5-flash"
# Fallback-Kette: nur Modelle die tatsächlich existieren.
# gemini-2.5-flash-lite RAUS — halluziniert massiv (lieferte 103 Filler in 93s Video).
# gemini-2.0-flash als einziger Fallback — bei Quota-Erschöpfung lieber Pipeline abbrechen
# als unbrauchbare Audio-Analyse zu produzieren.
GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash"]

RETRY_DELAYS_SEC = [3, 10, 30]
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


VISUAL_ANALYSIS_PROMPT = """
Du bist ein erfahrener Video-Editor mit Adlerohren. Du analysierst das hochgeladene Video
sowohl VISUELL als auch AKUSTISCH.

🎯 Schlüssel-Unterscheidung: Du musst feinfühlig zwischen einem "Nachdenken bei hoher Sprechenergie"
(KEIN Fehler) und einem "echten Take-Abbruch" (Outtake) unterscheiden. Ein menschlicher Cutter
würde authentische Mini-Glances NIE rausschneiden.

# Erstelle ein LÜCKENLOSES Array aller visuellen Phasen mit folgenden Labels:

- "perfect_take":           Stabiler, direkter Blickkontakt zur Kamera + HOHE Sprechenergie.
- "thinking_glance":        Sprecher blickt kurz weg (nach oben/links/rechts), spricht dabei aber
                             OHNE Unterbrechung flüssig und energetisch weiter. Stimme bleibt stabil.
                             WICHTIG: KEIN Fehler — das ist Authentizität!
- "script_reading_silence": Sprecher schweigt, schaut nach unten auf Notizen oder sammelt sich
                             für den nächsten Versuch.
- "outtake_break":          Sprecher BRICHT den Satz ab, Stimme stoppt/bricht, verzieht das Gesicht,
                             schüttelt den Kopf, sackt körpersprachlich zusammen, flucht/seufzt.
                             Eindeutiger Fehler/Outtake.
- "idle":                   Start- oder Endphase ohne Sprechaktion.

# Entscheidungsregel — der Stimmen-Test
- Sprecher schaut weg + Stimme läuft FLÜSSIG weiter → thinking_glance ✅
- Sprecher schaut weg + Stimme STOPPT/BRICHT → outtake_break ❌
- Sprecher schaut weg + Stille (schaut auf Notiz) → script_reading_silence

# Zeit-Format
- Alle Zeitangaben in SEKUNDEN als float.
- Lückenlos: end_sec von Phase N == start_sec von Phase N+1.
- Erste Phase startet bei 0.00, letzte endet bei Video-Gesamtdauer.

# 🎧 ZWEITER PFLICHT-OUTPUT: AUDIO_ISSUES
Zusätzlich zur visuellen Analyse: höre genau hin und melde ALLE Audio-Probleme
mit präzisen Zeitstempeln. Das ist EXTREM wichtig — du fängst Sachen die ein Text-Transkript übersieht.

Audio-Issue-Typen:
- "filler":           Klar hörbares Füllwort ("Ähm", "Äh", "Uh", "Öh", "Mmh"). MELDE AUCH WENN UNSICHER —
                      ist besser einmal zu viel zu melden als einmal zu wenig.
- "long_pause":       Stille > 800ms mitten im Satz (unbeabsichtigtes Stocken).
- "voice_break":      Stimme bricht ab, Lautstärke fällt abrupt, Stottern, Versprecher.
- "mispronunciation": Falsch ausgesprochenes Wort, Versprecher der korrigiert wurde.

Pro Issue: start_sec, end_sec (in Sekunden, float), type, text_heard (was du tatsächlich gehört hast),
confidence ("low" | "medium" | "high"), description.

# Output (NUR DIESES JSON, kein Markdown):
{
  "visual_analysis": [
    {"start_sec": 0.00, "end_sec": 5.20, "visual_quality": "perfect_take", "description": "Starker Einstieg mit Blickkontakt."},
    {"start_sec": 5.20, "end_sec": 7.50, "visual_quality": "thinking_glance", "description": "Blickt kurz nach oben rechts während flüssiger Erklärung."},
    {"start_sec": 7.50, "end_sec": 10.10, "visual_quality": "outtake_break", "description": "Verhaspelt sich, bricht ab und seufzt kurz."}
  ],
  "audio_issues": [
    {"start_sec": 4.32, "end_sec": 4.68, "type": "filler", "text_heard": "ähm", "confidence": "high", "description": "klar gedehntes Ähm"},
    {"start_sec": 12.10, "end_sec": 12.40, "type": "filler", "text_heard": "äh", "confidence": "high", "description": ""},
    {"start_sec": 18.50, "end_sec": 19.80, "type": "long_pause", "text_heard": "", "confidence": "medium", "description": "Atempause mitten im Satz"},
    {"start_sec": 22.05, "end_sec": 22.45, "type": "voice_break", "text_heard": "Kund-", "confidence": "high", "description": "Sprecher bricht 'Kunde' ab"}
  ],
  "total_duration_sec": 10.10
}

# Hinweise
- Im Zweifel: "thinking_glance" statt "outtake_break". Outtake NUR bei eindeutigem Stimmen-Bruch.
- Mikro-Blicke (< 200ms) ignorieren.
- Bei audio_issues: SEHR großzügig melden — wir filtern lieber später als zu wenig zu erkennen.
- Filler-Zeitstempel präzise: start_sec = erster Laut des Ähms, end_sec = letzter Laut.
"""


def _call_with_retry(label: str, callable_fn):
    last_err = None
    for attempt in range(len(RETRY_DELAYS_SEC) + 1):
        try:
            return callable_fn()
        except genai_errors.APIError as e:
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            if status not in RETRYABLE_STATUS_CODES:
                raise
            last_err = e
            if attempt >= len(RETRY_DELAYS_SEC):
                break
            delay = RETRY_DELAYS_SEC[attempt]
            print(f"  [GEMINI] WARN: {label} → {status} (Versuch {attempt+1}). Retry in {delay}s …")
            time.sleep(delay)
    raise RuntimeError(
        f"Gemini {label} nach {len(RETRY_DELAYS_SEC)+1} Versuchen fehlgeschlagen. Letzter Fehler: {last_err}"
    )


def _state_name(state) -> str:
    return getattr(state, "name", str(state))


def _upload_video_to_gemini(video_path: Path):
    size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"  [GEMINI] Upload-Start: {video_path.name} ({size_mb:.1f} MB)")
    t0 = time.time()
    video_file = _call_with_retry(
        "files.upload",
        lambda: client.files.upload(file=str(video_path)),
    )
    print(f"  [GEMINI] ✓ Upload abgeschlossen in {time.time() - t0:.1f}s")

    t1 = time.time()
    poll_count = 0
    while _state_name(video_file.state) == "PROCESSING":
        poll_count += 1
        time.sleep(2)
        video_file = _call_with_retry(
            "files.get",
            lambda vf=video_file: client.files.get(name=vf.name),
        )
    if _state_name(video_file.state) == "FAILED":
        raise RuntimeError(f"Gemini File Upload fehlgeschlagen: {video_file.state}")
    print(f"  [GEMINI] ✓ Files-API-Processing in {time.time() - t1:.1f}s ({poll_count} Polls)")
    return video_file


def analyse_visual_phases(video_path: Path) -> tuple[list[VisualPhase], list[AudioIssue], float]:
    """
    Multimodale Gemini-Analyse: Visual Phases + Audio Issues. Mit Modell-Fallback bei 503.

    Returns:
        (visual_phases, audio_issues, total_duration_sec)
    """
    t_total_start = time.time()
    video_file = _upload_video_to_gemini(video_path)

    models_to_try = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS
    response = None
    last_err = None
    for model_name in models_to_try:
        t_gen_start = time.time()
        print(f"  [GEMINI] Visual-Inference ({model_name}) …")
        try:
            response = _call_with_retry(
                f"generate_content[{model_name}]",
                lambda m=model_name: client.models.generate_content(
                    model=m,
                    contents=[video_file, VISUAL_ANALYSIS_PROMPT],
                ),
            )
            print(f"  [GEMINI] ✓ Visual-Inference ({model_name}) in {time.time() - t_gen_start:.1f}s")
            break
        except (RuntimeError, genai_errors.APIError) as e:
            last_err = e
            print(f"  [GEMINI] ✗ {model_name} fehlgeschlagen ({type(e).__name__}: {str(e)[:120]}) — nächstes Modell …")
            continue

    if response is None:
        raise RuntimeError(
            f"Alle Gemini-Modelle ({', '.join(models_to_try)}) nicht verfügbar. Letzter Fehler: {last_err}"
        )

    raw = response.text.strip()
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if json_match:
        raw = json_match.group()
    data = json.loads(raw)

    phases_data = data.get("visual_analysis", [])
    phases = [VisualPhase(**p) for p in phases_data]

    audio_data = data.get("audio_issues", [])
    audio_issues: list[AudioIssue] = []
    for a in audio_data:
        try:
            audio_issues.append(AudioIssue(**a))
        except Exception as e:
            print(f"  [GEMINI] WARN: konnte audio_issue nicht parsen: {a} ({e})")

    total_duration = float(data.get("total_duration_sec", phases[-1].end_sec if phases else 0.0))

    # Stats nach Typ
    type_counts: dict[str, int] = {}
    for ai in audio_issues:
        type_counts[ai.type] = type_counts.get(ai.type, 0) + 1
    counts_str = ", ".join(f"{k}={v}" for k, v in type_counts.items()) or "—"

    print(f"  [GEMINI] ✓ {len(phases)} Visual-Phasen, {len(audio_issues)} Audio-Issues ({counts_str}) — Gesamt: {time.time() - t_total_start:.1f}s")
    return phases, audio_issues, total_duration
