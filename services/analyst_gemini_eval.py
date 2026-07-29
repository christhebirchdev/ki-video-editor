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
            "nicht in visuelle_aesthetik.probleme, nicht in sprechqualitaet.probleme, keine Empfehlung "
            "dazu, kein Abzug. Die unten stehende Blickkontakt-Pflicht gilt hier NUR für den Fall, dass er "
            "erkennbar einen Text abliest (Augen wandern zeilenweise, ohne Bezug zum eingeblendeten Video).\n"
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
            "WICHTIG, abweichend vom System-Prompt oben: Der System-Prompt ist für einen Modus geschrieben, "
            "in dem der Bewerter das Video NICHT sieht. Das gilt hier nicht. Ignoriere daher: (a) den Satz "
            "„Du hast das Video nie gesehen\"; (b) Verweise auf eine vorgefertigte Szenenliste, Bild-Fakten "
            "oder einen separaten „dedizierten Blick-Pass\" — die gibt es hier nicht; (c) Warnungen vor "
            "„Gemma-OCR-Fehlern\" — du liest Bildtext selbst direkt ab.\n"
            "NUTZE aktiv deinen visuellen Vorteil (das ist der Mehrwert): beurteile Blickrichtung (in die "
            "Linse vs. Ablesen nach unten/zur Seite), Bildtext, Schnitt/Pacing, Effekte/Zooms und Mimik "
            "aus dem bewegten Bild selbst.\n"
            "BILDTEXT: Du liest jeden Bildtext direkt ab — gleiche ihn mit dem TRANSKRIPT unten ab. Was dort "
            "(nahezu) wortgleich vorkommt, sind UNTERTITEL und nie die Texthook, auch wenn der Text statisch "
            "stehen bleibt oder oben im Bild steht. Details in der Regel „UNTERTITEL sind KEIN Text-Hook“ "
            "im System-Prompt.\n"
            + _format_instruction(result) +
            "PFLICHT Blickkontakt (Ausnahmen im Format-Block oben beachten): Beurteile den Blick IMMER. Geht "
            "er auffällig oft nach unten/zur Seite (Ablesen/Teleprompter), MUSS das (a) in "
            "visuelle_aesthetik.probleme stehen UND (b) als konkreter top_tipp: die betroffenen Stellen mit "
            "B-Roll/Einblendung überdecken und den Blick in die Linse richten. Liegt der Blick überwiegend "
            "in der Linse, sag das positiv und zieh keinen Abzug.\n"
            "HOOK-REDUNDANZ-CHECK (Pflicht): Vergleiche den Sprech-Hook — die ersten Worte des PROTAGONISTEN "
            "ab protagonist_ab_sek, nicht zwingend der Anfang des Transkripts — mit dem Text-Overlay der "
            "Eröffnung (Text-Hook). Sind sie wörtlich gleich oder "
            "fast gleich, ist das eine SCHWÄCHE — die Text-Hook doppelt nur das Gesprochene und verschenkt eine "
            "zweite Ebene. Dann: text_hook_score deutlich niedriger (NICHT höher als der Sprech-Hook, eher 1–2 "
            "Punkte darunter), die Doppelung in text_hook_grund klar benennen, UND als PRIO-1-Handlungsempfehlung "
            "aufnehmen: das Text-Overlay am Anfang für eine zweite, überraschende Ebene nutzen (offene Frage oder "
            "konkreter Fakt) statt den gesprochenen Satz zu wiederholen.\n"
            "HOOK-VERBESSERUNG (nutze dieses Framework, wenn Sprech- oder Text-Hook schwach ist, fehlt oder "
            "redundant): Eine Hook wirkt auf 3 Ebenen — (1) TEXT-HOOK (Bildschirmtext, Länge nach der Regel "
            "„LÄNGE der Text-Hook“ im System-Prompt, für einen 13-Jährigen SOFORT verständlich, kein "
            "Fachwort — greift die, die ohne Ton "
            "scrollen); (2) SPRECH-HOOK (erster gesprochener Satz — muss Neugier wecken ODER einen Pain Point "
            "treffen); (3) REGIE (Energie in der Stimme + ein visueller Bruch der Erwartung, markenkonform). "
            "Eine starke Hook hat: ein krasses/kontroverses Statement, wirkt „wie ein Unfall“ (zwingt zum "
            "Hinsehen) und triggert GENAU die Zielgruppe (sortiert andere bewusst aus — eine Hook für alle "
            "stoppt niemanden). Die Zielgruppe muss NICHT in beiden Ebenen genannt sein. Wenn du eine Hook "
            "empfiehlst, liefere KONKRET bis zu 3 Text-Hook-Varianten mit JE UNTERSCHIEDLICHER Mechanik "
            "(z. B. Provokation / Neugierlücke / konkrete Zahl oder Pain Point / Erwartungsbruch / POV), "
            "passend zum echten Thema DIESES Videos (nicht generisch), damit der Nutzer sie per Instagram-"
            "Testreel gegeneinander testen kann. Formuliere die Empfehlung ausdrücklich mit dem Wort "
            "„Texthook“ (bzw. „Sprechhook“) — diese Begriffe sind unseren Kunden bekannt und sollen genutzt werden.\n"
            + _texthook_instruction(result) +
            "AUDIO-QUALITÄT (Pflicht, gut hinhören): Die Messwerte (LUFS) sagen NICHTS über Störgeräusche — das "
            "musst du HÖREN. Achte gezielt auf Hintergrundrauschen, Brummen, Hall oder Übersteuerung. Ist der "
            "Ton verrauscht/unsauber, ist das eine SCHWÄCHE (in sprechqualitaet.probleme benennen) und darf "
            "NICHT als Stärke gelobt werden. Nur wirklich sauberer Ton ist ein Pluspunkt.\n"
            "SPRECHPAUSEN: Die Sprachstatistik unten listet jede Pause MIT Position. Geh sie nach der Regel "
            "„Sprechpausen — nach FUNKTION beurteilen\" im System-Prompt durch: schau dir jede Stelle im Video "
            "an und bestimme, ob die Pause eine Stockung/ein Anlauf ist (rausschneiden) oder dramaturgisch "
            "bzw. ein Übergang (lassen). Kannst du die Funktion nicht sicher bestimmen, sag NICHTS dazu.\n"
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
