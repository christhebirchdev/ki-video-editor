# CLAUDE.md — ki-video-editor

Diese Datei ist der verbindliche Einstiegskontext. Bei Widersprüchen zu anderen Dokumenten
gilt: **Code > CLAUDE.md > Vault-Doku.** Wenn du hier etwas findest, das der Code widerlegt,
korrigiere diese Datei im selben Commit.

Stand: 2026-07-28 (aus dem Code verifiziert, nicht aus Doku übernommen).

---

## Was hier drin liegt

Ein Repo, **zwei Anwendungen**:

- **AI Video Analyst** (aktiv, produktiv auf dem Server) — analysiert Short-Form-Videos und gibt
  strukturiertes Feedback. `api/analyst.py`, `services/analyst_*`, `models/analyst.py`.
- **KI Video Editor** (nur lokal, nicht auf dem Server) — Schnitt-/Untertitel-/Shorts-Pipeline.
  `api/{projects,pipeline,subtitles,shorts,feedback}.py`, `services/cut_engine_*`, `shorts_*`, `subtitle_*`.

Getrennt wird per `ANALYST_ONLY=1` in der `.env`: dann bindet `main.py` die Editor-Router gar nicht
erst ein. Auf dem Server steht das Flag, lokal nicht.

**Arbeitest du am Analyst, fass den Editor nicht an — und umgekehrt.** Ausnahme: `cut_engine_v2.py`
ist trotz des Namens gemeinsame Grundlage (`group_into_sentences`, `Sentence`,
`estimated_max_word_duration`) und wird von `shorts_planner`, `outtake_detector`, `verbatim_whisper`
und `post_cut_cleanup` mitbenutzt. Nicht als „alte Version" löschen.

---

## Analyst-Pipeline (so läuft es wirklich)

**V2 Hybrid ist der Standard und der einzige Modus im Frontend** (`static/app.jsx`:
`const engine = "v2_hybrid"`):

1. **Whisper lokal** — `faster-whisper`, CPU, int8. Modell aus `settings.whisper_model` (Default `small`).
2. **Audio-Messwerte** — ffmpeg `ebur128`, deterministisch (`services/analyst_quality.py`).
3. **Gemini bewertet das Video direkt** in einem Lauf (`services/analyst_gemini_eval.py:evaluate_hybrid`) —
   Video plus die lokalen Messwerte gehen zusammen rein.
4. **Nachbearbeitung im Code**, nicht im Prompt (`services/analyst_eval.py`).

**Kein Ollama, kein lokales Gemma-Modell.** Das Wort „Gemma" steht noch in `analyst_observations.md`,
`analyst_eval_skill.md` und einigen Prompt-Zeilen — das ist historische Benennung aus der Zeit, als die
Bildbeschreibung lokal lief. Beschreibung und Bewertung laufen heute beide über die Gemini-API.

**V1 ist Legacy**, aus dem Frontend entfernt, über die API noch erreichbar (`_run_v1` in
`analyst_engine.py`): Gemini/VLM beschreibt in Segmenten, danach bewertet **Claude** über
`analyst_eval.evaluate()`. Nur dieser Pfad nutzt die Anthropic-API.

### Was bewusst im Code steht statt im Prompt

Diese Regeln haben als Prompt-Anweisung nachweislich nicht zuverlässig gegriffen. Sie zurück in den
Prompt zu verschieben ist ein Rückschritt, kein Refactoring.

**Alle laufen in `analyst_eval.nachbearbeiten()`** — die eine Stelle, an der die Nachbearbeitung steht.
Beide Engines (V1 und V2) rufen nur diese Funktion. Die Reihenfolge dort ist nicht beliebig: erst die
Urteils-Korrekturen, dann die Eingriffe in `empfehlungen`, `verteile_empfehlungen()` zuletzt.

- `erzwinge_nutzer_format()` — die Format-Auswahl des Nutzers überschreibt das Modell-Feld.
- `bereinige_fremd_texthook()` — bei Reaction-Format ohne eigene Texthook: Score hart auf 0. Gemini
  unterscheidet eingebrannten Fremdtext visuell nicht vom eigenen Overlay.
- `neutralisiere_stumme_scores()` — kein Transkript → `sprech_hook_score` und `sprechqualitaet.score`
  auf `None` („nicht bewertbar"), nicht auf 0/1. Beide Felder sind deshalb `Optional[int]`, das
  Frontend zeigt dafür „–". Ein stummes Format ist eine Entscheidung, kein Mangel (Feedback 08e908d7).
- `entferne_bestaetigungen()` — Empfehlungen, die den Ist-Zustand bestätigen („die Pause unbedingt
  behalten"), fliegen raus. Sie verbrannten regelmäßig einen der nur drei Top-Plätze (Feedback 10ff4193).
  Die Phrasenliste ist bewusst eng: „Lass den Zuschauer raten" ist eine echte Handlung.
- `erzwinge_hook_empfehlungen()` — wird eine Hook unten kritisiert, MUSS oben eine Handlung stehen:
  `text_hook_score == 0` → Texthook-Schritt, `sprech_hook_score` zwischen 1 und 3 → Sprechhook-Schritt,
  beide auf Sekunde 0. Grund: Der Texthook-Score wird teils erst im Code geklemmt, davon konnte das
  Modell nichts wissen (Feedback e7fdf99d); beim Sprech-Hook stand die Kritik im Score, aber keine
  Handlung im Output (Feedback f2312dc9). **Score 0 zählt beim Sprech-Hook NICHT als schwach** — das
  ist der Modell-Default bei Altläufen, keine Bewertung.
- `erzwinge_anlauf_schnitt()` — `sprechbeginn_sec > 0.8` und Format ≠ Reaction → Schnitt-Empfehlung auf
  Sekunde 0. Die Regel stand im Skill und feuerte nicht (Feedback a4fbb8ae, Sprechbeginn 0.98 s).
  Eigene Anlauf-Empfehlungen des Modells werden dabei **ersetzt, nicht ergänzt**. Erkannt wird über
  Zeitfenster + Schnitt-Verb, nicht über das `gruppe`-Label: Das Modell schrieb `"anlauf_weg"` statt
  `"anlauf"` und beide Schritte landeten im Output (Feedback 61d39035, „2 mal derselbe tipp").
  `_SCHNITT_VERB` ist bewusst eng (`schneid|entfern`) — mit `kürz` würde „ersetze die Texthook durch
  eine kürzere Variante" bei Sekunde 0 mitgelöscht.
- `baue_pausen_schritt()` — aus dem Schema-Feld `pausen_urteile` (Urteil je Pause: raus/lassen/unklar)
  entsteht EIN gebündelter Schnitt-Schritt mit allen Zeitpunkten. Über Freitext war das unmöglich:
  `verteile_empfehlungen()` bündelt nur bei identischem Text, und das Modell schrieb pro Pause einen
  eigenen Satz — es wurde faktisch nie gebündelt (Feedback 25b8b2f6, fünf fast gleiche Schritte).
  Eigene Pausen-Empfehlungen des Modells werden ersetzt. **Altläufe ohne das Feld bleiben unverändert.**
- `baue_einblendungs_schritt()` — aus dem Feld `einblendungen` (Stelle + was verstärkt werden soll)
  entsteht EIN Schritt für höchstens 3 Stellen, der dem Nutzer Grafik/Symbol/Emoji/Foto/B-Roll zur
  Wahl lässt (Feedback 3185d209: „maximal an 3 stellen empfehlen"). Bewusst EIN Eintrag statt mehrerer
  mit gleichem Text: Beim Bündeln über `verteile_empfehlungen` überlebt nur eine Anweisung, und dann
  wäre weg, WAS an welcher Stelle verstärkt werden soll. Der Filter gegen Modell-eigene Einblendungen
  greift nur bei Zeitpunkt-Treffer (±2 s) — ein „Folgen-Knopf" am Ende ist ein CTA und bleibt stehen.
- `gueltige_texthook_varianten()` + `_texthook_anweisung()` — Varianten kommen als Liste aus
  `texthook_varianten`, der Code zählt die Wörter (max. 9) und baut die Empfehlung. Die Regel stand im
  Prompt samt „zähle die Wörter" und wurde trotzdem gerissen (Feedback f2312dc9: 13 Wörter). Zählen ist
  Arithmetik. **Ab `text_hook_score` 4 wird gar keine Texthook-Empfehlung mehr gebaut** — das Modell
  liefert trotz Prompt-Bitte auch bei Score 5 Varianten, und die Empfehlung stand dann in fast jedem
  Lauf auf Platz 1 (Feedback b98c88b1).
- `berechne_performance_score()` — Gesamtscore aus den sieben Einzel-Scores × `SCORE_GEWICHTE`,
  **funnel-unabhängig** (Vorgabe Chris): Hooks + Ton- + Bildqualität am stärksten (je 17–18), dann
  Spannungsbogen/Struktur/Schnitt (je 10). Nicht bewertbare Dimensionen (None) fallen raus, ihr Gewicht
  verteilt sich proportional um. Vorher bestimmte das Modell den Wert frei — über 11 Läufe kam fünfmal
  exakt 68 heraus. Nach dem Umbau liegt die Spanne derselben Läufe bei 45–96.
- `verteile_empfehlungen()` — Top-3-`action_steps` **strikt nach frühestem Zeitpunkt im Video**, Rest nach
  `weitere_empfehlungen`. Gebündelt wird nur bei gleichem Label *und* gleicher Anweisung (Label allein
  führte zu Fehlmerges). **Bekannte Grenze:** Bei Sprechpausen greift die Bündelung faktisch nie, weil
  das Modell pro Pause individuellen Freitext schreibt. Der geplante Fix ist ein eigenes Schema-Feld
  `pausen_urteile` — seit 2026-07-29 umgesetzt, siehe `baue_pausen_schritt()` oben.

**`PAUSE_THRESHOLD_SEC = 0.8` in `services/analyst_speech.py` nicht zurücksetzen.** Bei 0.5 s landeten
regelmäßig Pausen von 0.5–0.6 s als Schnitt-Empfehlung im Output, die beim Zuschauen niemand wahrnimmt
(Feedback 25b8b2f6 und 093dc5a7). Die Schwelle wird über `pausen_txt()` im Prompt mitgenannt — sonst rät
das Modell an Stellen herum, die gar nicht gemeldet wurden.

### Prompt-Kanon: jede Regel genau einmal

Die Empfehlungs-Regeln standen dreimal fast wortgleich (Skill, V2-Override, JSON-Vertrag). Redundanz
erzeugt Varianten im Output. Seit 2026-07-29 gilt:

- Die **kanonische Fassung lebt im Skill** (`## Empfehlungen — die kanonische Regel`).
- V2-Override und `OUTPUT_SCHEMA` **verweisen nur** darauf. Der Override enthält ausschließlich, was
  V2-spezifisch ist (Video sichtbar, echte Zeitpunkte, Format-Ausnahmen).
- Dasselbe gilt für die Texthook-Längenregel: nur im Skill, gilt für Bewertung *und* Vorschläge.
- Tests in `tests/test_analyst.py` schlagen an, wenn eine Regel wieder doppelt auftaucht.

**Untertitel vs. Texthook** braucht ZWEI Merkmale gleichzeitig: der Wortlaut kommt im Transkript vor
UND der Text läuft über das Video mit (laufend neue Blöcke). Nur beides zusammen ist eine
Untertitelspur — dann auch bei statischen Blöcken oben im Bild (Feedback 25b8b2f6). Ein einzelner
Textblock am Anfang bleibt eine Texthook, selbst wenn er den gesprochenen Satz wiederholt; er ist dann
**redundant, nicht abwesend** (Score-Abzug statt 0). Die erste Fassung dieser Regel prüfte nur den
Wortlaut und kippte genau diesen Fall (Feedback 3185d209: „nicht richtig. texthook ist vorhanden.").
Greift auch das nicht, ist der nächste Schritt ein Nutzer-Auswahlfeld im Frontend — nicht noch eine
Prompt-Runde.

### Änderungen an der Nachbearbeitung gegen echte Läufe prüfen

```
python3 tools/replay_nachbearbeitung.py [run-id ...]
```

Schickt die gespeicherten rohen `empfehlungen` aus `analyst_runs/*/analysis.json` durch die aktuelle
`nachbearbeiten()`-Kette und zeigt den Diff — ohne einen einzigen API-Call. Prompt-Änderungen lassen
sich damit NICHT prüfen (dafür braucht es einen echten Lauf), alles danach schon.

---

## Betriebsregeln

**`uvicorn` niemals mit `--reload` starten.** Auch nicht für kurze Tests. Bei jeder Dateiänderung
startet der Prozess neu und bricht laufende Hintergrund-Analysen hart ab — der Status bleibt für immer
auf der letzten Phase stehen und das Frontend pollt endlos. Das war die Ursache des „hängenden
Fortschrittsbalkens bei 93 %", kein Analyst-Bug.

**Das GitHub-Repo ist öffentlich.** Keine Secrets, keine API-Keys, **keine Default-Passwörter** in
Python-Dateien. `admin_password` ist absichtlich `""` (fail closed) und kommt ausschließlich aus der
`.env`. Ein hartkodierter Default war hier schon einmal drin und wurde vor dem Push entfernt.

**`PROMPT_VERSION` in `services/analyst_eval.py` bei inhaltlichen Prompt-Änderungen hochzählen.**
Der Wert wird an jedes gespeicherte Feedback gestempelt (`analyst_runs/<id>/feedback.jsonl`). Ohne
Erhöhung ist altes Feedback später nicht von neuem unterscheidbar. Aktuell: `"2026-07-29b"` — bei einer
zweiten inhaltlichen Änderung am selben Tag wird ein Buchstabe angehängt.

**Die Determinismus-Zeilen in `services/whisper_service.py` nicht „aufräumen".** `temperature=0.0`,
`condition_on_previous_text=False` und `beam_size=5` gehören zusammen und sind einzeln begründet
(siehe Kommentare dort). Ohne sie schwankt der Wortlaut zwischen Läufen — real beobachtet: 99 vs. 111
Wörter bei byte-identischer Datei. Nicht erkannte Wörter sehen im Code exakt wie Sprechpausen aus, die
Bewertung feuert dann auf Phantompausen.

---

## Deploy

**Push auf `main` = Deploy.** `.github/workflows/deploy.yml` läuft auf einem self-hosted GitHub-Runner,
der **auf dem VPS selbst** liegt, und führt dort aus:

```
cd /docker/analyst && git pull origin main && docker compose up -d --build
```

Kein SSH nötig, keine Secrets im Workflow. Der Container läuft hinter dem vorhandenen Traefik
(`analyst.srv1691345.hstgr.cloud`), Basic-Auth über die Middleware `analyst-auth@file`. Uploads und
Ergebnisse liegen in Docker-Volumes (`analyst_runs`, `analyst_models`) und überleben Redeploys.

### Branch-Regel

`main` bedeutet per Definition „das, was live ist". Es gibt keine Staging-Stufe.

- Arbeit passiert auf einem Branch (`feature/…`, `fix/…`).
- Lokal testen, `pytest` grün.
- Erst dann nach `main` mergen — der Merge löst den Deploy aus.

**Nicht ungefragt auf `main` pushen.** Ein Push ist ein Deploy auf ein produktiv genutztes System.

Abgesichert durch `.git/hooks/pre-push`: Pushes auf `main` verlangen die getippte Bestätigung
`DEPLOY` an einem echten Terminal. Ohne Terminal (Skript, CI, Agent) bricht der Push ab. Der Hook
liegt in `.git/` und ist daher **nicht versioniert** — nach einem frischen `git clone` ist er weg
und muss neu angelegt werden. Notausgang: `git push --no-verify` (bewusst nutzen, nicht gewohnheitsmäßig).

### Lokal starten

```
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1     # ohne --reload
```

Ein Worker, weil die Analyst-Warteschlange ein prozess-lokaler Semaphore ist
(`analyst_engine._SLOTS`). Mehrere Worker heben die Begrenzung faktisch auf.

Tests für den Analyst: `venv/bin/python -m pytest tests/test_analyst.py -q` → 82 Tests.

**`pytest` ohne Argument läuft derzeit nicht.** Es bricht schon beim Einsammeln ab (`Interrupted:
1 error during collection`) und führt dann *keinen einzigen* Test aus — `tests/test_models.py` und
`tests/test_services.py` importieren `TakeAnalysis` und `CutDecision`, die es seit Commit `a44ae5c`
(2026-06-10) nicht mehr in `models/analysis.py` gibt. Reine Editor-Altlast, der Analyst ist nicht
betroffen. Wichtig zu wissen, weil ein grünes „82/82" **nicht** heißt, dass die Suite läuft.

---

## Doku

- **Dieses Repo ist die Wahrheit** für technischen Stand, Architektur und Betriebsregeln — weil
  versioniert: zu jedem deployten Commit ist nachlesbar, welche Regeln damals galten.
- **Obsidian-Vault** (`…/AI Content Team/🔍 KI Video Analyst/`) — konzeptionelle Arbeit, Session-Logs,
  Entscheidungshistorie. Ältere Dateien dort sind teils überholt; sie sind als Archiv markiert.
  Nicht als Quelle für den aktuellen Stand verwenden.
- `services/analyst_eval_skill.md` — Bewertungslogik, menschenlesbar, wird zur Laufzeit als
  System-Prompt geladen. Änderungen hier ändern das Produktverhalten.
- `services/analyst_video_reference.md` — Referenz-Prinzipien, wird an den System-Prompt angehängt.
- `analyst_observations.md` — Beobachtungsregeln (R1…) aus manuellen Vergleichen.

---

## Arbeiten an diesem Projekt (für Claude)

**Diese Datei ist dein einziges Gedächtnis.** Zwischen Sessions bleibt nichts erhalten außer dem,
was auf der Platte steht. Was hier nicht drinsteht, ist beim nächsten Mal weg.

**Pflege-Regel:** Ändert eine Arbeit etwas an Architektur, Betriebsregeln oder Deploy — CLAUDE.md
im **selben Commit** mitändern. Nicht „später nachtragen". Ein Commit, in dem der Code eine Regel
widerlegt, die hier noch steht, ist ein kaputter Commit.

Was hierher gehört: Betriebsregeln, Architektur-Fakten, teuer gelernte Fallen („so nicht, weil…"),
verworfene Optionen mit Begründung. Faustregel: **musstest du es zweimal erklären, gehört es hierher.**

Was NICHT hierher gehört: Verlauf einzelner Sessions, inhaltliche Kalibrierung, Testnotizen — die
gehen in den Vault.

**Vor Behauptungen über den Ist-Stand: im Code nachsehen, nicht in Dokumenten.** Die Vault-Doku
war am 2026-07-28 an mehreren Stellen nachweislich falsch (`beam_size`, Sortierregel im Prompt statt
im Code, angeblich tote `cut_engine_v2`). Doku altert, Code nicht.

---

## Offene Punkte (Stand 2026-07-28)

- Server-`ADMIN_PASSWORD`: unbestätigt, ob es von `feedback` auf etwas Längeres geändert wurde.
- Unbestätigt, ob `feedback.jsonl` auf dem Server tatsächlich geschrieben wird.
- V1 war nach einem Deploy noch im Browser sichtbar — Verdacht Browser-Cache. Cache-Busting über
  versionierten Query-String auf `app.jsx` vorgeschlagen, nicht umgesetzt.
- Gemini-Analysedauer schwankt stark (35–177 s bei ~gleichem Video), Ursache vermutlich
  Rate-Limiting. Ungeklärt: Free- oder Paid-Tier des API-Keys.
- Block B (Login pro Kunde, Kundenprofile, Verlaufs-Kontext) — nicht begonnen.
- **Offene Feedback-Fixes:** `docs/offene-fixes-analyst.md`. Nach der zweiten Runde am 2026-07-29 ist
  davon nur noch **P2** offen (Redundanz-Check ohne Schwelle und ohne Abschaltung bei gutem Score).
- `tests/test_models.py` und `tests/test_services.py` reparieren oder entfernen (siehe „Lokal
  starten"). Editor-Thema, blockiert aber die gesamte Testsuite.

**Verworfen (2026-07-28):** Analyst in ein eigenes Repo trennen. Der Aufwand (neuer Runner,
Umhängen von `/docker/analyst`, Volume-Migration) steht in keinem Verhältnis zum Nutzen, solange
`ANALYST_ONLY` die Trennung zur Laufzeit sauber erledigt. Nicht erneut vorschlagen ohne neuen Grund.
