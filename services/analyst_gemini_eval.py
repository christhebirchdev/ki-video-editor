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

from config import settings
from models.analyst import AnalystEvaluationV2, AnalystResult
from services import analyst_eval, analyst_prompt_log, analyst_vlm, gemini_service
from services.analyst_vlm import _generate  # generate_content mit Modell-Fallback


def _metrics_txt(result: AnalystResult) -> str:
    # Eine Stelle für die Entscheidung „sind Bildwerte echte Messungen?" — v2 zieht keine Frames,
    # dort stehen Schärfe/Helligkeit/Kontrast auf 0.0. Die als Fakt zu senden hat im Lauf c12db030
    # die Bildkritik komplett unterdrückt (siehe analyst_eval.bildwerte_belastbar).
    return analyst_eval.metrics_txt(result.quality_metrics)


def _format_instruction(result: AnalystResult) -> str:
    """Format = Nutzerangabe (bindend). Protagonist-Erkennung = Modellaufgabe.

    Klare Arbeitsteilung: Der Nutzer weiß, was für ein Video er gedreht hat — seine Angabe ist
    verlässlicher als jede Modell-Klassifikation. WO im Video der Protagonist einsetzt, weiß er
    nicht auf die Sekunde — das Modell sieht und hört das Video und kann es bestimmen.

    Hintergrund (Run a2808a62): Ohne Format-Angabe hat das Modell ein Reaction-Video als
    „Talking-Head" klassifiziert und das Audio des eingeblendeten Fremdvideos als Sprech-Hook des
    Protagonisten bewertet. Die gesamte Bewertung hing an diesem einen Fehler.
    """
    gewaehlt = (getattr(result, "gewaehltes_format", "") or "").strip()
    if not gewaehlt:
        # Altlauf vor der Pflicht-Auswahl: Modell klassifiziert selbst (wie vor Option C).
        return (
            "FORMAT: Es liegt keine Nutzerangabe vor — bestimme das Format selbst aus dem Video "
            "und trag es in das Feld format ein.\n"
            "PROTAGONIST: Prüfe, ob das Audio am Anfang wirklich vom Protagonisten stammt oder aus "
            "einem eingeblendeten Fremdvideo/Einspieler. Trag den Zeitpunkt, ab dem er selbst "
            "spricht, in protagonist_ab_sek ein.\n"
        )
    txt = (
        f"FORMAT (vom Nutzer vor der Analyse angegeben — das ist ein FAKT, nicht deine Einschätzung): "
        f"„{gewaehlt}“. Übernimm es unverändert in das Feld format und bewerte Schnitt, Pacing, Struktur "
        f"und Spannungsbogen gegen GENAU dieses Format. Widersprich der Angabe nicht — der Nutzer kennt "
        f"sein Video.\n"
        "PROTAGONIST (deine Aufgabe — du siehst und hörst das Video): Bestimme, ab welcher Sekunde der "
        "Protagonist SELBST spricht, und trag sie in protagonist_ab_sek ein.\n"
    )
    if gewaehlt == "Reaction":
        txt += (
            "ACHTUNG Reaction — das ist der wichtigste Punkt dieser Analyse: Ein Teil des Tons stammt "
            "aus dem eingeblendeten FREMDVIDEO, nicht vom Protagonisten. Das Transkript unten mischt "
            "beide Quellen zu einem Text und markiert NICHT, wer spricht — verlass dich hier auf Bild "
            "und Ton, nicht auf das Transkript.\n"
            "Daraus folgt zwingend:\n"
            "- Der Sprech-Hook ist der erste Satz, den der PROTAGONIST sagt — NICHT der erste Satz im "
            "Transkript. Bewerte niemals fremde Worte als seinen Hook.\n"
            "- Text-Overlays im reagierten FREMDVIDEO sind NICHT die Texthook des Protagonisten. Als seine "
            "Texthook gilt nur statischer Text, den er selbst über sein eigenes Bild legt. (Ob eine eigene "
            "Texthook existiert, steuert der Nutzer separat — bewerte den Fremdvideo-Text hier nicht als "
            "seinen.)\n"
            "- „Sprechbeginn“ in der Sprachstatistik misst das Fremdvideo, wenn es zuerst läuft. Leite "
            "daraus KEINEN verzögerten Hook und keinen „Anlauf wegschneiden“-Tipp ab.\n"
            "- Sprechtempo/WPM mischt beide Sprecher und ist damit für die Sprechqualität des "
            "Protagonisten unbrauchbar — urteile hier nach Gehör.\n"
            "- Dass der Protagonist am Anfang schweigt, während das Fremdvideo läuft, ist FORMATTYPISCH "
            "und KEIN Fehler.\n"
            "- Die ÜBERGANGSPAUSE — der Moment, in dem das Fremdvideo endet und der Protagonist zu seinem "
            "ersten inhaltlichen Satz ansetzt — gehört zum Format und bleibt DRIN. Empfiehl sie NICHT zum "
            "Rausschneiden und behandle sie NICHT als „Anlauf“, „Durchatmen vor dem ersten Wort“ oder "
            "„verzögerten Hook“. Genau diese Pause trägt den Wechsel; sie wegzuschneiden zerstört den "
            "Reaction-Rhythmus. Das gilt für JEDE Pause um protagonist_ab_sek herum (±2 s).\n"
            "- Ist der Einstieg des Fremdvideos laut, schrill oder lustig, ist das eine Eigenschaft des "
            "ZITIERTEN Materials. Es darf in staerken/probleme auftauchen, aber nicht als Leistung oder "
            "Schwäche des Protagonisten.\n"
            "- BLICK: Dass er auf Laptop, Handy oder einen zweiten Bildschirm schaut, ist im Reaction-Format "
            "FUNKTIONAL — dort läuft das Video, auf das er reagiert. Das ist KEIN Ablesen und KEIN Mangel: "
            "blickkontakt.urteil bleibt `in_der_linse`, keine Empfehlung dazu. Die unten stehende "
            "Blickkontakt-Pflicht gilt hier NUR für den Fall, dass er erkennbar einen Text abliest (Augen "
            "wandern zeilenweise, ohne Bezug zum eingeblendeten Video) — dann `abgelesen`.\n"
        )
    return txt


def _texthook_instruction(result: AnalystResult) -> str:
    """Texthook-Logik je nach Freifeld: ausgefüllt = geplante Texthook bewerten; leer = im Video erwartet."""
    gth = (getattr(result, "geplante_texthook", "") or "").strip()
    if gth:
        return (
            f"GEPLANTE TEXTHOOK (vom Nutzer VOR der Analyse eingetragen): „{gth}“. "
            "Diese Texthook ist geplant und wird evtl. erst nachträglich ins Video eingefügt — sie ist im Video "
            "vielleicht noch NICHT zu sehen. Behandle sie TROTZDEM als die vorhandene Texthook: setze "
            "text_hook_vorhanden=true und bewerte GENAU DIESEN eingetragenen Text als Texthook (Länge, Neugier, "
            "Zielgruppe). Geh NICHT davon aus, dass keine Texthook existiert.\n"
        )
    return (
        "Es wurde KEINE geplante Texthook eingetragen → die Texthook soll bereits IM VIDEO sichtbar sein. Ist "
        "im Video keine statische Texthook zu sehen (mitlaufende Untertitel zählen NICHT), ist das ein Fehler: "
        "text_hook_vorhanden=false, text_hook_score=0, und weise klar darauf hin, dass eine Texthook nötig ist.\n"
    )


def _user_message(result: AnalystResult, mode: str) -> str:
    head = f"Video: {result.filename}, Länge {result.duration_sec:.1f}s."
    if mode == "hybrid":
        return (
            "Du erhältst das VIDEO direkt — sieh es dir WIRKLICH an (Bild und Ton) und bewerte aus dem, "
            "was du siehst und hörst.\n"
            # Hier stand bis 2026-07-31 ein Widerruf-Block, der bei JEDEM Lauf die Prämisse des
            # System-Prompts zurücknahm („Du hast das Video nie gesehen", Szenenliste, Bild-Fakten,
            # Gemma-OCR). Seit V1 abgeschafft ist, ist der System-Prompt für DIESEN Modus geschrieben
            # — es gibt nichts zu widerrufen. Ein Prompt, der sich selbst korrigiert, schwächt jede
            # Regel darin: Das Modell muss bei jeder Aussage mitentscheiden, ob sie noch gilt.
            "NUTZE aktiv deinen visuellen Vorteil (das ist der Mehrwert): beurteile Blickrichtung (in die "
            "Linse vs. Ablesen nach unten/zur Seite), Bildtext, Schnitt/Pacing, Effekte/Zooms und Mimik "
            "aus dem bewegten Bild selbst.\n"
            "BILDTEXT: Du liest jeden Bildtext direkt ab — gleiche ihn mit dem TRANSKRIPT unten ab. "
            "Wortgleichheit allein macht daraus aber KEINE Untertitel. Dafür müssen BEIDE Merkmale "
            "zutreffen: (1) wortgleich zum Gesprochenen UND (2) über das Video hinweg kommen laufend neue "
            "Blöcke. Ein einzelner Textblock, der durchgehend an derselben Position steht oder nach einer "
            "Weile verschwindet und nicht wiederkommt, ist eine TEXTHOOK — auch wenn er mitgesprochen wird. "
            "Dann ist er nicht „nicht vorhanden“, sondern redundant. Details in der Regel „UNTERTITEL sind "
            "KEIN Text-Hook“ im System-Prompt.\n"
            + _format_instruction(result) +
            "PFLICHT Untertitel: Laufen Untertitel mit, beurteile sie IMMER — Wörter pro Block und "
            "Rhythmus, Regel im System-Prompt unter Schnitt & Pacing. Sind die Blöcke zu lang oder "
            "zu statisch, MUSS das in den `untertitel`-Block. Laufen KEINE mit, obwohl gesprochen wird, "
            "ist das ein Mangel — setz `untertitel.vorhanden=false`, den Schritt baut das System.\n"
            "PFLICHT Bildaufbau und Bildqualität — PRÜFEN heißt nicht KRITISIEREN: Beurteile IMMER "
            "(a) den KOPFRAUM (Regel im System-Prompt) und (b) die technische BILDQUALITÄT aus dem "
            "bewegten Bild. Ist es in Ordnung, SAG DAS als Stärke und zieh keinen Abzug — es gibt "
            "einen Toleranzbereich, und nicht jedes Video muss ein Studio sein. Nur was einem "
            "Zuschauer beim ersten Sehen sofort auffällt, gehört als DEUTLICHER Mangel in "
            "visuelle_aesthetik.probleme; Kleinigkeiten gehören in visuelle_aesthetik.hinweise und "
            "senken den Score NICHT.\n"
            "PFLICHT Blickkontakt (Ausnahmen im Format-Block oben beachten): Beurteile den Blick IMMER und "
            "trag ihn AUSSCHLIESSLICH in das Feld `blickkontakt` ein (Regel im System-Prompt unter "
            "„Blickkontakt“). NICHT in visuelle_aesthetik, NICHT in sprechqualitaet — der Blick fließt in "
            "keinen Score. Bei `abgelesen` baut das System den Handlungsschritt selbst; schreib dazu keine "
            "eigene Empfehlung, außer du kannst konkrete Sekunden nennen.\n"
            "HOOK-REDUNDANZ-CHECK (Pflicht): Es gilt die Redundanz-Regel aus dem System-Prompt. V2-spezifisch "
            "kommt dazu: Der Sprech-Hook sind die ersten Worte des PROTAGONISTEN ab protagonist_ab_sek — nicht "
            "zwingend der Anfang des Transkripts. Vergleiche gegen den Bildtext der Eröffnung, den du selbst "
            "abliest.\n"
            "HOOK-VERBESSERUNG (nutze dieses Framework, wenn Sprech- oder Text-Hook schwach ist, fehlt oder "
            "redundant): Eine Hook wirkt auf 3 Ebenen — (1) TEXT-HOOK (Bildschirmtext, Länge nach der Regel "
            "„LÄNGE der Text-Hook“ im System-Prompt, für einen 13-Jährigen SOFORT verständlich, kein "
            "Fachwort — greift die, die ohne Ton "
            "scrollen); (2) SPRECH-HOOK (erster gesprochener Satz — muss Neugier wecken ODER einen Pain Point "
            "treffen); (3) REGIE (Energie in der Stimme + ein visueller Bruch der Erwartung, markenkonform). "
            "Eine starke Hook hat: ein krasses/kontroverses Statement, wirkt „wie ein Unfall“ (zwingt zum "
            "Hinsehen) und triggert GENAU die Zielgruppe (sortiert andere bewusst aus — eine Hook für alle "
            "stoppt niemanden). Die Zielgruppe muss NICHT in beiden Ebenen genannt sein. Konkrete Text-Hook-"
            "Varianten gehören ins Feld `texthook_varianten` (Regel im System-Prompt) — nicht in eine "
            "Empfehlung. Empfiehlst du etwas zum gesprochenen Einstieg, nutze dafür das Wort „Sprechhook“; "
            "der Begriff ist unseren Kunden bekannt.\n"
            + _texthook_instruction(result) +
            "AUDIO-QUALITÄT (Pflicht, gut hinhören): Die Messwerte (LUFS) sagen NICHTS über Störgeräusche — das "
            "musst du HÖREN. Achte gezielt auf Hintergrundrauschen, Brummen, Hall oder Übersteuerung. Ist der "
            "Ton verrauscht/unsauber, ist das eine SCHWÄCHE (in sprechqualitaet.probleme benennen) und darf "
            "NICHT als Stärke gelobt werden. Nur wirklich sauberer Ton ist ein Pluspunkt.\n"
            "SPRECHPAUSEN: Die Sprachstatistik unten listet jede Pause MIT Position. Schau dir JEDE dieser "
            "Stellen im Video an und trag dein Urteil in `pausen_urteile` ein (raus / lassen / unklar, Regel "
            "im System-Prompt). Übernimm dabei die `start_sec` unverändert aus der Statistik, damit sich die "
            "Urteile zuordnen lassen. Keine eigene Pausen-Empfehlung schreiben — den Schritt baut das System.\n"
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
            "EMPFEHLUNGEN: Es gilt der Abschnitt „Empfehlungen — die kanonische Regel“ aus dem System-Prompt, "
            "unverändert. V2-spezifisch kommt nur dazu: Du SIEHST das Video — setz `zeitpunkt_sek` auf die "
            "ECHTE Sekunde, an der die Stelle im Bild liegt (z. B. 3.0), nicht auf einen geratenen Wert.\n\n"
            f"{head}\n\n"
            f"TRANSKRIPT (Whisper, verlässlicher Wortlaut):\n{result.transcript or '(leer)'}\n\n"
            f"SPRACHSTATISTIK: {analyst_eval.stats_txt(result.speech_stats)}\n\n"
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


def _evaluate(video_path: Path, result: AnalystResult, mode: str, run_dir=None) -> AnalystEvaluationV2:
    video_file = gemini_service._upload_video_to_gemini(video_path)
    system = analyst_eval.build_system_prompt()  # exakt der Claude-Bewertungsprompt
    user = _user_message(result, mode)
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        temperature=0.0,
    )
    raw = (_generate([video_file, user], cfg, f"analyst_eval_{mode}").text or "")
    parsed = analyst_eval.nachbearbeiten(AnalystEvaluationV2(**analyst_eval._extract_json(raw)), result)
    analyst_prompt_log.log_call(
        run_dir, call=f"eval_{mode}", recipient="Gemini",
        # Das Modell, das TATSÄCHLICH geantwortet hat — nicht „ggf. Fallback". Sonst lässt sich ein
        # abweichender Lauf nicht von einem Fallback-Lauf unterscheiden.
        model=analyst_vlm.letztes_modell or gemini_service.GEMINI_MODEL,
        system_prompt=system, user_message=user, output_raw=raw, output_parsed=parsed,
        attachments=[f"Video: {result.filename}"],
        inputs={
            "engine": f"v2_{mode}", "filename": result.filename,
            "duration_sec": result.duration_sec, "mode": mode,
            "geplante_texthook": getattr(result, "geplante_texthook", ""),
            "gewaehltes_format": getattr(result, "gewaehltes_format", ""),
            "whisper_modell": settings.whisper_model,
            "transkript_hash": getattr(result, "transkript_hash", ""),
        },
    )
    return parsed


def evaluate_pure(video_path: Path, result: AnalystResult, run_dir=None) -> AnalystEvaluationV2:
    return _evaluate(video_path, result, "pure", run_dir)


def evaluate_hybrid(video_path: Path, result: AnalystResult, run_dir=None) -> AnalystEvaluationV2:
    return _evaluate(video_path, result, "hybrid", run_dir)


# --- V1.2: zwei Calls statt einem ------------------------------------------------------------

def eroeffnungs_kontext(teil1: AnalystEvaluationV2) -> str:
    """Ergebnis von Call 1 kompakt für Call 2.

    Vorgabe Chris: „wichtig wäre, dass der zweite Run den Kontext auch vom ersten Run bekommt und
    auch die Bewertung." Ohne den Hook beurteilt Call 2 den Spannungsbogen blind — der Bogen setzt
    genau dort an, wo die Eröffnung aufhört.

    Bewusst kurz (~300 Zeichen): Der ganze Sinn des Splits ist weniger Kontext pro Call. Was Call 2
    braucht, ist das Ergebnis, nicht die Herleitung.
    """
    h = teil1.hook
    zeilen = [
        "ERGEBNIS VON SCHRITT 1 (Eröffnung — schon bewertet, nicht neu bewerten):",
        f"- Zielgruppe: {teil1.zielgruppe or '(offen)'}",
        f"- Funnel: {teil1.funnel or '(offen)'}",
        f"- Sprech-Hook: {h.sprech_hook_score if h.sprech_hook_score is not None else '–'}/5"
        f" — {h.sprech_hook_grund or '(ohne Begründung)'}",
        f"- Text-Hook: {h.text_hook_score if h.text_hook_score is not None else '–'}/5"
        + (f" — Wortlaut: „{h.text_hook_wortlaut}“" if h.text_hook_wortlaut else " — kein Bildtext"),
        f"- Visueller Hook: {h.visuell_hook_score if h.visuell_hook_score is not None else '–'}/5",
        f"- Protagonist spricht ab Sek. {teil1.protagonist_ab_sek:.1f}",
        "Nutze das als gegebene Ausgangslage: Der Spannungsbogen setzt dort an, wo die Eröffnung "
        "aufhört. Wiederhole KEINE Hook-Bewertung und schreib keine Hook-Empfehlung — das ist erledigt.",
    ]
    return "\n".join(zeilen)


_TEIL_AUFGABE = {
    "eroeffnung": (
        "DEINE AUFGABE IN DIESEM SCHRITT: Bewerte ausschließlich die ERÖFFNUNG — die drei "
        "Hook-Ebenen (gesprochen, Bildtext, visuell), Zielgruppe, Funnel und ab wann der "
        "Protagonist inhaltlich spricht. Alles andere (Struktur, Schnitt, Ton, Bild, Untertitel) "
        "bewertet ein zweiter Schritt — lass diese Felder weg und schreib dazu keine Empfehlungen.\n"
    ),
    "handwerk": (
        "DEINE AUFGABE IN DIESEM SCHRITT: Bewerte das HANDWERK über das ganze Video — Struktur, "
        "Spannungsbogen, Schnitt und Pacing, Sprechqualität, visuelle Ästhetik, Untertitel, "
        "Dynamik, Energie und Blickkontakt. Die Eröffnung ist bereits bewertet (Ergebnis unten) — "
        "bewerte die Hooks NICHT erneut.\n"
    ),
}


def _evaluate_teil(video_file, result: AnalystResult, teil: str, run_dir,
                   kontext: str = "") -> AnalystEvaluationV2:
    """Ein Teil-Call. Das Video wird als bereits hochgeladene Datei-Referenz übergeben — die Files
    API erlaubt die Wiederverwendung über mehrere Requests, es wird also nicht zweimal geladen."""
    system = analyst_eval.build_system_prompt(teil=teil)
    user = _TEIL_AUFGABE[teil] + _user_message(result, "hybrid")
    if kontext:
        user += "\n\n" + kontext
    cfg = types.GenerateContentConfig(
        system_instruction=system, response_mime_type="application/json", temperature=0.0,
    )
    raw = (_generate([video_file, user], cfg, f"analyst_eval_split_{teil}").text or "")
    parsed = AnalystEvaluationV2(**analyst_eval._extract_json(raw))
    analyst_prompt_log.log_call(
        run_dir, call=f"eval_split_{teil}", recipient="Gemini",
        model=analyst_vlm.letztes_modell or gemini_service.GEMINI_MODEL,
        system_prompt=system, user_message=user, output_raw=raw, output_parsed=parsed,
        attachments=[f"Video: {result.filename}"],
        inputs={
            "engine": "v2_split", "teil": teil, "filename": result.filename,
            "duration_sec": result.duration_sec,
            "geplante_texthook": getattr(result, "geplante_texthook", ""),
            "gewaehltes_format": getattr(result, "gewaehltes_format", ""),
            "whisper_modell": settings.whisper_model,
            "transkript_hash": getattr(result, "transkript_hash", ""),
            "system_prompt_zeichen": len(system),
        },
    )
    return parsed


def evaluate_split(video_path: Path, result: AnalystResult, run_dir=None) -> AnalystEvaluationV2:
    """V1.2 — zwei Calls statt einem, geschnitten nach Kriterien.

    Call 1 bewertet die Eröffnung, Call 2 das Handwerk über das ganze Video und bekommt das
    Ergebnis von Call 1 als gegebene Ausgangslage mit. Beide sehen dieselbe hochgeladene
    Videodatei und dasselbe Transkript.

    Die Nachbearbeitung läuft EINMAL über das zusammengeführte Ergebnis — sonst würden Regeln wie
    der Score-Deckel oder die Empfehlungs-Verteilung zweimal auf Teilmengen greifen und die Top 3
    aus einer unvollständigen Liste bilden.
    """
    video_file = gemini_service._upload_video_to_gemini(video_path)
    teil1 = _evaluate_teil(video_file, result, "eroeffnung", run_dir)
    teil2 = _evaluate_teil(video_file, result, "handwerk", run_dir,
                           kontext=eroeffnungs_kontext(teil1))
    return analyst_eval.nachbearbeiten(analyst_eval.merge_teilergebnisse(teil1, teil2), result)
