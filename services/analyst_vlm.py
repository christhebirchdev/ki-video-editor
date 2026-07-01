# services/analyst_vlm.py
"""
Deterministische Beschreibung via Gemini.

Wir ziehen Frames selbst (analyst_frames: feste fps + Dedup) → identische Frames bei jedem Lauf,
nichts wird "weggefasst". Diese distinct Frames gehen GEBÜNDELT in EINEN Gemini-Call (ein Objekt
pro Frame, temperature 0). Audio kommt aus einem zweiten Call auf dem echten Video (Standbilder
haben keinen Ton). Gemini beschreibt nur — die Bewertung macht Claude.
"""
import json
import re
from pathlib import Path

from google.genai import types

from config import settings
from services import analyst_frames, gemini_service
from services.gemini_service import client, _call_with_retry, GEMINI_MODEL, GEMINI_FALLBACK_MODELS

SYSTEM_PROMPT = """Du bist ein präzises, objektives Beschreibungs-Tool für Kurzvideos.
Du bekommst MEHRERE Einzelbilder (Frames) aus EINEM Video, in zeitlicher Reihenfolge
(Zeitstempel stehen im User-Text). Beschreibe JEDEN Frame EINZELN und objektiv —
keine Deutung, keine Wertung. Nutze die REIHENFOLGE: Unterschiede zwischen aufeinanderfolgenden
Frames zeigen Dynamik (Textwechsel, Zoom, Animation, Bewegung).

Felder pro Frame:
- handlung: was im Bild passiert (Aktion, Objekte, Umgebung), 1 Satz.
- personen: Anzahl, grobe Beschreibung, Tätigkeit, Blickrichtung (in die Linse / nach unten / zur Seite / wechselnd — rein objektiv beschreiben, NICHT deuten, also nicht "unsicher"). Keine Identitäts-/Absichts-Spekulation.
- texte: Liste ALLER unabhängigen Texteinblendungen im Frame. Sind mehrere voneinander unabhängige
  Texte zu sehen (z.B. ein fester Titel oben UND ein mitlaufender Untertitel unten), liste sie EINZELN.
  Pro Eintrag: "wortlaut" (der Text WÖRTLICH) und "darstellung" (gehört zu DIESEM Text:
  DYNAMISCH = ändert sich von Frame zu Frame, z.B. wortweise Untertitel / STATISCH = über mehrere
  Frames gleich; dazu Position oben/mitte/unten und Stil). Kein Text → leere Liste [].
- kamera: Einstellungsgröße (Nah/Halbnah/Total), Perspektive.
- bild_fakten: komposition, licht, hintergrund.
- effekte: Vergleiche diesen Frame mit dem/den vorherigen und benenne Bewegung/Effekte:
  ZOOM (Ausschnitt wird größer/kleiner), Kamerafahrt, ANIMATION (Elemente erscheinen/bewegen sich,
  Text wird schrittweise aufgebaut), Aufblitzen/Flash, Übergänge, eingeblendetes B-Roll/Overlay,
  Zeitlupe. Wenn im Vergleich zum Vorframe nichts davon → "".

VERBOTEN: Interpretation, Wertung, Einordnung als "Hook/Titel/Untertitel", Dinge außerhalb des Bildes.
Antworte auf Deutsch, NUR als JSON-Array mit GENAU einem Objekt pro Frame, gleiche Reihenfolge:
[{"handlung":"...","personen":"...","texte":[{"wortlaut":"...","darstellung":"..."}],"kamera":"...",
"bild_fakten":{"komposition":"...","licht":"...","hintergrund":"..."},"effekte":"..."}]"""

AUDIO_PROMPT = """Du bist ein objektives Audio-Beschreibungs-Tool. Höre das Video genau.
Beschreibe NUR das Hörbare als kurze Zeitleiste (deutscher Fließtext mit groben Zeitmarken):
- Musik: vorhanden ja/nein, Stil/Stimmung, ab wann/bis wann, relative Lautstärke.
- Soundeffekte (SFX): Whoosh, Pieptöne, Klicks, Riser etc. mit ungefährem Zeitpunkt und relativer Lautstärke (dezent/präsent/zu laut ggü. der Stimme).
- Stimme objektiv: Lautstärke (laut/leise), Dynamik/Energie (ruhig/energetisch/monoton), Sprechtempo.
- Aufnahmequalität: Störgeräusche/Rauschen/Hall (ja/nein), Verständlichkeit der Stimme (klar/undeutlich/nuschelig), abrupt abgeschnittene Wortanfänge am Anfang oder an Schnitten.
KEINE Bild-Beschreibung, KEINE Wertung. Wenn nichts Hörbares: schreibe "kein nennenswertes Audio"."""

AUDIO_USER = "Beschreibe ausschließlich die Tonspur des Videos."

GAZE_PROMPT = """Du bist ein objektives Beobachtungs-Tool für den BLICKKONTAKT in einem Sprecher-Video.
Sieh dir das Video an (Bewegung, nicht Standbild) und achte AUSSCHLIESSLICH auf die Augen/Blickrichtung
der sprechenden Person. Kernfrage: Schaut sie in die Kamera-LINSE, oder geht der Blick wiederholt/dauerhaft
daneben — nach unten oder zur Seite (typisch, weil neben/unter der Linse ein Skript oder Teleprompter
abgelesen wird)?
Gib eine kurze deutsche Zeitleiste mit groben Zeitmarken (00:00–00:00):
- Fenster, in denen der Blick in der Linse liegt.
- Fenster, in denen der Blick wiederholt nach unten/zur Seite geht (Ablesen) — diese Stellen explizit nennen.
- Wenn der Blick ständig hin- und herwechselt, sag das.
Nur BEOBACHTEN, NICHT werten (nicht „unsicher"/„unprofessionell" schreiben). Keine sonstige Bildbeschreibung.
Kein Gesicht/keine Person erkennbar → schreibe „kein Gesicht erkennbar"."""

GAZE_USER = "Beschreibe ausschließlich die Blickrichtung der sprechenden Person über die Zeit."


def is_available() -> tuple[bool, str]:
    if not settings.gemini_api_key:
        return False, "GEMINI_API_KEY fehlt in der .env."
    return True, ""


def _texte(d: dict) -> list[dict]:
    """Normalisiert die Texteinblendungen (verknüpft Wortlaut + Darstellung). Robust gegen Altformat."""
    out = []
    raw = d.get("texte")
    if isinstance(raw, list):
        for t in raw:
            if isinstance(t, dict):
                out.append({"wortlaut": str(t.get("wortlaut", "") or t.get("text", "") or ""),
                            "darstellung": str(t.get("darstellung", "") or "")})
            elif isinstance(t, str):
                out.append({"wortlaut": t, "darstellung": ""})
    elif d.get("text_overlays"):  # Fallback: altes flaches Format
        out.append({"wortlaut": str(d.get("text_overlays", "")),
                    "darstellung": str(d.get("text_darstellung", "") or "")})
    return [t for t in out if t["wortlaut"].strip()]


def _desc_fields(d: dict) -> dict:
    """Beschreibungs-Felder eines Frames (ohne start/end — die kommen aus den Frame-Zeitstempeln)."""
    handlung = str(d.get("handlung", "") or d.get("beschreibung", "") or "")
    bf = d.get("bild_fakten") or {}
    if not isinstance(bf, dict):
        bf = {}
    texte = _texte(d)
    return {
        "handlung": handlung,
        "beschreibung": handlung,
        "personen": str(d.get("personen", "") or ""),
        "texte": texte,
        "text_overlays": " | ".join(t["wortlaut"] for t in texte),  # abgeleitet (Hook-Kandidat/Kompat)
        "kamera": str(d.get("kamera", "") or ""),
        "bild_fakten": {
            "komposition": str(bf.get("komposition", "") or ""),
            "licht": str(bf.get("licht", "") or ""),
            "hintergrund": str(bf.get("hintergrund", "") or ""),
        },
        "effekte": str(d.get("effekte", "") or ""),
        "raw": json.dumps(d, ensure_ascii=False),
    }


def _parse_array(raw: str) -> list[dict]:
    """Holt das JSON-Array aus der Antwort. Robust gegen Markdown, {"frames":[...]}
    UND gegen abgeschnittene Antworten (Truncation) — rettet alle vollständigen Objekte,
    statt am letzten halben Objekt zu crashen."""
    # 1. Glücklicher Pfad: ganze Antwort sauber parsen.
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    try:
        data = json.loads(m.group(0) if m else raw)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return [d for d in data if isinstance(d, dict)]
    except (json.JSONDecodeError, AttributeError):
        pass
    # 2. Truncation-Rettung: ab dem ersten "[" Objekt für Objekt dekodieren,
    #    bis es bricht (= abgeschnittenes letztes Objekt → Rest verwerfen).
    start = raw.find("[")
    if start == -1:
        return []
    dec, i, n, out = json.JSONDecoder(), start + 1, len(raw), []
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n or raw[i] == "]":
            break
        try:
            obj, i = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _generate(contents, cfg, label):
    """generate_content mit Modell-Fallback (2.5-flash → 2.0-flash)."""
    last_err = None
    for model_name in [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS:
        try:
            return _call_with_retry(
                f"{label}[{model_name}]",
                lambda m=model_name: client.models.generate_content(model=m, contents=contents, config=cfg),
            )
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Gemini {label} fehlgeschlagen: {last_err}")


def _media_passes(video_path: Path) -> tuple[str, str]:
    """EIN Upload des echten Videos, zwei Calls darauf: Audio + Blickkontakt.
    Frames haben keinen Ton, und Standbilder verraten die Blickrichtung nicht zuverlässig
    (im Frame-Batch produziert Gemini Boilerplate) → beides braucht das bewegte Video.
    Derselbe hochgeladene File-Handle wird wiederverwendet (kein zweiter Upload → quota-schonend)."""
    video_file = gemini_service._upload_video_to_gemini(video_path)
    acfg = types.GenerateContentConfig(system_instruction=AUDIO_PROMPT, temperature=0.0)
    audio = (_generate([video_file, AUDIO_USER], acfg, "describe_audio").text or "").strip()
    gcfg = types.GenerateContentConfig(system_instruction=GAZE_PROMPT, temperature=0.0)
    gaze = (_generate([video_file, GAZE_USER], gcfg, "describe_gaze").text or "").strip()
    return audio, gaze


def describe_video(video_path: Path, frames_dir: Path) -> tuple[list[dict], float, str, str]:
    """Deterministische Frames → 1 gebündelter Visual-Call + Audio- & Blick-Call (1 Upload).
    Returns (segmente, gesamtdauer, audio_overview, gaze_overview)."""
    duration = analyst_frames.probe_duration(video_path)
    distinct = analyst_frames.dedup(analyst_frames.extract_frames(video_path, frames_dir))
    if not distinct:
        audio, gaze = _media_passes(video_path)
        return [], duration, audio, gaze

    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg") for _, p in distinct]
    ts_list = ", ".join(f"#{i + 1}={ts:.2f}s" for i, (ts, _) in enumerate(distinct))
    instr = (
        f"{len(distinct)} Frames in zeitlicher Reihenfolge. Zeitstempel: {ts_list}. "
        f"Beschreibe JEDEN Frame einzeln; gib ein JSON-Array mit GENAU {len(distinct)} Objekten "
        f"in genau dieser Reihenfolge."
    )
    vcfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, response_mime_type="application/json", temperature=0.0,
    )
    objs = _parse_array(_generate([instr, *parts], vcfg, "describe_frames").text or "")

    segments = []
    for i, (ts, _p) in enumerate(distinct):
        d = objs[i] if i < len(objs) else {}
        f = _desc_fields(d)
        f["start"] = ts
        f["end"] = distinct[i + 1][0] if i + 1 < len(distinct) else duration
        segments.append(f)

    audio, gaze = _media_passes(video_path)
    return segments, duration, audio, gaze
