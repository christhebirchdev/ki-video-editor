#!/usr/bin/env python3
"""Prüft an EINEM prompt_log.md, ob der Server wirklich den neuen Code fährt.

    python tools/preflight_check.py analyst_runs/<id>/prompt_log.md

Gestern lief der Server auf alten Modulen (`.py` erst nach Neustart aktiv, `.md` sofort) — der Log
war ein Mischzustand und die 3 Läufe waren wertlos. Dieser Check fängt das ab, BEVOR du alle drei
Läufe machst. Ein einziges ✗ heißt: Server nicht sauber neu gestartet → nochmal.
"""
import sys
from pathlib import Path

# (Beschreibung, muss_vorhanden_sein, phrase)
CHECKS = [
    ("Format-Auswahl kam an", True, "gewaehltes_format"),
    ("Reaction-Block im Prompt", True, "FREMDVIDEO"),
    ("Protagonist-Feld im Schema", True, "protagonist_ab_sek"),
    ("Pausen mit Position", True, "PAUSEN (Position im Video)"),
    ("Neue Empfehlungs-Regel", True, "zeitpunkt_sek"),
    ("Transkript-Hash geloggt", True, "transkript_hash"),
    ("ALTE Sortierregel entfernt", False, "3 FRÜHESTEN"),
    ("ALTES Pausenformat entfernt", False, "Pausen >0.5s (längste"),
]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    print(f"\nPreflight: {sys.argv[1]}\n" + "=" * 60)
    alles_ok = True
    for beschreibung, muss_da, phrase in CHECKS:
        vorhanden = phrase in text
        ok = vorhanden if muss_da else not vorhanden
        alles_ok &= ok
        print(f"  {'✓' if ok else '✗ FEHLER'}  {beschreibung}")
    print("=" * 60)
    if alles_ok:
        print("  ✓ Neuer Code läuft. Mach die 2 weiteren Läufe → vergleiche_laeufe.py\n")
    else:
        print("  ✗ Server fährt NICHT sauber neuen Code. Nochmal: pkill uvicorn, dann --reload starten.")
        print("    (Nicht während eines laufenden Jobs neu starten — Warteschlange ist prozess-lokal.)\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
