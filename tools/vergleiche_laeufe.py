#!/usr/bin/env python3
"""Vergleicht mehrere prompt_log.md desselben Videos: Sind Inputs identisch? Wie stark streuen die Outputs?

Vergleichen ist mechanisch — deshalb Code statt Augenmaß (und wiederholbar bei jeder Prompt-Änderung).

    python tools/vergleiche_laeufe.py lauf1.md lauf2.md lauf3.md

Drei Schichten mit unterschiedlichem Anspruch:
  1. Input        → MUSS identisch sein. Wackelt er, misst man Whisper statt das Modell.
  2. Zahlen/Entscheidungen → sollten stabil sein. Streuung = Befund.
  3. Wortlaut     → variiert immer. Wird bewusst NICHT verglichen.
"""
import json
import re
import sys
from pathlib import Path

INPUT_BLOECKE = {
    "Transkript": r"TRANSKRIPT \(Whisper, verlässlicher Wortlaut\):\n(.*?)\n\n",
    "Sprachstatistik": r"SPRACHSTATISTIK: (.*?)\n",
    "Pausen": r"PAUSEN \(Position im Video\): (.*?)\n",
    "Messwerte": r"Bild: (Schärfe avg.*?)\n",
}

# Felder, die stabil sein SOLLTEN (Zahlen + Entscheidungen). Wortlaut steht bewusst nicht drin.
ZAHLEN = [
    ("performance_score", lambda d: d.get("performance_score")),
    ("protagonist_ab_sek", lambda d: d.get("protagonist_ab_sek")),
    ("hook.sprech", lambda d: d.get("hook", {}).get("sprech_hook_score")),
    ("hook.text", lambda d: d.get("hook", {}).get("text_hook_score")),
    ("struktur", lambda d: d.get("struktur", {}).get("score")),
    ("sprechqualitaet", lambda d: d.get("sprechqualitaet", {}).get("score")),
    ("schnitt_pacing", lambda d: d.get("schnitt_pacing", {}).get("score")),
    ("spannungsbogen", lambda d: d.get("spannungsbogen", {}).get("score")),
    ("visuelle_aesthetik", lambda d: d.get("visuelle_aesthetik", {}).get("score")),
]
ENTSCHEIDUNGEN = [
    ("format", lambda d: d.get("format")),
    ("funnel", lambda d: d.get("funnel")),
    ("text_hook_vorhanden", lambda d: d.get("hook", {}).get("text_hook_vorhanden")),
]


def lade(pfad: Path) -> dict:
    text = pfad.read_text(encoding="utf-8")
    eingaben = {}
    for name, muster in INPUT_BLOECKE.items():
        m = re.search(muster, text, re.S)
        eingaben[name] = m.group(1).strip() if m else "(nicht gefunden)"
    m = re.search(r"### Output — geparst \(JSON\)\n\n~~~json\n(.*?)\n~~~", text, re.S)
    return {
        "name": pfad.stem.replace("prompt_log-", ""),
        "eingaben": eingaben,
        "json": json.loads(m.group(1)) if m else {},
        "text": text,
    }


def zeige_inputs(laeufe: list[dict]) -> bool:
    print("=" * 78)
    print("SCHICHT 1 — INPUT (muss identisch sein, sonst ist alles Weitere wertlos)")
    print("=" * 78)
    alles_gleich = True
    for name in INPUT_BLOECKE:
        werte = {l["eingaben"][name] for l in laeufe}
        ok = len(werte) == 1
        alles_gleich &= ok
        print(f"  {'✓' if ok else '✗ ABWEICHUNG'}  {name}")
        if not ok:
            for l in laeufe:
                print(f"        {l['name']}: {l['eingaben'][name][:100]}")
    return alles_gleich


def zeige_felder(laeufe: list[dict], felder: list, titel: str) -> list[str]:
    print()
    print("=" * 78)
    print(titel)
    print("=" * 78)
    kopf = "  " + "Feld".ljust(22) + "".join(l["name"][:8].ljust(11) for l in laeufe) + "Streuung"
    print(kopf)
    print("  " + "-" * (len(kopf) - 2))
    wackler = []
    for label, hole in felder:
        werte = [hole(l["json"]) for l in laeufe]
        einzig = {str(v) for v in werte}
        if len(einzig) == 1:
            spanne = "–"
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in werte if v is not None):
            zahlen = [v for v in werte if v is not None]
            spanne = f"±{(max(zahlen) - min(zahlen)) / 2:g}  ⚠"
        else:
            spanne = "UNEINIG  ⚠"
        if len(einzig) > 1:
            wackler.append(label)
        print("  " + label.ljust(22) + "".join(str(v).ljust(11) for v in werte) + spanne)
    return wackler


def zeige_empfehlungen(laeufe: list[dict]) -> None:
    print()
    print("=" * 78)
    print("SCHICHT 2b — ACTION_STEPS (welche 3 Schritte, in welcher Reihenfolge?)")
    print("=" * 78)
    for l in laeufe:
        print(f"\n  [{l['name']}]")
        for s in l["json"].get("action_steps", []):
            print(f"    {str(s.get('zeitpunkt', '?')).ljust(22)} {s.get('anweisung', '')[:80]}")
        if not l["json"].get("action_steps"):
            print("    (keine)")


def regressionscheck(laeufe: list[dict]) -> None:
    """Die fünf Ja/Nein-Fragen aus Befund 0 — ist die Kaskade weg?"""
    print()
    print("=" * 78)
    print("SCHICHT 3 — REGRESSIONSCHECK Befund 0 (Reaction/Protagonist)")
    print("=" * 78)
    for l in laeufe:
        d, t = l["json"], l["text"]
        alle = json.dumps(d, ensure_ascii=False).lower()
        pausen_15 = bool(re.search(r"sek\.? 1[4-7]\b", alle)) and "pause" in alle
        hat_empf = "zeitpunkt_sek" in t
        print(f"\n  [{l['name']}]")
        print("    format == Reaction ............... " + ("✓" if d.get("format") == "Reaction" else "✗ " + str(d.get("format"))))
        print("    protagonist_ab_sek .............. " + str(d.get("protagonist_ab_sek", "✗ FEHLT")))
        print("    Prompt hat Reaction-Block ....... " + ("✓" if "FREMDVIDEO" in t else "✗"))
        print("    Modell füllte `empfehlungen` .... " + ("✓" if hat_empf else "✗ (Fallback aktiv!)"))
        print("    Pause ~Sek. 15 empfohlen ........ " + ("✗ JA (Regression!)" if pausen_15 else "✓ nein"))


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    laeufe = [lade(Path(p)) for p in sys.argv[1:]]
    inputs_ok = zeige_inputs(laeufe)
    w1 = zeige_felder(laeufe, ZAHLEN, "SCHICHT 2a — ZAHLEN (sollten stabil sein)")
    w2 = zeige_felder(laeufe, ENTSCHEIDUNGEN, "SCHICHT 2a — ENTSCHEIDUNGEN (sollten stabil sein)")
    zeige_empfehlungen(laeufe)
    regressionscheck(laeufe)
    print()
    print("=" * 78)
    print("FAZIT")
    print("=" * 78)
    print(f"  Inputs identisch: {'JA' if inputs_ok else 'NEIN — Output-Vergleich ist wertlos, erst Input fixen!'}")
    print(f"  Instabile Felder: {', '.join(w1 + w2) if (w1 + w2) else 'keine gefunden'}")
    print(f"  n={len(laeufe)}: Abweichung = starker Beleg für ein Problem.")
    print("            Übereinstimmung = SCHWACHER Beleg (3 Würfe können zufällig gleich fallen).")


if __name__ == "__main__":
    main()
