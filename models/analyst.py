# models/analyst.py
"""Pydantic-Modelle für den AI Video Analyst."""
from typing import Optional
from pydantic import BaseModel, Field


class BildFakten(BaseModel):
    """Rein beobachtbare Bild-Tatsachen (Grundlage für die Ästhetik-Bewertung)."""
    komposition: str = ""   # wo ist das Hauptsubjekt (zentriert/links/...)
    licht: str = ""         # hell / dunkel / gemischt / Gegenlicht
    hintergrund: str = ""   # was ist sachlich zu sehen


class TextEinblendung(BaseModel):
    """Eine unabhängige Texteinblendung: Wortlaut + zugehörige Darstellung (verknüpft)."""
    wortlaut: str = ""       # der Text wörtlich
    darstellung: str = ""    # dynamisch/kurz vs. statisch/fest, Position, Stil — gehört zu DIESEM Text


class SceneDescription(BaseModel):
    """Objektives Zeit-Segment (ein distinct Frame) aus dem Gemini-Frame-Lauf."""
    index: int
    start: float
    end: float
    handlung: str = ""
    beschreibung: str = ""           # Spiegel von handlung (Kompat)
    personen: str = ""
    texte: list[TextEinblendung] = Field(default_factory=list)  # jede Einblendung mit eigener Darstellung
    text_overlays: str = ""          # abgeleitet: alle Wortlaute zusammengefügt (für Hook-Kandidat/Kompat)
    gesprochener_text: str = ""       # Whisper-Wortlaut im Segmentfenster (verlässlicher als Bild-OCR)
    kamera: str = ""
    bild_fakten: Optional[BildFakten] = None
    effekte: str = ""                 # visuelle Effekte: Zoom, Flash, Animation, B-Roll, Übergänge
    raw: str = ""                     # 1:1 Gemini-Segment (JSON) für Debug/Dropdown
    error: str = ""


class SpeechStats(BaseModel):
    """Deterministische Sprachstatistik aus Whisper-Wörtern (Code, kein LLM)."""
    wort_anzahl: int
    sprech_dauer_sec: float
    wpm: float
    filler_count: int
    filler_words: list[str]
    pausen_count: int
    laengste_pause_sec: float
    sprechbeginn_sec: float = 0.0  # Start des ersten gesprochenen Worts (für Hook-Verzögerungs-Check)


class QualityMetrics(BaseModel):
    """Deterministische Mess-Daten (Code, kein LLM) als Basis für die Qualitäts-Bewertung."""
    schaerfe_avg: float = 0.0          # Laplacian-Varianz über alle Keyframes
    schaerfe_min: float = 0.0
    helligkeit_avg: float = 0.0        # Graustufen-Mittel 0–255
    kontrast_avg: float = 0.0          # Graustufen-Std
    lufs_integrated: Optional[float] = None   # Lautheit (EBU R128); None = kein Audio
    loudness_range: Optional[float] = None
    true_peak_db: Optional[float] = None


# ---------- V2: schlanke Bewertung (Scores + 1-Satz-Begründungen) ----------

class HookEval(BaseModel):
    """Hook getrennt nach Sprech- und Text-Hook (4-Faktoren-Rubrik, 1–5)."""
    sprech_hook_score: int = 0
    sprech_hook_grund: str = ""
    text_hook_vorhanden: bool = False
    text_hook_score: Optional[int] = None
    text_hook_grund: Optional[str] = None


class StrukturElemente(BaseModel):
    """Welche Storyline-Bausteine erkennbar sind."""
    hook: bool = False
    bridge: bool = False
    mid: bool = False
    peak: bool = False
    cta: bool = False


class StrukturEval(BaseModel):
    score: int = 0
    elemente: StrukturElemente = Field(default_factory=StrukturElemente)
    kommentar: str = ""


class ScoreProbleme(BaseModel):
    """Score 1–5 + nur stark auffällige Punkte (Frontend: Hover-Detail)."""
    score: int = 0
    probleme: list[str] = Field(default_factory=list)


class ScoreKommentar(BaseModel):
    """Score 1–5 + 1-Satz-Kommentar (Frontend: Hover-Detail)."""
    score: int = 0
    kommentar: str = ""


class AnalystEvaluationV2(BaseModel):
    """Schlanke Bewertung durch Claude — scannbar, ~80 % kürzer als V1."""
    zielgruppe: str = ""               # genau 1 Satz
    format: str = ""                   # Talking-Head / Sketch / ...
    performance_score: int = 0         # 0–100
    funnel: str = ""                   # TOFU / MOFU / BOFU / Mischung
    hook: HookEval = Field(default_factory=HookEval)
    struktur: StrukturEval = Field(default_factory=StrukturEval)
    sprechqualitaet: ScoreProbleme = Field(default_factory=ScoreProbleme)
    schnitt_pacing: ScoreKommentar = Field(default_factory=ScoreKommentar)
    spannungsbogen: ScoreKommentar = Field(default_factory=ScoreKommentar)
    visuelle_aesthetik: ScoreProbleme = Field(default_factory=ScoreProbleme)
    top_tipps: list[str] = Field(default_factory=list)


class AnalystResult(BaseModel):
    """Gesamtergebnis eines Analyse-Laufs (analysis.json)."""
    id: str
    filename: str
    duration_sec: float
    scene_count: int
    scenes: list[SceneDescription]
    audio_overview: str = ""           # dedizierter Gemini-Audio-Pass (Musik/SFX/Stimme, ganzes Video)
    gaze_overview: str = ""            # dedizierter Gemini-Blick-Pass (Linse vs. Ablesen, ganzes Video)
    transcript: str = ""
    speech_stats: Optional[SpeechStats] = None
    quality_metrics: Optional[QualityMetrics] = None
    evaluation: Optional[AnalystEvaluationV2] = None
    engine: str = "v1"                 # v1 (Claude) | v2_pure (nur Gemini) | v2_hybrid (Gemini + lokale Messwerte)
    elapsed_sec: float = 0.0           # reine Verarbeitungszeit (ohne Warteschlange), für Engine-Vergleich
