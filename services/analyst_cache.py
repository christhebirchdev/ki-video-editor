# services/analyst_cache.py
"""
Analyse-Cache: dieselbe Videodatei mit denselben Eingaben liefert dasselbe, bereits
gespeicherte Ergebnis.

Warum: Die Bewertung entsteht in einem LLM-Lauf und ist nicht deterministisch. Lädt ein Nutzer
sein Video ein zweites Mal hoch, bekäme er eine leicht abweichende Bewertung — und wüsste nicht,
welche Handlungsempfehlungen jetzt gelten. Der Cache macht die zweite Antwort byte-gleich zur
ersten und spart nebenbei einen kompletten Pipeline-Lauf.

Bewusst reine Funktionen ohne FastAPI-Bezug: die Regeln sind so ohne HTTP-Schicht testbar.
"""
import hashlib
import json
import shutil
from pathlib import Path

# 1 MiB: groß genug, dass der Hash die Upload-Geschwindigkeit nicht bestimmt, klein genug,
# dass auch ein 4-GB-Upload nie im RAM landet.
CHUNK = 1024 * 1024


def schreibe_und_hashe(quelle, ziel: Path) -> str:
    """Schreibt den Upload-Stream nach `ziel` und liefert den SHA-256 des Inhalts mit.

    In EINEM Durchgang statt „erst speichern, dann hashen": Die Datei ein zweites Mal zu lesen
    wäre auf dem VPS reine Wartezeit, während `hashlib` neben dem Schreiben kaum ins Gewicht
    fällt. Ersetzt `shutil.copyfileobj` — streamt wie dieses chunkweise.
    """
    h = hashlib.sha256()
    with ziel.open("wb") as f:
        while chunk := quelle.read(CHUNK):
            h.update(chunk)
            f.write(chunk)
    return h.hexdigest()


def cache_key(meta: dict):
    """Alles, was das Ergebnis bestimmt. `None` = dieser Lauf ist nicht cachefähig.

    `prompt_version` gehört dazu, weil dieselbe Datei unter einem geänderten Bewertungs-Prompt
    ein legitim anderes Ergebnis hat. Deshalb die Betriebsregel: **Prompt geändert →
    `PROMPT_VERSION` in services/analyst_eval.py hochzählen**, sonst liefert der Cache alte
    Ergebnisse zu neuer Logik.

    `ziel` gehört dazu, weil die Score-Gewichte bei v3 vom Ziel abhängen: Dieselbe Datei mit
    einem anderen Ziel ist ein legitim anderes Ergebnis. Ohne das Feld liefert der Cache das
    Ergebnis des zuerst gewählten Ziels — ein sichtbarer Fehler, kein stiller.
    Altläufe und v2 haben `ziel` nicht bzw. leer; `.get(..., "")` hält ihren Key stabil.

    Altläufe von vor diesem Feature haben weder Hash noch Version — sie liefern `None` und
    matchen damit nie.
    """
    if not meta.get("sha256") or not meta.get("prompt_version"):
        return None
    return (
        meta["sha256"],
        meta.get("format", ""),
        meta.get("ziel", ""),
        meta.get("planned_text_hook", ""),
        # Der Hash der Marken-/Zielgruppen-Datei: Derselbe Clip mit anderem Markenkontext ist ein
        # legitim anderes Ergebnis (andere Texthook-Varianten, anderer Zielgruppen-Abgleich).
        # Laeufe ohne Datei haben "" und behalten damit ihren bisherigen Key — sonst faellt der
        # gesamte gespeicherte Cache auf einen Schlag aus.
        meta.get("marke_hash", ""),
        meta.get("engine", ""),
        meta["prompt_version"],
    )


def finde_treffer(runs_dir: Path, key, ausser: str):
    """Ältester abgeschlossener Lauf mit demselben Key — oder `None`.

    Der ÄLTESTE gewinnt, damit auch der dritte Upload noch dasselbe Ergebnis zeigt wie der erste,
    statt eine Kette von Kopien aufzubauen.

    Nicht als Quelle in Frage kommen abgebrochene (`error`) und noch laufende Analysen: ein
    halbes Ergebnis ist schlechter als eine ehrliche zweite Analyse.

    ponytail: linearer Scan über die Lauf-Verzeichnisse statt Index oder Datenbank. Läuft einmal
    pro Analysestart (nicht pro Poll) und liest je Lauf zwei kleine JSON-Dateien.
    """
    if not key:
        return None
    treffer = []
    for d in runs_dir.iterdir():
        if not d.is_dir() or d.name == ausser or not (d / "analysis.json").exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text())
            phase = json.loads((d / "status.json").read_text()).get("phase")
        except (OSError, json.JSONDecodeError):
            continue   # halbfertiges Verzeichnis überspringen, nicht den Start abbrechen
        if phase != "done" or cache_key(meta) != key:
            continue
        treffer.append((meta.get("created_at", ""), d.name, d))
    treffer.sort()
    return treffer[0][2] if treffer else None


def uebernehmen(quelle: Path, ziel: Path) -> dict:
    """Kopiert das gespeicherte Ergebnis in den neuen Lauf und liefert die Herkunftsangaben.

    Kopie statt Verweis: Der neue Lauf bleibt für sich — Chat und Feedback landen nicht im
    fremden Verzeichnis, und wird der Quelllauf später gelöscht, bleibt dieses Ergebnis lesbar.
    """
    shutil.copyfile(quelle / "analysis.json", ziel / "analysis.json")
    try:
        quell_meta = json.loads((quelle / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        quell_meta = {}
    return {"cached_from": quelle.name, "cached_at": quell_meta.get("created_at", "")}
