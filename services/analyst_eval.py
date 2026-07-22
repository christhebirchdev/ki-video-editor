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
from models.analyst import ActionStep, AnalystEvaluationV2, AnalystResult, Empfehlung
from services import analyst_prompt_log

SKILL_PATH = Path(__file__).with_name("analyst_eval_skill.md")
# Separat gepflegte Referenz (kompakte Pipeline-Fassung: Prinzipien + Beispiel-Anker). Wird vom
# Skill referenziert und hier zur Laufzeit an den System-Prompt angehängt (das Pipeline-Claude hat
# keinen Dateizugriff). Optional: fehlt die Datei, läuft die Bewertung unverändert weiter.
# Ausführliche Fassung (volle Experten-Zitate + Musteranalysen) liegt separat als Trainingsdoc.
REFERENCE_PATH = SKILL_PATH.with_name("analyst_video_reference.md")

# Heuristische Richtwerte zur internen Messwert-Interpretation (nur Urteilsgrundlage,
# Zahlen dürfen laut Skill NICHT im Output erscheinen).
from services.analyst_quality import (
    LOUDNESS_OPTIMAL_HIGH, LOUDNESS_OPTIMAL_LOW, LOUDNESS_TOO_QUIET,
)

METRICS_GUIDE = (
    "Richtwerte (intern, Frames max 640px): Schärfe (Laplacian-Varianz) <50 unscharf, "
    "50–150 mäßig, >300 knackig. Helligkeit (0–255) Ziel ~90–160. Kontrast (Std) <30 flau. "
    f"Lautheit: optimaler Bereich {LOUDNESS_OPTIMAL_LOW} bis {LOUDNESS_OPTIMAL_HIGH} LUFS "
    f"(empirisch festgelegt). Innerhalb DIESES Bereichs ODER lauter (höherer LUFS-Wert, also näher an 0) "
    f"ist der Ton NICHT zu leise — dann keinen „zu leise\"-Hinweis geben und keinen Abzug. Erst deutlich "
    f"unter {LOUDNESS_TOO_QUIET} LUFS ist der Ton wirklich zu leise. True Peak > -1 dBFS = Clipping-Gefahr."
)


TOP_ACTION_STEPS = 3


def _normtext(s: str) -> str:
    """Anweisung auf Kern normalisieren, damit „Sprechpause rausschneiden" an drei Stellen als
    dieselbe Handlung erkannt wird, „Gehirn-Symbol" und „Telefon-Symbol" aber nicht."""
    return " ".join((s or "").lower().split())


def _zeit_label(sekunden: list[float]) -> str:
    """„ca. Sek. 3" bzw. „ca. Sek. 3, 15 und 24" — Zeitangaben sind Richtwerte (±1–2 s)."""
    s = [str(int(round(x))) for x in sekunden]
    return f"ca. Sek. {s[0]}" if len(s) == 1 else f"ca. Sek. {', '.join(s[:-1])} und {s[-1]}"


def verteile_empfehlungen(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Bündeln → sortieren → Top-3 abtrennen. Deterministisch im Code statt per Prompt-Regel.

    Das Modell liefert eine flache `empfehlungen`-Liste und entscheidet nur, WELCHE Einträge
    dieselbe Handlung sind (Feld `gruppe`) — das ist Urteil. Der Rest ist Arithmetik und stand
    vorher 3× fast wortgleich im Prompt, ohne sicher zu greifen (Befund 3):
    „sortiere nach Zeitpunkt, nimm die 3 frühesten, der Rest nach weitere_empfehlungen".

    Liefert das Modell `empfehlungen` nicht (Altlauf/altes Schema), bleiben die geparsten
    action_steps unverändert stehen.
    """
    if not parsed.empfehlungen:
        return parsed
    gruppen: dict[str, list[Empfehlung]] = {}
    for i, e in enumerate(parsed.empfehlungen):
        # Gebündelt wird nur, wenn Label UND Anweisung übereinstimmen. Das Label allein reicht NICHT:
        # das Modell nutzt `gruppe` sonst als KATEGORIE ("alles Einblendungen") und wirft verschiedene
        # Handlungen zusammen — real passiert (Lauf 702f9c11): Gehirn-Symbol @18s, Telefon @28s und
        # Folgen-Knopf @41s wurden zu „Gehirn-Symbol @18,28,41" verschmolzen. Untermerge ist harmlos
        # (zwei ähnliche Schritte), Fehlmerge zeigt dem Nutzer aktiv die falsche Handlung.
        label = (e.gruppe or "").strip().lower()
        key = f"{label}\x00{_normtext(e.anweisung)}" if label else f"\x00einzeln{i}"
        gruppen.setdefault(key, []).append(e)

    schritte: list[tuple[float, ActionStep]] = []
    for eintraege in gruppen.values():
        eintraege.sort(key=lambda e: e.zeitpunkt_sek)
        schritte.append((
            eintraege[0].zeitpunkt_sek,  # eine Gruppe zählt ab ihrem FRÜHESTEN Vorkommen
            ActionStep(
                zeitpunkt=_zeit_label([e.zeitpunkt_sek for e in eintraege]),
                anweisung=eintraege[0].anweisung,
            ),
        ))
    schritte.sort(key=lambda t: t[0])
    parsed.action_steps = [s for _, s in schritte[:TOP_ACTION_STEPS]]
    parsed.weitere_empfehlungen = [s for _, s in schritte[TOP_ACTION_STEPS:]]
    return parsed


FREMD_TEXTHOOK_HINWEIS = (
    "Der sichtbare Bildschirmtext gehört zum reagierten Fremdvideo, nicht zu dir — er zählt nicht als "
    "deine Texthook. Dir fehlt eine EIGENE statische Texthook. Erstelle mindestens 3 Varianten und teste "
    "sie über die Testreel-Funktion von Instagram gegeneinander."
)


def bereinige_fremd_texthook(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Reaction + leeres „Geplante Texthook"-Feld → der sichtbare Bildschirmtext gehört zum reagierten
    Fremdvideo, nicht zum Protagonisten → Score hart auf 0.

    Warum im Code und an DIESER Bedingung: Gemini unterscheidet einen in den reagierten Clip
    eingebrannten Text visuell NICHT von einem eigenen Overlay — es bewertete ihn über 5 Läufe stabil
    als eigene Texthook (Score 3), auch mit explizitem Prompt-Hinweis und Urteilsfeld. Der Nutzer weiß
    es dagegen sicher: hat er eine eigene Texthook, trägt er sie ins Feld ein; ist das Feld bei einer
    Reaction leer, ist der einzige Text der des Fremdvideos.
    Fällt das Feld aus (eigene Texthook eingetragen), greift die normale Bewertung dieses Textes."""
    ist_reaction = (getattr(result, "gewaehltes_format", "") or "").strip().lower() == "reaction"
    hat_eigene = bool((getattr(result, "geplante_texthook", "") or "").strip())
    if ist_reaction and not hat_eigene:
        parsed.hook.text_hook_vorhanden = False
        parsed.hook.text_hook_score = 0
        parsed.hook.text_hook_grund = FREMD_TEXTHOOK_HINWEIS
    return parsed


def erzwinge_nutzer_format(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Die Format-Auswahl des Nutzers ist bindend — der Prompt bittet darum, hier wird es garantiert.
    Ohne Auswahl (Altlauf) bleibt das Modell-Urteil stehen."""
    if gewaehlt := (getattr(result, "gewaehltes_format", "") or "").strip():
        parsed.format = gewaehlt
    return parsed


def pausen_txt(stats) -> str:
    """Pausen MIT Position rendern. Ohne Position kann das Modell eine gemessene Dauer
    keiner Stelle zuordnen — es fusioniert dann Zahl und Ort zu einer erfundenen Behauptung."""
    pausen = getattr(stats, "pausen", None) or []
    if not pausen:
        return "keine Pausen >0.5s gemessen"
    return " | ".join(f"{p.dauer_sec}s @ {p.start_sec}–{p.end_sec}s" for p in pausen)

OUTPUT_SCHEMA = """Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, exakt diese Felder, nichts davor/danach:
{
  "zielgruppe": "<genau 1 Satz: wer angesprochen wird>",
  "format": "<übernimm das vorgegebene Format aus der Aufgabe unverändert>",
  "protagonist_ab_sek": <float: ab welcher Sekunde der Protagonist seinen ersten INHALTLICHEN Satz beginnt — den, mit dem er anfängt zu ERKLÄREN/zu reden. NICHT seine erste Lautäußerung: mitreagierende Rufe („Ja!", „BÄM!"), Lacher oder das Mitsprechen zum Fremdvideo zählen NICHT. Spricht er von Beginn an inhaltlich: sein erstes Wort. Bei Reaction: erst wenn das Fremdvideo endet UND er zu reden anfängt (die Übergangspause davor gehört noch NICHT zu ihm).>,
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
  "empfehlungen": [{"zeitpunkt_sek": <float: die Sekunde im Video, auf die sich die Handlung bezieht — Richtwert, ±1–2 s>, "anweisung": "<EINE konkrete Handlung in SUPER EINFACHER Sprache, kein Fachjargon; wenn eine Einblendung: sag ob VOLLBILD oder KLEINE Einblendung im laufenden Bild, z.B. Woosh-Sound für 2s einfügen / kleine Einblendung mit Foto vom Hof / hier schneiden>", "gruppe": "<Label NUR für die WÖRTLICH GLEICHE Handlung an mehreren Stellen — z.B. dieselbe Sprechpause bei Sek. 3, 15, 24. Dann allen Einträgen dasselbe Label UND denselben anweisung-Text geben; sie werden zu EINEM Schritt. KEINE Kategorie: verschiedene Einblendungen (Gehirn-Symbol vs. Telefon vs. Folgen-Knopf) sind NICHT dieselbe Handlung → jeweils EIGENES Label, auch wenn alle 'Einblendung' sind. Im Zweifel eigenes Label.>"}]
}

REGELN empfehlungen: EINE flache Liste mit ALLEN Empfehlungen (3–10), in beliebiger Reihenfolge.
Sortieren, Auswählen und Zusammenfassen übernimmt das System — mach das NICHT selbst und teile die
Liste nicht auf. Deine einzige Aufgabe dabei: `zeitpunkt_sek` als Zahl setzen und über `gruppe` sagen,
welche Einträge dieselbe Handlung an verschiedenen Stellen sind.
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
        f"{stats.pausen_count} Pausen >0.5s, Sprechbeginn bei {getattr(stats, 'sprechbeginn_sec', 0.0)}s\n"
        f"PAUSEN (Position im Video): {pausen_txt(stats)}"
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


def evaluate(result: AnalystResult, run_dir=None) -> AnalystEvaluationV2:
    """Schlanke V2-Bewertung. Bekommt nur Text — das Video bleibt lokal.

    run_dir (optional): Ordner des Laufs; wenn gesetzt, wird der komplette Call
    (Input/Prompt/Output) nach prompt_log.md geschrieben.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    system = build_system_prompt()
    user = build_user_message(result)
    # Hinweis: KEIN temperature-Parameter (bei Sonnet 4.6 deprecated → 400 Bad Request)
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=2200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text
    parsed = verteile_empfehlungen(
        bereinige_fremd_texthook(
            erzwinge_nutzer_format(AnalystEvaluationV2(**_extract_json(raw)), result), result
        )
    )
    analyst_prompt_log.log_call(
        run_dir, call="eval_v1", recipient="Claude", model=settings.claude_model,
        system_prompt=system, user_message=user, output_raw=raw, output_parsed=parsed,
        inputs={
            "engine": "v1", "filename": result.filename,
            "duration_sec": result.duration_sec, "scene_count": result.scene_count,
        },
    )
    return parsed
