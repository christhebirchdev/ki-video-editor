#!/usr/bin/env python3
"""Zaehlt, wie oft ein erzwungener Standardsatz einen der drei Top-Plaetze belegt.

Wozu: Die `erzwinge_*`-Funktionen springen ein, wenn ein Score schwach ist, das Modell dazu aber
nichts geschrieben hat. Der Standardsatz ist immer schwaecher als eine individuelle Formulierung —
er soll bleiben, aber moeglichst selten greifen (Vorgabe Chris, 2026-09-12). Ohne Messung merkt
niemand, ob sich das bessert: Im fertigen Ergebnis sieht man einem Schritt nicht mehr an, woher
er kommt. Seit `Empfehlung.erzwungen`/`ActionStep.erzwungen` steht es in jeder analysis.json.

Gelesen wird das ROHE JSON, nicht ueber die Pydantic-Modelle: Die fuellen das fehlende Feld mit
dem Default `False` auf, und dann waere ein Altlauf nicht mehr von einem Lauf ohne Notnagel zu
unterscheiden. Genau diese Unterscheidung ist hier der Punkt.

    python3 tools/notnagel_quote.py
"""
import json
from collections import Counter
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "analyst_runs"


def _dimension(schritt: dict, empfehlungen: list) -> str:
    """Welcher Standardsatz war das? `ActionStep` fuehrt die Dimension nicht mit — der Schritt
    uebernimmt aber den Text der ersten Empfehlung seiner Gruppe, also ist er darueber eindeutig
    wiederzufinden."""
    for e in empfehlungen:
        if e.get("anweisung") == schritt.get("anweisung"):
            return (e.get("betrifft") or "").strip() or (e.get("gruppe") or "").strip() or "ohne Label"
    return "nicht zuzuordnen"


def main() -> None:
    gesamt = mit_schritten = mit_ziel = schritte = erzwungen = altlaeufe = 0
    nach_dimension: Counter = Counter()

    for pfad in sorted(RUNS.glob("*/analysis.json")):
        gesamt += 1
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        ev = daten.get("evaluation") or {}
        steps = ev.get("action_steps") or []
        if not steps:
            continue          # Lauf ohne angezeigte Schritte hat nichts beizutragen
        mit_schritten += 1
        # Gezaehlt werden V2- UND V3-Laeufe: Die `erzwinge_*`-Funktionen laufen in beiden Wegen.
        # Das Ziel wird nur mitgefuehrt, weil die V3-Laeufe der Massstab fuer die Zukunft sind.
        if (daten.get("gewaehltes_ziel") or "").strip():
            mit_ziel += 1
        empfehlungen = ev.get("empfehlungen") or []
        if not any("erzwungen" in s for s in steps):
            altlaeufe += 1    # vor Einfuehrung des Felds — zaehlt komplett als nicht erzwungen
        schritte += len(steps)
        for s in steps:
            if s.get("erzwungen"):
                erzwungen += 1
                nach_dimension[_dimension(s, empfehlungen)] += 1

    quote = (erzwungen / schritte * 100) if schritte else 0.0
    print("Notnagel-Quote — wie oft belegt ein erzwungener Standardsatz einen Top-Platz?\n")
    print(f"  Laeufe in analyst_runs/:        {gesamt:5d}")
    print(f"  davon mit Top-Schritten:        {mit_schritten:5d}")
    print(f"  davon mit Ziel (V3):            {mit_ziel:5d}")
    print(f"  Top-Schritte insgesamt:         {schritte:5d}")
    print(f"  davon erzwungen:                {erzwungen:5d}")
    print(f"  Quote:                          {quote:5.1f} %")

    print("\n  Verteilung nach Dimension:")
    for name, anzahl in nach_dimension.most_common():
        print(f"    {name:<24} {anzahl:3d}")
    if not nach_dimension:
        print("    (kein erzwungener Schritt gezaehlt)")

    if altlaeufe:
        print(f"\n  ACHTUNG: {altlaeufe} der {mit_schritten} ausgewerteten Laeufe stammen aus der Zeit vor "
              f"dem Feld `erzwungen`.\n  Dort zaehlt jeder Schritt als nicht erzwungen — die Quote "
              f"ist damit eine Untergrenze, kein Istwert.")


if __name__ == "__main__":
    main()
