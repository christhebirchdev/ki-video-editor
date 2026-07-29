# Offene Fixes — Analyst-Output

Stand 2026-07-29 (zweite Runde). Quelle: Auswertung von 8 Läufen mit `feedback.jsonl` plus zwei
Verifikationsläufen (61d39035, f2312dc9).

**Umgesetzt und getestet:** P1, P3, P5, P6, P7, P8, P9, P13 (erste Runde) sowie P4, P10, P11, P12, P14,
P15, P16 (zweite Runde).

**Offen: nur noch P2.**

---

## P2 — Redundanz-Check ist zum Boilerplate geworden

**Befund:** „Text wiederholt das Gesprochene" stand in 5 von 11 Läufen als Top-Tipp Nr. 1
(25b8b2f6, 652d4e22, 56748c94, a4fbb8ae, bab324f5) und dort meist auch als Handlungsschritt Nr. 1.

- **f0ea9f9a**, Chris: *„tipp ist ist mist. texthook ist vorhanden und wird gut eingeblendet."* — das
  Modell hatte selbst `text_hook_score = 4` vergeben.
- **56748c94** (Score 92, alle Dimensionen 4–5): Handlungsschritt 1 war *„Ersetze das Text-Overlay
  '16 LITER'"*. Das Modell empfiehlt, das stärkste Element des Videos umzubauen.

**Ursache:** Der Check ist im V2-Override als Pflicht deklariert, ohne Schwelle und ohne Abschaltung bei
gutem Score. Damit ist er in jedem Lauf der wahrscheinlichste Kandidat für Platz 1.

**Teilweise entschärft durch P1 und P12:** Untertitel lösen ihn nicht mehr aus, und die Abzugsrichtung
ist geklärt. Was fehlt, ist die Schwelle.

**Vorschlag:**
- (a) erst ab ~70 % Wortgleichheit auslösen,
- (b) bei `text_hook_score ≥ 4` keine Ersetzungs-Empfehlung mehr, höchstens ein Hinweis in
  `weitere_empfehlungen`,
- (c) Wortgleichheit im Code messen statt im Prompt schätzen — die Daten liegen vor (Transkript und
  der vom Modell erkannte Bildtext). Gleiches Muster wie bei `gueltige_texthook_varianten()`.

**Offene Entscheidungen:**
1. Ist 70 % die richtige Schwelle?
2. Soll der Hinweis bei gutem Score ganz verschwinden oder nach `weitere_empfehlungen` rutschen?
3. Für (c) müsste das Modell den erkannten Eröffnungstext in ein eigenes Feld schreiben
   (z.B. `text_hook_wortlaut`), damit der Code vergleichen kann. Ist das den Schema-Zuwachs wert?

---

## Prozess

- **C1 Regressionsset.** `tools/replay_nachbearbeitung.py` prüft jede Änderung an der Nachbearbeitung
  gegen alle gespeicherten Läufe, ohne API-Call — inklusive Score-Diff. Was fehlt: dieselbe Prüfung für
  Prompt-Änderungen, dafür braucht es echte Läufe gegen einen festen Satz Referenzvideos.
- **C2 Auswertungs-Skill.** Der Feedback-Loop ist bis „Sammeln" gebaut. Der Schritt „Muster → Plan → Fix
  → Report" existiert nicht und ist genau das, was am 2026-07-29 zweimal von Hand passiert ist.
- **C3 `PROMPT_VERSION`** bei jeder inhaltlichen Prompt-Änderung hochzählen.

---

## Erledigt in der zweiten Runde (2026-07-29)

| # | Was | Umsetzung |
|---|-----|-----------|
| **P4** | Sprechpausen-Bündelung griff strukturell nie | Schema-Feld `pausen_urteile` (raus/lassen/unklar je Pause); `baue_pausen_schritt()` erzeugt EINEN Schritt mit allen Zeitpunkten |
| **P10** | Texthook-Empfehlung fehlte trotz Score 0 | `erzwinge_hook_empfehlungen()`, deckt auch den Sprech-Hook ab (Score 1–3) |
| **P11** | Score klumpte fünfmal auf 68 | `berechne_performance_score()` aus sieben Dimensionen × festen Gewichten, funnel-unabhängig. Spanne über dieselben Läufe jetzt 45–96 |
| **P12** | Sprech-Hook-Redundanz nur in einer Richtung | Abzug geht im Zweifel an die Text-Hook; Sprech-Hook hat eigene Maßstäbe (darf länger sein, ergänzt statt wiederholt) |
| **P14** | Framing, Licht, Schärfe, Untertitel-Platzierung fehlten | Referenz-Standard in `visuelle_aesthetik`, Framing-Teil ausdrücklich nur für Talking Head |
| **P15** | ~35 von 61 Feedback-Einträgen waren Duplikate | Frontend speichert nur noch bei echter Änderung; Historie bleibt append-only |
| **P16** | Texthook-Vorschlag mit 13 statt 9 Wörtern | Schema-Feld `texthook_varianten`; der Code zählt, verwirft zu lange und baut die Empfehlung |
