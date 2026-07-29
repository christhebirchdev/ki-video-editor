# Offene Fixes — Analyst-Output

Stand 2026-07-29. Quelle: Auswertung von 8 Läufen mit `feedback.jsonl` (Prompt-Version 2026-07-22).
Umgesetzt und getestet sind P1, P3, P5, P6, P7, P8, P9, P13 — die hier gelisteten Punkte stehen noch aus
und brauchen Entscheidungen von Chris.

---

## P2 — Redundanz-Check ist zum Boilerplate geworden

**Befund:** „Text wiederholt das Gesprochene" steht in 5 von 11 Läufen als Top-Tipp Nr. 1
(25b8b2f6, 652d4e22, 56748c94, a4fbb8ae, bab324f5) und dort meist auch als Handlungsschritt Nr. 1.

- **f0ea9f9a**, Chris: *„tipp ist ist mist. texthook ist vorhanden und wird gut eingeblendet."* — das
  Modell hatte selbst `text_hook_score = 4` vergeben.
- **56748c94** (Score 92, alle Dimensionen 4–5): Handlungsschritt 1 ist *„Ersetze das Text-Overlay
  '16 LITER'"*. Das Modell empfiehlt, das stärkste Element des Videos umzubauen.

**Ursache:** Der Check ist im V2-Override als Pflicht deklariert, ohne Schwelle und ohne Abschaltung bei
gutem Score. Damit ist er in jedem Lauf der wahrscheinlichste Kandidat für Platz 1.

**Vorschlag:** (a) erst ab ~70 % Wortgleichheit auslösen; (b) bei `text_hook_score ≥ 4` keine
Ersetzungs-Empfehlung mehr, höchstens ein optionaler Zusatzhinweis; (c) nie gegen Untertitel prüfen
(seit P1 im Skill geregelt).

**Offen:** Ist 70 % die richtige Schwelle? Soll der Hinweis bei gutem Score ganz verschwinden oder in
`weitere_empfehlungen` rutschen?

---

## P4 — Sprechpausen: Bündelung greift strukturell nicht

**Befund 25b8b2f6:** 5 separate Pausen-Schritte im selben Lauf. Chris: *„sprechpausen als empfehlung
rauszuschneiden sollte gebündelt empfohlen werden und nur eine sehr lange sprechpause als beispiel
genommen werden. zum beispiel einfach: unnötige sprechpausen im video rausschneiden bspw. bei sek 24."*

**Ursache:** `verteile_empfehlungen()` bündelt nur bei identischem `gruppe`-Label **und** identischem
Anweisungstext. Das Modell schreibt pro Pause individuellen Freitext („Schneide die Pause von 0,6
Sekunden vor dem Satz 'Ich glaube nicht…' heraus"). Zwei Pausentexte sind nie identisch — die Regel kann
nicht greifen. Kein Prompt-Problem.

**Vorschlag:** Pausen aus dem Empfehlungs-Freitext ziehen. Neues Schema-Feld:

```
"pausen_urteile": [{"start_sec": <float>, "urteil": "raus" | "lassen" | "unklar"}]
```

Das Modell urteilt nur noch pro Pause (das ist Urteil), der Code baut daraus **genau einen** Sammelschritt
in Chris' Formulierung — längste „raus"-Pause als Beispiel, alle Zeitpunkte im Zeit-Label. Bei genau
einer Pause wird sie einzeln genannt.

**Offen:** Soll der Sammelschritt alle Zeitpunkte nennen („bspw. bei Sek. 24" vs. „bei Sek. 12, 24 und
47")? Chris' Beispielsatz nennt nur einen.

**Teilweise entschärft:** P5 (Messschwelle 0.8 s) entfernt bereits die Mehrzahl der Fehlalarme — in
25b8b2f6 fallen 5 von 8 gemessenen Pausen künftig weg, darunter genau die von Chris genannten bei Sek. 3
und Sek. 7.

---

## P10 — Texthook-Empfehlung fehlt, obwohl der Score 0 ist

**Befund e7fdf99d:** `text_hook_score = 0`, `text_hook_vorhanden = false` — aber kein einziger
Handlungsschritt zur Texthook, nur ein Top-Tipp. Chris: *„hier fehlt die texthook empfehlung komplett,
obwohl in der texthook bewertung unten klar erkannt wurde das es noch keine texthook gibt."*

**Ursache:** `bereinige_fremd_texthook()` setzt den Score nachträglich im Code auf 0. Das Modell hielt die
Fremd-Texthook für vorhanden und hatte deshalb keinen Grund, eine Texthook-Empfehlung zu schreiben. Der
Code korrigiert den Score, aber nicht die Empfehlungsliste.

**Vorschlag:** Überall, wo der Code `text_hook_score = 0` setzt, zusätzlich eine
`Empfehlung(zeitpunkt_sek=0.0, gruppe="texthook", …)` injizieren — vor `verteile_empfehlungen()`.
Zeitpunkt 0 heißt automatisch Platz 1. Das Muster steht seit P6 (`erzwinge_anlauf_schnitt`) bereits im
Code und ist getestet; ~10 Zeilen.

**Offen:** Nur bei der Reaction-Klemme oder auch, wenn das Modell selbst 0 vergibt? Und: Anlauf-Schritt
und Texthook-Schritt liegen dann beide auf Sekunde 0 — welcher soll zuerst stehen?

---

## P11 — Score klumpt und ist zu mild

**Befund:** `performance_score` über 11 Läufe: 68, 62, 68, 68, 74, 58, 62, 68, 92, 68, 72 — **fünfmal
exakt 68**.

- **25b8b2f6**, Chris: *„score sollte schlechter sein"* (Modell: 62), zu `schnitt_pacing`: *„würde es
  eher als 1/5 bewerten. es gibt einige unnötige sprechpausen. der blick auf das skript nach unten ist
  nicht rausgeschnitten und stört. das ende hätte auch geschnitten werden können."* (Modell: 2)

**Ursache:** Nur `hook` hat Score-Anker im Skill. Struktur, Sprechqualität, Schnitt, Spannungsbogen und
Ästhetik haben keine — das Modell rät in die Mitte. Der Gesamtscore ist zusätzlich als „qualitativ, keine
feste Formel" mit 101 möglichen Werten definiert.

**Vorschlag (von Chris bereits so entschieden):** Gesamtscore im Code aus Dimensionen × Funnel-Gewichten.
Normalisierung `(score−1)/4`, bei `text_hook` `score/5`. Vorgeschlagene Gewichte:

| Dimension | TOFU | MOFU | BOFU |
|---|---|---|---|
| Sprech-Hook | 15 | 20 | 15 |
| Text-Hook | 25 | 20 | 15 |
| Struktur (inkl. CTA) | 5 | 15 | 25 |
| Sprechqualität | 5 | 10 | 10 |
| Schnitt & Pacing | 20 | 12 | 10 |
| Spannungsbogen | 15 | 15 | 15 |
| Visuelle Ästhetik | 15 | 8 | 10 |

Gegenrechnung: 25b8b2f6 → 43 statt 62 (mit Chris' `schnitt_pacing = 1`: 40). 56748c94 → 95, das Modell
sagte 92.

**Offen:**
1. Sind die Gewichte so richtig?
2. Bei stummen Videos (P9) fallen Sprech-Hook und Sprechqualität weg — Gewichte proportional auf die
   restlichen Dimensionen verteilen, oder feste Ersatzgewichte?
3. `text_hook = 0` kostet bei TOFU 25 Punkte hart. Untergrenze setzen?

**Zweiter Teil:** Score-Anker für die fünf Dimensionen ohne Anker, kalibriert an Chris' Beispielen.
Das ist unabhängig von der Formel und wirkt auch dann, wenn die Formel nicht kommt.

---

## P12 — Sprech-Hook-Redundanz nur in einer Richtung

**Befund 652d4e22**, Sprech-Hook 4/5. Chris: *„ich finde den einstieg schlecht. es hookt nicht. inbound
vs outbound ist etwas das durch den text oben schon klar ist. die sprechhook ist dahingehend leider
nichts neues und triggert nicht."*

**Ursache:** Der Prompt kennt nur „Texthook doppelt den Sprech-Hook → Texthook abwerten". Der umgekehrte
Fall — der gesprochene Satz sagt nur, was oben schon steht — ist nicht geregelt.

**Vorschlag:** Regel symmetrisch formulieren: Wer zuerst da ist, gewinnt nicht automatisch. Wenn beide
dasselbe sagen, wird die Ebene abgewertet, die weniger Neues liefert.

**Offen:** Welche Ebene bekommt im Zweifel den Abzug? Zusammen mit P2 zu lösen, sonst entstehen zwei
Regeln, die sich widersprechen können.

---

## P14 — Fehlende Prüfpunkte in `visuelle_aesthetik`

**Befund e7fdf99d:** *„der bildausschnitt vom protagonisten wäre noch eine interessante bewertung
gewesen. mir fällt auf das der protagonist zu nah an der kamera ist. er hätte noch ein stück nach hinten
gehen können, sodass das kinn ungefähr in der mitte des videos ist und die untertitel dann unmittelbar
darunter platziert werden können. die untertitel sind zwar dynamisch aber könnten pro einblendung auf
1 bis maximal 4 wörter reduziert werden. das erzeugt mehr dynamik und retention im video."*

**Befund 652d4e22:** *„die bildqualität von dem protagonisten finde ich etwas schlecht. er ist ein wenig
verschwommen."* — `visuelle_aesthetik` war 3 mit **leerem** `probleme`-Array, obwohl die Schärfe als
Messwert vorliegt (`quality_metrics.schaerfe_avg`).

**Entschieden:** als Prüfpunkte innerhalb von `visuelle_aesthetik`, keine neuen Score-Chips.

**Offen:** Konkrete Anker. Ab wann ist jemand „zu nah"? Chris' Regel „Kinn ungefähr in der Bildmitte" ist
ein guter Anker — reicht das? Und ab welchem `schaerfe_avg` soll aktiv „unscharf" gemeldet werden
(`METRICS_GUIDE` nennt heute <50 unscharf, 50–150 mäßig)?

---

## P15 — Feedback-Daten sind verzerrt

**Befund:** 61 Einträge in 8 Dateien, davon ~35 Duplikate. Run 093dc5a7 enthält 7× denselben Text mit
wachsender Länge, 25b8b2f6 6× dieselbe Texthook-Kritik. `verdict` ist in der Mehrzahl leer.

**Ursache:** Das Frontend speichert bei jedem Tastendruck (on-change).

**Vorschlag:** on-blur statt on-change speichern (oder Debounce ~1 s). Für die Auswertung zusätzlich: pro
`(run_id, field_id)` nur den letzten Eintrag zählen — sonst gewichtet sich ein einzelner Kritikpunkt
siebenfach.

**Offen:** Soll die Historie erhalten bleiben (append-only war eine bewusste Entscheidung, „Drift als
Feature")? Dann reicht der Dedup beim Auswerten und das Frontend bleibt, wie es ist.

---

## C — Prozess

- **C1 Regressionsset.** Erster Baustein steht: `tools/replay_nachbearbeitung.py` prüft jede Änderung an
  der Nachbearbeitung gegen alle gespeicherten Läufe, ohne API-Call. Was fehlt: dieselbe Prüfung für
  Prompt-Änderungen — dafür braucht es echte Läufe gegen einen festen Satz Referenzvideos.
- **C2 Auswertungs-Skill.** Der Feedback-Loop ist bis „Sammeln" gebaut. Der Schritt „Muster → Plan → Fix
  → Report" existiert nicht und ist genau das, was am 2026-07-29 von Hand passiert ist.
- **C3 `PROMPT_VERSION`** bei jeder inhaltlichen Prompt-Änderung hochzählen. Aktuell `2026-07-29`.
