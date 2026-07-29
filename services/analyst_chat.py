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
from services import analyst_eval, analyst_prompt_log, gemini_service
from services.analyst_speech import PAUSE_THRESHOLD_SEC

CHAT_DATEI = "chat.jsonl"
VERLAUF_MD = "chat_verlauf.md"
GEMINI_DATEI_CACHE = "gemini_file.json"

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


# ---------- Ausschnitts-Analyse ----------
#
# Warum das nicht einfach ein zweiter Bewertungslauf ist:
#
# Die angezeigte Bewertung entsteht aus ZWEI Quellen — dem Skill-Prompt UND der
# Nachbearbeitung in analyst_eval.nachbearbeiten(). Die Code-Regeln dort gelten aber
# ausdrücklich für das GANZE Video: berechne_performance_score() gewichtet sieben
# Dimensionen über die volle Länge, verteile_empfehlungen() sortiert nach frühestem
# Zeitpunkt im Video, erzwinge_anlauf_schnitt() liest sprechbeginn_sec des Gesamtvideos,
# baue_pausen_schritt() alle gemessenen Pausen. Auf einen 6-Sekunden-Ausschnitt angewandt
# liefern die Unsinn.
#
# Also: Der Ausschnitt bekommt DIESELBE Urteilsgrundlage (analyst_eval_skill.md +
# analyst_video_reference.md, zur Laufzeit geladen — das ist das Produktverhalten), aber
# einen eigenen Ausgabe-Vertrag. Er ist ausdrücklich KEINE Bewertung, sondern eine
# Ausschnitts-Beobachtung: keine Gesamtnote, keine action_steps, kein Schreiben in
# analysis.json. Damit bleibt es bei EINER verbindlichen Bewertung pro Video.

SEGMENT_VERTRAG = f"""
--- AUSGABE FÜR DIE AUSSCHNITTS-ANALYSE ---

Du siehst NUR einen Ausschnitt des Videos, nicht das ganze Video. Antworte als Fließtext für
einen Chat, NICHT als JSON.

Es gelten alle Bewertungsregeln oben unverändert — dieselben Dimensionen, dieselbe 1–5-Skala,
dieselben Maßstäbe, dieselbe einfache Sprache.

VERBOTEN, weil diese Werte im Code über das GESAMTE Video berechnet werden und deine Version
der angezeigten Bewertung widersprechen würde:
- kein performance_score und keine Gesamtnote für den Ausschnitt
- keine action_steps und keine nummerierte Top-3-Liste
- keine Aussage darüber, wie sich der Ausschnitt auf die Gesamtbewertung auswirkt

ERLAUBT und erwünscht:
- Beobachtungen zu dem, was in diesem Zeitfenster tatsächlich passiert
- Einzelscores 1–5 für die Dimensionen, die in diesem Ausschnitt überhaupt beurteilbar sind,
  jeweils mit einem Satz Begründung — ausdrücklich als Ausschnitts-Einschätzung benannt
- konkrete Verbesserungsvorschläge für genau diese Stelle

Erfinde keine Zeitpunkte, Zahlen oder Messwerte. Gemessene Werte stehen in der Nachricht des
Nutzers; Pausen unter {PAUSE_THRESHOLD_SEC} Sekunden wurden gar nicht erst gemeldet und
existieren für dich nicht.

Format: kurze Absätze, Aufzählungen mit "- ", **fett** für wichtige Begriffe. Keine
Überschriften, keine Tabellen, kein Code, kein JSON.
""".strip()


def segment_system_prompt() -> str:
    """Urteilsgrundlage des Analysten + eigener Ausgabe-Vertrag.

    Bewusst `load_skill_body()` + `load_reference()` statt `build_system_prompt()`: Letzteres
    hängt OUTPUT_SCHEMA an, den strikten JSON-Vertrag für den vollen Lauf inklusive
    performance_score und action_steps. Genau die darf ein Ausschnitt nicht liefern.
    Ändert jemand die Bewertungsregeln in analyst_eval_skill.md, ändern sie sich hier mit —
    das ist der Punkt.
    """
    teile = [analyst_eval.load_skill_body()]
    ref = analyst_eval.load_reference()
    if ref:
        teile.append(
            "--- ANGEHÄNGTE REFERENZ: VIDEO-ANALYSE (EDITING + SKRIPT + TECHNIK/AUFTRETEN) ---\n"
            + ref
        )
    teile.append(SEGMENT_VERTRAG)
    return "\n\n".join(teile)


def _pausen_im_fenster(result: AnalystResult, start: float, ende: float) -> list:
    """Nur die gemessenen Pausen, die in den Ausschnitt fallen.

    Ohne diese Einschränkung nennt das Modell Pausen, die außerhalb des gezeigten Fensters
    liegen — es sieht sie im Video nicht und würde sie trotzdem als Beobachtung ausgeben.
    """
    if not result.speech_stats or not result.speech_stats.pausen:
        return []
    return [p for p in result.speech_stats.pausen if p.start_sec >= start and p.end_sec <= ende]


def baue_ausschnitt_nachricht(result: AnalystResult, start: float, ende: float, frage: str) -> str:
    """Die User-Message für die Ausschnitts-Analyse: Fenster, gemessene Fakten, Auftrag."""
    zeilen = [
        f"Analysiere den Ausschnitt von Sekunde {start:.1f} bis {ende:.1f} "
        f"(Gesamtlänge des Videos: {result.duration_sec:.1f} Sekunden).",
        f"Vom Nutzer gewähltes Format: {result.gewaehltes_format or '(nicht angegeben)'}",
    ]

    pausen = _pausen_im_fenster(result, start, ende)
    if pausen:
        liste = ", ".join(f"{p.start_sec:.1f}–{p.end_sec:.1f}s ({p.dauer_sec:.1f}s)" for p in pausen)
        zeilen.append(f"\nGemessene Sprechpausen in diesem Fenster: {liste}")
    else:
        zeilen.append(
            f"\nGemessene Sprechpausen in diesem Fenster: keine über {PAUSE_THRESHOLD_SEC} Sekunden."
        )

    if result.transcript:
        zeilen.append(f"\nTranskript des GESAMTEN Videos (zur Einordnung):\n{result.transcript}")

    zeilen.append(
        "\nAuftrag des Nutzers:\n" + (frage.strip() or
        "Beurteile diesen Ausschnitt nach den Analystenregeln und sag mir konkret, was hier "
        "besser gehen würde.")
    )
    return "\n".join(zeilen)


def _video_pfad(run_dir: Path) -> Path:
    """Die hochgeladene Originaldatei des Laufs (analyst_runs/<id>/raw/<filename>)."""
    raw = run_dir / "raw"
    if not raw.is_dir():
        raise FileNotFoundError("Zu diesem Lauf liegt keine Videodatei mehr vor")
    dateien = sorted(p for p in raw.iterdir() if p.is_file())
    if not dateien:
        raise FileNotFoundError("Zu diesem Lauf liegt keine Videodatei mehr vor")
    return dateien[0]


def hole_video_handle(run_dir: Path):
    """Gemini-File-Handle für das Video dieses Laufs, mit Wiederverwendung.

    Die Files API hält hochgeladene Dateien rund 48 Stunden. Ohne Cache würde jede einzelne
    Ausschnitts-Frage dasselbe Video erneut hochladen — bei 18 MB und mehr ist das die
    teuerste und langsamste Stelle des ganzen Features.

    Kein Ablaufdatum verwalten: Wir versuchen `files.get` und laden bei jedem Fehlschlag neu.
    Das ist genau eine Bedingung statt einer Zeitrechnung, die falsch gehen kann.
    """
    cache = run_dir / GEMINI_DATEI_CACHE
    if cache.exists():
        try:
            name = json.loads(cache.read_text(encoding="utf-8")).get("name", "")
            if name:
                handle = gemini_service.client.files.get(name=name)
                if gemini_service._state_name(handle.state) == "ACTIVE":
                    return handle
        except Exception:  # noqa: BLE001 — abgelaufen, gelöscht, Netzfehler: in allen Fällen neu laden
            pass

    handle = gemini_service._upload_video_to_gemini(_video_pfad(run_dir))
    try:
        cache.write_text(json.dumps({"name": handle.name}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass          # Cache ist Beschleunigung, kein Muss
    return handle


def stream_ausschnitt(run_dir: Path, result: AnalystResult, start: float, ende: float, frage: str):
    """Generator wie stream_antwort, aber MIT Video und auf ein Zeitfenster begrenzt.

    Das Zeitfenster wird über VideoMetadata gesetzt, nicht über einen Hinweis im Prompt:
    Gemini verarbeitet dann nur diesen Abschnitt. Ein Prompt-Hinweis würde das ganze Video
    abrechnen und das Modell trotzdem über Stellen reden lassen, die nicht gefragt waren.
    """
    auftrag = frage.strip() or f"Analysiere den Ausschnitt {start:.1f}–{ende:.1f} s."
    haenge_nachricht_an(run_dir, "user", f"[Ausschnitt {start:.1f}–{ende:.1f} s] {auftrag}")

    system = segment_system_prompt()
    nachricht = baue_ausschnitt_nachricht(result, start, ende, frage)
    handle = hole_video_handle(run_dir)

    video_part = types.Part(
        file_data=types.FileData(file_uri=handle.uri, mime_type=handle.mime_type),
        video_metadata=types.VideoMetadata(
            start_offset=f"{start:.1f}s", end_offset=f"{ende:.1f}s"
        ),
    )
    cfg = types.GenerateContentConfig(system_instruction=system)
    contents = [{"role": "user", "parts": [video_part, {"text": nachricht}]}]

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
        except Exception as e:  # noqa: BLE001
            if gesammelt:
                _abschliessen_ausschnitt(run_dir, "".join(gesammelt), modell, system, nachricht, True)
                return
            letzter_fehler = e
            continue
        _abschliessen_ausschnitt(run_dir, "".join(gesammelt), modell, system, nachricht)
        return

    raise RuntimeError(f"Ausschnitts-Analyse fehlgeschlagen: {letzter_fehler}")


def _abschliessen_ausschnitt(run_dir: Path, antwort: str, modell: str, system: str,
                             nachricht: str, abgebrochen: bool = False) -> None:
    haenge_nachricht_an(run_dir, "model", antwort)
    analyst_prompt_log.log_call(
        run_dir, call="chat_ausschnitt", recipient="Gemini", model=modell,
        system_prompt=system, user_message=nachricht, output_raw=antwort,
        attachments=["Video (Zeitfenster)"],
        inputs={"abgebrochen": abgebrochen},
    )
    schreibe_verlauf_md(run_dir)


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
