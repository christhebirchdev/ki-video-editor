# services/analyst_profil.py
"""Was der Analyst sich je Nutzer merkt — heute: die Zielgruppen- und Strategiedatei.

Anlass (Chris, 2026-11-03): Kunden laden bei jedem Video dieselben zwei Dateien aus dem Content-Hub
hoch. Gemerkt wird der EXTRAHIERTE Text (nicht die PDF): Er ist das, was in die Bewertung eingeht,
und er ist klein.

Ablage: `analyst_runs/_profile/<nutzer>.json` — bewusst INNERHALB des Lauf-Verzeichnisses, weil auf
dem Server nur dieses Volume einen Neustart ueberlebt. Alle Schleifen ueber die Laeufe ueberspringen
Verzeichnisse ohne meta.json, `_profile` stoert sie also nicht.
"""
import json
from datetime import datetime
from pathlib import Path

PROFIL_ORDNER = "_profile"


def _pfad(basis: Path, nutzer: str) -> Path:
    return basis / PROFIL_ORDNER / f"{nutzer}.json"


def lade_marke(basis: Path, nutzer: str) -> dict | None:
    if not nutzer:
        return None
    try:
        return json.loads(_pfad(basis, nutzer).read_text()).get("marke")
    except (OSError, json.JSONDecodeError):
        return None


def speichere_marke(basis: Path, nutzer: str, namen: list[str], text: str, gekuerzt: bool) -> None:
    if not nutzer:
        return
    pfad = _pfad(basis, nutzer)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps({"marke": {
        "dateien": namen, "text": text, "gekuerzt": gekuerzt,
        "gespeichert_am": datetime.now().isoformat(timespec="seconds"),
    }}, ensure_ascii=False))


def loesche_profil(basis: Path, nutzer: str) -> None:
    if nutzer:
        _pfad(basis, nutzer).unlink(missing_ok=True)


def uebertrage(basis: Path, von: str, nach: str) -> None:
    """Uebergang Browser-ID → Konto: Profil mitnehmen, ein vorhandenes Konto-Profil gewinnt."""
    if not von or not nach or von == nach:
        return
    quelle, ziel = _pfad(basis, von), _pfad(basis, nach)
    if quelle.exists() and not ziel.exists():
        quelle.rename(ziel)
