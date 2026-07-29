# services/analyst_chat.py
"""Rückfragen-Chat zur fertigen Analyse (V1.1).

Der Chat bekommt die fertige Bewertung, das Transkript und die gemessenen Werte als Text —
UND das Video. Damit kann er einen einzelnen Aspekt auf Anfrage wirklich neu ansehen
("schau dir nochmal die Untertitel an") statt ihn aus der vorhandenen Bewertung abzuleiten.

Preis dafür, bewusst in Kauf genommen: Gemini hat keinen serverseitigen Gesprächsspeicher,
jeder Turn schickt den kompletten Kontext erneut. Das Video wird also in JEDEM Turn verarbeitet
und abgerechnet. Der Upload passiert nur einmal je Lauf (hole_video_handle cached den
File-Handle), die Verarbeitung pro Call nicht.

Was der Chat NICHT darf: die gespeicherte Analyse ändern oder eine konkurrierende Gesamtnote
vergeben. Die angezeigte Bewertung entsteht nicht roh aus dem Modell, sondern erst nach
analyst_eval.nachbearbeiten() — dort wird der performance_score aus gewichteten Einzelscores
berechnet und die action_steps werden sortiert. Ein Chat, der das neu vergibt, erzeugt eine
zweite, stillschweigend abweichende Bewertungslogik. Deshalb: gleiche Urteilsgrundlage
(analyst_eval_skill.md, zur Laufzeit geladen), aber eigener Ausgabe-Vertrag (CHAT_VERTRAG).
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

# Lebensdauer des expliziten Caches. Deckt eine übliche Chat-Sitzung ab, ohne Speicher für
# einen Lauf zu bezahlen, den niemand mehr anfasst. Läuft er ab, wird beim nächsten
# Video-Turn neu angelegt.
CACHE_TTL = "1800s"

# Das Modell fordert das Video an, indem es GENAU diesen Marker als Erstes ausgibt. Er darf
# den Nutzer nie erreichen — stream_antwort puffert so lange, bis die Entscheidung feststeht.
VIDEO_MARKER = "[VIDEO]"

ENTSCHEIDUNGS_REGEL = f"""
--- ERSTE ENTSCHEIDUNG: BRAUCHST DU DAS VIDEO? ---

Für DIESE Antwort liegt dir das Video nicht vor, nur die Analyse als Text.

Prüfe zuerst: Lässt sich die Frage sauber und ohne Raten aus der Analyse beantworten?
- Ja → antworte ganz normal.
- Nein → gib GENAU {VIDEO_MARKER} aus und sonst NICHTS. Kein Wort davor, kein Wort danach.
  Du bekommst das Video dann sofort und beantwortest die Frage im zweiten Anlauf.

Gib {VIDEO_MARKER} immer aus, wenn der Nutzer dich bittet, dir etwas anzusehen, etwas
nachzuprüfen, einen Aspekt neu zu beurteilen, oder wenn er nach etwas fragt, das nicht als
Wert in der Analyse steht. Im Zweifel: {VIDEO_MARKER}. Raten ist schlechter als nachsehen.
""".strip()

CHAT_VERTRAG = f"""
--- AUSGABE FÜR DEN RÜCKFRAGEN-CHAT ---

Du bist der Video-Analyst von MEINFLUSS im Gespräch mit dem Nutzer über eine Analyse, die du
bereits erstellt hast. **Das Video liegt dir vor** — du kannst es erneut ansehen.

Deine Aufgabe: erklären, einordnen, konkreter werden. Und auf Wunsch einen einzelnen Aspekt
neu und genauer ansehen — Untertitel, Texteinblendungen, Hook, Schnitt, Blickkontakt, Ton,
was auch immer der Nutzer wissen will. Dafür schaust du dir das Video wirklich an, statt aus
der vorhandenen Bewertung abzuleiten.

Es gelten alle Bewertungsregeln oben unverändert — dieselben Dimensionen, dieselbe 1–5-Skala,
dieselben Maßstäbe, dieselbe einfache Sprache.

VERBOTEN, weil diese Werte im Code über das gesamte Video berechnet werden und deine Version
der angezeigten Bewertung widersprechen würde:
- kein neuer performance_score und keine neue Gesamtnote
- keine neuen action_steps und keine nummerierte Top-3-Handlungsliste
- keine Behauptung, du hättest die gespeicherte Bewertung geändert — das kannst du nicht

ERLAUBT und erwünscht:
- Einzelscores 1–5 für einen Aspekt, wenn der Nutzer danach fragt, mit einem Satz Begründung
  und ausdrücklich als vertiefte Einschätzung zu diesem Aspekt benannt
- konkrete Beobachtungen mit Zeitangabe, wenn du sie im Video wirklich siehst
- konkrete Verbesserungsvorschläge für genau die Stelle oder den Aspekt, um den es geht

Antworte auf Deutsch, in einfacher Sprache, ohne Fachjargon. Kurz und konkret — zwei bis fünf
Sätze reichen meistens, bei einer vertieften Aspekt-Analyse darf es länger werden. Keine
Einleitungsfloskeln.

Erfinde keine Messwerte. Gemessene Zahlen stehen im Kontext; Pausen unter
{PAUSE_THRESHOLD_SEC} Sekunden wurden gar nicht erst gemeldet und existieren für dich nicht.

Format: kurze Absätze, Aufzählungen mit "- ", **fett** für wichtige Begriffe. Keine
Überschriften, keine Tabellen, kein Code, kein JSON.
""".strip()


OHNE_VIDEO_HINWEIS = (
    "\n\nHINWEIS FÜR DIESES GESPRÄCH: Das Video liegt dir ausnahmsweise NICHT vor. "
    "Wenn eine Frage nur mit Ansehen des Videos zu beantworten wäre, sag das offen, "
    "statt zu raten."
)


def chat_system_prompt(mit_video: bool = True) -> str:
    """Urteilsgrundlage des Analysten + Ausgabe-Vertrag für den Chat.

    Bewusst `load_skill_body()` + `load_reference()` statt `build_system_prompt()`: Letzteres
    hängt OUTPUT_SCHEMA an, den strikten JSON-Vertrag des Gesamtlaufs inklusive
    performance_score und action_steps. Genau die darf der Chat nicht liefern — sie entstehen
    im Code über das ganze Video (berechne_performance_score() gewichtet sieben Dimensionen,
    verteile_empfehlungen() sortiert nach frühestem Zeitpunkt). Ein Chat, der sie neu vergibt,
    widerspricht der angezeigten Bewertung.

    Dieselbe Regelquelle heißt: Ändert jemand analyst_eval_skill.md, ändert sich der Chat mit.
    """
    teile = [analyst_eval.load_skill_body()]
    ref = analyst_eval.load_reference()
    if ref:
        teile.append(
            "--- ANGEHÄNGTE REFERENZ: VIDEO-ANALYSE (EDITING + SKRIPT + TECHNIK/AUFTRETEN) ---\n"
            + ref
        )
    vertrag = CHAT_VERTRAG if mit_video else CHAT_VERTRAG + OHNE_VIDEO_HINWEIS
    teile.append(vertrag)
    return "\n\n".join(teile)


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


# ---------- Video-Handle ----------

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
    Chat-Nachricht dasselbe Video erneut hochladen — bei 18 MB und mehr ist das die
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
    _merke(run_dir, {"name": handle.name})
    return handle


def _lies_notiz(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / GEMINI_DATEI_CACHE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merke(run_dir: Path, neu: dict) -> None:
    """Notizzettel für File-Handle und Cache. Beschleunigung, kein Muss — Fehler werden
    geschluckt, im schlimmsten Fall wird beim nächsten Mal neu hochgeladen."""
    daten = _lies_notiz(run_dir)
    daten.update(neu)
    try:
        (run_dir / GEMINI_DATEI_CACHE).write_text(
            json.dumps(daten, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def hole_cache(run_dir: Path, system: str, kontext: str, handle) -> str | None:
    """Expliziter Gemini-Cache aus Systemprompt + Video + Analyse-Kontext, oder None.

    Warum überhaupt: Diese drei Teile sind über alle Turns eines Laufs identisch und machen
    den Löwenanteil der Tokens aus. Implizites Caching läuft zwar seit Gemini 2.5 automatisch,
    garantiert aber nichts; explizit ist die Ersparnis zugesichert.

    **Der Cache ist an EIN Modell gebunden.** Läuft der Call auf ein Fallback-Modell, ist der
    Name dort ungültig — deshalb wird er ausschließlich beim Primärmodell verwendet und die
    Modellkennung mitgespeichert. Wechselt GEMINI_MODEL, wird neu angelegt.

    Schlägt irgendetwas fehl (Mindest-Tokenzahl unterschritten, Quota, Netz), gibt die Funktion
    None zurück und der Aufrufer schickt Video und Prompt wie gehabt inline mit. Caching darf
    den Chat nie blockieren.
    """
    notiz = _lies_notiz(run_dir)
    if notiz.get("cache") and notiz.get("cache_modell") == gemini_service.GEMINI_MODEL:
        try:
            gemini_service.client.caches.get(name=notiz["cache"])
            return notiz["cache"]
        except Exception:  # noqa: BLE001 — abgelaufen oder gelöscht: neu anlegen
            pass

    try:
        cache = gemini_service.client.caches.create(
            model=gemini_service.GEMINI_MODEL,
            config=types.CreateCachedContentConfig(
                display_name=f"analyst-chat-{run_dir.name}",
                system_instruction=system,
                contents=[
                    {"role": "user", "parts": [
                        types.Part(file_data=types.FileData(
                            file_uri=handle.uri, mime_type=handle.mime_type)),
                        {"text": f"Hier ist das Video und die Analyse dazu:\n\n{kontext}"},
                    ]}
                ],
                ttl=CACHE_TTL,
            ),
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [CHAT] Kein expliziter Cache für {run_dir.name} ({e}) — sende inline.")
        return None

    _merke(run_dir, {"cache": cache.name, "cache_modell": gemini_service.GEMINI_MODEL})
    return cache.name


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

def _contents(kontext: str, verlauf: list[dict], frage: str, video_part=None) -> list[dict]:
    """Kontext und Video hängen an der ERSTEN Nutzer-Nachricht, nicht an jeder.

    Innerhalb eines Calls wird das Video damit genau einmal referenziert statt einmal pro
    Verlaufseintrag. Die gefakte Modell-Antwort danach verankert die Rolle, ohne dass das
    Modell den Kontext erst zusammenfassen will.
    """
    erste_parts: list = []
    if video_part is not None:
        erste_parts.append(video_part)
    erste_parts.append({"text": f"Hier ist das Video und die Analyse dazu:\n\n{kontext}"})

    inhalte: list[dict] = [
        {"role": "user", "parts": erste_parts},
        {"role": "model", "parts": [{"text": "Verstanden. Stell deine Fragen."}]},
    ]
    for n in verlauf[-MAX_VERLAUF:]:
        rolle = "model" if n.get("rolle") == "model" else "user"
        inhalte.append({"role": rolle, "parts": [{"text": n.get("text", "")}]})
    inhalte.append({"role": "user", "parts": [{"text": frage}]})
    return inhalte


def _stream_mit_fallback(contents, system: str, info: dict, cache_name: str | None = None):
    """Textstücke vom ersten Modell, das antwortet. Schreibt Metadaten nach `info`.

    Der Cache-Name gilt nur für das Primärmodell (ein Cache ist an ein Modell gebunden) und
    ersetzt dort die system_instruction — sie steckt bereits im Cache.

    Bricht der Stream ab, NACHDEM schon Text geflossen ist, wird nicht auf ein anderes Modell
    umgeschaltet: Der Nutzer bekäme den Anfang sonst ein zweites Mal in dieselbe Blase.
    Abgeschnitten ist besser als doppelt.
    """
    letzter_fehler = None
    for modell in [gemini_service.GEMINI_MODEL] + gemini_service.GEMINI_FALLBACK_MODELS:
        nutzt_cache = bool(cache_name) and modell == gemini_service.GEMINI_MODEL
        cfg = (types.GenerateContentConfig(cached_content=cache_name) if nutzt_cache
               else types.GenerateContentConfig(system_instruction=system))
        etwas_geflossen = False
        try:
            for chunk in gemini_service.client.models.generate_content_stream(
                model=modell, contents=contents, config=cfg
            ):
                verbrauch = getattr(chunk, "usage_metadata", None)
                if verbrauch is not None:
                    info["cached_tokens"] = getattr(verbrauch, "cached_content_token_count", None)
                stueck = getattr(chunk, "text", None)
                if stueck:
                    etwas_geflossen = True
                    info["modell"] = modell
                    info["cache_genutzt"] = nutzt_cache
                    yield stueck
        except Exception as e:  # noqa: BLE001 — jeder Modellfehler soll das nächste Modell probieren
            if etwas_geflossen:
                info["abgebrochen"] = True
                return
            letzter_fehler = e
            continue
        info.setdefault("modell", modell)
        info.setdefault("cache_genutzt", nutzt_cache)
        return

    raise RuntimeError(f"Gemini-Chat fehlgeschlagen: {letzter_fehler}")


def stream_antwort(run_dir: Path, kontext: str, frage: str):
    """Generator: gibt Textstücke aus, sobald sie kommen, und schreibt am Ende die komplette
    Antwort in den Verlauf.

    **Zweistufig, damit das Video nur mitgeht, wenn es gebraucht wird.**
    Stufe 1 läuft ohne Video, nur mit der Analyse als Text. Kann das Modell die Frage daraus
    beantworten, tut es das und der Nutzer sieht die Antwort sofort. Braucht es das Video,
    gibt es stattdessen VIDEO_MARKER aus — dann läuft Stufe 2 mit Video.

    Die Entscheidung trifft bewusst das Modell und keine Stichwortliste: „schau mal auf die
    Einblendung bei Sekunde 12" und „warum ist meine Hook nur eine 3" unterscheiden sich nicht
    zuverlässig an einzelnen Wörtern, und eine Liste geht in beide Richtungen daneben.
    Kosten der Fehlentscheidung sind asymmetrisch — ein unnötiger Text-Call ist billig, eine
    geratene Antwort ohne Nachsehen ist falsch. Deshalb sagt die Regel: im Zweifel Video.

    Der Marker erreicht den Nutzer nie: Die ersten Stücke werden gepuffert, bis feststeht,
    ob ein Marker kommt.

    Bewusst ein SYNCHRONER Generator: FastAPI führt synchrone Endpunkte in einem Threadpool
    aus. Als `async` würde der blockierende Gemini-Call den Event-Loop anhalten — bei
    `--workers 1` steht dann die komplette App, inklusive laufender Hintergrund-Analysen.

    Keine Sampling-Parameter (temperature/top_p/top_k): bei 3.5 noch erlaubt, ab Gemini 3.6
    deprecated — siehe Hinweis in gemini_service.py. Für einen Chat braucht es sie ohnehin nicht.
    """
    haenge_nachricht_an(run_dir, "user", frage)
    verlauf = lade_verlauf(run_dir)[:-1]   # die gerade geschriebene Frage nicht doppelt senden

    info: dict = {}
    gesammelt: list[str] = []
    mit_video = False

    def _abschliessen(system_fuer_log: str) -> None:
        antwort = "".join(gesammelt)
        haenge_nachricht_an(run_dir, "model", antwort)
        analyst_prompt_log.log_call(
            run_dir, call="chat", recipient="Gemini", model=info.get("modell", ""),
            system_prompt=system_fuer_log, user_message=frage, output_raw=antwort,
            attachments=["Video"] if mit_video else [],
            # Der Analyse-Kontext geht als Historie bzw. über den Cache mit, nicht in der
            # User-Message. Ihn hier voll zu loggen würde prompt_log.md mit jedem Turn erneut
            # aufblähen — deshalb nur seine Größe, der Inhalt steht ohnehin in analysis.json.
            inputs={
                "verlauf_nachrichten": len(verlauf),
                "kontext_zeichen": len(kontext),
                "video_mitgeschickt": mit_video,
                "cache_genutzt": info.get("cache_genutzt", False),
                "cached_tokens": info.get("cached_tokens"),
                "abgebrochen": info.get("abgebrochen", False),
            },
        )
        schreibe_verlauf_md(run_dir)

    # ---- Stufe 1: ohne Video, mit Anforderungs-Möglichkeit ----
    system1 = chat_system_prompt(mit_video=False) + "\n\n" + ENTSCHEIDUNGS_REGEL
    puffer: list[str] = []
    entschieden = False
    braucht_video = False

    for stueck in _stream_mit_fallback(_contents(kontext, verlauf, frage), system1, info):
        if entschieden:
            gesammelt.append(stueck)
            yield stueck
            continue
        puffer.append(stueck)
        angefangen = "".join(puffer).lstrip()
        if len(angefangen) >= len(VIDEO_MARKER):
            entschieden = True
            braucht_video = angefangen.startswith(VIDEO_MARKER)
            if braucht_video:
                break
            gesammelt.extend(puffer)
            yield "".join(puffer)
            puffer = []
        elif not VIDEO_MARKER.startswith(angefangen):
            # Kann kein Marker mehr werden (z.B. „Weil…") → freigeben und normal weiterlaufen
            entschieden = True
            gesammelt.extend(puffer)
            yield "".join(puffer)
            puffer = []

    if not braucht_video:
        if puffer:                       # sehr kurze Antwort, Puffer nie freigegeben
            gesammelt.extend(puffer)
            yield "".join(puffer)
        _abschliessen(system1)
        return

    # ---- Stufe 2: mit Video ----
    try:
        handle = hole_video_handle(run_dir)
    except Exception as e:  # noqa: BLE001 — Datei weg, Upload fehlgeschlagen, Quota
        print(f"  [CHAT] WARN: Video für {run_dir.name} nicht verfügbar: {e}")
        hinweis = ("Dazu müsste ich mir das Video ansehen — die Datei zu diesem Lauf ist "
                   "aber nicht mehr verfügbar. Frag mich gern etwas zur bestehenden Bewertung.")
        gesammelt.append(hinweis)
        yield hinweis
        _abschliessen(system1)
        return

    mit_video = True
    system2 = chat_system_prompt(mit_video=True)
    cache_name = hole_cache(run_dir, system2, kontext, handle)

    if cache_name:
        # Systemprompt, Video und Kontext stecken im Cache → nur noch Verlauf und Frage senden.
        contents2 = []
        for n in verlauf[-MAX_VERLAUF:]:
            rolle = "model" if n.get("rolle") == "model" else "user"
            contents2.append({"role": rolle, "parts": [{"text": n.get("text", "")}]})
        contents2.append({"role": "user", "parts": [{"text": frage}]})
    else:
        video_part = types.Part(
            file_data=types.FileData(file_uri=handle.uri, mime_type=handle.mime_type)
        )
        contents2 = _contents(kontext, verlauf, frage, video_part)

    for stueck in _stream_mit_fallback(contents2, system2, info, cache_name):
        gesammelt.append(stueck)
        yield stueck

    _abschliessen(system2)
