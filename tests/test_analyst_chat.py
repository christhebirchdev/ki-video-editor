# tests/test_analyst_chat.py
"""Tests für den V1.1-Rückfragen-Chat.

Eigene Datei statt Anhang an test_analyst.py: Der Chat ist ein abgegrenztes Feature, und
test_analyst.py ist mit 82 Tests ohnehin schon lang.
"""
import json
import os

os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test")

import pytest

from models.analyst import AnalystEvaluationV2, AnalystResult, HookEval, Pause, SpeechStats


def _result() -> AnalystResult:
    """Minimales, aber realistisches Ergebnis — so sieht analysis.json aus."""
    return AnalystResult(
        id="abc123",
        filename="reel.mp4",
        duration_sec=31.5,
        scene_count=0,
        scenes=[],
        transcript="Hallo, heute zeige ich dir drei Fehler.",
        speech_stats=SpeechStats(
            wort_anzahl=7, sprech_dauer_sec=30.0, wpm=140.0,
            filler_count=1, filler_words=["ähm"],
            pausen_count=2, laengste_pause_sec=1.2, sprechbeginn_sec=0.4,
        ),
        evaluation=AnalystEvaluationV2(
            zielgruppe="Selbstständige Dachdecker",
            performance_score=72,
            hook=HookEval(sprech_hook_score=3, sprech_hook_grund="Startet zu allgemein."),
            staerken=["Klare Aussprache"],
            top_tipps=["Hook konkreter machen"],
        ),
        gewaehltes_format="Talking Head",
    )


# ---------- Kontext ----------

def test_kontext_enthaelt_bewertung_transkript_und_format():
    from services import analyst_chat
    text = analyst_chat.baue_kontext(_result())
    assert "Selbstständige Dachdecker" in text
    assert "Hallo, heute zeige ich dir drei Fehler." in text
    assert "Talking Head" in text
    assert "72" in text


def test_kontext_kennzeichnet_messwerte_als_deterministisch():
    """Ohne die Kennzeichnung behandelt das Modell gemessene Werte wie eigene Schätzungen
    und relativiert sie auf Nachfrage („könnte auch anders sein")."""
    from services import analyst_chat
    text = analyst_chat.baue_kontext(_result())
    assert "deterministisch" in text
    assert "140" in text          # wpm ist drin


def test_kontext_kommt_ohne_evaluation_nicht_ins_schleudern():
    """Ein Lauf mit skip_eval hat keine evaluation — der Chat darf daran nicht sterben."""
    from services import analyst_chat
    r = _result()
    r.evaluation = None
    text = analyst_chat.baue_kontext(r)
    assert "Hallo, heute zeige ich dir drei Fehler." in text
    assert isinstance(text, str) and text.strip()


# ---------- Verlauf ----------

def test_verlauf_wird_angehaengt_und_in_reihenfolge_gelesen(tmp_path):
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "Warum ist die Hook schwach?")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Weil sie allgemein startet.")
    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["rolle"] for n in verlauf] == ["user", "model"]
    assert verlauf[0]["text"] == "Warum ist die Hook schwach?"
    assert verlauf[1]["text"] == "Weil sie allgemein startet."
    assert verlauf[0]["ts"] <= verlauf[1]["ts"]


def test_verlauf_ohne_datei_ist_leer(tmp_path):
    from services import analyst_chat
    assert analyst_chat.lade_verlauf(tmp_path) == []


def test_kaputte_zeile_kippt_den_verlauf_nicht(tmp_path):
    """Ein abgebrochener Schreibvorgang darf nicht den ganzen Chat unlesbar machen."""
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "erste Frage")
    with (tmp_path / "chat.jsonl").open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Antwort")
    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["text"] for n in verlauf] == ["erste Frage", "Antwort"]


# ---------- Prompt ----------

def test_system_prompt_verbietet_das_aendern_der_bewertung():
    """V1.1 erklärt nur. Ohne diese Grenze erfindet das Modell neue Scores, die nicht mehr
    zu der Bewertung passen, die dem Nutzer angezeigt wird."""
    from services import analyst_chat
    p = analyst_chat.SYSTEM_PROMPT.lower()
    assert "änderst die bewertung nicht" in p
    assert "score" in p


def test_system_prompt_sagt_dass_das_video_nicht_vorliegt():
    """Sonst behauptet das Modell Beobachtungen, die es gar nicht machen konnte."""
    from services import analyst_chat
    assert "video nicht" in analyst_chat.SYSTEM_PROMPT.lower()


# ---------- Stream ----------

class _FakeChunk:
    def __init__(self, t):
        self.text = t


def test_stream_gibt_textstuecke_und_schreibt_verlauf(tmp_path, monkeypatch):
    from services import analyst_chat

    def fake_stream(*, model, contents, config):
        assert model
        return iter([_FakeChunk("Weil die "), _FakeChunk("Hook allgemein "), _FakeChunk("startet.")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)

    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext hier", "Warum schwach?"))
    assert "".join(stuecke) == "Weil die Hook allgemein startet."

    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["rolle"] for n in verlauf] == ["user", "model"]
    assert verlauf[1]["text"] == "Weil die Hook allgemein startet."


def test_stream_schickt_die_frage_nicht_doppelt(tmp_path, monkeypatch):
    """Die Frage wird VOR dem Call in den Verlauf geschrieben. Würde sie beim Bau der
    contents nicht wieder abgezogen, stünde sie zweimal im Prompt."""
    from services import analyst_chat
    gesehen = {}

    def fake_stream(*, model, contents, config):
        gesehen["contents"] = contents
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Meine Frage"))

    texte = [t["text"] for c in gesehen["contents"] for t in c["parts"]]
    assert texte.count("Meine Frage") == 1


def test_stream_faellt_auf_zweites_modell_zurueck(tmp_path, monkeypatch):
    """Wie im restlichen Projekt: erst Primärmodell, bei Fehler das nächste."""
    from services import analyst_chat
    versuche = []

    def fake_stream(*, model, contents, config):
        versuche.append(model)
        if len(versuche) == 1:
            raise RuntimeError("503")
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Frage?"))
    assert "".join(stuecke) == "ok"
    assert len(versuche) == 2


def test_abbruch_mitten_im_stream_wiederholt_nicht_auf_anderem_modell(tmp_path, monkeypatch):
    """Ein Neustart nach schon gesendeten Stücken würde dem Nutzer den Anfang ein zweites Mal
    in dieselbe Blase schreiben. Abgeschnitten ist besser als doppelt."""
    from services import analyst_chat
    versuche = []

    def fake_stream(*, model, contents, config):
        versuche.append(model)

        def gen():
            yield _FakeChunk("Erster Teil")
            raise RuntimeError("Verbindung weg")
        return gen()

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Frage?"))
    assert "".join(stuecke) == "Erster Teil"
    assert len(versuche) == 1                       # kein zweites Modell
    assert analyst_chat.lade_verlauf(tmp_path)[-1]["text"] == "Erster Teil"


def test_verlauf_wird_gedeckelt(tmp_path, monkeypatch):
    """Ohne Deckel zahlt jede neue Frage den kompletten bisherigen Chat mit."""
    from services import analyst_chat
    for i in range(30):
        analyst_chat.haenge_nachricht_an(tmp_path, "user" if i % 2 == 0 else "model", f"n{i}")
    gesehen = {}

    def fake_stream(*, model, contents, config):
        gesehen["contents"] = contents
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Neue Frage"))
    # 2 Kontext-Turns + max. MAX_VERLAUF Verlaufsnachrichten + 1 aktuelle Frage
    assert len(gesehen["contents"]) <= 2 + analyst_chat.MAX_VERLAUF + 1


# ---------- Ausschnitts-Analyse ----------

def test_segment_prompt_traegt_die_analysten_regeln():
    """Der Kern der Anforderung: Ein Ausschnitt wird nach denselben Regeln beurteilt wie das
    ganze Video. Die Regeln liegen in analyst_eval_skill.md und werden zur Laufzeit geladen —
    ändert jemand sie dort, ändern sie sich hier mit."""
    from services import analyst_chat, analyst_eval
    p = analyst_chat.segment_system_prompt()
    skill = analyst_eval.load_skill_body()
    assert skill[:400] in p, "Der Skill-Body muss unverändert im Segment-Prompt stehen"


def test_segment_prompt_enthaelt_nicht_den_json_vertrag_des_gesamtlaufs():
    """OUTPUT_SCHEMA verlangt performance_score und action_steps. Die berechnet der Code über
    das ganze Video — ein Ausschnitt, der sie mitliefert, widerspricht der Anzeige."""
    from services import analyst_chat, analyst_eval
    p = analyst_chat.segment_system_prompt()
    assert analyst_eval.OUTPUT_SCHEMA not in p


def test_segment_vertrag_verbietet_gesamtscore_und_action_steps():
    from services import analyst_chat
    v = analyst_chat.SEGMENT_VERTRAG.lower()
    assert "kein performance_score" in v
    assert "keine action_steps" in v


def test_segment_vertrag_nennt_die_pausenschwelle():
    """CLAUDE.md: Wird die Schwelle nicht genannt, rät das Modell an Stellen herum, die gar
    nicht gemeldet wurden."""
    from services import analyst_chat
    from services.analyst_speech import PAUSE_THRESHOLD_SEC
    assert str(PAUSE_THRESHOLD_SEC) in analyst_chat.SEGMENT_VERTRAG


def test_nur_pausen_im_fenster_gehen_mit():
    """Pausen außerhalb des gezeigten Fensters sieht das Modell im Video nicht — es würde sie
    trotzdem als Beobachtung ausgeben."""
    from services import analyst_chat
    r = _result()
    r.speech_stats.pausen = [
        Pause(start_sec=1.0, end_sec=2.0, dauer_sec=1.0),
        Pause(start_sec=12.0, end_sec=13.2, dauer_sec=1.2),
        Pause(start_sec=25.0, end_sec=26.0, dauer_sec=1.0),
    ]
    drin = analyst_chat._pausen_im_fenster(r, 10.0, 20.0)
    assert [p.start_sec for p in drin] == [12.0]

    text = analyst_chat.baue_ausschnitt_nachricht(r, 10.0, 20.0, "")
    assert "12.0" in text
    assert "25.0" not in text


def test_nachricht_ohne_pausen_sagt_das_ausdruecklich():
    """Schweigen wäre mehrdeutig: 'nicht gemessen' oder 'keine vorhanden'?"""
    from services import analyst_chat
    r = _result()
    r.speech_stats.pausen = []
    text = analyst_chat.baue_ausschnitt_nachricht(r, 0.0, 5.0, "")
    assert "keine über" in text


def test_ausschnitt_setzt_das_zeitfenster_am_video_part(tmp_path, monkeypatch):
    """Das Fenster muss über VideoMetadata gehen, nicht als Prompt-Hinweis — sonst wird das
    ganze Video verarbeitet und abgerechnet."""
    from services import analyst_chat

    class FakeHandle:
        uri = "files/abc"
        mime_type = "video/mp4"
        name = "files/abc"

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: FakeHandle())
    gesehen = {}

    def fake_stream(*, model, contents, config):
        gesehen["contents"] = contents
        gesehen["system"] = config.system_instruction
        return iter([_FakeChunk("Der Einstieg wirkt zu ruhig.")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)

    stuecke = list(analyst_chat.stream_ausschnitt(tmp_path, _result(), 3.0, 9.5, "Wie wirkt das?"))
    assert "".join(stuecke) == "Der Einstieg wirkt zu ruhig."

    part = gesehen["contents"][0]["parts"][0]
    assert part.video_metadata.start_offset == "3.0s"
    assert part.video_metadata.end_offset == "9.5s"
    assert part.file_data.file_uri == "files/abc"


def test_ausschnitt_landet_im_chatverlauf_und_im_log(tmp_path, monkeypatch):
    from services import analyst_chat

    class FakeHandle:
        uri = "files/abc"; mime_type = "video/mp4"; name = "files/abc"

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: FakeHandle())
    monkeypatch.setattr(
        analyst_chat.gemini_service.client.models, "generate_content_stream",
        lambda **_: iter([_FakeChunk("Beobachtung zum Ausschnitt.")]),
    )
    list(analyst_chat.stream_ausschnitt(tmp_path, _result(), 2.0, 8.0, ""))

    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert verlauf[0]["rolle"] == "user" and "Ausschnitt 2.0" in verlauf[0]["text"]
    assert verlauf[1]["text"] == "Beobachtung zum Ausschnitt."
    assert "chat_ausschnitt" in (tmp_path / "prompt_log.md").read_text(encoding="utf-8")


def test_video_handle_wird_wiederverwendet(tmp_path, monkeypatch):
    """Ohne Cache lädt jede Ausschnitts-Frage dasselbe Video erneut hoch — die teuerste und
    langsamste Stelle des Features."""
    from services import analyst_chat

    class FakeHandle:
        def __init__(self): self.name = "files/xyz"; self.uri = "files/xyz"; self.state = "ACTIVE"

    uploads = []
    monkeypatch.setattr(analyst_chat, "_video_pfad", lambda _: tmp_path / "video.mp4")
    monkeypatch.setattr(analyst_chat.gemini_service, "_upload_video_to_gemini",
                        lambda p: (uploads.append(p), FakeHandle())[1])
    monkeypatch.setattr(analyst_chat.gemini_service.client.files, "get",
                        lambda name: FakeHandle())

    analyst_chat.hole_video_handle(tmp_path)
    analyst_chat.hole_video_handle(tmp_path)
    assert len(uploads) == 1
    assert (tmp_path / "gemini_file.json").exists()


def test_video_handle_wird_neu_geladen_wenn_die_datei_weg_ist(tmp_path, monkeypatch):
    """Die Files API hält Uploads rund 48 Stunden. Danach muss neu hochgeladen werden."""
    from services import analyst_chat

    class FakeHandle:
        def __init__(self): self.name = "files/neu"; self.uri = "files/neu"; self.state = "ACTIVE"

    (tmp_path / "gemini_file.json").write_text(json.dumps({"name": "files/abgelaufen"}), encoding="utf-8")
    uploads = []
    monkeypatch.setattr(analyst_chat, "_video_pfad", lambda _: tmp_path / "video.mp4")
    monkeypatch.setattr(analyst_chat.gemini_service, "_upload_video_to_gemini",
                        lambda p: (uploads.append(p), FakeHandle())[1])

    def weg(name):
        raise RuntimeError("404 not found")

    monkeypatch.setattr(analyst_chat.gemini_service.client.files, "get", weg)
    handle = analyst_chat.hole_video_handle(tmp_path)
    assert len(uploads) == 1
    assert handle.name == "files/neu"


def test_video_pfad_meldet_fehlende_datei(tmp_path):
    from services import analyst_chat
    with pytest.raises(FileNotFoundError):
        analyst_chat._video_pfad(tmp_path)


# ---------- Endpoints ----------

def _fertiger_lauf(tmp_path, run_id="runA", dauer=31.5):
    lauf = tmp_path / run_id
    (lauf / "raw").mkdir(parents=True)
    (lauf / "raw" / "video.mp4").write_bytes(b"x")
    (lauf / "meta.json").write_text(json.dumps({"id": run_id, "filename": "video.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "done", "done": True}))
    (lauf / "analysis.json").write_text(json.dumps({
        "id": run_id, "filename": "video.mp4", "duration_sec": dauer,
        "scene_count": 0, "scenes": [],
    }))
    return lauf


@pytest.mark.parametrize("start,ende,grund", [
    (5.0, 5.0, "Ende nicht nach Start"),
    (5.0, 4.0, "Ende vor Start"),
    (0.0, 0.5, "kuerzer als das Minimum"),
    (0.0, 61.0, "laenger als das Maximum"),
    (0.0, 99.0, "laenger als das Video"),
])
def test_ausschnitt_endpoint_weist_ungueltige_fenster_ab(tmp_path, monkeypatch, start, ende, grund):
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    _fertiger_lauf(tmp_path)

    r = TestClient(main.app).post("/api/analyst/runA/chat/ausschnitt",
                                  json={"start_sec": start, "end_sec": ende})
    assert r.status_code == 422, grund


def test_ausschnitt_endpoint_meldet_fehlendes_video(tmp_path, monkeypatch):
    """Alte Läufe können aufgeräumt sein — dann ist das ein Zustandsproblem, kein Serverfehler."""
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    lauf = _fertiger_lauf(tmp_path, "runB")
    (lauf / "raw" / "video.mp4").unlink()
    (lauf / "raw").rmdir()

    r = TestClient(main.app).post("/api/analyst/runB/chat/ausschnitt",
                                  json={"start_sec": 1.0, "end_sec": 5.0})
    assert r.status_code == 409




def test_chat_endpoint_verweigert_lauf_ohne_fertige_analyse(tmp_path, monkeypatch):
    """Ein Chat über eine Analyse, die es nicht gibt, ist kein Serverfehler → 409, nicht 500."""
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)

    lauf = tmp_path / "run1"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run1", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "uploaded", "done": False}))

    r = TestClient(main.app).post("/api/analyst/run1/chat", json={"frage": "Warum?"})
    assert r.status_code == 409


def test_chat_endpoint_verweigert_leere_frage(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)

    lauf = tmp_path / "run2"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run2", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "done", "done": True}))
    (lauf / "analysis.json").write_text(json.dumps({
        "id": "run2", "filename": "a.mp4", "duration_sec": 10.0, "scene_count": 0, "scenes": [],
    }))

    r = TestClient(main.app).post("/api/analyst/run2/chat", json={"frage": "   "})
    assert r.status_code == 422


def test_jede_nachricht_bekommt_eine_eigene_id(tmp_path):
    """Die id ist die Klammer zum Admin-Feedback (field_id = "chat.<id>")."""
    from services import analyst_chat
    a = analyst_chat.haenge_nachricht_an(tmp_path, "user", "Frage")
    b = analyst_chat.haenge_nachricht_an(tmp_path, "model", "Antwort")
    assert a["id"] and b["id"] and a["id"] != b["id"]
    assert [n["id"] for n in analyst_chat.lade_verlauf(tmp_path)] == [a["id"], b["id"]]


def test_ids_verschieben_sich_nicht_durch_eine_kaputte_zeile(tmp_path):
    """Der eigentliche Grund für echte IDs: Über die Position würde ein bestehendes Feedback
    nach einer übersprungenen Zeile auf die falsche Antwort zeigen."""
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "eins")
    antwort = analyst_chat.haenge_nachricht_an(tmp_path, "model", "zwei")
    with (tmp_path / "chat.jsonl").open("r+", encoding="utf-8") as f:
        zeilen = f.readlines()
        f.seek(0)
        f.write("{kaputt\n" + "".join(zeilen))
    verlauf = analyst_chat.lade_verlauf(tmp_path)
    assert [n["text"] for n in verlauf] == ["eins", "zwei"]
    assert verlauf[1]["id"] == antwort["id"]     # trotz verschobener Position dieselbe id


def test_altnachricht_ohne_id_bekommt_positions_fallback(tmp_path):
    """Nachrichten aus der ersten Fassung haben keine id — das Frontend darf nicht auf
    undefined zugreifen."""
    from services import analyst_chat
    with (tmp_path / "chat.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-07-29T10:00:00", "rolle": "model", "text": "alt"}) + "\n")
    assert analyst_chat.lade_verlauf(tmp_path)[0]["id"] == "pos0"


def test_verlauf_md_enthaelt_nachrichten_und_ids(tmp_path):
    from services import analyst_chat
    (tmp_path / "meta.json").write_text(json.dumps({"filename": "reel.mp4"}), encoding="utf-8")
    analyst_chat.haenge_nachricht_an(tmp_path, "user", "Warum ist die Hook schwach?")
    antwort = analyst_chat.haenge_nachricht_an(tmp_path, "model", "Weil sie allgemein startet.")
    analyst_chat.schreibe_verlauf_md(tmp_path)

    md = (tmp_path / "chat_verlauf.md").read_text(encoding="utf-8")
    assert "reel.mp4" in md
    assert "Warum ist die Hook schwach?" in md
    assert "Weil sie allgemein startet." in md
    assert antwort["id"] in md


def test_verlauf_md_zeigt_die_admin_bewertung_zur_antwort(tmp_path):
    """Sonst müsste man feedback.jsonl und chat.jsonl beim Auswerten von Hand zusammenführen."""
    from services import analyst_chat
    antwort = analyst_chat.haenge_nachricht_an(tmp_path, "model", "Meine Antwort")
    with (tmp_path / "feedback.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "field_id": f"chat.{antwort['id']}", "verdict": "down",
            "text": "Zu allgemein, nennt keine Sekunde.",
        }, ensure_ascii=False) + "\n")
    analyst_chat.schreibe_verlauf_md(tmp_path)

    md = (tmp_path / "chat_verlauf.md").read_text(encoding="utf-8")
    assert "👎" in md
    assert "Zu allgemein, nennt keine Sekunde." in md


def test_verlauf_md_nimmt_den_letzten_feedback_eintrag(tmp_path):
    """feedback.jsonl ist append-only — für die Auswertung gilt der jeweils letzte Eintrag."""
    from services import analyst_chat
    antwort = analyst_chat.haenge_nachricht_an(tmp_path, "model", "Antwort")
    with (tmp_path / "feedback.jsonl").open("w", encoding="utf-8") as f:
        for verdict, text in (("down", "erst schlecht"), ("up", "doch gut")):
            f.write(json.dumps({
                "field_id": f"chat.{antwort['id']}", "verdict": verdict, "text": text,
            }, ensure_ascii=False) + "\n")
    analyst_chat.schreibe_verlauf_md(tmp_path)

    md = (tmp_path / "chat_verlauf.md").read_text(encoding="utf-8")
    assert "doch gut" in md
    assert "erst schlecht" not in md


def test_verlauf_md_bricht_den_chat_nicht_wenn_es_schiefgeht(tmp_path, monkeypatch):
    """Protokollieren darf nie die Funktion kippen — wie bei analyst_prompt_log."""
    from services import analyst_chat
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Antwort")
    monkeypatch.setattr(analyst_chat, "lade_verlauf", lambda _: (_ for _ in ()).throw(OSError("Platte voll")))
    analyst_chat.schreibe_verlauf_md(tmp_path)      # darf nicht werfen


def test_stream_schreibt_verlauf_md_und_prompt_log(tmp_path, monkeypatch):
    from services import analyst_chat

    def fake_stream(*, model, contents, config):
        return iter([_FakeChunk("Fertige Antwort.")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Meine Frage"))

    md = (tmp_path / "chat_verlauf.md").read_text(encoding="utf-8")
    assert "Meine Frage" in md and "Fertige Antwort." in md

    log = (tmp_path / "prompt_log.md").read_text(encoding="utf-8")
    assert "chat" in log and "Fertige Antwort." in log


def test_prompt_log_bekommt_nicht_den_ganzen_kontext(tmp_path, monkeypatch):
    """Der Analyse-Kontext geht als Historie mit, nicht in der User-Message. Ihn pro Turn voll
    zu loggen würde prompt_log.md mit jeder Frage erneut aufblähen."""
    from services import analyst_chat

    def fake_stream(*, model, contents, config):
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    riesiger_kontext = "EINZIGARTIGER_KONTEXT_MARKER " * 200
    list(analyst_chat.stream_antwort(tmp_path, riesiger_kontext, "Frage"))

    log = (tmp_path / "prompt_log.md").read_text(encoding="utf-8")
    assert "EINZIGARTIGER_KONTEXT_MARKER" not in log
    assert "kontext_zeichen" in log


def test_chat_feedback_landet_in_feedback_jsonl_und_im_verlauf(tmp_path, monkeypatch):
    """Der bestehende Feedback-Endpoint nimmt beliebige field_id — für den Chat brauchte es
    dort keine Änderung. Dieser Test hält das fest."""
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api
    from config import settings
    from services import analyst_chat

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)
    monkeypatch.setattr(settings, "admin_password", "geheim")

    lauf = tmp_path / "run4"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run4", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "done", "done": True}))
    antwort = analyst_chat.haenge_nachricht_an(lauf, "model", "Die Antwort der KI")

    r = TestClient(main.app).post(f"/api/analyst/run4/feedback", json={
        "password": "geheim", "field_id": f"chat.{antwort['id']}",
        "verdict": "up", "text": "Hilfreich erklärt.",
    })
    assert r.status_code == 200

    eintraege = [json.loads(z) for z in (lauf / "feedback.jsonl").read_text(encoding="utf-8").splitlines() if z.strip()]
    assert eintraege[-1]["field_id"] == f"chat.{antwort['id']}"
    assert eintraege[-1]["verdict"] == "up"

    md = (lauf / "chat_verlauf.md").read_text(encoding="utf-8")
    assert "Hilfreich erklärt." in md


def test_chat_verlauf_endpoint_liefert_gespeicherte_nachrichten(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from api import analyst as analyst_api
    from services import analyst_chat

    monkeypatch.setattr(analyst_api, "ANALYST_PATH", tmp_path)

    lauf = tmp_path / "run3"
    lauf.mkdir()
    (lauf / "meta.json").write_text(json.dumps({"id": "run3", "filename": "a.mp4"}))
    (lauf / "status.json").write_text(json.dumps({"phase": "done", "done": True}))
    analyst_chat.haenge_nachricht_an(lauf, "user", "Frage eins")

    r = TestClient(main.app).get("/api/analyst/run3/chat")
    assert r.status_code == 200
    assert r.json()["nachrichten"][0]["text"] == "Frage eins"
