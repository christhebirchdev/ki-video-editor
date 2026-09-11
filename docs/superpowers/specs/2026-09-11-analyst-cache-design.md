# Analyse-Cache: dieselbe Datei → dasselbe Ergebnis

**Datum:** 2026-09-11
**Status:** freigegeben (Chris, 2026-09-11)

## Problem

Lädt ein Nutzer dasselbe Video ein zweites Mal hoch, läuft die volle Pipeline erneut und liefert
eine leicht abweichende Bewertung (LLM-Nichtdeterminismus). Der Nutzer sieht zwei Ergebnisse mit
unterschiedlichen Handlungsempfehlungen und weiß nicht, welches gilt. Nebeneffekt: doppelte
Gemini-Kosten und doppelte Laufzeit auf einem VPS, der bewusst auf einen Slot gedrosselt ist.

## Ziel

Identische Eingaben liefern identische Ausgaben — byte-gleich, nicht „ähnlich".

## Cache-Key

`sha256(Videodatei) + format + planned_text_hook + engine + PROMPT_VERSION`

- **sha256**: beim Upload chunkweise berechnet, während die Datei ohnehin auf Platte geschrieben
  wird. Kein zweiter Lesevorgang, kein RAM-Peak bei 4-GB-Uploads.
- **format / planned_text_hook / engine**: gehen in den Prompt ein. Anderes Format = legitim
  anderes Ergebnis, also kein Treffer.
- **PROMPT_VERSION** (`services/analyst_eval.py`): wird beim Start in `meta.json` des Laufs
  festgeschrieben. Ein Lauf unter altem Prompt kann damit nie einen Lauf unter neuem Prompt
  bedienen.
  **Betriebsregel: Prompt geändert → `PROMPT_VERSION` hochzählen.** Sonst liefert der Cache alte
  Ergebnisse zu neuem Prompt. Gleiche Disziplin wie bei `feedback.jsonl`.

Altläufe ohne `sha256`/`prompt_version` in `meta.json` haben keinen Key und matchen nie. Der Cache
füllt sich ab Deploy.

## Wo geprüft wird

In `POST /api/analyst/{run_id}/start`, nicht beim Upload — Format und Hook stehen erst dort fest.

Gesucht wird über alle `analyst_runs/*/meta.json` nach einem Lauf, der
`status.phase == "done"` ist, eine `analysis.json` besitzt und denselben Key trägt.
Bei mehreren Treffern gewinnt der **älteste** (nach `created_at`): so zeigt auch der dritte Upload
noch dasselbe Ergebnis wie der erste, statt eine Kette von Kopien aufzubauen.

Nicht als Quelle benutzt werden: `error`-Läufe und laufende Läufe.

## Was bei einem Treffer passiert

1. `analysis.json` des Altlaufs wird in das neue Run-Verzeichnis kopiert.
2. `meta.json` bekommt `cached_from` (Quell-Run-ID) und `cached_at` (`created_at` der Quelle).
3. Status geht direkt auf `done`. Kein Background-Task, kein API-Call.
4. `POST .../start` antwortet mit `status: "cached"`.

Der neue Run bleibt ein **eigener** Run: Chat und Feedback vermischen sich nicht mit dem alten
Lauf, und das Frontend braucht am Polling nichts zu ändern — es bekommt beim ersten Poll `done`
plus `result`.

**Verworfen:** stattdessen einfach die alte `run_id` zurückgeben. Spart die doppelte Videodatei,
kippt aber fremden Chatverlauf und fremdes Feedback in die Sicht des zweiten Nutzers.

**Bewusst offen:** zwei gleichzeitig gestartete identische Uploads laufen beide durch (keiner ist
beim Start des anderen schon `done`). Ein Lock dafür wäre mehr Komplexität als der Fall wert.

## Force-Rerun (Admin)

`POST /api/analyst/{run_id}/start` nimmt optional den Body `{"password": "...", "force": true}`.
Geprüft über das bestehende `_admin_ok` (konstante Zeit). Passwort im **Body**, nicht als
Query-Parameter — sonst landet es in den Traefik-/Uvicorn-Logs. Falsches Passwort → 401, kein
stilles Ignorieren.

Frontend: Checkbox „Cache umgehen (neu analysieren)" nur sichtbar, wenn die Admin-Ansicht offen
ist. Sie überlebt `reset()`, damit bei Eval-Serien nicht jedes Mal neu geklickt werden muss.

## Hinweis im Ergebnis

`GET /api/analyst/{run_id}` liefert zusätzlich `cached_from` und `cached_at`. Das Frontend rendert
darüber eine Zeile über dem Ergebnis:

> Dieses Video wurde bereits am 05.09.2026 analysiert — du siehst das gespeicherte Ergebnis.

Begründung: der Sprung von mehreren Minuten Wartezeit auf null Sekunden irritiert sonst mehr, als
er hilft.

## Module

Neu: `services/analyst_cache.py` — reine Funktionen ohne FastAPI-Abhängigkeit
(`hash_und_schreibe`, `cache_key`, `finde_treffer`, `uebernehmen`), damit die Logik ohne
HTTP-Schicht testbar ist.

Geändert: `api/analyst.py` (Upload-Hash, Cache-Prüfung im Start, Cache-Felder im GET),
`static/app.jsx` (Admin-Checkbox, Hinweiszeile).

## Tests (`tests/test_analyst_cache.py`)

- Hash beim Upload wird geschrieben und ist für gleiche Bytes gleich, für andere verschieden
- Treffer bei identischem Key → `status: cached`, `analysis.json` byte-gleich, keine Pipeline
- Miss bei anderem Format
- Miss bei anderer Prompt-Version
- `error`-Läufe und Läufe ohne `analysis.json` werden nicht als Quelle benutzt
- Bei mehreren Treffern gewinnt der älteste
- `force` mit richtigem Passwort umgeht den Cache; mit falschem → 401
