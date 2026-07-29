# services/analyst_chat.py
"""Rückfragen-Chat zur fertigen Analyse (V1.1).

Bewusst OHNE Video: Der Chat bekommt die fertige Bewertung, das Transkript und die gemessenen
Werte als Text. Gemini hat keinen serverseitigen Gesprächsspeicher — jeder Turn schickt den
kompletten Verlauf erneut mit. Läge das Video darin, würden Kosten und Latenz mit jeder
Nachricht wachsen. Der Chat soll erklären, was in der Analyse steht, nicht neu beobachten.

Was der Chat NICHT darf: die gespeicherte Analyse ändern. Die angezeigte Bewertung entsteht
nicht roh aus dem Modell, sondern erst nach analyst_eval.nachbearbeiten() — dort wird der
performance_score aus gewichteten Einzelscores berechnet und die action_steps werden sortiert.
Ein Chat, der direkt in analysis.json schreibt, umgeht diese Kette und erzeugt eine zweite,
stillschweigend abweichende Bewertungslogik. Revision kommt separat, über nachbearbeiten(),
mit Versionierung und Bestätigungs-Gate.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from google.genai import types

from models.analyst import AnalystResult
from services import analyst_prompt_log, gemini_service

CHAT_DATEI = "chat.jsonl"
VERLAUF_MD = "chat_verlauf.md"

# Wie viele Nachrichten des Verlaufs mitgeschickt werden. Ohne Deckel wächst der Prompt mit
# jeder Runde weiter, und irgendwann zahlt jede Frage den gesamten bisherigen Chat mit.
MAX_VERLAUF = 20

SYSTEM_PROMPT = """Du bist der Video-Analyst von MEINFLUSS und beantwortest Rückfragen zu einer
Analyse, die du bereits erstellt hast.

Deine Aufgabe: erklären, einordnen, konkreter werden. Der Nutzer will verstehen, WARUM etwas
wichtig ist und WIE er es beim nächsten Video besser macht.

Regeln:
- Antworte auf Deutsch, in einfacher Sprache, ohne Fachjargon.
- Kurz und konkret. Zwei bis fünf Sätze reichen meistens. Keine Einleitungsfloskeln.
- Beziehe dich auf die Werte aus der Analyse. Erfinde keine Zahlen, Zeitpunkte oder
  Beobachtungen, die nicht im Kontext stehen.
- Du hast das Video NICHT vor dir. Wenn eine Frage nur mit erneutem Ansehen des Videos zu
  beantworten wäre, sag das offen, statt zu raten.
- Du änderst die Bewertung NICHT. Du vergibst keine neuen Scores und korrigierst keine
  bestehenden. Fragt der Nutzer nach einer neuen Bewertung, erkläre, dass das in dieser
  Version noch nicht geht, und beantworte stattdessen seine inhaltliche Frage.
- Formatierung: nur einfache Absätze, Aufzählungen mit "- " und **fett** für wichtige Begriffe.
  Keine Überschriften, keine Tabellen, kein Code."""


# ---------- Kontext ----------

def baue_kontext(result: AnalystResult) -> str:
    """Alles, was das Modell über dieses Video wissen muss — als Text, ohne Video.

    Die gemessenen Werte werden ausdrücklich als deterministisch gekennzeichnet: Sie kommen
    aus Code (Whisper, ffmpeg, OpenCV), nicht aus einem Modellurteil. Ohne diese Kennzeichnung
    behandelt das Modell sie wie seine eigene Schätzung und relativiert sie auf Nachfrage.
    """
    teile: list[str] = [
        f"Datei: {result.filename}",
        f"Länge: {result.duration_sec:.1f} Sekunden",
    ]
    if result.gewaehltes_format:
        teile.append(f"Vom Nutzer gewähltes Format: {result.gewaehltes_format}")
    if result.geplante_texthook:
        teile.append(f"Vom Nutzer geplante Texthook: {result.geplante_texthook}")

    if result.transcript:
        teile.append(f"\n## Transkript\n{result.transcript}")
    else:
        teile.append("\n## Transkript\n(kein gesprochenes Wort erkannt)")

    if result.speech_stats:
        s = result.speech_stats
        teile.append(
            "\n## Gemessene Sprachwerte (deterministisch, kein Modellurteil)\n"
            f"- Wörter: {s.wort_anzahl}\n"
            f"- Sprechtempo: {s.wpm:.0f} Wörter/Minute\n"
            f"- Füllwörter: {s.filler_count} ({', '.join(s.filler_words) or '—'})\n"
            f"- Pausen über der Schwelle: {s.pausen_count}, längste {s.laengste_pause_sec:.1f}s\n"
            f"- Sprechbeginn bei Sekunde {s.sprechbeginn_sec:.2f}"
        )

    if result.quality_metrics:
        q = result.quality_metrics
        lufs = f"{q.lufs_integrated:.1f} LUFS" if q.lufs_integrated is not None else "kein Audio"
        teile.append(
            "\n## Gemessene Bild- und Tonwerte (deterministisch)\n"
            f"- Schärfe (Durchschnitt/Minimum): {q.schaerfe_avg:.0f} / {q.schaerfe_min:.0f}\n"
            f"- Helligkeit: {q.helligkeit_avg:.0f} von 255, Kontrast {q.kontrast_avg:.0f}\n"
            f"- Lautheit: {lufs}"
        )

    if result.audio_overview:
        teile.append(f"\n## Ton-Beobachtung\n{result.audio_overview}")
    if result.gaze_overview:
        teile.append(f"\n## Blick-Beobachtung\n{result.gaze_overview}")

    if result.evaluation:
        teile.append(
            "\n## Die Bewertung, die dem Nutzer angezeigt wird\n"
            + json.dumps(result.evaluation.model_dump(), ensure_ascii=False, indent=2)
        )
    else:
        teile.append("\n## Bewertung\n(dieser Lauf wurde ohne Bewertung gestartet)")

    return "\n".join(teile)


# ---------- Verlauf ----------

def haenge_nachricht_an(run_dir: Path, rolle: str, text: str) -> dict:
    """Eine Nachricht ans Ende von chat.jsonl.

    Append-only wie feedback.jsonl: Der Verlauf bleibt damit auch bei einem Absturz mitten im
    Schreiben bis zur letzten vollständigen Zeile lesbar.

    Jede Nachricht bekommt eine eigene `id`. Die ist die Klammer zum Admin-Feedback: dort wird
    unter `field_id = "chat.<id>"` bewertet. Über die Position in der Datei ginge das nicht
    zuverlässig — lade_verlauf() überspringt kaputte Zeilen, dabei verschieben sich alle
    nachfolgenden Indizes und ein altes Feedback zeigt plötzlich auf die falsche Antwort.
    """
    eintrag = {
        "id": uuid.uuid4().hex[:8],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "rolle": rolle,          # "user" | "model"
        "text": text,
    }
    with (run_dir / CHAT_DATEI).open("a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    return eintrag


def lade_verlauf(run_dir: Path) -> list[dict]:
    """Alle Nachrichten in Schreibreihenfolge.

    Kaputte Zeilen werden übersprungen statt geworfen — eine halb geschriebene Zeile darf
    nicht den kompletten Chat unbenutzbar machen.

    Nachrichten aus der ersten Fassung haben noch keine `id`; die bekommen hier einen
    Positions-Fallback, damit das Frontend nicht auf `undefined` zugreift.
    """
    pfad = run_dir / CHAT_DATEI
    if not pfad.exists():
        return []
    nachrichten: list[dict] = []
    for i, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines()):
        if not zeile.strip():
            continue
        try:
            n = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if not n.get("id"):
            n["id"] = f"pos{i}"
        nachrichten.append(n)
    return nachrichten


# ---------- Lesbarer Verlauf ----------

def _feedback_je_feld(run_dir: Path) -> dict[str, dict]:
    """Letzter Feedback-Eintrag je field_id aus feedback.jsonl.

    Bewusst hier statt Import aus api/analyst.py: Ein Service, der einen API-Router importiert,
    dreht die Abhängigkeit um (der Router importiert diesen Service bereits).
    """
    pfad = run_dir / "feedback.jsonl"
    if not pfad.exists():
        return {}
    letzte: dict[str, dict] = {}
    try:
        for zeile in pfad.read_text(encoding="utf-8").splitlines():
            if not zeile.strip():
                continue
            try:
                e = json.loads(zeile)
            except json.JSONDecodeError:
                continue
            if e.get("field_id"):
                letzte[e["field_id"]] = e
    except OSError:
        return {}
    return letzte


def schreibe_verlauf_md(run_dir: Path) -> None:
    """Erzeugt `chat_verlauf.md` neu aus chat.jsonl — lesbarer Verlauf für die Auswertung.

    Abgeleitet, nicht zweite Quelle der Wahrheit: chat.jsonl bleibt die Datenbasis, diese
    Datei wird bei jeder neuen Nachricht komplett neu geschrieben. Dadurch kann sie nie von
    chat.jsonl abweichen — ein zweiter Append-Pfad könnte das.

    Die Admin-Bewertung wird mit eingeblendet. Ohne sie müsste man beim Auswerten
    feedback.jsonl und chat.jsonl von Hand über die IDs zusammenführen.

    Crash-sicher wie analyst_prompt_log: Ein Fehler beim Schreiben darf den Chat nicht kippen.
    """
    try:
        nachrichten = lade_verlauf(run_dir)
        if not nachrichten:
            return
        feedback = _feedback_je_feld(run_dir)

        filename = ""
        try:
            filename = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")).get("filename", "")
        except (OSError, json.JSONDecodeError):
            pass

        zeilen = [
            f"# Chatverlauf — Run `{run_dir.name}`",
            "",
            f"- **Video:** {filename or '(unbekannt)'}",
            f"- **Nachrichten:** {len(nachrichten)}",
            f"- **Zuletzt aktualisiert:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "> Abgeleitet aus `chat.jsonl` — bei jeder neuen Nachricht neu erzeugt.",
            "> Die Bewertung je Antwort stammt aus `feedback.jsonl` (`field_id = chat.<id>`).",
            "",
            "---",
            "",
        ]

        for n in nachrichten:
            wer = "Du" if n.get("rolle") == "user" else "KI"
            uhr = (n.get("ts") or "").replace("T", " ")
            zeilen.append(f"### {wer} · {uhr} · `{n.get('id', '')}`")
            zeilen.append("")
            zeilen.append(n.get("text", "").rstrip() or "_(leer)_")
            zeilen.append("")

            if n.get("rolle") == "model":
                fb = feedback.get(f"chat.{n.get('id', '')}")
                if fb:
                    symbol = {"up": "👍 gut", "down": "👎 schlecht"}.get(fb.get("verdict", ""), "— ohne Daumen")
                    zeilen.append(f"**Bewertung:** {symbol}")
                    if fb.get("text"):
                        zeilen.append("")
                        for kommentar in fb["text"].splitlines():
                            zeilen.append(f"> {kommentar}")
                    zeilen.append("")

            zeilen.append("---")
            zeilen.append("")

        (run_dir / VERLAUF_MD).write_text("\n".join(zeilen), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — Schreiben des Protokolls darf den Chat nie brechen
        print(f"  [CHAT] WARN: Konnte {VERLAUF_MD} nicht schreiben: {e}")


# ---------- Gemini ----------

def _contents(kontext: str, verlauf: list[dict], frage: str) -> list[dict]:
    """Der Kontext hängt an der ERSTEN Nutzer-Nachricht, nicht an jeder — sonst wird er mit
    jeder Runde erneut bezahlt. Die gefakte Modell-Antwort danach verankert die Rolle, ohne
    dass das Modell den Kontext nochmal zusammenfassen will."""
    inhalte: list[dict] = [
        {"role": "user", "parts": [{"text": f"Hier ist die Analyse:\n\n{kontext}"}]},
        {"role": "model", "parts": [{"text": "Verstanden. Stell deine Fragen."}]},
    ]
    for n in verlauf[-MAX_VERLAUF:]:
        rolle = "model" if n.get("rolle") == "model" else "user"
        inhalte.append({"role": rolle, "parts": [{"text": n.get("text", "")}]})
    inhalte.append({"role": "user", "parts": [{"text": frage}]})
    return inhalte


def stream_antwort(run_dir: Path, kontext: str, frage: str):
    """Generator: gibt Textstücke aus, sobald sie kommen, und schreibt am Ende die komplette
    Antwort in den Verlauf.

    Bewusst ein SYNCHRONER Generator: FastAPI führt synchrone Endpunkte in einem Threadpool
    aus. Als `async` würde der blockierende Gemini-Call den Event-Loop anhalten — bei
    `--workers 1` steht dann die komplette App, inklusive laufender Hintergrund-Analysen.

    Keine Sampling-Parameter (temperature/top_p/top_k): bei 3.5 noch erlaubt, ab Gemini 3.6
    deprecated — siehe Hinweis in gemini_service.py. Für einen Chat braucht es sie ohnehin nicht.
    """
    haenge_nachricht_an(run_dir, "user", frage)
    verlauf = lade_verlauf(run_dir)[:-1]   # die gerade geschriebene Frage nicht doppelt senden
    contents = _contents(kontext, verlauf, frage)
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)

    def _abschliessen(antwort: str, modell: str, abgebrochen: bool = False) -> None:
        """Antwort persistieren, Lauf protokollieren, lesbaren Verlauf neu erzeugen."""
        haenge_nachricht_an(run_dir, "model", antwort)
        analyst_prompt_log.log_call(
            run_dir, call="chat", recipient="Gemini", model=modell,
            system_prompt=SYSTEM_PROMPT, user_message=frage, output_raw=antwort,
            # Der Analyse-Kontext geht als Gesprächs-Historie mit, nicht in der User-Message.
            # Ihn hier voll zu loggen würde prompt_log.md mit jedem Turn erneut aufblähen —
            # deshalb nur seine Größe, der Inhalt steht ohnehin in analysis.json.
            inputs={
                "verlauf_nachrichten": len(verlauf),
                "kontext_zeichen": len(kontext),
                "abgebrochen": abgebrochen,
            },
        )
        schreibe_verlauf_md(run_dir)

    letzter_fehler = None
    for modell in [gemini_service.GEMINI_MODEL] + gemini_service.GEMINI_FALLBACK_MODELS:
        gesammelt: list[str] = []
        try:
            for chunk in gemini_service.client.models.generate_content_stream(
                model=modell, contents=contents, config=cfg
            ):
                stueck = getattr(chunk, "text", None)
                if stueck:
                    gesammelt.append(stueck)
                    yield stueck
        except Exception as e:  # noqa: BLE001 — jeder Modellfehler soll das nächste Modell probieren
            if gesammelt:
                # Mitten im Stream abgebrochen: Ein Neustart auf einem anderen Modell würde dem
                # Nutzer den Anfang ein zweites Mal in dieselbe Blase schreiben. Lieber das
                # Teilstück behalten und aufhören — abgeschnitten ist besser als doppelt.
                _abschliessen("".join(gesammelt), modell, abgebrochen=True)
                return
            letzter_fehler = e
            continue
        _abschliessen("".join(gesammelt), modell)
        return

    raise RuntimeError(f"Gemini-Chat fehlgeschlagen: {letzter_fehler}")
