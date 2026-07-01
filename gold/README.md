# Gold-Set — Experten-bewertete Referenz-Analysen

Zweck: kalibriert Claudes Urteil (Phase 2). Jedes Gold-Paar ist **(Input-Beschreibung → Experten-Urteil)**.
Im Echtbetrieb bekommt Claude genau so einen Input — also lernt es aus dem Gold-Set, *wie bei welcher
Beschreibung zu urteilen ist*.

## Workflow
1. Video durch den Analyst laufen lassen (skip_eval=AN). Aus `analyst_runs/<id>/analysis.json` bzw. dem
   Roh-Dropdown den **Szenen+Transkript-Block** kopieren → Feld `input`.
2. Im Experten-Meeting pro Kriterium bewerten (Sheet: `_meeting_sheet.md`) → Feld `urteil`.
3. Datei `gold/<kurzname>.json` aus `_template.json` ableiten und ausfüllen.

## Wichtig
- **Nicht alle Gold-Paare in jeden Prompt geben.** Später injiziert der Code 1–2 *format-passende* Beispiele.
- `input` muss der echten Claude-Eingabe entsprechen (Beschreibung, nicht das Video).
- `notizen` = das WARUM der Experten + Streitpunkte (für die Rubrik in `services/analyst_eval_skill.md`).
- Zielgröße: 10–15 Paare, Formate gemischt (Talking-Head, Sketch, Tutorial, …).
