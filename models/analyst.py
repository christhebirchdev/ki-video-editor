# models/analyst.py
"""Pydantic-Modelle für den AI Video Analyst."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


def liste_von_strings(v):
    """Macht `{"text": "…"}` wieder zu `"…"` — für Listenfelder, die Strings sein müssen.

    Anlass (2026-09-12, erster echter V3-Lauf): Seit `staerken` im V3-Schema Objekte sind, hat das
    Modell dieses Muster auf die BENACHBARTE Zeile übertragen und `top_tipps` als
    `[{"text": …}, …]` geliefert. Pydantic brach den Lauf ab — nachdem Whisper und Gemini schon
    gelaufen und bezahlt waren. Ein abgebrochener Lauf ist die teuerste aller Antworten.

    Eine Prompt-Bitte („top_tipps sind Strings") kann das nicht garantieren; dieser Validator schon.
    Er greift auf ALLE Listenfelder, die das Modell füllt — welches davon das Muster als nächstes
    erwischt, ist nicht vorhersagbar.

    Aus einem Objekt wird der erste sinnvolle Textwert genommen: die üblichen Schlüssel zuerst,
    sonst der erste String im Objekt. Was kein String ist, bleibt unverändert und läuft in die
    normale Validierung — ein stiller Fehlgriff wäre schlimmer als eine ehrliche Fehlermeldung.
    """
    if not isinstance(v, list):
        return v
    raus = []
    for e in v:
        if isinstance(e, dict):
            treffer = e.get("text") or e.get("tipp") or e.get("anweisung") or e.get("mangel")
            if not treffer:
                treffer = next((x for x in e.values() if isinstance(x, str)), None)
            e = treffer if treffer is not None else e
        raus.append(e)
    return raus


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
    # True, wenn der oben zitierte Wortlaut der mitlaufende UNTERTITEL ist, nicht ein statischer
    # Bildtext. Untertitel sind per Definition wortgleich zum Gesprochenen — ohne diese Unterscheidung
    # liest `bereinige_redundante_texthook` sie als vorgelesenen Bildtext und deckelt zu Unrecht
    # (Lauf 225cf73b: „Deine ersten Worte lesen den Bildtext vor" bei mitlaufenden Untertiteln, die der
    # Sprache nur FOLGTEN). Default False, damit Altläufe ohne dieses Feld sich wie bisher verhalten.
    text_hook_wortlaut_ist_untertitel: bool = False
    text_hook_offene_frage: str = ""   # wie sprech_hook_offene_frage, für die Text-Hook
    text_hook_mechanik: str = ""       # wie sprech_hook_mechanik, für die Text-Hook
    # Ist-Angabe zur Eröffnung, analog zu `text_hook_wortlaut`: Ohne sie empfiehlt der Analyst
    # Bewegung, die schon da ist. Läufe 225cf73b, b08f73bd und 7230d0f8 (dasselbe Video) empfahlen
    # alle „Nutze direkt zu Beginn einen schnellen digitalen Zoom auf dein Gesicht" — den Zoom gab
    # es längst, er war nach 0,4 s vorbei (10-fps-Frameanalyse: MAD 25,3/14,7/9,4 bei 0,1/0,2/0,3 s).
    # Das Modell benannte ihn in KEINEM Feld; „Zoom" stand im ganzen Ergebnis nur in der Empfehlung.
    # Das Benennen zwingt das Modell zum Hinsehen — dieselbe Wirkung wie `text_hook_wortlaut` beim
    # Bildtext. Default False/leer, damit sich Altläufe ohne diese Felder wie bisher verhalten.
    eroeffnung_hat_bewegung: bool = False   # Zoom, Kamerafahrt, Schnitt o.ä. in den ersten ~2 s
    eroeffnung_bewegung: str = ""           # was genau — Pflicht, wenn das Flag True ist
    # DRITTE HOOK-EBENE (Vorgabe Chris, 2026-08-06): Bewegung im Bild, Zoom, Schnitt, ein visueller
    # Bruch in den ersten Sekunden. Die Referenz kennt sie seit jeher (S1: „Hook auf 3 Ebenen —
    # Sprech × Text × visuell"), das Schema hatte nur zwei — die Ebene fiel damit still weg.
    # Nullable wie der Sprech-Hook: Ein Standbild ohne jede Bewegung ist bewertbar (Score 1), ein
    # Video, in dem die Eröffnung nicht beurteilbar ist, nicht.
    visuell_hook_score: Optional[int] = None
    visuell_hook_grund: str = ""
    # True, wenn der Score erst NACH dem Modell-Call im Code geklemmt wurde. Das Modell konnte davon
    # nichts wissen, also muss die Empfehlung dazu erzwungen werden (siehe erzwinge_hook_empfehlungen).
    text_hook_score_geklemmt: bool = False


class DynamikEval(BaseModel):
    """Wie viel im Bild passiert (Vorgabe Chris, 2026-08-06).

    Chris: „die Prüfung von einem geringen Bildreiz würde ich auf Basis der allgemeinen Dynamik im
    Video bewerten. Wenn es zum Beispiel nur einen Kamerawinkel gibt, dann ist das eben auch ein
    Zeichen dafür, dass es weniger Bildreiz gibt."

    Kein Score: Geringe Dynamik ist nicht per se schlecht — ein ruhiges Talking Head kann für sein
    Thema richtig sein. Das Urteil steuert nur, OB Effekte empfohlen werden.

    `urteil`: gering | mittel | hoch."""
    urteil: str = ""
    kommentar: str = ""


class EffektVorschlag(BaseModel):
    """Eine Stelle, an der ein Sound- oder ein kleiner visueller Effekt etwas bringen würde.

    Wie bei `Einblendung`: Das Modell nennt Stelle und Zweck, den fertigen Schritt baut der Code.
    `art`: sound | visuell."""
    zeitpunkt_sek: float = 0.0
    art: str = ""
    zweck: str = ""


class UntertitelEval(BaseModel):
    """Untertitel als eigener Block (Vorgabe Chris, 2026-08-06).

    Vorher standen sie als Freitext in `schnitt_pacing.kommentar` — dort erzeugten sie nie einen
    Handlungsschritt, und Position und Design wurden gar nicht erfasst. Chris hat die Kritik daran
    fünfmal gegeben (statische Blöcke, zu viele Wörter, falsche Platzierung).

    `maengel` trägt Werte aus UNTERTITEL_MANGEL_ARTEN. Wie bei `texthook_maengel` baut der Code die
    Empfehlung NUR aus dem, was gemeldet wurde — nicht aus der ganzen Prüfliste auf Verdacht.

    `vorhanden=False` IST ein Mangel, sobald im Video gesprochen wird (Vorgabe Chris, 2026-08-06:
    „ein Untertitel, der mitläuft, ist im Video absolut essentiell"). Nur ein Video ohne
    gesprochenes Wort hat nichts zu untertiteln. Bis dahin galt das Fehlen als Formatentscheidung —
    diese Regel ist seither umgekehrt, siehe `erzwinge_untertitel_empfehlung`.

    ZWEI Scores, weil es zwei verschiedene Fragen sind (Stufe 2):
    - `score` („gibt es sie?") gehört zur Kategorie Mittelteil — es ist eine RETENTION-Frage:
      Ein großer Teil der Zuschauer schaut ohne Ton und scrollt ohne Untertitel weiter.
    - `gestaltung_score` („wie sind sie gemacht?") gehört zum Editing — Platzierung, Wortzahl,
      Lesbarkeit, Timing. Das ist Handwerk.
    Beide sind nullable, und beide setzt bei den klaren Fällen der CODE, nicht das Modell
    (`setze_untertitel_scores`): Wird nicht gesprochen, sind beide null („nicht bewertbar");
    wird gesprochen und fehlen sie, ist `score` 1 und `gestaltung_score` null — an etwas, das
    es nicht gibt, ist nichts gestaltet. Nur bei vorhandenen Untertiteln urteilt das Modell."""
    vorhanden: bool = False
    maengel: list[str] = Field(default_factory=list)
    kommentar: str = ""
    score: Optional[int] = None            # „gibt es sie?" — Dimension untertitel_vorhanden
    gestaltung_score: Optional[int] = None  # „wie sind sie gemacht?" — Dimension untertitel_gestaltung

    @field_validator("maengel", mode="before")
    @classmethod
    def _objekte_zu_strings(cls, v):
        return liste_von_strings(v)


class EnergieEval(BaseModel):
    """Energie im Auftreten — score-frei wie der Blick (Vorgabe Chris, 2026-08-06).

    Chris: „mit welcher Energy spricht er in die Kamera? Diese Energy hat auch einen krassen
    Einfluss auf die Form dieses Videos." Bisher war sie in `sprechqualitaet` eingeschmolzen
    (Skill: „Tempo, Energie, Deutlichkeit zu EINEM Score") — man konnte nie sehen, ob eine 3 an der
    Aussprache oder an fehlender Energie lag.

    Kein Score, weil hohe Energie nicht per se besser ist: Ein ruhiger, ernster Vortrag kann für
    sein Thema genau richtig sein. Bewertet wird die PASSUNG zum Inhalt, und das ist ein Urteil,
    kein Messwert.

    `urteil`: traegt | flach | uebertrieben. Nur `flach` löst einen Schritt aus."""
    urteil: str = ""
    kommentar: str = ""


class BlickEval(BaseModel):
    """Blickrichtung — bewusst OHNE Score (Vorgabe Chris, 2026-08-06).

    Vorher lief die Blickrichtung über `visuelle_aesthetik.probleme`. Das hatte zwei Folgen, die
    beide nicht gewollt waren: Sie zählte für `deckle_score_auf_probleme` und lief damit über die
    17 Gewichtspunkte der Ästhetik in den Performance-Score, und sie war über 41 Läufe der mit
    Abstand häufigste Ästhetik-Befund (54 %) — der Score maß am Ende überwiegend den Blick.

    Ein Urteil ohne Score kann trotzdem eine Handlungsempfehlung auslösen: Chris hält die Wirkung
    für stark genug, dass sie in den Schritten auftauchen MUSS, wenn sie negativ auffällt.

    `urteil`: in_der_linse | abgelesen | unklar. `abgelesen` ist der einzige Auslöser."""
    urteil: str = ""
    kommentar: str = ""


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

    @field_validator("probleme", "hinweise", mode="before")
    @classmethod
    def _objekte_zu_strings(cls, v):
        return liste_von_strings(v)


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
    erzwungen: bool = False   # True = vom Code eingesetzter Standardsatz, weil das Modell zu
                              # dieser Dimension nichts geschrieben hat. Nur zur Messung: Wie oft
                              # greift der Notnagel noch? Ziel ist, dass er selten greift
                              # (Vorgabe Chris, 2026-09-12). Kein Einfluss auf Reihenfolge,
                              # Auswahl oder Anzeige.


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

    `bereits_vorhanden` trennt Beobachtung von Empfehlung: Ohne dieses Feld nennt das Modell die
    inhaltlich stärksten Momente — und das sind genau die, an denen ein guter Cutter längst eine
    Einblendung gesetzt hat. Default False, damit gespeicherte Altläufe sich nicht rückwirkend
    ändern.
    """
    zeitpunkt_sek: float = 0.0
    verstaerkt: str = ""   # das Wort oder die Aussage, die verstärkt werden soll
    bereits_vorhanden: bool = False   # True = an dieser Stelle liegt schon eine Einblendung


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

    erzwungen: bool = False   # True = vom Code eingesetzter Standardsatz, weil das Modell zu
                              # dieser Dimension nichts geschrieben hat. Nur zur Messung: Wie oft
                              # greift der Notnagel noch? Ziel ist, dass er selten greift
                              # (Vorgabe Chris, 2026-09-12). Kein Einfluss auf Reihenfolge,
                              # Auswahl oder Anzeige.


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

# Vom Nutzer beim Upload gewähltes Videoziel (genau EINES, Pflicht bei Engine v3). Wie FORMATE
# Single Source of Truth: Die API validiert dagegen, der Prompt bekommt die Auswahl als FAKT.
# Anzeige im Frontend in Nutzersprache, hier nur die Funnel-Stufe:
#   TOFU = "Neue Menschen erreichen" | MOFU = "Vertrauen und Expertenstatus aufbauen"
#   BOFU = "Kundenanfragen gewinnen"
# Kein Wert "Mischung": Wer alles auswählen kann, bekommt kein scharfes Urteil, und das Modell
# bekommt eine Ausrede, sich nicht festzulegen (Vorgabe Chris, 2026-09-11).
ZIELE = ("TOFU", "MOFU", "BOFU")

# Score-Gewichte je Ziel. Summe je Spalte = 100, damit der Score zwischen den Zielen dieselbe
# Skala hat — vergleichbar sind zwei Läufe damit trotzdem nur bei GLEICHEM Ziel, deshalb nennt
# das Frontend-Label das Ziel mit ("78 . gemessen an: ...").
#
# Herleitung (Vorgabe Chris, 2026-09-12):
# - TOFU: Hook am wichtigsten, alle drei Ebenen; Spannungsbogen am unwichtigsten; Bildqualität hoch.
# - MOFU: visuelle Hook fällt deutlich, Sprech-/Text-Hook bleiben hoch, Spannungsbogen steigt stark,
#   Bildqualität sinkt.
# - BOFU: wie MOFU, ergänzt um den CTA.
#
# `untertitel_vorhanden`, `untertitel_gestaltung`, `audioqualitaet` und `cta` sind seit Stufe 2
# real bewertet (UntertitelEval.score/.gestaltung_score, AnalystEvaluationV2.audioqualitaet/.cta).
# `berechne_performance_score` überspringt Dimensionen ohne Score weiterhin automatisch und verteilt
# ihr Gewicht proportional — das trägt die Altläufe, die diese Felder nicht kennen.
SCORE_GEWICHTE_JE_ZIEL = {
    "TOFU": {
        "sprech_hook": 16, "text_hook": 16, "visuell_hook": 13,
        "spannungsbogen": 7, "struktur": 7, "untertitel_vorhanden": 8,
        "schnitt_pacing": 7, "untertitel_gestaltung": 3,
        "sprechqualitaet": 8, "visuelle_aesthetik": 10, "audioqualitaet": 5,
        "cta": 0,
    },
    "MOFU": {
        "sprech_hook": 15, "text_hook": 15, "visuell_hook": 7,
        "spannungsbogen": 15, "struktur": 10, "untertitel_vorhanden": 10,
        "schnitt_pacing": 6, "untertitel_gestaltung": 3,
        "sprechqualitaet": 10, "visuelle_aesthetik": 6, "audioqualitaet": 3,
        "cta": 0,
    },
    "BOFU": {
        "sprech_hook": 14, "text_hook": 14, "visuell_hook": 6,
        "spannungsbogen": 13, "struktur": 9, "untertitel_vorhanden": 9,
        "schnitt_pacing": 5, "untertitel_gestaltung": 3,
        "sprechqualitaet": 9, "visuelle_aesthetik": 6, "audioqualitaet": 3,
        "cta": 9,
    },
}

# Welche Dimension in welchem Output-Block erscheint (Frontend Stufe 3, Lob-Filter Stufe 1).
KATEGORIEN = {
    "hook": ("sprech_hook", "text_hook", "visuell_hook"),
    "mittelteil": ("spannungsbogen", "struktur", "untertitel_vorhanden", "cta"),
    "editing": ("schnitt_pacing", "untertitel_gestaltung"),
    "auftreten": ("sprechqualitaet", "visuelle_aesthetik", "audioqualitaet"),
}


class Staerke(BaseModel):
    """EIN positiver Punkt, mit dem Bezug zu der Dimension, aus der er stammt.

    `betrifft` ist Pflicht, damit der Code prüfen kann, ob die Scores das Lob decken: Eine
    Stärke wird nur angezeigt, wenn ihre Kategorie mindestens eine Dimension mit Score >= 4 hat.
    Ohne dieses Feld war „erfinde kein Lob" eine reine Prompt-Bitte — dieselbe Falle wie bei P2
    in docs/offene-fixes-analyst.md, wo eine Pflicht ohne Schwelle zu Boilerplate wurde.

    Altläufe lieferten `staerken` als Liste von Strings. Der Validator in AnalystEvaluationV2
    nimmt beide Formen entgegen, damit gespeicherte Ergebnisse unverändert laden.
    """
    text: str = ""
    betrifft: str = ""    # Name aus SCORE_GEWICHTE_JE_ZIEL, oder leer


class AnalystEvaluationV2(BaseModel):
    """Schlanke Bewertung durch Claude — scannbar, ~80 % kürzer als V1."""
    zielgruppe: str = ""               # genau 1 Satz
    # Wie relevant das Video FÜR diese Zielgruppe ist. Anlass (Lauf d9988b7d, Feedback zu
    # `zielgruppe`): „falls die zielgruppen und branddaten vorhanden sind, soll hier ergänzt
    # werden, inwiefern das video relevant für die zielgruppe ist." Die Brand-/Zielgruppen-Datei
    # ist noch nicht gebaut — bis dahin liefert das Modell nichts und das Feld bleibt leer. Genau
    # das ist der gewollte Zustand: Ohne hinterlegte Daten wäre jede Relevanz-Aussage geraten.
    format: str = ""                   # vom Nutzer gewählt (FORMATE), Code überschreibt das Modell-Feld
    protagonist_ab_sek: float = 0.0    # ab wann der Protagonist selbst spricht; >0 z.B. bei Reaction
                                       # (davor läuft fremdes Audio — das ist NICHT sein Sprech-Hook)
                                       # null vom Modell → 0.0, siehe _null_ist_sekunde_null unten
    performance_score: int = 0         # 0–100
    zielgruppen_relevanz: str = ""     # nur gefüllt, wenn Zielgruppen-/Markendaten vorliegen
    funnel: str = ""                   # TOFU / MOFU / BOFU / Mischung — die ABSICHT: bei V3 trägt
                                       # der Code hier das gewählte Nutzerziel ein
    # Auf welche Funnel-Stufe das Video TATSÄCHLICH einzahlt — Einschätzung des Modells, bewusst
    # getrennt von `funnel`. Anlass (Lauf dc5c0a3d): Dort stand `funnel: MOFU`, exakt das gewählte
    # Ziel — kein Zufall, der V3-Skill verlangte wörtlich „Trag das Ziel unverändert in das Feld
    # funnel ein". Das Modell schätzte nichts ein, es schrieb ab, und damit war der interessanteste
    # Vergleich (Absicht gegen Wirkung) unmöglich. Leer heißt „keine belastbare Einschätzung":
    # `pruefe_funnel_wirkung` leert das Feld, wenn kein Wert aus ZIELE drinsteht — lieber nichts
    # als geraten.
    funnel_wirkung: str = ""           # TOFU / MOFU / BOFU
    funnel_wirkung_grund: str = ""     # EIN Satz, woran das Modell die Wirkung festmacht
    # Was der Nutzer TUN müsste, damit das Video zum gewählten Ziel passt. Anlass (Lauf d9988b7d,
    # Feedback zu `performance_score`): „wenn es vorbeigeht bitte eine empfehlung geben wie man das
    # video gestalten müsste, das es zum ziel passt. erklärung bitte beispielhaft an dem inhalt des
    # videos". `funnel_wirkung_grund` sagt bisher nur, WAS das Video tut — die Handlung fehlte.
    # Nur bei Abweichung gefüllt; `pruefe_funnel_wirkung` leert das Feld sonst.
    funnel_wirkung_empfehlung: str = ""
    hook: HookEval = Field(default_factory=HookEval)
    struktur: StrukturEval = Field(default_factory=StrukturEval)
    sprechqualitaet: ScoreProbleme = Field(default_factory=ScoreProbleme)
    schnitt_pacing: ScoreKommentar = Field(default_factory=ScoreKommentar)
    spannungsbogen: ScoreKommentar = Field(default_factory=ScoreKommentar)
    visuelle_aesthetik: ScoreProbleme = Field(default_factory=ScoreProbleme)
    # Klang, nicht Lautheit: Störgeräusche, Hall, Verständlichkeit beurteilt das Modell selbst —
    # Gemini bekommt das Video MIT Ton. Die Lautheit ist dagegen gemessen (quality_metrics), und
    # genau dort hat das Modell schon einmal danebengelegen (Lauf dc5c0a3d: „um ca. 3 Dezibel
    # anheben" bei −35,8 LUFS, es fehlten rund 22 LU). Deshalb deckelt `deckle_audioqualitaet`
    # den Score gegen den gemessenen Korridor.
    audioqualitaet: ScoreProbleme = Field(default_factory=ScoreProbleme)
    # Call to Action — gewichtet NUR bei BOFU (SCORE_GEWICHTE_JE_ZIEL: dort 9, sonst 0).
    # `StrukturElemente.cta` bleibt als reine Beobachtung bestehen („ist einer da?"); bewertet
    # („ist er konkret, sitzt er richtig?") wird hier.
    cta: ScoreKommentar = Field(default_factory=ScoreKommentar)
    untertitel: UntertitelEval = Field(default_factory=UntertitelEval)
    dynamik: DynamikEval = Field(default_factory=DynamikEval)     # steuert, ob Effekte empfohlen werden
    effekt_vorschlaege: list[EffektVorschlag] = Field(default_factory=list)  # Code bündelt zu EINEM Schritt
    blickkontakt: BlickEval = Field(default_factory=BlickEval)    # score-frei, kann aber einen Schritt auslösen
    energie: EnergieEval = Field(default_factory=EnergieEval)     # score-frei, siehe EnergieEval
    staerken: list[Staerke] = Field(default_factory=list)   # positives Feedback, siehe Staerke
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

    @field_validator("top_tipps", "texthook_varianten", "texthook_maengel", mode="before")
    @classmethod
    def _objekte_zu_strings(cls, v):
        """Siehe liste_von_strings: Das Objekt-Muster von `staerken` färbt auf Nachbarfelder ab."""
        return liste_von_strings(v)

    @field_validator("staerken", mode="before")
    @classmethod
    def _strings_bleiben_lesbar(cls, v):
        """Altläufe (und das V2-Schema) liefern Strings statt Objekten. Ohne diese Umwandlung
        würde jedes gespeicherte Ergebnis beim Laden mit einem Validierungsfehler brechen —
        und ein abgebrochener Lauf ist die teuerste aller Antworten."""
        if not isinstance(v, list):
            return v
        return [{"text": e, "betrifft": ""} if isinstance(e, str) else e for e in v]


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
    phasen_sek: dict[str, float] = Field(default_factory=dict)
    # Dauer je Phase (transkript / messwerte / bewertung). Entscheidungsgrundlage für
    # ANALYST_MAX_CONCURRENT: „transkript" belegt die CPU, „bewertung" wartet nur auf das
    # Gemini-Netz. Nur aus dem Verhältnis lässt sich sagen, ob ein zweiter paralleler Lauf
    # Durchsatz bringt oder nur zwei Läufe gleichzeitig ausbremst. Altläufe: leeres Dict.
    geplante_texthook: str = ""        # vom Nutzer vor der Analyse eingetragene, geplante Texthook (Freifeld)
    gewaehltes_format: str = ""        # vom Nutzer beim Upload gewähltes Format (Pflicht, genau eines aus
                                       # FORMATE); leer nur bei Altläufen → Modell klassifiziert dann selbst
    gewaehltes_ziel: str = ""           # vom Nutzer gewähltes Videoziel (Pflicht bei Engine v3,
                                        # genau eines aus ZIELE). Leer = Altlauf oder v2 → die
                                        # Nachbearbeitung verhält sich wie vor V3. Dieses Feld ist
                                        # der Schalter für die V3-Logik, NICHT `engine`:
                                        # analyst_engine._run() setzt `result.engine` erst NACH dem
                                        # Aufruf von nachbearbeiten(), dort stünde sonst der Default.
