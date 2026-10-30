# services/analyst_nutzer.py
"""Wem gehoert ein Analyse-Lauf? — die EINE Nahtstelle zum Nutzerkonto (Spec V4, Abschnitt 3/6).

Heute schickt das Frontend eine im Browser erzeugte ID. Nach der Integration ins Agentursystem
setzt dessen Proxy den Header `X-Analyst-Nutzer` serverseitig mit der Konto-ID (und ueberschreibt
dabei die Browser-ID). Dieser Code bleibt dabei gleich — nur `ANALYST_NUTZER_PFLICHT=1` kommt dazu.

`X-Analyst-Geraet` traegt immer die Browser-ID. Weicht sie im Pflichtmodus von der Konto-ID ab,
werden die Laeufe der Browser-ID einmalig auf das Konto umgeschrieben (`uebertrage`): So nimmt
jeder Kunde seine Historie aus der Uebergangszeit mit, ohne dass jemand dafuer Code schreibt.
"""
import json
import re
from pathlib import Path

NUTZER_HEADER = "X-Analyst-Nutzer"
GERAET_HEADER = "X-Analyst-Geraet"
# Nur Zeichen, die in keinem Pfad, Header oder Log etwas anrichten koennen. Eine UUID passt,
# eine typische Konto-ID (Zahl, Slug, UUID) auch.
_GUELTIG = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _sauber(wert: str | None) -> str:
    wert = (wert or "").strip()
    return wert if _GUELTIG.match(wert) else ""


def aktueller_nutzer(request) -> str:
    """Die Nutzer-ID dieser Anfrage oder "" (kein oder ungueltiger Header)."""
    return _sauber(request.headers.get(NUTZER_HEADER))


def geraet(request) -> str:
    return _sauber(request.headers.get(GERAET_HEADER))


def darf_zugreifen(meta: dict, nutzer: str, pflicht: bool) -> bool:
    """Ohne Pflicht: nur gesperrt, wenn BEIDE Seiten einen Nutzer haben und sie sich unterscheiden
    (Altlaeufe und das V3-Frontend ohne Header bleiben bedienbar).
    Mit Pflicht: nur der eingetragene Besitzer — Laeufe ohne Besitzer gehoeren niemandem."""
    besitzer = meta.get("nutzer", "")
    if pflicht:
        return bool(nutzer) and besitzer == nutzer
    return not (besitzer and nutzer and besitzer != nutzer)


def _metas(basis: Path):
    for d in basis.iterdir():
        if not d.is_dir():
            continue
        try:
            yield d, json.loads((d / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue


def eigene_laeufe(basis: Path, nutzer: str) -> list[tuple[Path, dict]]:
    """Alle Laeufe dieses Nutzers, neueste zuerst. Ohne Nutzer: keine — sonst saehe ein Aufruf
    ohne Header die Laeufe aller anderen ohne Besitzer."""
    if not nutzer:
        return []
    laeufe = [(d, m) for d, m in _metas(basis) if m.get("nutzer") == nutzer]
    laeufe.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    return laeufe


def uebertrage(basis: Path, von: str, nach: str) -> int:
    """Schreibt die Laeufe von `von` auf `nach` um. Rueckgabe: Anzahl."""
    if not von or not nach or von == nach:
        return 0
    n = 0
    for d, m in _metas(basis):
        if m.get("nutzer") == von:
            m["nutzer"] = nach
            (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False))
            n += 1
    return n
