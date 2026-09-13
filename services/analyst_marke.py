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

# 2.000 Woerter entsprechen grob 3.000 Tokens. Darueber verdraengt ein Marken-Manual im Prompt die
# Videodaten — und genau die sind das, was diese Analyse von einer Textanalyse unterscheidet.
MAX_WOERTER = 2000

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


def extrahiere(daten: bytes, dateiname: str) -> tuple[str, bool]:
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
            f"Dateityp {endung or '(ohne Endung)'} wird nicht unterstützt. "
            f"Erlaubt: {', '.join(ENDUNGEN)} — am besten .md oder .txt."
        )
    if endung == ".pdf":
        text = _aus_pdf(daten)
    elif endung == ".docx":
        text = _aus_docx(daten)
    else:
        text = daten.decode("utf-8", errors="replace")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError(
            "In der Datei steht kein lesbarer Text. Bei einem gescannten PDF fehlt die Textebene — "
            "kopier den Inhalt in eine .md- oder .txt-Datei."
        )
    return _kuerze(text)


def hash_von(text: str) -> str:
    """Teil des Cache-Keys. Gehasht wird der EXTRAHIERTE Text, nicht die Datei: Dieselbe
    Zielgruppenbeschreibung einmal als .docx und einmal als .md ist derselbe Kontext und soll
    denselben Lauf treffen."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
