# services/analyst_gemini_eval.py
"""
V2-Bewertung: EIN Gemini-Lauf analysiert UND bewertet das Video direkt.

Gedacht als Vergleich zu V1 (dort beschreibt Gemini nur, Claude bewertet aus Text).
Hier bekommt Gemini das echte Video und liefert dasselbe JSON wie Claude — es nutzt
den IDENTISCHEN Bewertungs-System-Prompt (analyst_eval.build_system_prompt:
Skill-Body + Referenz + strikter JSON-Vertrag), damit der Vergleich fair ist.

Zwei Modi:
- pure:   nur das Video. Gemini leitet Transkript, Sprechtempo, Lautheit etc. selbst ab
          (Test: schafft Gemini die ganze Pipeline allein?).
- hybrid: Video + dieselben deterministischen Messwerte, die auch Claude bekommt
          (Whisper-Transkript, Sprachstatistik, ffmpeg-LUFS). Nur die Bewertungs-Instanz
          wechselt von Claude zu Gemini.
"""
from pathlib import Path

from google.genai import types

from models.analyst import AnalystEvaluationV2, AnalystResult
from services import analyst_eval, gemini_service
from services.analyst_vlm import _generate  # generate_content mit Modell-Fallback


def _stats_txt(result: AnalystResult) -> str:
    s = result.speech_stats
    if not s:
        return "Keine Sprache erkannt."
    return (
        f"{s.wort_anzahl} Wörter, {s.wpm} WPM, {s.filler_count} Füllwörter, "
        f"{s.pausen_count} Pausen >0.5s (längste {s.laengste_pause_sec}s), "
        f"Sprechbeginn bei {getattr(s, 'sprechbeginn_sec', 0.0)}s"
    )


def _metrics_txt(result: AnalystResult) -> str:
    qm = result.quality_metrics
    if not qm:
        return "Keine Messwerte verfügbar."
    audio = (
        f"Lautheit {qm.lufs_integrated} LUFS, Loudness Range {qm.loudness_range} LU, "
        f"True Peak {qm.true_peak_db} dBFS"
        if qm.lufs_integrated is not None else "kein Audio messbar"
    )
    return (
        f"Bild: Schärfe avg {qm.schaerfe_avg} (min {qm.schaerfe_min}), "
        f"Helligkeit avg {qm.helligkeit_avg}, Kontrast avg {qm.kontrast_avg}. "
        f"Audio: {audio}.\n{analyst_eval.METRICS_GUIDE}"
    )


def _user_message(result: AnalystResult, mode: str) -> str:
    head = f"Video: {result.filename}, Länge {result.duration_sec:.1f}s."
    if mode == "hybrid":
        return (
            "Du erhältst das VIDEO direkt — sieh es dir WIRKLICH an (Bild und Ton) und bewerte aus dem, "
            "was du siehst und hörst.\n"
            "WICHTIG, abweichend vom System-Prompt oben: Der System-Prompt ist für einen Modus geschrieben, "
            "in dem der Bewerter das Video NICHT sieht. Das gilt hier nicht. Ignoriere daher: (a) den Satz "
            "„Du hast das Video nie gesehen\"; (b) Verweise auf eine vorgefertigte Szenenliste, Bild-Fakten "
            "oder einen separaten „dedizierten Blick-Pass\" — die gibt es hier nicht; (c) Warnungen vor "
            "„Gemma-OCR-Fehlern\" — du liest Bildtext selbst direkt ab.\n"
            "NUTZE aktiv deinen visuellen Vorteil (das ist der Mehrwert): beurteile Blickrichtung (in die "
            "Linse vs. Ablesen nach unten/zur Seite), statische Text-Overlays vs. mitlaufende Untertitel, "
            "Schnitt/Pacing, Effekte/Zooms und Mimik aus dem bewegten Bild selbst.\n"
            "Zusätzlich liegen deterministisch gemessene Werte vor; NUTZE sie für die quantitativen Urteile "
            "(Sprechtempo, Füllwörter, Lautheit) — bei diesen Zahlen sind sie verlässlicher als dein Seheindruck.\n\n"
            "TIEFE & KONKRETHEIT (Pflicht für top_tipps und alle Begründungen):\n"
            "- Bleib NICHT bei reiner Editing-Mechanik (schneiden, zoomen, Pausen kürzen). Mindestens EIN "
            "top_tipp muss den INHALT/das Skript selbst verbessern (z. B. einen konkreten emotionalen Moment "
            "vorschlagen statt einer Aufzählung: „statt vier Punkte aufzuzählen einen einzigen schmerzhaften "
            "Moment beschreiben\").\n"
            "- Wenn du eine bessere Formulierung empfiehlst (Sprech-Hook, Text-Overlay, CTA), gib ein "
            "KONKRETES Beispiel in Anführungszeichen, das zum tatsächlichen Thema DIESES Videos passt — "
            "keine generischen Platzhalter wie „Wie ich es geschafft habe\".\n"
            "- Jede Begründung und jeder Tipp nennt das WARUM (die Wirkung beim Zuschauer), nicht nur das WAS.\n\n"
            f"{head}\n\n"
            f"TRANSKRIPT (Whisper, verlässlicher Wortlaut):\n{result.transcript or '(leer)'}\n\n"
            f"SPRACHSTATISTIK: {_stats_txt(result)}\n\n"
            f"MESSWERTE (intern, NICHT im Output nennen):\n{_metrics_txt(result)}"
        )
    # pure
    return (
        "Du erhältst AUSSCHLIESSLICH das VIDEO. Analysiere Bild und Ton vollständig selbst — "
        "Transkript/Wortlaut, Hook, Struktur, Sprechqualität inkl. Sprechtempo und Füllwörtern, "
        "Schnitt/Pacing, Spannungsbogen, Blickkontakt, visuelle Ästhetik und Lautheit — und bewerte "
        "es nach den obigen Kriterien. Es liegen KEINE externen Messwerte vor: leite alle Urteile "
        "allein aus dem Video ab.\n\n"
        f"{head}"
    )


def _evaluate(video_path: Path, result: AnalystResult, mode: str) -> AnalystEvaluationV2:
    video_file = gemini_service._upload_video_to_gemini(video_path)
    cfg = types.GenerateContentConfig(
        system_instruction=analyst_eval.build_system_prompt(),  # exakt der Claude-Bewertungsprompt
        response_mime_type="application/json",
        temperature=0.0,
    )
    raw = (_generate([video_file, _user_message(result, mode)], cfg, f"analyst_eval_{mode}").text or "")
    return AnalystEvaluationV2(**analyst_eval._extract_json(raw))


def evaluate_pure(video_path: Path, result: AnalystResult) -> AnalystEvaluationV2:
    return _evaluate(video_path, result, "pure")


def evaluate_hybrid(video_path: Path, result: AnalystResult) -> AnalystEvaluationV2:
    return _evaluate(video_path, result, "hybrid")
