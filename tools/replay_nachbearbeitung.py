#!/usr/bin/env python3
"""Alte Läufe durch die AKTUELLE Nachbearbeitung schicken und zeigen, was sich ändert.

Wozu: `analyst_eval.nachbearbeiten()` greift nach dem Modell-Call — die rohen `empfehlungen`
liegen in jeder `analysis.json`. Damit lässt sich jede Code-Änderung an der Nachbearbeitung
gegen echte Läufe prüfen, ohne einen einzigen API-Call zu zahlen.

Der Prompt lässt sich so NICHT testen (dafür braucht es einen neuen Lauf) — nur alles, was
danach im Code passiert: Filter, erzwungene Schritte, Score-Korrekturen, Sortierung.

    python3 tools/replay_nachbearbeitung.py [run-id ...]

Ohne Argument: alle Läufe in analyst_runs/.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.analyst import AnalystEvaluationV2, AnalystResult  # noqa: E402
from services.analyst_eval import nachbearbeiten  # noqa: E402
from services.analyst_speech import PAUSE_THRESHOLD_SEC  # noqa: E402

RUNS = Path(__file__).resolve().parent.parent / "analyst_runs"


def _steps(items) -> list[str]:
    return [f"{s['zeitpunkt'] if isinstance(s, dict) else s.zeitpunkt} | "
            f"{(s['anweisung'] if isinstance(s, dict) else s.anweisung)[:80]}" for s in items]


def replay(run_id: str) -> bool:
    """True, wenn sich für diesen Lauf etwas ändert."""
    pfad = RUNS / run_id / "analysis.json"
    if not pfad.exists():
        return False
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    roh = daten.get("evaluation") or {}
    if not roh.get("empfehlungen"):
        return False  # Altlauf ohne flache Liste — die Nachbearbeitung greift dort nicht

    result = AnalystResult(**{k: v for k, v in daten.items() if k in AnalystResult.model_fields})
    neu = nachbearbeiten(AnalystEvaluationV2(**roh), result)

    alt_steps, neu_steps = _steps(roh.get("action_steps") or []), _steps(neu.action_steps)
    scores_alt = (roh.get("hook", {}).get("sprech_hook_score"),
                  (roh.get("sprechqualitaet") or {}).get("score"))
    scores_neu = (neu.hook.sprech_hook_score, neu.sprechqualitaet.score)
    stats = daten.get("speech_stats") or {}
    unter_schwelle = [p for p in (stats.get("pausen") or []) if p["dauer_sec"] <= PAUSE_THRESHOLD_SEC]

    if alt_steps == neu_steps and scores_alt == scores_neu and not unter_schwelle:
        return False

    print("=" * 78)
    print(f"{run_id} | Format: {daten.get('gewaehltes_format') or '?'} | "
          f"Sprechbeginn: {stats.get('sprechbeginn_sec')}")
    if alt_steps != neu_steps:
        print(" Handlungsempfehlungen ALT:")
        for s in alt_steps:
            print("   -", s)
        print(" Handlungsempfehlungen NEU:")
        for s in neu_steps:
            print("   +", s)
    if scores_alt != scores_neu:
        print(f" Scores: sprech_hook {scores_alt[0]} → {scores_neu[0]}, "
              f"sprechqualitaet {scores_alt[1]} → {scores_neu[1]}")
    if unter_schwelle:
        # Wirkt erst beim NÄCHSTEN echten Lauf: das Modell sieht diese Pausen dann gar nicht mehr.
        print(f" Pausen unter der Messschwelle ({PAUSE_THRESHOLD_SEC}s), künftig nicht mehr gemeldet: "
              + ", ".join(f"{p['dauer_sec']}s@{p['start_sec']}s" for p in unter_schwelle))
    return True


def main() -> None:
    ids = sys.argv[1:] or sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    geaendert = sum(replay(r) for r in ids)
    print(f"\n{geaendert} von {len(ids)} Läufen ändern sich durch die aktuelle Nachbearbeitung.")


if __name__ == "__main__":
    main()
