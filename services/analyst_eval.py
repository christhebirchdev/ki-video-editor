# services/analyst_eval.py
"""
V2-Bewertungs-Modul für den AI Video Analyst.

Single Source of Truth der Bewertungslogik ist die Markdown-Datei
`analyst_eval_skill.md` (Mensch-lesbar, versioniert, ohne Python editierbar).
Dieses Modul lädt den Skill-Body und hängt den strikten JSON-Output-Vertrag an,
bevor es ihn als System-Prompt an die Claude-API gibt.

Der Schema-Block lebt bewusst HIER (nicht in der .md): Im interaktiven Cowork-
Gebrauch will man Fließtext, in der Pipeline striktes JSON — derselbe Skill-Body,
zwei Modi.
"""
import json
import re
from pathlib import Path

import anthropic

from config import settings
from models.analyst import AnalystEvaluationV2, AnalystResult

SKILL_PATH = Path(__file__).with_name("analyst_eval_skill.md")
# Separat gepflegte Referenz (kompakte Pipeline-Fassung: Prinzipien + Beispiel-Anker). Wird vom
# Skill referenziert und hier zur Laufzeit an den System-Prompt angehängt (das Pipeline-Claude hat
# keinen Dateizugriff). Optional: fehlt die Datei, läuft die Bewertung unverändert weiter.
# Ausführliche Fassung (volle Experten-Zitate + Musteranalysen) liegt separat als Trainingsdoc.
REFERENCE_PATH = SKILL_PATH.with_name("analyst_video_reference.md")

# Heuristische Richtwerte zur internen Messwert-Interpretation (nur Urteilsgrundlage,
# Zahlen dürfen laut Skill NICHT im Output erscheinen).
METRICS_GUIDE = (
    "Richtwerte (intern, Frames max 640px): Schärfe (Laplacian-Varianz) <50 unscharf, "
    "50–150 mäßig, >300 knackig. Helligkeit (0–255) Ziel ~90–160. Kontrast (Std) <30 flau. "
    "Lautheit Reels-Richtwert ~ -14 LUFS; unter -20 LUFS zu leise. True Peak > -1 dBFS = Clipping-Gefahr."
)

OUTPUT_SCHEMA = """Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, exakt diese Felder, nichts davor/danach:
{
  "zielgruppe": "<genau 1 Satz: wer angesprochen wird>",
  "format": "<Talking-Head | B-Roll/Voiceover | Sketch | Tutorial | Vlog | Sonstiges>",
  "performance_score": <int 0-100>,
  "funnel": "<TOFU | MOFU | BOFU | Mischung>",
  "hook": {
    "sprech_hook_score": <int 1-5>,
    "sprech_hook_grund": "<1-2 Sätze>",
    "text_hook_vorhanden": <true|false>,
    "text_hook_score": <int 0-5; 0 wenn keine statische Text-Hook vorhanden (nur Untertitel zählen nicht)>,
    "text_hook_grund": "<1-2 Sätze; bei score 0 die Ansage + Tipp (3 Varianten über Instagram-Testreel testen)>"
  },
  "struktur": {
    "score": <int 1-5>,
    "elemente": {"hook": <bool>, "bridge": <bool>, "mid": <bool>, "peak": <bool>, "cta": <bool>},
    "kommentar": "<1-2 Sätze>"
  },
  "sprechqualitaet": {"score": <int 1-5>, "probleme": ["<nur stark Auffälliges, je 1-2 Sätze, sonst []>"]},
  "schnitt_pacing": {"score": <int 1-5>, "kommentar": "<1-2 Sätze, format-bewusst>"},
  "spannungsbogen": {"score": <int 1-5>, "kommentar": "<1-2 Sätze>"},
  "visuelle_aesthetik": {"score": <int 1-5>, "probleme": ["<nur Auffälliges, je 1-2 Sätze, sonst []>"]},
  "staerken": ["<1-3 konkrete positive Aspekte, was schon gut funktioniert, in einfacher ermutigender Sprache>"],
  "top_tipps": ["<3-5 wichtigste Hebel, je 1-2 Sätze, nach Wirkung priorisiert>"],
  "action_steps": [{"zeitpunkt": "<z.B. 0:03 oder 'ca. Sek. 3'>", "anweisung": "<EINE konkrete Handlung in SUPER EINFACHER Sprache, kein Fachjargon; wenn eine Einblendung: sag ob VOLLBILD oder KLEINE Einblendung im laufenden Bild, z.B. Woosh-Sound für 2s einfügen / kleine Einblendung mit Foto vom Hof / hier schneiden>"}],
  "weitere_empfehlungen": [{"zeitpunkt": "<z.B. 0:20>", "anweisung": "<zusätzliche, ausführlichere Handlungsempfehlung für Nutzer, die tiefer optimieren wollen>"}]
}

REGELN action_steps: GENAU MAXIMAL 3 Stück — die Empfehlungen, die am WEITESTEN VORNE im Video ansetzen.
Sortiere ALLE gefundenen Empfehlungen nach ihrem Zeitpunkt (kleinste Sekunde zuerst) und nimm die 3 FRÜHESTEN.
Der Anfang (Texthook/Sprechhook/erste Sekunden, erstes Drittel) hat IMMER Vorrang. Eine Empfehlung, die einen
SPÄTEREN Teil des Videos betrifft (Mitte oder hintere Hälfte), gehört NICHT in die Top 3, sondern in
weitere_empfehlungen — auch wenn sie wirkungsvoll ist. Beispiel: eine Texthook-Verbesserung (Sek. 0) MUSS in
die Top 3; eine Sprechpause bei Sek. 30 gehört in weitere_empfehlungen. Lieber wenige, klare Schritte.
REGELN weitere_empfehlungen: alle übrigen Empfehlungen — VOR ALLEM die, die SPÄTER im Video ansetzen (Mitte/
hintere Hälfte) —, ausführlicher (0–7 Stück), für Nutzer, die tiefer optimieren wollen.
staerken: nenne echte positive Aspekte (nicht schönreden) — sie kommen im Ergebnis zuerst."""


def load_skill_body() -> str:
    """Liest den Skill-Body und entfernt das YAML-Frontmatter."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def load_reference() -> str:
    """Liest die separat gepflegte Referenz (kompakte Pipeline-Fassung: Prinzipien + Beispiel-Anker).
    Wird als zusätzliche Urteilsgrundlage an den System-Prompt angehängt; ändert NICHT den
    Output-Vertrag. Fehlt die Datei, wird sie still übersprungen."""
    if not REFERENCE_PATH.exists():
        return ""
    return REFERENCE_PATH.read_text(encoding="utf-8").strip()


def build_system_prompt() -> str:
    """Skill-Body (Logik) + optionale Editing-Referenz + strikter JSON-Vertrag (Pipeline-Modus)."""
    parts = [load_skill_body()]
    ref = load_reference()
    if ref:
        parts.append(
            "--- ANGEHÄNGTE REFERENZ: VIDEO-ANALYSE (EDITING + SKRIPT + TECHNIK/AUFTRETEN) ---\n"
            "Zusätzliche Urteilsgrundlage für hook, struktur, spannungsbogen, schnitt_pacing, sprechqualitaet, visuelle_aesthetik und top_tipps. "
            "KEIN Ausgabe-Template — der Output bleibt strikt knapp + JSON wie unten definiert.\n\n"
            + ref
        )
    parts.append(OUTPUT_SCHEMA)
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict:
    """Holt das erste vollständige JSON-Objekt aus der Antwort.

    Nutzt raw_decode ab der ersten '{' → dekodiert genau EIN Objekt und ignoriert alles
    danach. Das ist robust gegen „Extra data" (LLM hängt nach dem JSON noch Text/ein zweites
    Objekt/Markdown-Fences an — kommt bei Gemini trotz JSON-Modus und bei Fallback-Modellen vor).
    """
    start = text.find("{")
    if start == -1:
        raise ValueError(f"Kein JSON in Antwort: {text[:200]}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        # Fallback: gieriger Match (falls vor der ersten '{' Störzeichen den Offset verschieben).
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def build_user_message(result: AnalystResult) -> str:
    """Baut die User-Message. Hook-Kandidaten werden explizit markiert."""
    def _scene_line(s) -> str:
        tag = " [ERÖFFNUNG]" if s.index == 0 else ""
        kern = s.handlung or s.beschreibung or "(keine Beschreibung)"
        extra = ""
        if s.personen:
            extra += f" | Person/Blick: {s.personen}"
        for t in (s.texte or []):
            d = f" ({t.darstellung})" if t.darstellung else ""
            extra += f" | Text: „{t.wortlaut}\"{d}"
        if s.gesprochener_text:
            extra += f" | Gesprochen (Whisper, verlässlicher Wortlaut): {s.gesprochener_text}"
        if s.kamera:
            extra += f" | Kamera: {s.kamera}"
        if s.bild_fakten:
            bf = s.bild_fakten
            extra += f" | Bild: {bf.komposition}, Licht {bf.licht}, Hintergrund {bf.hintergrund}"
        if s.effekte:
            extra += f" | Effekte: {s.effekte}"
        return f"Segment {s.index + 1} ({s.start:.1f}–{s.end:.1f}s){tag}: {kern}{extra}"

    scenes_txt = "\n".join(_scene_line(s) for s in result.scenes) or "(keine Szenen erkannt)"

    opening = result.scenes[0] if result.scenes else None
    text_hook = (opening.text_overlays if opening and opening.text_overlays else "") or "(keins erkannt)"
    first_words = (result.transcript or "").strip()[:160] or "(keine Sprache)"

    # Blickkontakt: der dedizierte Whole-Video-Pass (gaze_overview) ist die VERLÄSSLICHE Quelle —
    # Standbilder verraten den Blick nicht zuverlässig (Gemini produziert im Frame-Batch Boilerplate
    # wie "blickt direkt in die Linse"). Per-Szene-personen nur noch als Fallback, wenn der Pass leer ist.
    if result.gaze_overview:
        blick_txt = result.gaze_overview
    else:
        _away_keys = ("nach unten", "runter", "zur seite", "seitlich", "abgelesen", "blickt weg", "liest", "weg von")
        _gaze_away = sum(1 for s in result.scenes if any(k in (s.personen or "").lower() for k in _away_keys))
        blick_txt = (
            f"In {_gaze_away} von {len(result.scenes)} Szenen ist der Blick NICHT in die Kamera gerichtet "
            f"(nach unten/zur Seite) — Indiz für Ablesen/Skript; Authentizität & Sicherheit im Auftreten bewerten."
            if _gaze_away else "Blick überwiegend in die Kamera (Hinweis: nur grobe Szenen-Schätzung, kein dedizierter Blick-Pass)."
        )

    stats = result.speech_stats
    stats_txt = (
        f"{stats.wort_anzahl} Wörter, {stats.wpm} WPM, {stats.filler_count} Füllwörter, "
        f"{stats.pausen_count} Pausen >0.5s (längste {stats.laengste_pause_sec}s), "
        f"Sprechbeginn bei {getattr(stats, 'sprechbeginn_sec', 0.0)}s"
        if stats else "Keine Sprache erkannt."
    )

    qm = result.quality_metrics
    if qm:
        audio_txt = (
            f"Lautheit {qm.lufs_integrated} LUFS, Loudness Range {qm.loudness_range} LU, "
            f"True Peak {qm.true_peak_db} dBFS"
            if qm.lufs_integrated is not None else "kein Audio messbar"
        )
        metrics_txt = (
            f"Bild: Schärfe avg {qm.schaerfe_avg} (min {qm.schaerfe_min}), "
            f"Helligkeit avg {qm.helligkeit_avg}, Kontrast avg {qm.kontrast_avg}. "
            f"Audio: {audio_txt}.\n{METRICS_GUIDE}"
        )
    else:
        metrics_txt = "Keine Messwerte verfügbar — Videoqualität nur grob aus Bild-Fakten ableiten."

    return (
        f"Video: {result.filename}, Länge {result.duration_sec:.1f}s, {result.scene_count} Szenen.\n\n"
        f"SEGMENTE (visuell):\n{scenes_txt}\n\n"
        f"AUDIO (ganzes Video): {result.audio_overview or '(keine Audio-Beschreibung)'}\n\n"
        f"SPRECH-HOOK-KANDIDAT (erste Worte): {first_words}\n"
        f"TEXT-HOOK-KANDIDAT (Overlay der Eröffnung): {text_hook}\n\n"
        f"BLICKKONTAKT (ganzes Video, dedizierter Gemini-Pass): {blick_txt}\n\n"
        f"TRANSKRIPT:\n{result.transcript or '(leer)'}\n\n"
        f"SPRACHSTATISTIK: {stats_txt}\n\n"
        f"MESSWERTE (intern, NICHT im Output nennen):\n{metrics_txt}"
    )


def evaluate(result: AnalystResult) -> AnalystEvaluationV2:
    """Schlanke V2-Bewertung. Bekommt nur Text — das Video bleibt lokal."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    # Hinweis: KEIN temperature-Parameter (bei Sonnet 4.6 deprecated → 400 Bad Request)
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=2200,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": build_user_message(result)}],
    )
    return AnalystEvaluationV2(**_extract_json(msg.content[0].text))
