# services/analyst_marke.py
"""Optionale Marken-/Zielgruppen-Datei: Text herausholen, kuerzen, hashen.

Zweck der Datei (Spec 1.2, in dieser Reihenfolge): Texthook-Varianten in der Sprache des Nutzers,
Zielgruppen-Abgleich, Sprachlevel-Pruefung, CTA-Passung bei BOFU, Positionierungs-Konflikte.

Was hier NICHT passiert: das Bewerten. Dieses Modul liefert nur Text. Der GELTUNGSBEREICH — worauf
der Kontext wirken darf und worauf nicht — steht im Prompt (services/analyst_eval.build_system_prompt),
weil nur dort das Modell ihn liest.
"""
import hashlib
import io
import re

# Obergrenze fuer den GESAMTEN Kontext, ueber alle hochgeladenen Dateien hinweg. Darueber
# verdraengt er im Prompt die Videodaten — und genau die sind das, was diese Analyse von einer
# Textanalyse unterscheidet.
#
# 2026-09-16 von 2.000 auf 3.000 angehoben: Seit der Nutzer Zielgruppen- UND Strategiedatei aus dem
# Content-Hub hochlaedt, sind es zwei Dokumente statt einem. Bei 2.000 Woertern waere die Kuerzung
# der Normalfall gewesen, und abgeschnitten wird am Ende — also ausgerechnet die Strategie.
# Preis: rund 2.900 Zeichen mehr pro Call, zweimal je Analyse.
MAX_WOERTER = 3000

ENDUNGEN = (".md", ".txt", ".markdown", ".pdf", ".docx")


def _aus_pdf(daten: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(daten))
    # Nur so viele Seiten, wie das Wortlimit ueberhaupt fassen kann: Ein 40-seitiges Manual komplett
    # zu parsen kostet Zeit fuer Text, den `_kuerze` gleich danach wegwirft.
    text = []
    for seite in reader.pages:
        text.append(seite.extract_text() or "")
        if sum(len(t.split()) for t in text) > MAX_WOERTER:
            break
    return "\n".join(text)


def _aus_docx(daten: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(daten))
    absaetze = [p.text for p in d.paragraphs]
    # Tabellen mitnehmen: Zielgruppen-Steckbriefe und Tonalitaets-Matrizen stehen in Brand-Dokumenten
    # oft ausschliesslich in Tabellen, und die liegen NICHT in document.paragraphs.
    for tabelle in d.tables:
        for zeile in tabelle.rows:
            zellen = [z.text.strip() for z in zeile.cells if z.text.strip()]
            if zellen:
                absaetze.append(" | ".join(zellen))
    return "\n".join(absaetze)


def _kuerze(text: str) -> tuple[str, bool]:
    woerter = text.split()
    if len(woerter) <= MAX_WOERTER:
        return text, False
    return " ".join(woerter[:MAX_WOERTER]), True


def extrahiere(daten: bytes, dateiname: str, kuerzen: bool = True) -> tuple[str, bool]:
    """Text + „wurde gekuerzt" aus einer hochgeladenen Datei.

    Wirft ValueError bei unbekannter Endung und bei Dateien ohne lesbaren Text. Beides ist ein
    Bedienfehler, den der Nutzer sofort sehen soll: Ein gescanntes PDF ohne Textebene wuerde sonst
    still als „Marke hinterlegt" durchgehen und die bedingt bewertbaren Dimensionen
    (protagonist_auftreten, zielgruppen_relevanz) freischalten, ohne dass es eine Grundlage gibt.
    """
    name = (dateiname or "").lower()
    endung = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if endung not in ENDUNGEN:
        raise ValueError(
            f"{dateiname or 'Die Datei'}: Dateityp {endung or '(ohne Endung)'} wird nicht "
            f"unterstützt. Erlaubt: {', '.join(ENDUNGEN)}."
        )
    # Jeder Parser kann an einer beschaedigten Datei mit seiner EIGENEN Ausnahme aussteigen
    # (pypdf wirft PdfStreamError, python-docx PackageNotFoundError). Ungefangen wird daraus ein
    # 500er, und der Nutzer sieht nur "Interner Fehler" statt zu erfahren, dass sein Export kaputt
    # ist. Seit die Dateien als PDF aus dem Content-Hub kommen, ist das kein Randfall mehr.
    try:
        if endung == ".pdf":
            text = _aus_pdf(daten)
        elif endung == ".docx":
            text = _aus_docx(daten)
        else:
            text = daten.decode("utf-8", errors="replace")
    except ValueError:
        raise
    except Exception as fehler:
        raise ValueError(
            f"{dateiname or 'Die Datei'} lässt sich nicht lesen ({type(fehler).__name__}). "
            "Exportier sie neu aus dem Content-Hub oder kopier den Inhalt in eine .txt-Datei."
        ) from fehler
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError(
            f"In {dateiname or 'der Datei'} steht kein lesbarer Text. Bei einem gescannten PDF "
            "fehlt die Textebene — exportier sie neu oder kopier den Inhalt in eine .txt-Datei."
        )
    return _kuerze(text) if kuerzen else (text, False)


def extrahiere_mehrere(dateien) -> tuple[str, bool, list[str]]:
    """Mehrere Uploads zu EINEM Kontext — Text, „wurde gekuerzt" und die Dateinamen.

    `dateien` ist eine Folge von (bytes, dateiname). Erwartet werden die Zielgruppen- und die
    Strategiedatei aus dem Content-Hub (Vorgabe Chris, 2026-09-16); mehr oder weniger geht auch.

    Jede Datei bekommt eine UEBERSCHRIFT mit ihrem Namen. Das ist kein Schmuck: Ohne sie steht im
    Prompt ein Block, in dem Zielgruppen-Beschreibung und Strategie ineinanderlaufen, und das
    Modell kann nicht mehr sagen, worauf es sich beruft.

    Gekuerzt wird am Ende der SUMME, nicht je Datei — sonst faellt aus beiden Dokumenten die
    Haelfte weg statt aus dem laengeren.
    """
    teile, namen = [], []
    for daten, name in dateien:
        text, _ = extrahiere(daten, name, kuerzen=False)
        teile.append(f"--- {name} ---\n{text}")
        namen.append(name)
    gesamt, gekuerzt = _kuerze("\n\n".join(teile))
    return gesamt, gekuerzt, namen


def hash_von(text: str) -> str:
    """Teil des Cache-Keys. Gehasht wird der EXTRAHIERTE Text, nicht die Datei: Dieselbe
    Zielgruppenbeschreibung einmal als .docx und einmal als .md ist derselbe Kontext und soll
    denselben Lauf treffen."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
