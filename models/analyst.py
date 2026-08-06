# models/analyst.py
"""Pydantic-Modelle für den AI Video Analyst."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


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


class Pause(BaseModel):
    """Eine Sprechpause MIT Position — die Position ist für die Bewertung entscheidend:
    Ohne sie kann das Modell eine gemessene Dauer keiner Stelle im Video zuordnen und
    fusioniert Zahl und Ort zu einer erfundenen Behauptung."""
    start_sec: float   # Ende des Worts davor
    end_sec: float     # Start des Worts danach
    dauer_sec: float


class SpeechStats(BaseModel):
    """Deterministische Sprachstatistik aus Whisper-Wörtern (Code, kein LLM)."""
    wort_anzahl: int
    sprech_dauer_sec: float
    wpm: float
    filler_count: int
    filler_words: list[str]
    pausen_count: int
    laengste_pause_sec: float
    pausen: list[Pause] = []       # alle Pausen >PAUSE_THRESHOLD_SEC mit Zeitstempel
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
    """Hook getrennt nach Sprech- und Text-Hook (4-Faktoren-Rubrik, 1–5).

    sprech_hook_score ist nullable: In einem Video ohne gesprochenes Wort gibt es keinen Sprech-Hook,
    den man bewerten könnte. Eine 1 hieße „schlecht gemacht", null heißt „nicht bewertbar" — das ist
    der Unterschied zwischen einem Mangel und einer Formatentscheidung (Feedback Run 08e908d7).
    """
    sprech_hook_score: Optional[int] = 0
    sprech_hook_grund: str = ""
    # Die Frage, die der Hook offen lässt — PFLICHT, in EINEM Satz.
    # Zweck: „erzeugt Neugier" ist eine Behauptung, die über jeden Text aufgestellt werden kann und
    # die das Modell nicht falsifizieren muss. Eine formulierbare offene Frage ist dagegen prüfbar:
    # Gibt es keine, gibt es keinen Haken. Anlass: Hooks wurden gut bewertet, obwohl sie
    # nachweislich nicht neugierig machten.
    sprech_hook_offene_frage: str = ""
    # Welche Mechanik der Hook nutzt — einer der Werte aus HOOK_MECHANIKEN. „keine" heißt:
    # Es ist eine Aussage, kein Hook.
    sprech_hook_mechanik: str = ""
    text_hook_vorhanden: bool = False
    text_hook_score: Optional[int] = None
    text_hook_grund: Optional[str] = None
    # Wortlaut des Textes, den das Modell als Text-Hook wertet — PFLICHT wenn vorhanden.
    # Zwei Gründe: (1) Ohne Zitat ist eine Fehlklassifikation unsichtbar. Im Lauf 5502bb37 wertete
    # das Modell die Spaltenüberschrift einer Vergleichsgrafik als Texthook mit Score 4; im Output
    # stand nur eine Begründung, nicht WAS bewertet wurde. (2) Bei v2_hybrid ist `scenes` leer, der
    # Bildtext steht also nirgends im Ergebnis — ohne dieses Feld kann der Code die Redundanz zum
    # Transkript nicht messen.
    text_hook_wortlaut: str = ""
    text_hook_offene_frage: str = ""   # wie sprech_hook_offene_frage, für die Text-Hook
    text_hook_mechanik: str = ""       # wie sprech_hook_mechanik, für die Text-Hook
    # True, wenn der Score erst NACH dem Modell-Call im Code geklemmt wurde. Das Modell konnte davon
    # nichts wissen, also muss die Empfehlung dazu erzwungen werden (siehe erzwinge_hook_empfehlungen).
    text_hook_score_geklemmt: bool = False


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
    """Score 1–5 + nur stark auffällige Punkte (Frontend: Hover-Detail).

    score ist nullable — siehe HookEval: bei sprechqualitaet ohne gesprochenes Wort bedeutet null
    „nicht bewertbar", nicht „schlecht". Das Frontend zeigt dafür „–"."""
    score: Optional[int] = 0
    # DEUTLICHE Mängel: zählen für den Score-Deckel und erzeugen eine Handlungsempfehlung.
    probleme: list[str] = Field(default_factory=list)
    # Leichte Auffälligkeiten: werden erwähnt, wirken NICHT auf Score oder Empfehlungen.
    # Grund (Läufe 30d6b472, 82bda700): Die Pflicht-Beurteilung von Kopfraum und Bildqualität hat
    # das Modell gezwungen, immer etwas zu nennen — zwei Einträge waren praktisch garantiert und
    # der Score-Deckel machte daraus in 5 von 5 Läufen exakt eine 3. Feedback: „bildausschnitt ist
    # gut", „der raum zwischen kopf und rand ist nahezu perfekt groß". Ohne diese Trennung kann
    # das Modell eine Randnotiz nicht von einem echten Mangel unterscheiden.
    # Zwei Listen statt eines Schwere-Attributs pro Eintrag: `probleme` bleibt `list[str]`, damit
    # Altläufe und das Frontend unverändert weiterlesen.
    hinweise: list[str] = Field(default_factory=list)


class ScoreKommentar(BaseModel):
    """Score 1–5 + 1-Satz-Kommentar (Frontend: Hover-Detail)."""
    score: int = 0
    kommentar: str = ""


class ActionStep(BaseModel):
    """Konkreter Umsetzungs-Schritt als To-do: Zeitpunkt + eine einfache Handlung.
    Wird NICHT mehr vom Modell geliefert, sondern in analyst_eval.verteile_empfehlungen()
    aus `empfehlungen` berechnet (sortieren/bündeln/splitten ist Arithmetik, kein Urteil)."""
    zeitpunkt: str = ""      # z.B. "ca. Sek. 3" oder "ca. Sek. 3, 15 und 24" (Richtwert, ±1–2 s)
    anweisung: str = ""      # EINE konkrete Handlung in super einfacher Sprache


class PausenUrteil(BaseModel):
    """Urteil des Modells zu EINER gemessenen Pause — Urteil, nicht Formulierung.

    Vorher schrieb das Modell pro Pause eine eigene Empfehlung im Freitext („Schneide die Pause von
    0,6 Sekunden vor dem Satz 'Ich glaube nicht…' heraus"). Die Bündelung in verteile_empfehlungen()
    verlangt identischen Text — zwei Pausentexte sind nie identisch, also wurde nie gebündelt und der
    Nutzer bekam fünf fast gleiche Schritte (Feedback 25b8b2f6). Jetzt urteilt das Modell nur noch
    „raus / lassen / unklar", den Satz baut der Code.
    """
    start_sec: float
    urteil: str = "unklar"   # raus | lassen | unklar


class Einblendung(BaseModel):
    """EINE Stelle, an der eine visuelle Einblendung den Inhalt verstärken würde.

    Wie bei PausenUrteil liefert das Modell nur die Stelle und den Zweck; den Satz baut der Code.
    Grund: Einzelne Einblendungs-Empfehlungen belegten mehrere der nur drei Top-Plätze (Feedback
    3185d209: „den tipp mit den grafiken kann man auch zusammenfassen. maximal an 3 stellen
    empfehlen."). Sie im Prompt über `gruppe` zu bündeln ist keine Option — dabei hat das Modell
    schon einmal drei verschiedene Motive zu einer falschen Empfehlung verschmolzen (Lauf 702f9c11).
    """
    zeitpunkt_sek: float = 0.0
    verstaerkt: str = ""   # das Wort oder die Aussage, die verstärkt werden soll


class Empfehlung(BaseModel):
    """EINE Empfehlung, wie das Modell sie liefert — flach, mit Zeitpunkt als ZAHL.

    Vorher sollte das Modell selbst sortieren, die 3 frühesten wählen, gleiche Handlungen bündeln
    und auf zwei Listen verteilen. Das ist deterministisch und gehört damit in den Code; die Regel
    dafür stand 3× im Prompt und griff trotzdem nicht sicher (Befund 3).
    """
    zeitpunkt_sek: float = 0.0   # Sekunde im Video — als Zahl, damit der Code sortieren kann
    anweisung: str = ""          # EINE konkrete Handlung in super einfacher Sprache
    gruppe: str = ""             # gleiches Label = DIESELBE Handlung an mehreren Stellen (z.B.
                                 # "sprechpausen") → der Code fasst sie zu EINEM Schritt zusammen.
                                 # Was dieselbe Handlung ist, weiß nur das Modell — das bleibt Urteil.
    # Welche BEWERTUNGSDIMENSION diese Empfehlung adressiert. Einer der Namen aus DIMENSIONEN
    # (models.analyst.DIMENSIONEN) oder leer, wenn sie zu keiner gehört.
    #
    # Zweck: Der Code erzwingt bei schwachem Score eine Empfehlung — soll das aber NICHT tun, wenn
    # das Modell dazu schon eine geschrieben hat. Ohne dieses Feld war die Doppelung garantiert:
    # In 4 von 5 Läufen am 2026-07-30 wiederholte Tipp 3 die Tipps 1/2 wortnah, weil beide aus
    # denselben `probleme` entstanden (Lauf e9f69518: „Fernseher im Hintergrund" stand als
    # Modell-Empfehlung UND als erzwungener Sammeltipp).
    # Vier Versuche, das über Textmuster zu erkennen, sind gescheitert (`bild` traf „Text im Bild",
    # `sprech` traf „Sprechhook", `hintergrund` traf eine Farb-Empfehlung, Wortmengen-Überlappung
    # scheiterte an Paraphrasen). Eine Zuordnung durch das Modell ist exakt statt geraten.
    betrifft: str = ""


# Die sieben Bewertungsdimensionen als Single Source of Truth: Schema-Vertrag, Score-Gewichte und
# die Zuordnung in `Empfehlung.betrifft` müssen dieselben Namen benutzen.
DIMENSIONEN = (
    "sprech_hook", "text_hook", "sprechqualitaet", "visuelle_aesthetik",
    "spannungsbogen", "struktur", "schnitt_pacing",
)

# Gestaltungs- und Inhaltsmängel einer vorhandenen Text-Hook. Das Modell meldet NUR die Aspekte,
# die wirklich schwach sind; der Code baut daraus die Empfehlung.
# Grund (Lauf 4e56336e): Die feste Empfehlung nannte alle Gestaltungspunkte, auch die intakten —
# „bei tipp 2 hätte nur die textinhaltsanpassung gereicht. optisch ist die texthook in ordnung."
# Womit ein Hook arbeitet. Die Liste diente bisher nur den VORSCHLÄGEN (`texthook_varianten`);
# sie auch für die BEWERTUNG zu verlangen macht das Urteil prüfbar: Wer keine Mechanik benennen
# kann, hat keinen Hook vor sich, sondern eine Aussage.
HOOK_MECHANIKEN = (
    "provokation",     # widerspricht dem, was die Zielgruppe glaubt
    "neugierluecke",   # lässt bewusst offen, was der Zuschauer wissen will
    "zahl",            # konkrete Zahl oder konkreter Pain Point
    "erwartungsbruch", # das Gegenteil dessen, was man erwartet
    "pov",             # versetzt den Zuschauer in eine Lage
    "konflikt",        # zwei Seiten, ein Widerspruch, ein Fehler mit Folgen
    "versprechen",     # ein konkretes Ergebnis in Aussicht
    "keine",           # nichts davon — dann ist es eine Aussage, kein Hook
)

TEXTHOOK_MANGEL_ARTEN = (
    "wortlaut",    # sagt inhaltlich zu wenig, macht nicht neugierig
    "laenge",      # zu viele Wörter
    "redundanz",   # wiederholt das Gesprochene
    "groesse",     # zu groß oder zu klein im Bild
    "farbe",       # grell oder schlecht zum Look passend
    "lesbarkeit",  # Schriftart/Kontrast schwer lesbar
    "dauer",       # zu kurz eingeblendet
    "position",    # klebt am Rand, wird von der Plattform-UI überdeckt
)


# Vom Nutzer beim Upload wählbares Format (genau EINES, Pflicht). Single Source of Truth:
# Die API validiert dagegen, der Prompt bekommt die Auswahl als FAKT (das Modell klassifiziert
# das Format nicht mehr selbst — der Nutzer kennt sein Video, das ist die verlässlichere Quelle).
FORMATE = ("Talking Head", "Reaction", "Sketch", "Tutorial", "Vlog", "Andere")


class AnalystEvaluationV2(BaseModel):
    """Schlanke Bewertung durch Claude — scannbar, ~80 % kürzer als V1."""
    zielgruppe: str = ""               # genau 1 Satz
    format: str = ""                   # vom Nutzer gewählt (FORMATE), Code überschreibt das Modell-Feld
    protagonist_ab_sek: float = 0.0    # ab wann der Protagonist selbst spricht; >0 z.B. bei Reaction
                                       # (davor läuft fremdes Audio — das ist NICHT sein Sprech-Hook)
                                       # null vom Modell → 0.0, siehe _null_ist_sekunde_null unten
    performance_score: int = 0         # 0–100
    funnel: str = ""                   # TOFU / MOFU / BOFU / Mischung
    hook: HookEval = Field(default_factory=HookEval)
    struktur: StrukturEval = Field(default_factory=StrukturEval)
    sprechqualitaet: ScoreProbleme = Field(default_factory=ScoreProbleme)
    schnitt_pacing: ScoreKommentar = Field(default_factory=ScoreKommentar)
    spannungsbogen: ScoreKommentar = Field(default_factory=ScoreKommentar)
    visuelle_aesthetik: ScoreProbleme = Field(default_factory=ScoreProbleme)
    staerken: list[str] = Field(default_factory=list)             # positives Feedback: was schon gut ist
    top_tipps: list[str] = Field(default_factory=list)            # ausführliches Verbesserungs-Feedback
    empfehlungen: list[Empfehlung] = Field(default_factory=list)  # ROH vom Modell: flach, unsortiert
    # Urteile statt Formulierungen — der Code baut daraus die fertigen Schritte:
    pausen_urteile: list[PausenUrteil] = Field(default_factory=list)
    texthook_varianten: list[str] = Field(default_factory=list)   # je max. 9 Wörter; Code prüft und filtert
    # Nur die Aspekte, die an einer VORHANDENEN Text-Hook wirklich schwach sind (Werte aus
    # TEXTHOOK_MANGEL_ARTEN). Der Code baut die Empfehlung genau daraus — nennt das Modell nur
    # „lesbarkeit", steht in der Empfehlung auch nur die Schriftart.
    texthook_maengel: list[str] = Field(default_factory=list)
    einblendungen: list[Einblendung] = Field(default_factory=list)  # Code bündelt zu EINEM Schritt
    # Die beiden folgenden Listen berechnet der Code aus `empfehlungen` — das Modell füllt sie nicht:
    action_steps: list[ActionStep] = Field(default_factory=list)  # die 3 frühesten Handlungsempfehlungen
    weitere_empfehlungen: list[ActionStep] = Field(default_factory=list)  # alle übrigen (aufklappbar)

    @field_validator("protagonist_ab_sek", mode="before")
    @classmethod
    def _null_ist_sekunde_null(cls, v):
        """Gemini schreibt `null`, wenn es keinen Sprechbeginn erkennt — bei drei Läufen
        (102130ce, 3dce018c, d5ff1737, alle Format „Andere") ist daran die komplette Analyse
        gescheitert: ein `float_type`-Fehler, `phase: error`, Ergebnis weg.
        Kein Sprechbeginn wird wie „ab Sekunde 0" behandelt. Das ist derselbe Wert, den jeder
        Altlauf ohne das Feld bekommt, und kein Code liest ihn weiter aus — er geht nur in den
        Prompt und in `tools/vergleiche_laeufe.py`.
        Ein abgebrochener Lauf ist die teuerste aller Antworten: das Video ist schon durch
        Whisper und durch Gemini gelaufen, bezahlt und verworfen."""
        return 0.0 if v is None else v


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
    transkript_hash: str = ""          # sha256(transcript)[:12] — macht Whisper-Drift zwischen Läufen
                                       # sofort sichtbar, statt sie über Zufallsfunde zu entdecken
    speech_stats: Optional[SpeechStats] = None
    quality_metrics: Optional[QualityMetrics] = None
    evaluation: Optional[AnalystEvaluationV2] = None
    engine: str = "v1"                 # v1 (Claude) | v2_pure (nur Gemini) | v2_hybrid (Gemini + lokale Messwerte)
    elapsed_sec: float = 0.0           # reine Verarbeitungszeit (ohne Warteschlange), für Engine-Vergleich
    geplante_texthook: str = ""        # vom Nutzer vor der Analyse eingetragene, geplante Texthook (Freifeld)
    gewaehltes_format: str = ""        # vom Nutzer beim Upload gewähltes Format (Pflicht, genau eines aus
                                       # FORMATE); leer nur bei Altläufen → Modell klassifiziert dann selbst
