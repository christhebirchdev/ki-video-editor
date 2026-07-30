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

from models.analyst import AnalystEvaluationV2, AnalystResult, HookEval, SpeechStats


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


# ---------- Prompt: Regelerhalt ----------

def test_chat_prompt_traegt_die_analysten_regeln():
    """Der Kern der Anforderung: Auch eine vertiefte Analyse einzelner Aspekte folgt denselben
    Regeln wie die Hauptanalyse. Die Regeln liegen in analyst_eval_skill.md und werden zur
    Laufzeit geladen — ändert jemand sie dort, ändern sie sich hier mit."""
    from services import analyst_chat, analyst_eval
    p = analyst_chat.chat_system_prompt()
    skill = analyst_eval.load_skill_body()
    assert skill[:400] in p, "Der Skill-Body muss unverändert im Chat-Prompt stehen"


def test_chat_prompt_enthaelt_nicht_den_json_vertrag_des_gesamtlaufs():
    """OUTPUT_SCHEMA verlangt performance_score und action_steps. Die berechnet der Code über
    das ganze Video — ein Chat, der sie neu vergibt, widerspricht der Anzeige."""
    from services import analyst_chat, analyst_eval
    assert analyst_eval.OUTPUT_SCHEMA not in analyst_chat.chat_system_prompt()


def test_chat_vertrag_verbietet_gesamtnote_und_action_steps():
    from services import analyst_chat
    v = analyst_chat.CHAT_VERTRAG.lower()
    assert "kein neuer performance_score" in v
    assert "keine neuen action_steps" in v


def test_chat_vertrag_sagt_dass_das_video_vorliegt():
    """Vorher stand hier das Gegenteil — der Chat antwortete, er habe das Video nicht."""
    from services import analyst_chat
    assert "video liegt dir vor" in analyst_chat.CHAT_VERTRAG.lower()
    assert "video nicht" not in analyst_chat.CHAT_VERTRAG.lower()


def test_prompt_sagt_ausdruecklich_bescheid_wenn_das_video_fehlt():
    """Alte Läufe können aufgeräumt sein. Dann darf das Modell nicht so tun, als sähe es etwas."""
    from services import analyst_chat
    ohne = analyst_chat.chat_system_prompt(mit_video=False)
    assert "NICHT vor" in ohne
    assert "NICHT vor" not in analyst_chat.chat_system_prompt(mit_video=True)


def test_chat_vertrag_nennt_die_pausenschwelle():
    """CLAUDE.md: Wird die Schwelle nicht genannt, rät das Modell an Stellen herum, die gar
    nicht gemeldet wurden."""
    from services import analyst_chat
    from services.analyst_speech import PAUSE_THRESHOLD_SEC
    assert str(PAUSE_THRESHOLD_SEC) in analyst_chat.CHAT_VERTRAG


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


# ---------- Video im Chat ----------

class _FakeHandle:
    uri = "files/abc"
    mime_type = "video/mp4"
    name = "files/abc"
    state = "ACTIVE"


def _stufen_stream(monkeypatch, analyst_chat, antworten, protokoll):
    """Fake-Stream, der pro Aufruf die nächste vorbereitete Antwort liefert und jeden Call
    mitschreibt. `antworten` ist eine Liste von Textlisten — eine je Stufe."""
    def fake_stream(*, model, contents, config):
        protokoll.append({
            "model": model,
            "contents": contents,
            "system": getattr(config, "system_instruction", None),
            "cached": getattr(config, "cached_content", None),
        })
        stuecke = antworten[min(len(protokoll) - 1, len(antworten) - 1)]
        return iter([_FakeChunk(s) for s in stuecke])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    return protokoll


def test_ohne_video_wenn_die_analyse_reicht(tmp_path, monkeypatch):
    """Der Punkt der Zweistufigkeit: Reicht die Analyse, wird das Video gar nicht erst
    geschickt — es bleibt bei EINEM Call ohne Videotokens."""
    from services import analyst_chat

    monkeypatch.setattr(analyst_chat, "hole_video_handle",
                        lambda _: (_ for _ in ()).throw(AssertionError("Video darf nicht geholt werden")))
    calls = _stufen_stream(monkeypatch, analyst_chat, [["Weil die Hook ", "allgemein startet."]], [])

    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Warum nur eine 3?"))
    assert "".join(stuecke) == "Weil die Hook allgemein startet."
    assert len(calls) == 1
    assert not [p for c in calls[0]["contents"] for p in c["parts"] if hasattr(p, "file_data")]


def test_zweite_stufe_mit_video_wenn_das_modell_es_anfordert(tmp_path, monkeypatch):
    from services import analyst_chat

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())
    monkeypatch.setattr(analyst_chat, "hole_cache", lambda *a, **k: None)
    calls = _stufen_stream(monkeypatch, analyst_chat,
                           [["[VIDEO]"], ["Die Untertitel ", "laufen zu schnell."]], [])

    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Schau dir die Untertitel an"))
    assert "".join(stuecke) == "Die Untertitel laufen zu schnell."
    assert len(calls) == 2

    video_parts = [p for c in calls[1]["contents"] for p in c["parts"] if hasattr(p, "file_data")]
    assert len(video_parts) == 1                  # genau einmal, an der ersten Nachricht
    assert "NICHT vor" not in calls[1]["system"]


def test_marker_erreicht_den_nutzer_nie(tmp_path, monkeypatch):
    """Der Marker ist Maschinerie. Stünde er in der Blase, wäre das ein sichtbarer Bug —
    auch dann, wenn er stückweise über mehrere Chunks eintrudelt."""
    from services import analyst_chat

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())
    monkeypatch.setattr(analyst_chat, "hole_cache", lambda *a, **k: None)
    _stufen_stream(monkeypatch, analyst_chat, [["[VI", "DEO]"], ["Antwort mit Video."]], [])

    stuecke = list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Sieh nach"))
    voll = "".join(stuecke)
    assert analyst_chat.VIDEO_MARKER not in voll
    assert voll == "Antwort mit Video."
    assert analyst_chat.VIDEO_MARKER not in analyst_chat.lade_verlauf(tmp_path)[-1]["text"]


def test_kurze_antwort_ohne_marker_geht_vollstaendig_raus(tmp_path, monkeypatch):
    """Randfall: Die Antwort ist kürzer als der Marker — der Puffer darf sie nicht verschlucken."""
    from services import analyst_chat

    _stufen_stream(monkeypatch, analyst_chat, [["Ja."]], [])
    assert "".join(list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Reicht das?"))) == "Ja."


def test_cache_wird_nur_beim_primaermodell_verwendet(tmp_path, monkeypatch):
    """Ein Cache ist an EIN Modell gebunden. Auf einem Fallback-Modell wäre der Name ungültig,
    der Call würde scheitern statt nur teurer zu sein."""
    from services import analyst_chat

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())
    monkeypatch.setattr(analyst_chat, "hole_cache", lambda *a, **k: "caches/xyz")
    protokoll = []

    def fake_stream(*, model, contents, config):
        protokoll.append({"model": model, "cached": getattr(config, "cached_content", None),
                          "system": getattr(config, "system_instruction", None)})
        if len(protokoll) == 1:
            return iter([_FakeChunk("[VIDEO]")])
        if len(protokoll) == 2:
            raise RuntimeError("503")           # Primärmodell fällt in Stufe 2 aus
        return iter([_FakeChunk("ok")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Sieh nach"))

    primaer = protokoll[1]
    fallback = protokoll[2]
    assert primaer["model"] == analyst_chat.gemini_service.GEMINI_MODEL
    assert primaer["cached"] == "caches/xyz" and primaer["system"] is None
    assert fallback["cached"] is None and fallback["system"], "Fallback braucht den Systemprompt inline"


def test_ohne_cache_geht_alles_inline(tmp_path, monkeypatch):
    """Schlägt das Anlegen fehl (Mindest-Tokenzahl, Quota), darf der Chat nicht stehenbleiben."""
    from services import analyst_chat

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())

    def platzt(*a, **k):
        raise RuntimeError("Mindest-Tokenzahl unterschritten")

    # client.caches ist ein Property → die Methoden darauf patchen, nicht das Attribut
    monkeypatch.setattr(analyst_chat.gemini_service.client.caches, "get", platzt)
    monkeypatch.setattr(analyst_chat.gemini_service.client.caches, "create", platzt)
    calls = _stufen_stream(monkeypatch, analyst_chat, [["[VIDEO]"], ["Antwort."]], [])

    assert "".join(list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Sieh nach"))) == "Antwort."
    assert calls[1]["cached"] is None
    assert [p for c in calls[1]["contents"] for p in c["parts"] if hasattr(p, "file_data")]


def test_cache_wird_wiederverwendet_und_ist_modellgebunden(tmp_path, monkeypatch):
    from services import analyst_chat

    erstellt = []

    class FakeCache:
        name = "caches/abc"

    def fake_get(name):
        if name != "caches/abc":
            raise RuntimeError("unbekannt")
        return FakeCache()

    def fake_create(**kwargs):
        erstellt.append(kwargs)
        return FakeCache()

    monkeypatch.setattr(analyst_chat.gemini_service.client.caches, "get", fake_get)
    monkeypatch.setattr(analyst_chat.gemini_service.client.caches, "create", fake_create)

    a = analyst_chat.hole_cache(tmp_path, "sys", "kontext", _FakeHandle())
    b = analyst_chat.hole_cache(tmp_path, "sys", "kontext", _FakeHandle())
    assert a == b == "caches/abc"
    assert len(erstellt) == 1

    # Modellwechsel → der alte Cache gilt nicht mehr
    monkeypatch.setattr(analyst_chat.gemini_service, "GEMINI_MODEL", "gemini-anders")
    analyst_chat.hole_cache(tmp_path, "sys", "kontext", _FakeHandle())
    assert len(erstellt) == 2


def test_bisheriger_chatverlauf_geht_in_beide_stufen_mit(tmp_path, monkeypatch):
    """Ohne den Verlauf wäre jede Nachricht ein Gespräch von vorn — „und was war mit dem
    zweiten Punkt?" wäre nicht beantwortbar. Gilt für die Textstufe UND die Videostufe."""
    from services import analyst_chat

    analyst_chat.haenge_nachricht_an(tmp_path, "user", "Was ist mit der Hook?")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Sie startet zu allgemein.")

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())
    monkeypatch.setattr(analyst_chat, "hole_cache", lambda *a, **k: None)
    calls = _stufen_stream(monkeypatch, analyst_chat, [["[VIDEO]"], ["Konkret: …"]], [])

    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Und wie mache ich das konkret?"))

    for stufe in (0, 1):
        texte = [t.get("text", "") for c in calls[stufe]["contents"]
                 for t in c["parts"] if isinstance(t, dict)]
        assert "Was ist mit der Hook?" in texte, f"Stufe {stufe + 1} ohne Verlauf"
        assert "Sie startet zu allgemein." in texte, f"Stufe {stufe + 1} ohne Verlauf"
        assert "Und wie mache ich das konkret?" in texte


def test_verlauf_geht_auch_mit_cache_mit(tmp_path, monkeypatch):
    """Im Cache stecken Systemprompt, Video und Analyse — der Verlauf NICHT, der ändert sich
    ja mit jeder Nachricht. Er muss weiter mitgeschickt werden."""
    from services import analyst_chat

    analyst_chat.haenge_nachricht_an(tmp_path, "user", "Frühere Frage")
    analyst_chat.haenge_nachricht_an(tmp_path, "model", "Frühere Antwort")

    monkeypatch.setattr(analyst_chat, "hole_video_handle", lambda _: _FakeHandle())
    monkeypatch.setattr(analyst_chat, "hole_cache", lambda *a, **k: "caches/xyz")
    calls = _stufen_stream(monkeypatch, analyst_chat, [["[VIDEO]"], ["ok"]], [])

    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Neue Frage"))

    texte = [t.get("text", "") for c in calls[1]["contents"]
             for t in c["parts"] if isinstance(t, dict)]
    assert "Frühere Frage" in texte and "Frühere Antwort" in texte
    assert "Neue Frage" in texte
    # Video und Kontext stecken im Cache, nicht nochmal in den contents
    assert not [p for c in calls[1]["contents"] for p in c["parts"] if hasattr(p, "file_data")]
    assert not any("Hier ist das Video" in t for t in texte)


def test_denkstufe_wird_gesetzt_und_ist_kettenweit_gueltig(tmp_path, monkeypatch):
    """Ohne Angabe denkt gemini-3.5-flash auf "medium" — der Hauptgrund für die Wartezeit vor
    dem ersten Wort. "minimal" wäre schneller, unterstützt aber gemini-3.1-pro-preview aus der
    Fallback-Kette nicht; "low" alle Modelle der Kette."""
    from services import analyst_chat

    protokoll = []

    def fake_stream(*, model, contents, config):
        protokoll.append(getattr(config, "thinking_config", None))
        return iter([_FakeChunk("kurz")])

    monkeypatch.setattr(analyst_chat.gemini_service.client.models, "generate_content_stream", fake_stream)
    list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Frage?"))

    assert protokoll[0] is not None
    # Das SDK normalisiert den String zu einem Enum (ThinkingLevel.LOW) → über den Wert prüfen
    stufe = protokoll[0].thinking_level
    assert str(getattr(stufe, "value", stufe)).lower() == analyst_chat.DENK_STUFE
    assert analyst_chat.DENK_STUFE == "low", "minimal unterstuetzt 3.1-pro-preview nicht"


def test_chat_laeuft_weiter_wenn_das_video_fehlt(tmp_path, monkeypatch):
    """Alte Läufe können aufgeräumt sein. Ein harter Fehler wäre die schlechtere Wahl —
    der Nutzer bekommt eine Erklärung statt eines roten Kastens."""
    from services import analyst_chat

    def kaputt(_):
        raise FileNotFoundError("weg")

    monkeypatch.setattr(analyst_chat, "hole_video_handle", kaputt)
    _stufen_stream(monkeypatch, analyst_chat, [["[VIDEO]"]], [])

    voll = "".join(list(analyst_chat.stream_antwort(tmp_path, "Kontext", "Wie sind die Untertitel?")))
    assert "nicht mehr verfügbar" in voll
    assert analyst_chat.VIDEO_MARKER not in voll


def test_video_handle_wird_wiederverwendet(tmp_path, monkeypatch):
    """Ohne Cache lädt jede Chat-Nachricht dasselbe Video erneut hoch — die teuerste und
    langsamste Stelle des Features."""
    from services import analyst_chat

    uploads = []
    monkeypatch.setattr(analyst_chat, "_video_pfad", lambda _: tmp_path / "video.mp4")
    monkeypatch.setattr(analyst_chat.gemini_service, "_upload_video_to_gemini",
                        lambda p: (uploads.append(p), _FakeHandle())[1])
    monkeypatch.setattr(analyst_chat.gemini_service.client.files, "get", lambda name: _FakeHandle())

    analyst_chat.hole_video_handle(tmp_path)
    analyst_chat.hole_video_handle(tmp_path)
    assert len(uploads) == 1
    assert (tmp_path / "gemini_file.json").exists()


def test_video_handle_wird_neu_geladen_wenn_die_datei_weg_ist(tmp_path, monkeypatch):
    """Die Files API hält Uploads rund 48 Stunden. Danach muss neu hochgeladen werden."""
    from services import analyst_chat

    (tmp_path / "gemini_file.json").write_text(json.dumps({"name": "files/abgelaufen"}), encoding="utf-8")
    uploads = []
    monkeypatch.setattr(analyst_chat, "_video_pfad", lambda _: tmp_path / "video.mp4")
    monkeypatch.setattr(analyst_chat.gemini_service, "_upload_video_to_gemini",
                        lambda p: (uploads.append(p), _FakeHandle())[1])

    def weg(name):
        raise RuntimeError("404 not found")

    monkeypatch.setattr(analyst_chat.gemini_service.client.files, "get", weg)
    analyst_chat.hole_video_handle(tmp_path)
    assert len(uploads) == 1


def test_video_pfad_meldet_fehlende_datei(tmp_path):
    from services import analyst_chat
    with pytest.raises(FileNotFoundError):
        analyst_chat._video_pfad(tmp_path)


def test_video_pfad_findet_die_hochgeladene_datei(tmp_path):
    from services import analyst_chat
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "reel.mp4").write_bytes(b"x")
    assert analyst_chat._video_pfad(tmp_path).name == "reel.mp4"


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
