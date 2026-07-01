# services/subtitle_engine.py
"""
Untertitel-Engine V1 — Stil "Bold Pop (Cyan)".

Architektur: subtitles.json ist die Single Source of Truth.
  rough_cut.mp4 → Whisper (Wort-Timestamps AUF DEM CUT, exakt synchron)
               → Phrasen (max 4 Wörter, Bruch bei Pause >450ms / Satzzeichen)
               → 1 Keyword-Highlight pro Phrase (deterministische Heuristik)
               → subtitles.json
  Frontend rendert daraus das Live-Overlay (Drag-Position in %),
  Burn-in generiert daraus ASS (Pop-In via \\t-Scale) + ffmpeg → subtitled_cut.mp4

Design-Spec (Chris):
- ALL CAPS, extrem fette Sans ("The Bold Font" → Montserrat Black → Arial Black)
- Weiß #FFFFFF, Keyword Cyan #00D2FF, weicher schwarzer Drop-Shadow
- Wort-für-Wort synchron (Wort erscheint erst wenn gesprochen), Pop-In 90→100%
- max 5 Wörter sichtbar (Ziel 2-4), Position: zentriert, obere Kante unteres Drittel
- Position per Maus verschiebbar → x_pct/y_pct in subtitles.json

Bestehender AssemblyAI-Pfad (subtitle_service.py) bleibt unberührt.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from services.whisper_service import transcribe_with_word_timestamps
from models.analysis import WhisperWord

SUBTITLES_FILE = "subtitles.json"
SUBTITLED_OUTPUT = "subtitled_cut.mp4"

# --- Phrasen-Gruppierung ---
PHRASE_MAX_WORDS = 5                 # Spec-Limit: max 5 Wörter sichtbar
PHRASE_MAX_CHARS = 34                # Zeichen-Limit pro Phrase (2 Zeilen à ~17)
LINE_MAX_CHARS = 18                  # ab hier wird auf 2 Zeilen umbrochen
PHRASE_BREAK_PAUSE_SEC = 0.45        # Pause zwischen Wörtern → neue Phrase
PHRASE_HOLD_MAX_SEC = 0.60           # Phrase bleibt nach letztem Wort max so lange stehen
SENTENCE_END_CHARS = (".", "!", "?", ":")

# --- Größe & Rand-Garantie ---
DEFAULT_FONT_SIZE = 24               # px bei 480p-Referenz (=96px @1920) — vollbild-tauglich; skaliert mit Videohöhe
FONT_SIZE_MIN, FONT_SIZE_MAX = 10, 48
SAFE_WIDTH_PCT = 0.80                # Text nutzt max 80% der Breite → ≥10% Rand beidseitig
CHAR_WIDTH_FACTOR = 0.68             # konservative Breiten-Schätzung pro Zeichen (fette Caps)

# --- Keyword-Heuristik (V1, deterministisch; später: Claude) ---
KEYWORD_MIN_CHARS = 6
KEYWORD_STOPWORDS = {
    "dieser", "dieses", "diesen", "diesem", "welche", "welcher", "einfach",
    "werden", "worden", "wurden", "können", "könnte", "müssen", "sollen",
    "machen", "gemacht", "haben", "hatten", "wirklich", "natürlich", "vielleicht",
    "trotzdem", "deshalb", "darum", "dadurch", "irgendwie", "sowieso", "übrigens",
}

# --- Default-Stil + Position ---
DEFAULT_STYLE = "bold_pop_cyan"
AVAILABLE_STYLES = {
    "bold_pop_cyan": {
        "label": "Bold Pop (Cyan)",
        # Nur gebündelte Familien (static/fonts/, OFL Montserrat). font_chain[0] ist
        # der Default bei der Generierung; Burn-in + Frontend laden exakt diese Dateien.
        "font_chain": ["Montserrat Black", "Montserrat ExtraBold", "Montserrat SemiBold", "Montserrat"],
        "text_color": "#FFFFFF",
        "highlight_color": "#00D2FF",
        "all_caps": True,
        "pop_in_ms": 110,            # Pop-In-Dauer (90% → 100%)
        "pop_in_scale_from": 90,
        "font_size_pct": 4.6,        # Schriftgröße in % der Videohöhe
        "shadow": "soft_black",
    },
}
DEFAULT_POSITION = {"x_pct": 50.0, "y_pct": 65.0}   # zentriert, obere Kante unteres Drittel

# Gebündelte Familien (static/fonts/, == @font-face im Frontend == Dropdown). NUR diese
# garantieren Vorschau==Export. Jeder andere Name (z.B. Altbestand "The Bold Font") würde
# in libass per fontconfig zu einem Fremd-Font auflösen → genau der behobene Bug.
BUNDLED_FONTS = {
    "Montserrat Black", "Montserrat ExtraBold", "Montserrat SemiBold", "Montserrat",
    "Plus Jakarta Sans ExtraBold", "DM Sans", "DM Serif Display", "Fraunces Black",
}
DEFAULT_FONT_FAMILY = "Montserrat Black"


def _clean(word: str) -> str:
    return word.strip().strip(".,!?;:\"'()[]")


def group_words_into_phrases(words: list[WhisperWord]) -> list[dict]:
    """Deterministisch: max 4 Wörter, Bruch bei Pause >450ms oder Satzzeichen."""
    phrases: list[dict] = []
    buf: list[WhisperWord] = []

    def flush():
        nonlocal buf
        if not buf:
            return
        phrases.append({
            "words": [
                {"text": _clean(w.word), "start": round(w.start, 3), "end": round(w.end, 3),
                 "highlight": False}
                for w in buf if _clean(w.word)
            ],
            "start": round(buf[0].start, 3),
            "end": round(buf[-1].end, 3),
        })
        buf = []

    char_count = 0
    for w in words:
        w_len = len(_clean(w.word))
        if buf:
            pause = w.start - buf[-1].end
            prev_sentence_end = buf[-1].word.rstrip().endswith(SENTENCE_END_CHARS)
            too_long = char_count + 1 + w_len > PHRASE_MAX_CHARS
            if pause > PHRASE_BREAK_PAUSE_SEC or prev_sentence_end or len(buf) >= PHRASE_MAX_WORDS or too_long:
                flush()
                char_count = 0
        buf.append(w)
        char_count += w_len + (1 if char_count else 0)
    flush()

    phrases = [p for p in phrases if p["words"]]

    # Anzeige-Ende: bis zur nächsten Phrase, max +HOLD
    for i, p in enumerate(phrases):
        if i + 1 < len(phrases):
            p["display_end"] = round(min(phrases[i + 1]["start"], p["end"] + PHRASE_HOLD_MAX_SEC), 3)
        else:
            p["display_end"] = round(p["end"] + PHRASE_HOLD_MAX_SEC, 3)
        p["line_break_after"] = compute_line_break(p["words"])
    return phrases


def compute_line_break(words: list[dict]) -> int | None:
    """
    Zeilenumbruch bei zu breiten Phrasen: Index des letzten Wortes von Zeile 1,
    oder None (einzeilig). Balanciert — minimiert die längere der beiden Zeilen.
    """
    texts = [w["text"] for w in words]
    full = " ".join(texts)
    if len(full) <= LINE_MAX_CHARS or len(texts) < 2:
        return None
    best_idx, best_max = None, len(full)
    for i in range(len(texts) - 1):
        l1 = len(" ".join(texts[: i + 1]))
        l2 = len(" ".join(texts[i + 1:]))
        m = max(l1, l2)
        if m < best_max:
            best_max, best_idx = m, i
    return best_idx


def longest_line_chars(p: dict) -> int:
    """Länge der längsten Zeile einer Phrase (für die Rand-Garantie)."""
    texts = [w["text"] for w in p["words"]]
    lb = p.get("line_break_after")
    if lb is None:
        return len(" ".join(texts))
    return max(len(" ".join(texts[: lb + 1])), len(" ".join(texts[lb + 1:])))


def pick_keywords(phrases: list[dict]) -> None:
    """Pro Phrase max 1 Highlight: längstes inhaltstragendes Wort ≥6 Zeichen."""
    for p in phrases:
        best = None
        best_len = 0
        for w in p["words"]:
            token = "".join(c for c in w["text"] if c.isalpha())
            if len(token) >= KEYWORD_MIN_CHARS and token.lower() not in KEYWORD_STOPWORDS:
                if len(token) > best_len:
                    best, best_len = w, len(token)
        if best is not None:
            best["highlight"] = True


def generate_subtitles(
    cut_video_path: Path,
    project_path: Path,
    style: str = DEFAULT_STYLE,
) -> dict:
    """Whisper auf dem Cut → Phrasen → subtitles.json. Returns das JSON-Dokument."""
    if style not in AVAILABLE_STYLES:
        style = DEFAULT_STYLE
    print(f"  [SUBTITLES] Whisper-Pass auf {cut_video_path.name} für Wort-Sync …")
    words, _transcript = transcribe_with_word_timestamps(cut_video_path, language="de")
    phrases = group_words_into_phrases(words)
    pick_keywords(phrases)

    style_def = AVAILABLE_STYLES[style]
    doc = {
        "version": 3,
        "style": style,
        "style_def": style_def,
        "enabled": True,
        "position": dict(DEFAULT_POSITION),
        "font_size": DEFAULT_FONT_SIZE,
        # Optik-Overrides (live im Frontend einstellbar, Burn-in nutzt dieselben Werte)
        "font_family": style_def["font_chain"][0],
        "text_color": style_def["text_color"],
        "highlight_color": style_def["highlight_color"],
        "source_video": cut_video_path.name,
        "phrases": phrases,
    }
    (project_path / SUBTITLES_FILE).write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    n_high = sum(1 for p in phrases for w in p["words"] if w["highlight"])
    print(f"  [SUBTITLES] ✓ {len(phrases)} Phrasen, {n_high} Highlights → {SUBTITLES_FILE}")
    return doc


def load_subtitles(project_path: Path) -> dict | None:
    f = project_path / SUBTITLES_FILE
    if not f.exists():
        return None
    return json.loads(f.read_text())


def update_subtitle_settings(project_path: Path, settings_patch: dict) -> dict:
    """Position/enabled/style aktualisieren (Drag & Checkbox aus dem Frontend)."""
    doc = load_subtitles(project_path)
    if doc is None:
        raise FileNotFoundError("subtitles.json existiert nicht — erst generieren")
    if "position" in settings_patch:
        pos = settings_patch["position"]
        doc["position"] = {
            "x_pct": max(5.0, min(95.0, float(pos.get("x_pct", doc["position"]["x_pct"])))),
            "y_pct": max(5.0, min(95.0, float(pos.get("y_pct", doc["position"]["y_pct"])))),
        }
    if "enabled" in settings_patch:
        doc["enabled"] = bool(settings_patch["enabled"])
    if "font_size" in settings_patch:
        doc["font_size"] = max(FONT_SIZE_MIN, min(FONT_SIZE_MAX, int(settings_patch["font_size"])))
    if "font_family" in settings_patch:
        fam = str(settings_patch["font_family"]).strip()[:60]
        if fam:
            doc["font_family"] = fam
    import re as _re
    for color_key in ("text_color", "highlight_color"):
        if color_key in settings_patch:
            val = str(settings_patch[color_key]).strip()
            if _re.fullmatch(r"#[0-9a-fA-F]{6}", val):
                doc[color_key] = val.upper()
    if "style" in settings_patch and settings_patch["style"] in AVAILABLE_STYLES:
        doc["style"] = settings_patch["style"]
        doc["style_def"] = AVAILABLE_STYLES[settings_patch["style"]]
    (project_path / SUBTITLES_FILE).write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return doc


# ===== ffmpeg-Erkennung (Burn-in braucht den 'ass'-Filter = libass) =====
#
# Root-Cause-Befund 2026-06-11: Homebrew hat die Standard-ffmpeg-Formel
# abgespeckt — KEIN libass mehr ("No such filter: 'ass'", rc=8). Die
# libass-fähige Variante heißt jetzt "ffmpeg-full" und ist keg-only
# (nicht im PATH). Deshalb: Kandidaten durchprobieren, Fähigkeit testen.

FFMPEG_CANDIDATES = [
    os.environ.get("FFMPEG_SUBTITLES_BIN", ""),          # expliziter Override
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",          # brew ffmpeg-full (Apple Silicon, keg-only)
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",             # brew ffmpeg-full (Intel)
    shutil.which("ffmpeg") or "",                        # PATH-ffmpeg (oft die schlanke Formel)
]

_ffmpeg_ass_bin: Optional[str] = None


def _ffmpeg_supports_ass(binary: str) -> bool:
    """Capability-Check: hat dieses ffmpeg den 'ass'-Filter (libass)?"""
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return False
        return any(
            line.split()[1] == "ass"
            for line in result.stdout.splitlines()
            if len(line.split()) >= 2
        )
    except Exception:
        return False


def find_ffmpeg_with_ass() -> str:
    """Erstes ffmpeg mit ass-Filter (gecacht). Wirft verständlichen Fehler wenn keins da."""
    global _ffmpeg_ass_bin
    if _ffmpeg_ass_bin:
        return _ffmpeg_ass_bin
    for cand in FFMPEG_CANDIDATES:
        if cand and Path(cand).exists() and _ffmpeg_supports_ass(cand):
            _ffmpeg_ass_bin = cand
            print(f"  [SUBTITLES] ffmpeg mit libass gefunden: {cand}")
            return cand
    raise RuntimeError(
        "Kein ffmpeg mit Untertitel-Unterstützung (libass / 'ass'-Filter) gefunden. "
        "Die Homebrew-Standard-Formel enthält libass nicht mehr. "
        "Fix (einmalig): `brew install ffmpeg-full` ausführen, dann Server neu starten — "
        "der Editor findet das Binary automatisch unter /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg."
    )


# ===== ASS-Generierung + Burn-in =====

def _hex_to_ass_bgr(hex_color: str) -> str:
    """#RRGGBB → ASS &HBBGGRR& (BGR!)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def effective_font_px(base_px: int, phrase_char_len: int, frame_width: int) -> int:
    """
    Rand-Garantie: Text darf nie breiter als SAFE_WIDTH_PCT der Bildbreite werden.
    Konservative Schätzung: Zeichenbreite ≈ CHAR_WIDTH_FACTOR × Fontgröße (fette Caps).
    Gleiche Formel wie im Frontend-Overlay → Preview und Burn-in sind konsistent.
    """
    if phrase_char_len <= 0:
        return base_px
    max_px = int((frame_width * SAFE_WIDTH_PCT) / (CHAR_WIDTH_FACTOR * phrase_char_len))
    return max(8, min(base_px, max_px))


def build_ass(doc: dict, video_w: int, video_h: int,
              text_hex: str | None = None, highlight_hex: str | None = None) -> str:
    """
    ASS aus subtitles.json. Wort-für-Wort: pro Wort ein Dialogue-Event, das die
    bisherigen Wörter der Phrase zeigt (neue Wörter erscheinen beim Sprechen).
    Pop-In: \\t-Scale 90→100% auf dem ersten Event jeder Phrase.
    Größe: font_size (Default 16) = Pixel bei 480p-Referenz, skaliert mit Videohöhe.

    text_hex/highlight_hex: optionale Farb-Overrides (#RRGGBB). Beim HDR-Burn sind das
    die in den Quell-Farbraum (HLG/bt2020) umkodierten Werte, damit der Untertitel nach
    der Player-Dekodierung exakt im Soll-sRGB erscheint (kein Rotstich), OHNE das Video
    selbst umzurechnen. SDR: hier None → rohes sRGB wie im Frontend.
    """
    style_def = doc["style_def"]
    pos = doc["position"]
    # Doc-Overrides (Settings-Panel) schlagen Stil-Defaults — Preview und Burn identisch.
    # font ist ein gebündelter Familienname (z.B. "Montserrat Black"); libass findet die
    # passende Datei über fontsdir (siehe burn_subtitles). Bold-Flag im Style steht auf 0:
    # das Gewicht steckt in der Datei, kein synthetisches Fetten (sonst ≠ Frontend).
    font = doc.get("font_family") or DEFAULT_FONT_FAMILY
    if font not in BUNDLED_FONTS:        # Altbestand/Fremdname → auf gebündelten Default
        font = DEFAULT_FONT_FAMILY
    fallback_fonts = ",".join(style_def["font_chain"][1:3])
    font_size = max(8, int(round(doc.get("font_size", DEFAULT_FONT_SIZE) * video_h / 480)))
    white = _hex_to_ass_bgr(text_hex or doc.get("text_color") or style_def["text_color"])
    cyan = _hex_to_ass_bgr(highlight_hex or doc.get("highlight_color") or style_def["highlight_color"])
    x = int(video_w * pos["x_pct"] / 100)
    y = int(video_h * pos["y_pct"] / 100)
    pop_ms = style_def["pop_in_ms"]
    scale_from = style_def["pop_in_scale_from"]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: BoldPop,{font},{font_size},{white},{white},&H00000000,&H9C000000,0,0,0,0,100,100,1,0,1,0,3,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for p in doc["phrases"]:
        words = p["words"]
        if not words:
            continue
        # Rand-Garantie: Größe pro Phrase auf Safe-Width einpassen — Maß ist die
        # LÄNGSTE ZEILE (bei Umbruch), damit die Größe nicht springt
        lb = p.get("line_break_after")
        if lb is None:
            lb = compute_line_break(words)
        eff_px = effective_font_px(font_size, longest_line_chars({**p, "line_break_after": lb}), video_w)
        for i, w in enumerate(words):
            ev_start = w["start"]
            ev_end = words[i + 1]["start"] if i + 1 < len(words) else p["display_end"]
            if ev_end - ev_start < 0.02:
                continue
            parts = []
            for j in range(i + 1):
                txt = words[j]["text"].upper() if style_def["all_caps"] else words[j]["text"]
                txt = txt.replace("{", "(").replace("}", ")")
                if words[j]["highlight"]:
                    parts.append(f"{{\\c{cyan}&}}{txt}{{\\c{white}&}}")
                else:
                    parts.append(txt)
                # Zeilenumbruch nach dem letzten Wort von Zeile 1 (ASS: \N)
                if lb is not None and j == lb and j < i:
                    parts.append("\\N")
            line = " ".join(parts).replace(" \\N ", "\\N")
            # Pop-In nur beim ersten Wort der Phrase (diskret, aggressiv)
            pop = ""
            if i == 0:
                pop = (f"\\fscx{scale_from}\\fscy{scale_from}"
                       f"\\t(0,{pop_ms},\\fscx100\\fscy100)")
            tags = f"{{\\an5\\pos({x},{y})\\fs{eff_px}\\blur1.2{pop}}}"
            events.append(
                f"Dialogue: 0,{_ass_time(ev_start)},{_ass_time(ev_end)},BoldPop,,0,0,0,,{tags}{line}"
            )
    _ = fallback_fonts  # Fallback regelt libass über installierte Fonts
    return header + "\n".join(events) + "\n"


def _is_hdr(video_path: Path) -> bool:
    """HDR-Quelle? (iPhone HLG: transfer=arib-std-b67, primaries=bt2020; auch PQ/smpte2084).
    Bei HDR wird das Video NICHT umgerechnet — stattdessen die Untertitelfarbe in den
    Quell-Farbraum kodiert (siehe burn_subtitles), sonst kippt sie im Player nach Rot."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer,color_primaries",
             "-of", "default=nw=1", str(video_path)],
            capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        ).stdout.lower()
    except Exception:
        return False
    return ("arib-std-b67" in out or "smpte2084" in out or "bt2020" in out)


def _color_tags(video_path: Path) -> tuple[str, str, str]:
    """Quell-Farbtags (color_space, primaries, transfer); Fallback bt709/bt709/bt709."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_space,color_primaries,color_transfer",
             "-of", "default=noprint_wrappers=1", str(video_path)],
            capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        ).stdout
    except Exception:
        return ("bt709", "bt709", "bt709")
    d = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    return (d.get("color_space") or "bt709", d.get("color_primaries") or "bt709",
            d.get("color_transfer") or "bt709")


def _hdr_zscale_space(video_path: Path):
    """zscale-Namen (primaries, transfer) der HDR-Quelle, sonst None."""
    _, cp, ct = _color_tags(video_path)
    prim = "2020" if "2020" in cp else None
    trc = "arib-std-b67" if "arib-std-b67" in ct else ("smpte2084" if "smpte2084" in ct else None)
    return (prim, trc) if (prim and trc) else None


def _srgb_to_video_hex(hex_color: str, prim: str, trc: str, ffmpeg_bin: str) -> str:
    """Kodiert eine sRGB-Farbe in den Video-Farbraum (bt2020/HLG) → #RRGGBB der Code-Werte.
    So ist der Untertitel homogen mit dem (unveränderten) HDR-Video und kippt nicht nach Rot."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        raw = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", f"color=c=0x{h}:s=8x8:d=1",
             "-vf", f"format=rgb48le,zscale=pin=709:tin=709:rin=full:p={prim}:t={trc}:r=full,format=rgb24",
             "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, check=True, stdin=subprocess.DEVNULL,
        ).stdout
        if len(raw) >= 3:
            return f"#{raw[0]:02X}{raw[1]:02X}{raw[2]:02X}"
    except Exception:
        pass
    return hex_color


def _probe_dimensions(video_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(video_path)],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )
    for s in json.loads(result.stdout)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("Kein Video-Stream gefunden")


def burn_subtitles(project_path: Path) -> Path:
    """subtitles.json → ASS → ffmpeg burn-in → output/subtitled_cut.mp4."""
    doc = load_subtitles(project_path)
    if doc is None:
        raise FileNotFoundError("subtitles.json fehlt — erst /generate-subtitles aufrufen")
    cut = project_path / "output" / "rough_cut.mp4"
    if not cut.exists():
        raise FileNotFoundError("rough_cut.mp4 fehlt — erst execute-cut")

    output_dir = (project_path / "output").resolve()
    fonts_dir = (Path(__file__).resolve().parent.parent / "static" / "fonts")
    ffmpeg_bin = find_ffmpeg_with_ass()

    w, h = _probe_dimensions(cut)
    # Ziel: 1080p (kürzere Seite ≤ 1080) + 30 fps. Quelle, die schon 1080p ist, bleibt unskaliert.
    short = min(w, h)
    if short > 1080:
        s = 1080 / short
        tw, th = int(round(w * s)) // 2 * 2, int(round(h * s)) // 2 * 2
    else:
        tw, th = w, h

    # Grundprinzip: Das Video-Bild wird NIE farblich umgerechnet/getonemappt — nur die
    # Untertitel werden eingebrannt. Ist die Quelle HDR (HLG/bt2020), kippt rohes sRGB im
    # Untertitel nach Rot (Player wendet die HDR-Übertragungsfunktion darauf an). Lösung:
    # die Untertitelfarbe vorab in den Quell-Farbraum kodieren (sRGB→HLG/bt2020), das Video
    # unangetastet lassen und die Quell-Farbtags 1:1 weitergeben. SDR-Quelle: rohes sRGB.
    hdr = _is_hdr(cut)
    cs, cp, ct = _color_tags(cut)
    if hdr:
        space = _hdr_zscale_space(cut)          # (prim, trc) zscale-Namen der HDR-Quelle
        if space:
            prim, trc = space
            text_hex = _srgb_to_video_hex(doc.get("text_color") or "#FFFFFF", prim, trc, ffmpeg_bin)
            hl_hex = _srgb_to_video_hex(doc.get("highlight_color") or "#00D2FF", prim, trc, ffmpeg_bin)
        else:
            text_hex = hl_hex = None
        out_color = ["-colorspace", cs, "-color_primaries", cp, "-color_trc", ct]
    else:
        text_hex = hl_hex = None
        out_color = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]

    ass_path = output_dir / "subtitles.ass"
    ass_path.write_text(build_ass(doc, tw, th, text_hex=text_hex, highlight_hex=hl_hex))

    out = output_dir / SUBTITLED_OUTPUT
    out.unlink(missing_ok=True)   # alte/partielle Datei nie im Weg stehen lassen

    # Benannte Filter-Option + cwd=output_dir (versionssicher ffmpeg 4–8); fontsdir =
    # gebündelte Fonts (Optik == Frontend). Scale nur falls Downscale auf 1080p nötig.
    # KEIN format=yuv420p am Kettenende bei HDR: das würde nach bt601/709 quantisieren und
    # die HLG-Tags entwerten. pix_fmt regelt der Encoder; das Video bleibt im Quellraum.
    ass_filter = f"ass=filename=subtitles.ass:fontsdir={fonts_dir}"
    scale_part = f"scale={tw}:{th}:flags=lanczos," if (tw, th) != (w, h) else ""
    vf = f"{scale_part}{ass_filter}"

    cmd = [
        ffmpeg_bin, "-y", "-nostdin", "-i", str(cut.resolve()),
        "-vf", vf, "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        *out_color,
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ]
    print(f"  [SUBTITLES] Burn-in → {out.name} (hdr={hdr}, src_color={cs}/{cp}/{ct}, {tw}x{th}@30)")
    result = subprocess.run(cmd, check=False, capture_output=True, stdin=subprocess.DEVNULL, cwd=str(output_dir))
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"FFmpeg-Burn-in fehlgeschlagen (rc={result.returncode}):\n{tail}")
    print(f"  [SUBTITLES] ✓ {out.name}")
    return out
