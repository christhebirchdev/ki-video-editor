# services/analyst_eval.py
"""
V2-Bewertungs-Modul für den AI Video Analyst.

Single Source of Truth der Bewertungslogik ist die Markdown-Datei
`analyst_eval_skill.md` (Mensch-lesbar, versioniert, ohne Python editierbar).
Dieses Modul lädt den Skill-Body und hängt den strikten JSON-Output-Vertrag an,
bevor es ihn als System-Prompt an die Claude-API gibt.

Der Schema-Block lebt bewusst HIER (nicht in der .md): Im interaktiven Cowork-
Gebrauch will man Fließtext, in der Pipeline striktes JSON — derselbe Skill-Body,
zwei Modi.
"""
import json
import re
from pathlib import Path

import anthropic

from config import settings
from models.analyst import ActionStep, AnalystEvaluationV2, AnalystResult, Empfehlung
from services import analyst_prompt_log

# Version der Bewertungslogik (Skill + Schema + Nachbearbeitung). Wird an jedes gespeicherte
# Feedback gestempelt: Feedback zu einer alten Prompt-Version ist für spätere Auswertungen sonst
# irreführend ("wurde längst gefixt"). Bei inhaltlichen Prompt-Änderungen hochzählen.
# Suffix, wenn sich der Prompt am selben Tag ein zweites Mal inhaltlich ändert — sonst wäre das
# Feedback vom Abend nicht vom Feedback des Vormittags zu unterscheiden.
PROMPT_VERSION = "2026-07-31"

SKILL_PATH = Path(__file__).with_name("analyst_eval_skill.md")
# Separat gepflegte Referenz (kompakte Pipeline-Fassung: Prinzipien + Beispiel-Anker). Wird vom
# Skill referenziert und hier zur Laufzeit an den System-Prompt angehängt (das Pipeline-Claude hat
# keinen Dateizugriff). Optional: fehlt die Datei, läuft die Bewertung unverändert weiter.
# Ausführliche Fassung (volle Experten-Zitate + Musteranalysen) liegt separat als Trainingsdoc.
REFERENCE_PATH = SKILL_PATH.with_name("analyst_video_reference.md")

# Heuristische Richtwerte zur internen Messwert-Interpretation (nur Urteilsgrundlage,
# Zahlen dürfen laut Skill NICHT im Output erscheinen).
from services.analyst_quality import (
    LOUDNESS_OPTIMAL_HIGH, LOUDNESS_OPTIMAL_LOW, LOUDNESS_TOO_QUIET,
)
from services.analyst_speech import PAUSE_THRESHOLD_SEC

_AUDIO_GUIDE = (
    f"Lautheit: optimaler Bereich {LOUDNESS_OPTIMAL_LOW} bis {LOUDNESS_OPTIMAL_HIGH} LUFS "
    f"(empirisch festgelegt). Innerhalb DIESES Bereichs ODER lauter (höherer LUFS-Wert, also näher an 0) "
    f"ist der Ton NICHT zu leise — dann keinen „zu leise\"-Hinweis geben und keinen Abzug. Erst deutlich "
    f"unter {LOUDNESS_TOO_QUIET} LUFS ist der Ton wirklich zu leise. True Peak > -1 dBFS = Clipping-Gefahr."
)

_BILD_GUIDE = (
    "Richtwerte Bild (intern, Frames max 640px): Schärfe (Laplacian-Varianz) <50 unscharf, "
    "50–150 mäßig, >300 knackig. Helligkeit (0–255) Ziel ~90–160. Kontrast (Std) <30 flau. "
)

METRICS_GUIDE = _BILD_GUIDE + _AUDIO_GUIDE

# Wird ausgegeben, wenn es KEINE Bildmesswerte gibt. Ohne diesen Satz stand im Prompt
# „Schärfe avg 0.0, Helligkeit avg 0.0, Kontrast avg 0.0" als FAKT, direkt gefolgt von der
# Anweisung, den Messwerten mehr zu glauben als dem eigenen Seheindruck (Lauf c12db030).
# Ergebnis: Das Modell kritisierte die Bildqualität gar nicht mehr.
KEINE_BILDWERTE_HINWEIS = (
    "Für das BILD liegen in diesem Modus KEINE Messwerte vor. Beurteile Schärfe, Belichtung, "
    "Rauschen und Bildqualität ausschließlich aus dem Video, das du siehst — hier gilt dein "
    "Seheindruck, nicht eine Zahl."
)


def bildwerte_belastbar(qm) -> bool:
    """Sind die Bildmesswerte echte Messungen oder nur Defaults?

    `v2_hybrid` zieht keine Frames, deshalb bleiben Schärfe, Helligkeit und Kontrast auf 0.0.
    Drei Nullen gleichzeitig sind keine dunkle, flaue, unscharfe Aufnahme — sie sind „nicht
    gemessen". Diese Unterscheidung an EINER Stelle, damit v1 und v2 sie nicht auseinanderlaufen.
    """
    if qm is None:
        return False
    return not (
        (qm.schaerfe_avg or 0) == 0
        and (qm.helligkeit_avg or 0) == 0
        and (qm.kontrast_avg or 0) == 0
    )


def metrics_txt(qm) -> str:
    """Messwert-Block für den Prompt — Bildzeile nur, wenn wirklich gemessen wurde."""
    if qm is None:
        return "Keine Messwerte verfügbar."
    audio = (
        f"Lautheit {qm.lufs_integrated} LUFS, Loudness Range {qm.loudness_range} LU, "
        f"True Peak {qm.true_peak_db} dBFS"
        if qm.lufs_integrated is not None else "kein Audio messbar"
    )
    if bildwerte_belastbar(qm):
        return (
            f"Bild: Schärfe avg {qm.schaerfe_avg} (min {qm.schaerfe_min}), "
            f"Helligkeit avg {qm.helligkeit_avg}, Kontrast avg {qm.kontrast_avg}. "
            f"Audio: {audio}.\n{METRICS_GUIDE}"
        )
    return f"Audio: {audio}.\n{_AUDIO_GUIDE}\n{KEINE_BILDWERTE_HINWEIS}"


TOP_ACTION_STEPS = 3

# Gruppen, die aus einem schwachen Dimensions-Score entstehen — allgemeine Sammel-Tipps ohne echte
# Stelle im Video. Hooks und Anlauf gehören NICHT dazu: die betreffen die ersten Sekunden und
# behalten Vorrang.
SAMMEL_GRUPPEN = frozenset({"sprechqualitaet", "aesthetik", "spannungsbogen", "struktur", "schnitt"})

# Wie viele Sammel-Tipps höchstens in den Top 3 stehen (Vorgabe Chris). Ein Platz bleibt so für
# einen videospezifischen Schritt mit echter Sekundenangabe frei.
MAX_SAMMEL_OBEN = 2


def _normtext(s: str) -> str:
    """Anweisung auf Kern normalisieren, damit „Sprechpause rausschneiden" an drei Stellen als
    dieselbe Handlung erkannt wird, „Gehirn-Symbol" und „Telefon-Symbol" aber nicht."""
    return " ".join((s or "").lower().split())


def _zeit_label(sekunden: list[float]) -> str:
    """„ca. Sek. 3" bzw. „ca. Sek. 3, 15 und 24" — Zeitangaben sind Richtwerte (±1–2 s)."""
    s = [str(int(round(x))) for x in sekunden]
    return f"ca. Sek. {s[0]}" if len(s) == 1 else f"ca. Sek. {', '.join(s[:-1])} und {s[-1]}"


def verteile_empfehlungen(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Bündeln → sortieren → Top-3 abtrennen. Deterministisch im Code statt per Prompt-Regel.

    Das Modell liefert eine flache `empfehlungen`-Liste und entscheidet nur, WELCHE Einträge
    dieselbe Handlung sind (Feld `gruppe`) — das ist Urteil. Der Rest ist Arithmetik und stand
    vorher 3× fast wortgleich im Prompt, ohne sicher zu greifen (Befund 3):
    „sortiere nach Zeitpunkt, nimm die 3 frühesten, der Rest nach weitere_empfehlungen".

    Liefert das Modell `empfehlungen` nicht (Altlauf/altes Schema), bleiben die geparsten
    action_steps unverändert stehen.
    """
    if not parsed.empfehlungen:
        return parsed
    gruppen: dict[str, list[Empfehlung]] = {}
    for i, e in enumerate(parsed.empfehlungen):
        # Gebündelt wird nur, wenn Label UND Anweisung übereinstimmen. Das Label allein reicht NICHT:
        # das Modell nutzt `gruppe` sonst als KATEGORIE ("alles Einblendungen") und wirft verschiedene
        # Handlungen zusammen — real passiert (Lauf 702f9c11): Gehirn-Symbol @18s, Telefon @28s und
        # Folgen-Knopf @41s wurden zu „Gehirn-Symbol @18,28,41" verschmolzen. Untermerge ist harmlos
        # (zwei ähnliche Schritte), Fehlmerge zeigt dem Nutzer aktiv die falsche Handlung.
        label = (e.gruppe or "").strip().lower()
        key = f"{label}\x00{_normtext(e.anweisung)}" if label else f"\x00einzeln{i}"
        gruppen.setdefault(key, []).append(e)

    # (Zeitpunkt, Gruppen-Label, Schritt) — das Label muss durch die Sortierung mitlaufen,
    # weil die Auswahl unten danach entscheidet.
    schritte: list[tuple[float, str, ActionStep]] = []
    for eintraege in gruppen.values():
        eintraege.sort(key=lambda e: e.zeitpunkt_sek)
        schritte.append((
            eintraege[0].zeitpunkt_sek,  # eine Gruppe zählt ab ihrem FRÜHESTEN Vorkommen
            (eintraege[0].gruppe or "").strip().lower(),
            ActionStep(
                zeitpunkt=_zeit_label([e.zeitpunkt_sek for e in eintraege]),
                anweisung=eintraege[0].anweisung,
            ),
        ))
    schritte.sort(key=lambda t: t[0])

    # Höchstens zwei Sammel-Tipps in den Top 3 (Vorgabe Chris). In Lauf 26a1adbf belegten
    # Anlauf-Schnitt plus zwei erzwungene Dimensions-Tipps alle drei Plätze; die konkreten Tipps
    # mit echter Sekundenangabe (Sek. 10, 15, 20) rutschten komplett nach unten. Ein Platz bleibt
    # deshalb für einen videospezifischen Schritt frei.
    # Hook- und Anlauf-Schritte zählen NICHT zum Deckel — sie betreffen die ersten Sekunden und
    # behalten Vorrang (frühere Vorgabe).
    oben: list[ActionStep] = []
    rest: list[ActionStep] = []
    sammel = 0
    for _, gruppe, schritt in schritte:
        ist_sammel = gruppe in SAMMEL_GRUPPEN
        if len(oben) < TOP_ACTION_STEPS and not (ist_sammel and sammel >= MAX_SAMMEL_OBEN):
            oben.append(schritt)
            sammel += ist_sammel
        else:
            rest.append(schritt)

    parsed.action_steps = oben
    parsed.weitere_empfehlungen = rest
    return parsed


FREMD_TEXTHOOK_HINWEIS = (
    "Der sichtbare Bildschirmtext gehört zum reagierten Fremdvideo, nicht zu dir — er zählt nicht als "
    "deine Texthook. Dir fehlt eine EIGENE statische Texthook. Erstelle mindestens 3 Varianten und teste "
    "sie über die Testreel-Funktion von Instagram gegeneinander."
)


def bereinige_fremd_texthook(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Reaction + leeres „Geplante Texthook"-Feld → der sichtbare Bildschirmtext gehört zum reagierten
    Fremdvideo, nicht zum Protagonisten → Score hart auf 0.

    Warum im Code und an DIESER Bedingung: Gemini unterscheidet einen in den reagierten Clip
    eingebrannten Text visuell NICHT von einem eigenen Overlay — es bewertete ihn über 5 Läufe stabil
    als eigene Texthook (Score 3), auch mit explizitem Prompt-Hinweis und Urteilsfeld. Der Nutzer weiß
    es dagegen sicher: hat er eine eigene Texthook, trägt er sie ins Feld ein; ist das Feld bei einer
    Reaction leer, ist der einzige Text der des Fremdvideos.
    Fällt das Feld aus (eigene Texthook eingetragen), greift die normale Bewertung dieses Textes."""
    ist_reaction = (getattr(result, "gewaehltes_format", "") or "").strip().lower() == "reaction"
    hat_eigene = bool((getattr(result, "geplante_texthook", "") or "").strip())
    if ist_reaction and not hat_eigene:
        parsed.hook.text_hook_vorhanden = False
        parsed.hook.text_hook_score = 0
        parsed.hook.text_hook_grund = FREMD_TEXTHOOK_HINWEIS
        parsed.hook.text_hook_score_geklemmt = True
    return parsed


# Wie viele Wörter am Transkript-Anfang als „Eröffnung" gelten. 15 Wörter sind bei ~140 WPM
# gut 6 Sekunden — der Bereich, in dem eine Hook wirkt.
REDUNDANZ_FENSTER_WOERTER = 15

# Deckel bei gemessener Redundanz. Die Texthook zahlt mehr: Sie hat den knapperen Platz und muss
# genau das liefern, was das Gesprochene noch nicht abdeckt (Regel steht so im Skill).
# Der Sprech-Hook hat die Eröffnung verschenkt, kann sich danach aber fangen — deshalb 3, nicht 2.
REDUNDANZ_DECKEL_TEXT = 2
REDUNDANZ_DECKEL_SPRECH = 3

REDUNDANZ_TEXT_HINWEIS = (
    "Dein Bildtext wird in den ersten Sekunden fast wörtlich mitgesprochen. Damit verschenkst du eine "
    "komplette Hook-Ebene: Der Text soll etwas liefern, was das Gesprochene noch NICHT sagt — eine "
    "Zuspitzung, eine Zahl, eine offene Frage. Doppelt gesagt wirkt beides schwächer."
)

REDUNDANZ_SPRECH_HINWEIS = (
    "Deine ersten Worte lesen den Bildtext vor. Genau die wertvollsten Sekunden gehen so ohne "
    "Gegenwert weg — die Neugier entsteht erst danach. Steig direkt mit dem ein, was für den "
    "Zuschauer auf dem Spiel steht."
)

_WORTZEICHEN = re.compile(r"[^\wäöüß ]+", re.IGNORECASE)


def _normalisiert(text: str) -> str:
    """Kleinschreibung, ohne Satzzeichen, einfache Leerzeichen — für den Textvergleich."""
    return " ".join(_WORTZEICHEN.sub(" ", (text or "").lower()).split())


def bereinige_redundante_texthook(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Liest der Sprecher die Texthook zu Beginn vor, ist das eine verschenkte Hook-Ebene.

    Warum im Code und nicht (nur) im Prompt: Die Regel „Redundanz Sprech-Hook = Text-Hook ist eine
    SCHWÄCHE" steht seit Längerem im Skill — und griff im Lauf 5502bb37 nicht. Das Modell gab beiden
    Hooks eine 4, obwohl das Transkript wörtlich mit der Bildüberschrift beginnt
    („Inbound vs. Outbound, wer beide gleich behandelt…"). Der Grund: Die Regel setzt voraus, dass
    das Modell die Überlappung ERKENNT. Ob zwei Textstellen übereinstimmen, ist aber keine
    Ermessensfrage, sondern ein Stringvergleich — und der gehört damit hierher.

    Gemessen wird gegen `text_hook_wortlaut` — das Feld führt den ERÖFFNUNGS-BILDTEXT, auch wenn er
    nicht als Hook zählt. Ohne Zitat kein Check; das ist Absicht, geraten wird hier nicht.

    **Bewusst NICHT an `text_hook_vorhanden` gekoppelt.** Grafischer Inhalt (Spaltenüberschrift einer
    Vergleichsgrafik) wird laut Skill als „keine Text-Hook" mit Score 0 gemeldet — würde die Prüfung
    das verlangen, entfiele genau im Fall 5502bb37 der Sprech-Hook-Abzug für das Vorlesen. Die
    Text-Klemme greift dann ohnehin nicht (0 ist bereits das Minimum), die Sprech-Klemme sehr wohl.

    Beide Scores werden nur gedeckelt, nie erhöht. Die Deckel lösen zugleich die vorhandenen
    Erzwingungen aus: Sprech-Score ≤3 triggert die Sprechhook-Empfehlung,
    `text_hook_score_geklemmt` die Texthook-Empfehlung.
    """
    wortlaut = _normalisiert(getattr(parsed.hook, "text_hook_wortlaut", ""))
    if len(wortlaut.split()) < 2:
        return parsed          # nichts zitiert oder zu kurz für einen belastbaren Vergleich

    eroeffnung = " ".join(_normalisiert(getattr(result, "transcript", "")).split()[:REDUNDANZ_FENSTER_WOERTER])
    if not eroeffnung or wortlaut not in eroeffnung:
        return parsed

    alt_text = parsed.hook.text_hook_score
    if alt_text is not None and alt_text > REDUNDANZ_DECKEL_TEXT:
        parsed.hook.text_hook_score = REDUNDANZ_DECKEL_TEXT
        parsed.hook.text_hook_score_geklemmt = True
        # ERSETZEN, nicht anhängen — wie bei bereinige_fremd_texthook. Die alte Begründung lobte
        # genau das, was hier abgewertet wird; aneinandergehängt liest der Kunde einen Widerspruch
        # („zieht Blicke an" … „verschenkst eine Hook-Ebene").
        parsed.hook.text_hook_grund = REDUNDANZ_TEXT_HINWEIS

    alt_sprech = parsed.hook.sprech_hook_score
    if alt_sprech is not None and alt_sprech > REDUNDANZ_DECKEL_SPRECH:
        parsed.hook.sprech_hook_score = REDUNDANZ_DECKEL_SPRECH
        parsed.hook.sprech_hook_grund = REDUNDANZ_SPRECH_HINWEIS

    return parsed


def erzwinge_nutzer_format(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Die Format-Auswahl des Nutzers ist bindend — der Prompt bittet darum, hier wird es garantiert.
    Ohne Auswahl (Altlauf) bleibt das Modell-Urteil stehen."""
    if gewaehlt := (getattr(result, "gewaehltes_format", "") or "").strip():
        parsed.format = gewaehlt
    return parsed


# Formulierungen, die den Ist-Zustand BESTÄTIGEN, statt eine Änderung zu verlangen. Das Modell legt
# sie regelmäßig unter `empfehlungen` ab statt unter `staerken` und verbrennt damit einen der nur drei
# Top-Plätze (Feedback Run 10ff4193: „handlungsempfehlungen sollen immer zur veränderung beitragen und
# nicht das bestehende gut reden"; Run f0ea9f9a dasselbe). Bewusst eine enge Phrasenliste statt einer
# allgemeinen „lassen/behalten"-Regel: „Lass den Zuschauer raten" ist eine echte Handlung.
_BESTAETIGUNG = re.compile(
    r"\bbeibehalten\b"
    r"|\b(behalte|behalten|belassen)\b"
    r"|\b(so|drin|dabei|stehen|unverändert)\s+lassen\b"
    r"|\b(im|beim)\s+(video|schnitt|schnittprogramm|bild)\s+lassen\b"
    r"|\bbestehen\s+lassen\b"
    r"|\bnicht\s+(raus)?schneiden\b"
    r"|\bnicht\s+(ändern|verändern|anfassen|entfernen|kürzen)\b",
    re.IGNORECASE,
)


def entferne_bestaetigungen(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Empfehlungen ohne Handlung rauswerfen. Lieber zwei echte Schritte als drei mit einem Füller."""
    if parsed.empfehlungen:
        parsed.empfehlungen = [
            e for e in parsed.empfehlungen if not _BESTAETIGUNG.search(e.anweisung or "")
        ]
    return parsed


# Ab wann der Start als „Anlauf" gilt: Atmen, Einrichten, Denkpause vor dem ersten Wort.
ANLAUF_SCHWELLE_SEC = 0.8

# Bewusst eng: „kürz" wäre hier falsch, weil „ersetze die Texthook durch eine kürzere Variante"
# bei Sekunde 0 sonst als Anlauf-Schnitt gelesen und gelöscht würde.
_SCHNITT_VERB = re.compile(r"(schneid|entfern)", re.IGNORECASE)

ANLAUF_HINWEIS = (
    "Schneide den Anlauf am Anfang weg — dein erstes Wort kommt erst bei Sekunde {sek}. "
    "Lass das Video direkt mit dem gesprochenen Satz starten, damit die Hook sofort sitzt."
)


def erzwinge_anlauf_schnitt(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Verzögerter Sprechbeginn → Schnitt-Empfehlung auf Sekunde 0 erzwingen.

    Warum im Code: Die Regel steht seit Langem im Skill („Hook-Start"), feuerte aber nicht
    zuverlässig (Feedback Run a4fbb8ae, sprechbeginn 0.98s: „die sprechpause am anfang wurde nicht
    empfohlen rauszuschneiden. das hätte ich mir hier gewünscht."). Der Messwert liegt vor und das
    Urteil ist eindeutig — damit ist es Arithmetik, kein Urteil, und gehört hierher.

    Bei Reaction NICHT: Dort misst `sprechbeginn_sec` das eingeblendete Fremdvideo, und die
    Übergangspause davor trägt den Formatwechsel.
    """
    stats = getattr(result, "speech_stats", None)
    if not stats:
        return parsed
    beginn = getattr(stats, "sprechbeginn_sec", 0.0) or 0.0
    ist_reaction = (getattr(result, "gewaehltes_format", "") or "").strip().lower() == "reaction"
    if beginn <= ANLAUF_SCHWELLE_SEC or ist_reaction:
        return parsed
    # Eigene Anlauf-Empfehlungen des Modells verwerfen — der erzwungene Schritt ist die kanonische
    # Fassung. Ein Vergleich über das `gruppe`-Label reichte nicht: das Modell schrieb "anlauf_weg"
    # statt "anlauf" und beide Schritte landeten im Output (Feedback Run 61d39035: „2 mal derselbe
    # tipp"). Erkannt wird stattdessen über Zeitfenster + Schnitt-Verb — eine Texthook-Empfehlung bei
    # Sekunde 0 bleibt damit unangetastet, weil sie nichts schneidet.
    parsed.empfehlungen = [
        e for e in parsed.empfehlungen
        if not (e.zeitpunkt_sek <= beginn + 0.5 and _SCHNITT_VERB.search(e.anweisung or ""))
    ]
    parsed.empfehlungen.insert(0, Empfehlung(
        zeitpunkt_sek=0.0, gruppe="anlauf",
        # :g statt round(): „Sekunde 1" statt „Sekunde 1.0", aber „Sekunde 1,4" bleibt genau.
        anweisung=ANLAUF_HINWEIS.format(sek=f"{round(beginn, 1):g}"),
    ))
    return parsed


# Ab diesem Score gilt eine Hook als verbesserungswürdig. 3 heißt laut Skill „funktional aber
# generisch" — da ist immer Luft nach oben, und genau dieser Fall blieb ohne Handlung (Run f2312dc9).
HOOK_SCHWACH_SCORE = 3

TEXTHOOK_EMPFEHLUNG = (
    "Blende in den ersten 3 Sekunden eine Texthook ein — kurzer Text im Bild, maximal 9 Wörter, der "
    "neugierig macht. Mindestens 5 Sekunden sichtbar, im oberen Drittel mit Abstand zum Rand. "
    "Schreib dir 3 Varianten und teste sie über die Testreel-Funktion von Instagram gegeneinander."
)

SPRECHHOOK_EMPFEHLUNG = (
    "Formuliere deinen ersten gesprochenen Satz um: Er soll entweder sofort neugierig machen oder ein "
    "konkretes Problem deiner Zielgruppe ansprechen. Sag gleich im ersten Satz, worum es geht — nicht "
    "erst im zweiten."
)


TEXTHOOK_MAX_WOERTER = 9

# Bewusst KEIN Schriftgrößen-Zielwert in den Empfehlungen (2026-07-30, Entscheidung Chris —
# ein konkreter Wert war kurz drin und wurde wieder entfernt). Die Größe wird nur relativ am Bild
# beurteilt und empfohlen; ein App-Wert wäre im gerenderten Video ohnehin nicht nachprüfbar.
# Nicht ohne neuen Grund erneut vorschlagen.

PAUSEN_ANWEISUNG = "Schneide diese unnötige Sprechpause raus, damit das Video flüssiger läuft."

# Eine Empfehlung, die eine Pause behandelt: „Pause" plus eine Schnitt-Handlung. Breiter als
# _SCHNITT_VERB (hier ist „kürzen" gemeint), aber durch das Wort „Pause" trotzdem eng.
_PAUSEN_EMPFEHLUNG = re.compile(r"pause.*(schneid|kürz|entfern|weg)|(schneid|kürz|entfern).*pause",
                                re.IGNORECASE | re.DOTALL)


def baue_pausen_schritt(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Aus `pausen_urteile` EINEN gebündelten Schnitt-Schritt bauen.

    Chris zu Run 25b8b2f6: „sprechpausen als empfehlung rauszuschneiden sollte gebündelt empfohlen
    werden". Über Freitext war das unmöglich — siehe PausenUrteil. Hier entsteht pro „raus"-Pause ein
    Eintrag mit IDENTISCHEM Text und gleichem Label; verteile_empfehlungen() fasst sie danach zu einem
    Schritt mit allen Zeitpunkten zusammen („ca. Sek. 12, 24 und 47").

    Liefert das Modell das Feld nicht (Altlauf), bleibt alles unverändert — sonst verlöre so ein Lauf
    seine Pausen-Empfehlungen ersatzlos.
    """
    if not parsed.pausen_urteile:
        return parsed
    parsed.empfehlungen = [
        e for e in parsed.empfehlungen if not _PAUSEN_EMPFEHLUNG.search(e.anweisung or "")
    ]
    for p in sorted((u for u in parsed.pausen_urteile if u.urteil == "raus"), key=lambda u: u.start_sec):
        parsed.empfehlungen.append(Empfehlung(
            zeitpunkt_sek=p.start_sec, gruppe="sprechpausen", anweisung=PAUSEN_ANWEISUNG))
    return parsed


EINBLENDUNGEN_MAX = 3

# Eine Empfehlung, die eine visuelle Einblendung vorschlägt. Wird nur zusammen mit einem
# Zeitpunkt-Treffer ausgewertet, deshalb darf das Muster breit sein.
_EINBLENDUNG_EMPFEHLUNG = re.compile(r"(einblend|blende|grafik|symbol|emoji|b-roll|broll)", re.IGNORECASE)


def baue_einblendungs_schritt(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Aus `einblendungen` EINEN Schritt bauen, der alle Stellen nennt — höchstens drei.

    Chris zu Run 3185d209: „den tipp mit den grafiken kann man auch zusammenfassen. maximal an 3
    stellen empfehlen. gerne auch statt bildgrafik auch optional eine b-roll aufnahme oder ähnliches
    empfehlen. hauptsache es geht um eine visuelle einblendung die den inhalt verstärkt und für
    abwechslung sorgt."

    Anders als bei den Pausen entsteht hier EIN Eintrag statt mehrerer mit gleichem Text: Die
    Information, WAS an welcher Stelle verstärkt werden soll, muss erhalten bleiben — beim Bündeln
    über `verteile_empfehlungen` überlebt nur eine Anweisung.
    """
    if not parsed.einblendungen:
        return parsed
    stellen = sorted(parsed.einblendungen, key=lambda e: e.zeitpunkt_sek)[:EINBLENDUNGEN_MAX]
    # Eigene Einblendungs-Empfehlungen des Modells an genau diesen Stellen verwerfen. Der
    # Zeitpunkt-Treffer (±2 s) hält den Filter eng: ein „Folgen-Knopf einblenden" am Videoende ist
    # ein CTA und keine Inhalts-Verstärkung — der bleibt.
    parsed.empfehlungen = [
        e for e in parsed.empfehlungen
        if not (_EINBLENDUNG_EMPFEHLUNG.search(e.anweisung or "")
                and any(abs(e.zeitpunkt_sek - s.zeitpunkt_sek) <= 2.0 for s in stellen))
    ]
    liste = ", ".join(
        f"bei Sek. {round(s.zeitpunkt_sek):g}" + (f" zu „{s.verstaerkt}“" if s.verstaerkt else "")
        for s in stellen
    )
    parsed.empfehlungen.append(Empfehlung(
        zeitpunkt_sek=stellen[0].zeitpunkt_sek, gruppe="einblendungen",
        anweisung=(
            f"Blende an diesen Stellen etwas Visuelles ein, das den Inhalt verstärkt — eine Grafik, "
            f"ein Symbol, ein Emoji, ein Foto oder eine kurze B-Roll-Aufnahme: {liste}. "
            f"Das bringt Abwechslung ins Bild und hält die Zuschauer länger im Video."
        ),
    ))
    return parsed


def gueltige_texthook_varianten(varianten: list[str]) -> list[str]:
    """Varianten über der Wortgrenze verwerfen. Zählen ist Arithmetik und gehört deshalb hierher.

    Die Regel steht im Skill samt Aufforderung „zähle die Wörter" — und wurde trotzdem gerissen
    (Run f2312dc9: 13 Wörter statt 9). Wortzählen ist genau die Aufgabe, die Sprachmodelle mal
    treffen und mal nicht.
    """
    return [v.strip() for v in varianten if v and len(v.split()) <= TEXTHOOK_MAX_WOERTER]


# Baustein je gemeldetem Mangel. Der Code setzt NUR die Sätze zusammen, die das Modell als
# schwach markiert hat (Feld `texthook_maengel`) — nicht mehr alle Gestaltungspunkte auf Verdacht.
# Feedback 4e56336e: „bei tipp 2 hätte nur die textinhaltsanpassung gereicht. optisch ist die
# texthook in ordnung … er soll aber dynamisch sein und von fall zu fall unterschiedlich
# formuliert sein."
_TEXTHOOK_BAUSTEINE = {
    "wortlaut":   "formuliere sie zugespitzter, damit sie wirklich neugierig macht",
    "laenge":     f"kürze sie auf höchstens {TEXTHOOK_MAX_WOERTER} Wörter",
    "redundanz":  "sag darin etwas, was du NICHT auch schon sprichst",
    "groesse":    "setz sie kleiner — eine Zeile nicht höher als der Kopf im Bild",
    "farbe":      "nimm eine ruhigere Farbe, die zum Look des Videos passt",
    "lesbarkeit": "nimm eine besser lesbare Schrift mit mehr Kontrast",
    "dauer":      "lass sie mindestens 5 Sekunden stehen",
    "position":   "setz sie ins obere Drittel mit Abstand zum Rand, damit die Instagram-Oberfläche sie nicht überdeckt",
}


def _texthook_anweisung(varianten: list[str], vorhanden: bool = False,
                        maengel: list[str] | None = None) -> str:
    """Texthook-Empfehlung — bei vorhandener Hook aus den GEMELDETEN Mängeln zusammengesetzt.

    Zwei Fassungen, weil „Blende eine Texthook ein" faktisch falsch ist, wenn eine existiert
    (Lauf c12db030: `text_hook_vorhanden=true` und trotzdem diese Aufforderung auf Platz 1).

    Bei vorhandener Hook nennt die Empfehlung nur, was das Modell als schwach gemeldet hat. Meldet
    es nichts, bleibt ein allgemeiner Satz — erfundene Detailkritik wäre schlimmer als eine
    unspezifische Bitte.
    """
    liste = " | ".join(f"„{v}“" for v in varianten) if varianten else ""
    if vorhanden:
        punkte = [_TEXTHOOK_BAUSTEINE[m] for m in (maengel or [])
                  if m in _TEXTHOOK_BAUSTEINE]
        if punkte:
            text = "Überarbeite deine Texthook: " + "; ".join(punkte) + "."
        else:
            text = ("Überarbeite deine Texthook — sie stoppt in dieser Form beim Scrollen noch "
                    "nicht zuverlässig.")
        if liste:
            text += f" Varianten zum Testen: {liste}"
        return text
    if not varianten:
        return TEXTHOOK_EMPFEHLUNG
    return (
        f"Blende in den ersten 3 Sekunden eine Texthook ein — kurzer Text im Bild, der neugierig macht. "
        f"Teste diese Varianten über die Testreel-Funktion von Instagram gegeneinander: {liste}"
    )


# Erkennt eine Empfehlung, die inhaltlich die Texthook behandelt. Bewusst breiter als „texthook":
# Im Lauf c12db030 lautete der doppelte Schritt „Ändere den Text auf dem Bildschirm so, dass …" —
# er enthielt keines der Hook-Wörter und rutschte durch den Filter, sodass ZWEI Empfehlungen zur
# selben Sache im Output standen.
_TEXTHOOK_THEMA = re.compile(
    r"text-?hook|text auf dem bildschirm|bildschirmtext|text im bild|eingeblendeten text|overlay",
    re.IGNORECASE,
)


def erzwinge_hook_empfehlungen(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Wird eine Hook unten kritisiert, MUSS oben eine Handlung dazu stehen.

    Der häufigste Leerlauf im Output: Score und Begründung benennen die Schwäche, aber unter den
    Handlungsempfehlungen taucht sie nicht auf (Feedback Run e7fdf99d für die Texthook, Run f2312dc9
    für die Sprechhook: „hier hätte noch ein tipp zur sprechhook ergänzt werden können. das wird ja
    unten in der bewertung auch kritisiert."). Bei der Texthook kommt dazu, dass der Score teils erst
    NACH dem Modell-Call im Code auf 0 gesetzt wird (bereinige_fremd_texthook) — das Modell konnte
    davon gar nichts wissen.

    Beide Schritte liegen auf Sekunde 0 und landen damit vorn; ein bereits vorhandener Schritt zum
    selben Thema wird nicht verdoppelt.
    """
    def fehlt(*stichworte: str) -> bool:
        texte = [(e.anweisung or "").lower() for e in parsed.empfehlungen]
        return not any(s in t for t in texte for s in stichworte)

    # 1..3, nicht <=3: 0 ist kein gültiger Sprech-Hook-Score, sondern der Modell-Default bei
    # Altläufen und Teil-Antworten. Daraus eine „schwache Hook" zu machen wäre erfunden.
    if parsed.hook.sprech_hook_score is not None \
            and 1 <= parsed.hook.sprech_hook_score <= HOOK_SCHWACH_SCORE \
            and fehlt("sprechhook", "sprech-hook", "erster satz", "ersten satz"):
        parsed.empfehlungen.insert(0, Empfehlung(
            zeitpunkt_sek=0.0, gruppe="sprechhook", betrifft="sprech_hook",
            anweisung=SPRECHHOOK_EMPFEHLUNG))

    # Texthook-Empfehlung NUR bei schwacher Text-Hook. Der Prompt bittet darum, `texthook_varianten`
    # bei Score 4/5 leer zu lassen — das Modell hält sich nicht daran und liefert sie trotzdem
    # (Feedback b98c88b1, Score 5: „die texthook empfehlung bei 1 ist unnötig, da ja eine schon sehr
    # gute texthook vorhanden ist"). Ohne diese Schranke steht in fast jedem Lauf eine
    # Texthook-Empfehlung auf Platz 1 — derselbe Boilerplate-Effekt wie vorher beim Redundanz-Check.
    th = parsed.hook.text_hook_score
    if th is None or th > HOOK_SCHWACH_SCORE:
        return parsed
    varianten = gueltige_texthook_varianten(parsed.texthook_varianten)
    vorhanden = bool(parsed.hook.text_hook_vorhanden)
    # Eigene Formulierungen des Modells zur Texthook ERSETZEN, nicht ergänzen — sonst stehen zwei
    # Empfehlungen zur selben Sache. Der Filter läuft jetzt immer, nicht nur bei Varianten: Im Lauf
    # c12db030 gab es keine Varianten-Ersetzung, aber trotzdem eine Modell-eigene Texthook-Empfehlung.
    parsed.empfehlungen = [e for e in parsed.empfehlungen
                           if not _TEXTHOOK_THEMA.search(e.anweisung or "")]
    # `geklemmt` gehört gleichberechtigt neben `th == 0`: Wurde der Score erst im Code gedeckelt
    # (Fremd-Texthook oder gemessene Redundanz), konnte das Modell davon nichts wissen und hat
    # entsprechend keine Varianten geliefert. Im Lauf 5502bb37 hat genau diese Lücke die wichtigste
    # Empfehlung verschluckt: falscher Score 4 → Varianten unterdrückt → keine Texthook-Empfehlung.
    geklemmt = bool(getattr(parsed.hook, "text_hook_score_geklemmt", False))
    if varianten or th == 0 or geklemmt or vorhanden:
        parsed.empfehlungen.insert(0, Empfehlung(
            zeitpunkt_sek=0.0, gruppe="texthook", betrifft="text_hook",
            anweisung=_texthook_anweisung(varianten, vorhanden, parsed.texthook_maengel)))
    return parsed


SCHWACH_SCORE = 3   # „3 oder schlechter" löst aus (Vorgabe Chris, Lauf 3c9a8d95)

# Welche Dimension bekommt bei schwachem Score einen erzwungenen Schritt, mit welchem Rückfalltext.
# Die Hooks stehen NICHT hier — sie haben eigene Logik (Varianten, vorhanden, geklemmt) in
# erzwinge_hook_empfehlungen und werden davor eingefügt, damit sie vorn stehen.
# `thema` erkennt einen Schritt, den das Modell zum selben Bereich schon geschrieben hat.
#
# KEINE Stichwort-Erkennung für „hat das Modell dazu schon was geschrieben?".
# Drei Versuche, drei Fehlalarme: `bild` traf die Texthook-Empfehlung („kurzer Text im Bild"),
# `sprech` traf „Sprechhook", und `hintergrund` traf eine Empfehlung zur Textfarbe — jedes Mal
# wurde dadurch der PFLICHT-Tipp unterdrückt, also genau das, was Chris eingefordert hat.
# Entschieden wird nur über das `gruppe`-Label. Nach der Logik, die im Repo schon für das Bündeln
# gilt: ein zusätzlicher, thematisch ähnlicher Schritt ist harmlos — ein fehlender Pflicht-Schritt
# ist der eigentliche Fehler.
_DIMENSIONEN = (
    ("sprechqualitaet", "sprechqualitaet",
     "Arbeite an der Sprache: sprich die Kernsätze langsamer und deutlicher, und lass nach den "
     "wichtigsten Aussagen eine kurze Pause stehen, damit sie ankommen."),
    ("visuelle_aesthetik", "aesthetik",
     "Bring das Bild in Ordnung: Achte auf den Abstand zwischen Kopf und oberem Bildrand "
     "(etwa 10–15 % Luft), einen ruhigen Hintergrund und eine scharfe, gut belichtete Aufnahme."),
    ("spannungsbogen", "spannungsbogen",
     "Halte die Spannung im Mittelteil: Setz dort einen neuen Haken — eine überraschende Zahl, "
     "einen Einwand oder eine offene Frage —, statt gleichförmig weiterzuerzählen."),
    ("struktur", "struktur",
     "Bau das Video klarer auf: Hook, dann ein Übergang, der neugierig hält, dann der Kern, dann "
     "ein Höhepunkt. Sag am Anfang, worauf es hinausläuft."),
    ("schnitt_pacing", "schnitt",
     "Zieh das Tempo an: Schneide Leerlauf zwischen den Sätzen weg und wechsle etwa alle drei bis "
     "fünf Sekunden etwas im Bild — Bildgröße, Perspektive oder eine Einblendung."),
)


_FUELLWOERTER = {
    "dein", "deine", "deinem", "deinen", "der", "die", "das", "den", "dem", "ein", "eine", "einen",
    "einem", "eines", "und", "oder", "aber", "auch", "sehr", "etwas", "wirkt", "wirkst", "ist",
    "sind", "wird", "sich", "nicht", "mehr", "noch", "dadurch", "wodurch", "weil", "dass", "als",
    "wie", "was", "sodass", "damit", "über", "unter", "nach", "beim", "durch", "einige", "manche",
}


def _inhaltswoerter(text: str) -> set:
    """Bedeutungstragende Wörter eines Problemtexts — Basis für den Doppler-Vergleich."""
    return {w for w in _normalisiert(text).split() if len(w) >= 4 and w not in _FUELLWOERTER}


def _schon_genannt(text: str, bereits: list[set]) -> bool:
    """Steht `text` fast wortgleich schon in einem früheren Problem?

    **Sicherheitsnetz, nicht die Hauptlösung.** Die Schwelle liegt hoch (0.8), weil eine
    Fehl-Zusammenlegung schlimmer ist als eine Doppelung: Sie unterschlägt einen echten Mangel.
    Das ist dieselbe Abwägung, die im Repo schon fürs Bündeln der Empfehlungen dokumentiert ist.

    Was hier NICHT geht, und warum: Im Lauf 26a1adbf stand der Blickkontakt zweimal, aber als
    PARAPHRASE — „wirkst abgelenkt, weil dein Blick abschweift" (sprechqualitaet) gegen „Dein Blick
    wandert häufig nach unten oder zur Seite" (visuelle_aesthetik). Gemeinsam ist genau ein
    Inhaltswort. Kein Wortmaß erkennt das, ohne bei echten Mängeln falsch zu greifen; drei Versuche
    mit thematischen Stichwörtern (`bild`, `sprech`, `hintergrund`) sind vorher genauso gescheitert.
    Die Doppelung wird deshalb im Prompt verhindert (Regel „jede Beobachtung nur EINMAL") — dort
    weiß das Modell, dass es dieselbe Sache zweimal schreibt. Hier bleibt nur der Fall des
    fast identischen Texts.
    """
    woerter = _inhaltswoerter(text)
    if not woerter:
        return False
    for frueher in bereits:
        if not frueher:
            continue
        if len(woerter & frueher) / min(len(woerter), len(frueher)) >= 0.8:
            return True
    return False


def deckle_score_auf_probleme(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Benennt das Modell Probleme, darf der Score nicht hoch bleiben (Vorgabe Chris).

    Lauf 041770c1: `visuelle_aesthetik` = 4 UND zwei benannte Probleme („Kopfraum recht groß",
    „Bildausschnitt unruhig"). Die Score-Anker im Skill haben das nicht verhindert — zweiter
    Versuch, zweites Mal nicht gegriffen. Damit feuert die ≤3-Regel nicht, obwohl Mängel
    dokumentiert sind: Der Score ist der falsche Auslöser, solange er den Problemen nicht folgt.

    Ein Problem → höchstens 4, zwei oder mehr → höchstens 3. Nur deckeln, nie anheben.

    Gilt ausschließlich für Dimensionen mit einer echten `probleme`-Liste. Die übrigen führen nur
    `kommentar`; ein Kommentar ist nicht zwingend ein Mangel, daraus einen Abzug zu machen wäre
    erfunden.
    """
    for attribut in ("sprechqualitaet", "visuelle_aesthetik"):
        block = getattr(parsed, attribut, None)
        if block is None or getattr(block, "score", None) is None:
            continue
        anzahl = len([p for p in (getattr(block, "probleme", None) or []) if p.strip()])
        if anzahl == 0:
            continue
        deckel = 3 if anzahl >= 2 else 4
        if block.score > deckel:
            block.score = deckel
    return parsed


def erzwinge_empfehlungen_bei_schwachen_scores(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Score 3 oder schlechter in einer Dimension MUSS eine Handlungsempfehlung erzeugen.

    Vorgabe Chris (Lauf 3c9a8d95): „überall wo der score eine 3 oder schlechter ist, sollte eine
    handlungsempfehlung oben beschrieben werden … vor allem weil dieser auch die watchtime
    beeinflusst."

    Das ist die Verallgemeinerung des häufigsten Leerlaufs im Output: Score und Begründung benennen
    eine Schwäche, unter den Handlungsempfehlungen taucht sie nicht auf. Belegt in beiden Läufen vom
    2026-07-30 — `spannungsbogen: 3` mit „plätschert im Mittelteil" ohne Schritt (3c9a8d95), zwei
    benannte Ästhetik-Mängel ohne Schritt (e35a568b). Vorher gab es dafür Einzelklemmen pro
    Dimension; die sind hier zusammengefasst, damit es EINE Regel bleibt.

    **Reihenfolge (Vorgabe Chris):** Hooks zuerst, dann nach den vorhandenen `SCORE_GEWICHTE`.
    Erreicht wird das ohne Sortier-Ausnahme: Diese Schritte werden ANGEHÄNGT, die Hook- und
    Anlauf-Schritte per `insert(0, …)` davorgesetzt. Alle liegen auf Sekunde 0, und
    `verteile_empfehlungen` sortiert stabil — die Einfügereihenfolge entscheidet.

    **1..3, nicht `<= 3`:** 0 ist kein Urteil, sondern der Pydantic-Default bei Altläufen und
    Teil-Antworten (dieselbe Falle wie beim Sprech-Hook).

    Was das Modell selbst zu einer Dimension geschrieben hat, wird übernommen — erfunden wird
    nichts. Fehlt eine Begründung, greift der allgemeine Rückfalltext.
    """
    schwach = []
    for attribut, gruppe, rueckfall in _DIMENSIONEN:
        block = getattr(parsed, attribut, None)
        if block is None:
            continue
        score = getattr(block, "score", None)
        if score is None or not (1 <= score <= SCHWACH_SCORE):
            continue
        if any(e.gruppe == gruppe for e in parsed.empfehlungen):
            continue          # exakt dieses Label gibt es schon
        # Das Modell hat zu dieser Dimension schon eine eigene Empfehlung geschrieben — exakt
        # erkennbar über `betrifft`, nicht über Textmuster. DAS war die Ursache der doppelten
        # Tipps: derselbe Mangel einmal als Modell-Empfehlung, einmal als erzwungener Sammeltipp.
        if any((e.betrifft or "").strip() == attribut for e in parsed.empfehlungen):
            continue
        schwach.append((SCORE_GEWICHTE.get(attribut, 0), score, attribut, gruppe, block, rueckfall))

    # Gewicht absteigend, bei gleichem Gewicht der schlechtere Score zuerst
    schwach.sort(key=lambda t: (-t[0], t[1]))

    # Problemtexte über ALLE Dimensionen hinweg, damit dieselbe Beobachtung nicht zweimal
    # als Tipp erscheint (Lauf 26a1adbf: Blickkontakt stand in sprechqualitaet UND aesthetik).
    bereits: list[set] = []

    for _, _, attribut, gruppe, block, rueckfall in schwach:
        eigene = []
        for p in (getattr(block, "probleme", None) or []):
            p = p.strip().rstrip(".")
            if not p or _schon_genannt(p, bereits):
                continue
            bereits.append(_inhaltswoerter(p))
            eigene.append(p)
        kommentar = (getattr(block, "kommentar", "") or "").strip().rstrip(".")
        if eigene:
            # Kein Vorspann: „Diese Punkte fallen sofort auf …" stand in zwei Tipps untereinander
            # und war reiner Füllsatz (Feedback 26a1adbf).
            anweisung = ". ".join(eigene) + "."
        elif kommentar:
            anweisung = f"{kommentar}. {rueckfall}"
        elif getattr(block, "probleme", None):
            continue          # alle Probleme waren Doppler → kein eigener Schritt
        else:
            anweisung = rueckfall
        parsed.empfehlungen.append(
            Empfehlung(zeitpunkt_sek=0.0, gruppe=gruppe, betrifft=attribut, anweisung=anweisung))
    return parsed


STUMM_HINWEIS = (
    "In diesem Video wird nicht gesprochen. Das ist eine Entscheidung fürs Format und kein Fehler — "
    "der Sprech-Hook wird deshalb nicht bewertet."
)


def neutralisiere_stumme_scores(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Kein gesprochenes Wort → Sprech-Hook und Sprechqualität auf „nicht bewertbar" (null).

    Vorher gab es dafür 1/5 bzw. 0/5 plus den Tipp „sprich trotz des stummen Formats einen Satz ein"
    (Run 08e908d7). Chris: „wenn es gar kein gesprochenes wort gibt, ist das ein zeichen dafür, dass
    auch kein gesprochenes wort sinn und zweck war." Eine 1 heißt „schlecht gemacht", null heißt
    „gab es nicht" — nur das Zweite stimmt hier.
    """
    if (result.transcript or "").strip() or getattr(result, "speech_stats", None):
        return parsed
    parsed.hook.sprech_hook_score = None
    parsed.hook.sprech_hook_grund = STUMM_HINWEIS
    parsed.sprechqualitaet.score = None
    parsed.sprechqualitaet.probleme = []
    return parsed


# Gewichte für den performance_score, Summe 100. Bewusst FUNNEL-UNABHÄNGIG (Vorgabe Chris,
# 2026-07-29): Am stärksten zählen die beiden Hooks sowie Ton- und Bildqualität — das entscheidet in
# den ersten Sekunden darüber, ob überhaupt jemand dranbleibt. Danach Spannungsbogen, Struktur,
# Schnitt & Pacing. Vorher bestimmte das Modell den Score frei: über 11 Läufe kam fünfmal exakt 68
# heraus, und Chris hielt ihn mehrfach für zu mild.
SCORE_GEWICHTE = {
    "sprech_hook": 18, "text_hook": 18, "sprechqualitaet": 17, "visuelle_aesthetik": 17,
    "spannungsbogen": 10, "struktur": 10, "schnitt_pacing": 10,
}


def berechne_performance_score(parsed: AnalystEvaluationV2) -> AnalystEvaluationV2:
    """Gesamtscore aus den Einzel-Scores statt aus dem Bauch des Modells.

    Jede Dimension wird auf 0..1 normalisiert (1–5 → (s-1)/4; der Text-Hook auf 0–5 → s/5, weil dort
    die 0 „fehlt komplett" bedeutet und nicht „Modell hat nichts gesagt"). Nicht bewertbare
    Dimensionen (None, z.B. Sprech-Hook in einem stummen Video) fallen raus und ihr Gewicht verteilt
    sich proportional auf den Rest — sonst würde ein bewusst stummes Format doppelt bestraft.
    """
    dimensionen = {
        "sprech_hook": (parsed.hook.sprech_hook_score, 1),
        "text_hook": (parsed.hook.text_hook_score, 0),
        "sprechqualitaet": (parsed.sprechqualitaet.score, 1),
        "visuelle_aesthetik": (parsed.visuelle_aesthetik.score, 1),
        "spannungsbogen": (parsed.spannungsbogen.score, 1),
        "struktur": (parsed.struktur.score, 1),
        "schnitt_pacing": (parsed.schnitt_pacing.score, 1),
    }
    summe = gewicht_gesamt = 0.0
    for name, (score, minimum) in dimensionen.items():
        if score is None or score < minimum:
            continue  # nicht bewertbar, oder 0 als Modell-Default statt echter Bewertung
        gewicht = SCORE_GEWICHTE[name]
        summe += gewicht * (score - minimum) / (5 - minimum)
        gewicht_gesamt += gewicht
    if gewicht_gesamt:
        parsed.performance_score = round(summe / gewicht_gesamt * 100)
    return parsed


def nachbearbeiten(parsed: AnalystEvaluationV2, result: AnalystResult) -> AnalystEvaluationV2:
    """Alle deterministischen Korrekturen in fester Reihenfolge — die eine Stelle, an der sie stehen.

    Reihenfolge ist nicht beliebig: erst die Urteils-Korrekturen (Format, Texthook, stumme Scores) —
    sie bestimmen, was danach erzwungen wird. Dann die Eingriffe in `empfehlungen`: filtern, bevor
    ergänzt wird. Zuletzt `verteile_empfehlungen` — es liest die fertige Liste und schneidet die Top 3
    ab. Die beiden Ergänzungen liegen beide auf Sekunde 0; ihre Einfüge-Reihenfolge entscheidet damit
    über die Reihenfolge im Output: Anlauf (schnellste Handlung) vor Texthook vor Sprechhook.
    """
    parsed = erzwinge_nutzer_format(parsed, result)
    parsed = bereinige_fremd_texthook(parsed, result)
    # Nach der Fremd-Texthook: Ist der Score dort schon auf 0, gibt es hier nichts mehr zu deckeln.
    parsed = bereinige_redundante_texthook(parsed, result)
    parsed = neutralisiere_stumme_scores(parsed, result)
    parsed = entferne_bestaetigungen(parsed)
    parsed = baue_pausen_schritt(parsed)
    parsed = baue_einblendungs_schritt(parsed)
    # VOR den Erzwingungen: Benannte Probleme deckeln den Score, damit die ≤3-Regel danach
    # überhaupt greift (Lauf 041770c1: zwei Probleme benannt, Score trotzdem 4).
    parsed = deckle_score_auf_probleme(parsed)
    parsed = erzwinge_hook_empfehlungen(parsed)
    parsed = erzwinge_anlauf_schnitt(parsed, result)
    # NACH den Hook- und Anlauf-Schritten: Alle liegen auf Sekunde 0, die Einfügereihenfolge
    # entscheidet damit über die Reihenfolge im Output. Hook- und Anlauf-Schritte werden vorne
    # eingefügt, diese hier angehängt — Hooks behalten Vorrang (Vorgabe Chris).
    parsed = erzwinge_empfehlungen_bei_schwachen_scores(parsed)
    parsed = berechne_performance_score(parsed)
    return verteile_empfehlungen(parsed)


def pausen_txt(stats) -> str:
    """Pausen MIT Position rendern. Ohne Position kann das Modell eine gemessene Dauer
    keiner Stelle zuordnen — es fusioniert dann Zahl und Ort zu einer erfundenen Behauptung.

    Die Schwelle wird mitgenannt, damit das Modell weiß, dass kürzere Lücken bewusst gar nicht
    gemeldet werden — sonst rät es an Stellen herum, die nicht in der Liste stehen.
    """
    pausen = getattr(stats, "pausen", None) or []
    if not pausen:
        return f"keine Pausen >{PAUSE_THRESHOLD_SEC}s gemessen (Messschwelle: {PAUSE_THRESHOLD_SEC}s)"
    return (
        " | ".join(f"{p.dauer_sec}s @ {p.start_sec}–{p.end_sec}s" for p in pausen)
        + f"  (Messschwelle: {PAUSE_THRESHOLD_SEC}s — kürzere Lücken werden bewusst nicht gemeldet)"
    )

def stats_txt(stats) -> str:
    """Sprachstatistik für den Prompt. EINE Fassung für V1 und V2 — die beiden Renderings standen
    vorher wortgleich in analyst_eval und analyst_gemini_eval und liefen bei jeder Änderung auseinander."""
    if not stats:
        return "Keine Sprache erkannt."
    return (
        f"{stats.wort_anzahl} Wörter, {stats.wpm} WPM, {stats.filler_count} Füllwörter, "
        f"{stats.pausen_count} Pausen >{PAUSE_THRESHOLD_SEC}s, "
        f"Sprechbeginn bei {getattr(stats, 'sprechbeginn_sec', 0.0)}s\n"
        f"PAUSEN (Position im Video): {pausen_txt(stats)}"
    )


OUTPUT_SCHEMA = """Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, exakt diese Felder, nichts davor/danach:
{
  "zielgruppe": "<genau 1 Satz: wer angesprochen wird>",
  "format": "<übernimm das vorgegebene Format aus der Aufgabe unverändert>",
  "protagonist_ab_sek": <float: ab welcher Sekunde der Protagonist seinen ersten INHALTLICHEN Satz beginnt — den, mit dem er anfängt zu ERKLÄREN/zu reden. NICHT seine erste Lautäußerung: mitreagierende Rufe („Ja!", „BÄM!"), Lacher oder das Mitsprechen zum Fremdvideo zählen NICHT. Spricht er von Beginn an inhaltlich: sein erstes Wort. Bei Reaction: erst wenn das Fremdvideo endet UND er zu reden anfängt (die Übergangspause davor gehört noch NICHT zu ihm).>,
  "performance_score": <int 0-100; reiner Fallback — das System berechnet ihn aus den Einzel-Scores und überschreibt diesen Wert>,
  "funnel": "<TOFU | MOFU | BOFU | Mischung>",
  "hook": {
    "sprech_hook_score": <int 1-5, oder null wenn im Video niemand spricht — siehe „Videos ohne gesprochenes Wort">,
    "sprech_hook_grund": "<1-2 Sätze>",
    "text_hook_vorhanden": <true|false>,
    "text_hook_score": <int 0-5; 0 wenn in der Eröffnung kein nicht-gesprochener Bildtext zu sehen ist (Untertitel zählen nie)>,
    "text_hook_wortlaut": "<PFLICHT wenn text_hook_vorhanden=true: der Text WÖRTLICH, den du als Text-Hook bewertest. Leer bei false. Kein Kommentar, nur der Wortlaut>",
    "text_hook_grund": "<1-2 Sätze; bei score 0 die Ansage + Tipp (3 Varianten über Instagram-Testreel testen)>"
  },
  "struktur": {
    "score": <int 1-5>,
    "elemente": {"hook": <bool>, "bridge": <bool>, "mid": <bool>, "peak": <bool>, "cta": <bool>},
    "kommentar": "<1-2 Sätze>"
  },
  "sprechqualitaet": {"score": <int 1-5, oder null wenn niemand spricht>, "probleme": ["<nur DEUTLICHE Mängel, je 1-2 Sätze, sonst []>"], "hinweise": ["<leichte Auffälligkeiten ohne Score-Wirkung, sonst []>"]},
  "schnitt_pacing": {"score": <int 1-5>, "kommentar": "<1-2 Sätze, format-bewusst>"},
  "spannungsbogen": {"score": <int 1-5>, "kommentar": "<1-2 Sätze>"},
  "visuelle_aesthetik": {"score": <int 1-5>, "probleme": ["<nur DEUTLICHE Mängel, je 1-2 Sätze, sonst []>"], "hinweise": ["<leichte Auffälligkeiten ohne Score-Wirkung, sonst []>"]},
  "staerken": ["<1-3 konkrete positive Aspekte, was schon gut funktioniert, in einfacher ermutigender Sprache>"],
  "top_tipps": ["<3-5 wichtigste Hebel, je 1-2 Sätze, nach Wirkung priorisiert>"],
  "pausen_urteile": [{"start_sec": <float: die start_sec EINER gemessenen Pause aus der Sprachstatistik, unverändert übernommen>, "urteil": "<raus | lassen | unklar — siehe „Sprechpausen — nach FUNKTION beurteilen">"}],
  "texthook_varianten": ["<bis zu 3 Vorschläge für eine bessere Text-Hook, je HÖCHSTENS 9 Wörter, je andere Mechanik; LEER LASSEN, wenn text_hook_score 4 oder 5 ist>"],
  "texthook_maengel": ["<NUR die Aspekte, die an der VORHANDENEN Text-Hook wirklich schwach sind, aus: wortlaut | laenge | redundanz | groesse | farbe | lesbarkeit | dauer | position. Ist die Hook in Ordnung: []>"],
  "einblendungen": [{"zeitpunkt_sek": <float: Stelle, an der eine visuelle Einblendung den Inhalt verstärken würde>, "verstaerkt": "<das Wort oder die Aussage, die dort verstärkt werden soll — z.B. Hof, Selbstbewusstsein>"}],
  "empfehlungen": [{"zeitpunkt_sek": <float: die Sekunde im Video, auf die sich die Handlung bezieht — Richtwert, ±1–2 s>, "anweisung": "<EINE konkrete Handlung, die etwas VERÄNDERT, in SUPER EINFACHER Sprache>", "gruppe": "<Label nur für die WÖRTLICH GLEICHE Handlung an mehreren Stellen, sonst leer>", "betrifft": "<welche Bewertungsdimension diese Handlung behebt, aus: sprech_hook | text_hook | sprechqualitaet | visuelle_aesthetik | spannungsbogen | struktur | schnitt_pacing. Gehört sie zu keiner: leer>"}]
}

Sprechpausen, Text-Hook-Varianten und inhaltsverstärkende Einblendungen gehören NICHT in
`empfehlungen` — dafür gibt es die drei Felder darüber. Das System baut daraus die fertigen Schritte.

Inhaltliche Regeln zu `empfehlungen` stehen im Abschnitt „Empfehlungen — die kanonische Regel" oben
und gelten unverändert; hier steht nur das Datenformat.
staerken: nenne echte positive Aspekte (nicht schönreden) — sie kommen im Ergebnis zuerst."""


def load_skill_body() -> str:
    """Liest den Skill-Body und entfernt das YAML-Frontmatter."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def load_reference() -> str:
    """Liest die separat gepflegte Referenz (kompakte Pipeline-Fassung: Prinzipien + Beispiel-Anker).
    Wird als zusätzliche Urteilsgrundlage an den System-Prompt angehängt; ändert NICHT den
    Output-Vertrag. Fehlt die Datei, wird sie still übersprungen."""
    if not REFERENCE_PATH.exists():
        return ""
    return REFERENCE_PATH.read_text(encoding="utf-8").strip()


def build_system_prompt() -> str:
    """Skill-Body (Logik) + optionale Editing-Referenz + strikter JSON-Vertrag (Pipeline-Modus)."""
    parts = [load_skill_body()]
    ref = load_reference()
    if ref:
        parts.append(
            "--- ANGEHÄNGTE REFERENZ: VIDEO-ANALYSE (EDITING + SKRIPT + TECHNIK/AUFTRETEN) ---\n"
            "Zusätzliche Urteilsgrundlage für hook, struktur, spannungsbogen, schnitt_pacing, sprechqualitaet, visuelle_aesthetik und top_tipps. "
            "KEIN Ausgabe-Template — der Output bleibt strikt knapp + JSON wie unten definiert.\n\n"
            + ref
        )
    parts.append(OUTPUT_SCHEMA)
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict:
    """Holt das erste vollständige JSON-Objekt aus der Antwort.

    Nutzt raw_decode ab der ersten '{' → dekodiert genau EIN Objekt und ignoriert alles
    danach. Das ist robust gegen „Extra data" (LLM hängt nach dem JSON noch Text/ein zweites
    Objekt/Markdown-Fences an — kommt bei Gemini trotz JSON-Modus und bei Fallback-Modellen vor).
    """
    start = text.find("{")
    if start == -1:
        raise ValueError(f"Kein JSON in Antwort: {text[:200]}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        # Fallback: gieriger Match (falls vor der ersten '{' Störzeichen den Offset verschieben).
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def build_user_message(result: AnalystResult) -> str:
    """Baut die User-Message. Hook-Kandidaten werden explizit markiert."""
    def _scene_line(s) -> str:
        tag = " [ERÖFFNUNG]" if s.index == 0 else ""
        kern = s.handlung or s.beschreibung or "(keine Beschreibung)"
        extra = ""
        if s.personen:
            extra += f" | Person/Blick: {s.personen}"
        for t in (s.texte or []):
            d = f" ({t.darstellung})" if t.darstellung else ""
            extra += f" | Text: „{t.wortlaut}\"{d}"
        if s.gesprochener_text:
            extra += f" | Gesprochen (Whisper, verlässlicher Wortlaut): {s.gesprochener_text}"
        if s.kamera:
            extra += f" | Kamera: {s.kamera}"
        if s.bild_fakten:
            bf = s.bild_fakten
            extra += f" | Bild: {bf.komposition}, Licht {bf.licht}, Hintergrund {bf.hintergrund}"
        if s.effekte:
            extra += f" | Effekte: {s.effekte}"
        return f"Segment {s.index + 1} ({s.start:.1f}–{s.end:.1f}s){tag}: {kern}{extra}"

    scenes_txt = "\n".join(_scene_line(s) for s in result.scenes) or "(keine Szenen erkannt)"

    opening = result.scenes[0] if result.scenes else None
    text_hook = (opening.text_overlays if opening and opening.text_overlays else "") or "(keins erkannt)"
    first_words = (result.transcript or "").strip()[:160] or "(keine Sprache)"

    # Blickkontakt: der dedizierte Whole-Video-Pass (gaze_overview) ist die VERLÄSSLICHE Quelle —
    # Standbilder verraten den Blick nicht zuverlässig (Gemini produziert im Frame-Batch Boilerplate
    # wie "blickt direkt in die Linse"). Per-Szene-personen nur noch als Fallback, wenn der Pass leer ist.
    if result.gaze_overview:
        blick_txt = result.gaze_overview
    else:
        _away_keys = ("nach unten", "runter", "zur seite", "seitlich", "abgelesen", "blickt weg", "liest", "weg von")
        _gaze_away = sum(1 for s in result.scenes if any(k in (s.personen or "").lower() for k in _away_keys))
        blick_txt = (
            f"In {_gaze_away} von {len(result.scenes)} Szenen ist der Blick NICHT in die Kamera gerichtet "
            f"(nach unten/zur Seite) — Indiz für Ablesen/Skript; Authentizität & Sicherheit im Auftreten bewerten."
            if _gaze_away else "Blick überwiegend in die Kamera (Hinweis: nur grobe Szenen-Schätzung, kein dedizierter Blick-Pass)."
        )

    # Dieselbe Entscheidung wie im V2-Pfad: genullte Bildwerte sind keine Messung (siehe
    # bildwerte_belastbar). V1 zieht zwar Frames, aber ein abgebrochener Frame-Pass hinterlässt
    # dieselben Nullen — die Unterscheidung gehört hier genauso hin.
    messwerte = metrics_txt(result.quality_metrics)

    return (
        f"Video: {result.filename}, Länge {result.duration_sec:.1f}s, {result.scene_count} Szenen.\n\n"
        f"SEGMENTE (visuell):\n{scenes_txt}\n\n"
        f"AUDIO (ganzes Video): {result.audio_overview or '(keine Audio-Beschreibung)'}\n\n"
        f"SPRECH-HOOK-KANDIDAT (erste Worte): {first_words}\n"
        f"TEXT-HOOK-KANDIDAT (Overlay der Eröffnung): {text_hook}\n\n"
        f"BLICKKONTAKT (ganzes Video, dedizierter Gemini-Pass): {blick_txt}\n\n"
        f"TRANSKRIPT:\n{result.transcript or '(leer)'}\n\n"
        f"SPRACHSTATISTIK: {stats_txt(result.speech_stats)}\n\n"
        f"MESSWERTE (intern, NICHT im Output nennen):\n{messwerte}"
    )


def evaluate(result: AnalystResult, run_dir=None) -> AnalystEvaluationV2:
    """Schlanke V2-Bewertung. Bekommt nur Text — das Video bleibt lokal.

    run_dir (optional): Ordner des Laufs; wenn gesetzt, wird der komplette Call
    (Input/Prompt/Output) nach prompt_log.md geschrieben.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    system = build_system_prompt()
    user = build_user_message(result)
    # Hinweis: KEIN temperature-Parameter (bei Sonnet 4.6 deprecated → 400 Bad Request)
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=2200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text
    parsed = nachbearbeiten(AnalystEvaluationV2(**_extract_json(raw)), result)
    analyst_prompt_log.log_call(
        run_dir, call="eval_v1", recipient="Claude", model=settings.claude_model,
        system_prompt=system, user_message=user, output_raw=raw, output_parsed=parsed,
        inputs={
            "engine": "v1", "filename": result.filename,
            "duration_sec": result.duration_sec, "scene_count": result.scene_count,
        },
    )
    return parsed
