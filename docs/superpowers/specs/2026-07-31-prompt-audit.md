# Prompt-Audit — Struktur und Effizienz

**Ausgangslage:** V1-Engine (Claude bewertet aus Text, ohne Video) ist abgeschafft. Es gibt nur
noch zwei genutzte Ansichten, beide fahren `v2_hybrid` — das Modell **sieht das Video**.

**Was pro Lauf an Gemini geht:** ~46.900 Zeichen ≈ 11.700 Tokens, plus Video.

| Teil | Zeichen | Wo |
|---|---|---|
| Skill-Body | 31.439 | `system_instruction` |
| V2-Override | 6.543 | user-turn |
| Referenz | 4.692 | `system_instruction` |
| OUTPUT_SCHEMA | 4.195 | `system_instruction` |

---

## B1 — Falsche Prämisse plus Widerruf bei jedem Lauf

Der Skill beginnt mit:
> „Du bekommst die OBJEKTIVE Analyse eines Kurzvideos: Szenenliste mit Beschreibungen und
> Bild-Fakten … **Du hast das Video nie gesehen** — urteile nur über diese Daten."

Der Override nimmt das jeden Lauf zurück (430 Zeichen):
> „WICHTIG, abweichend vom System-Prompt oben: … Ignoriere daher: (a) den Satz „Du hast das Video
> nie gesehen"; (b) Verweise auf eine vorgefertigte Szenenliste, Bild-Fakten oder einen separaten
> „dedizierten Blick-Pass" — die gibt es hier nicht; (c) Warnungen vor „Gemma-OCR-Fehlern"."

**Der Schaden ist nicht die Länge, sondern die Verwässerung.** Das Modell muss bei jeder Aussage im
Prompt mitentscheiden, ob sie noch gilt. Das ist die plausibelste Erklärung dafür, dass Regeln
mehrfach nicht gegriffen haben, obwohl sie klar formuliert dastanden — Gestaltung der Texthook
zweimal ignoriert, Kopfraum bis zur Pflichtzeile übersprungen.

Am alten Modus hängen im Skill nur **1.830 Zeichen** in drei Absätzen: die Einleitung, der
Blickrichtungs-Absatz (verweist auf einen „dedizierten Gemini-Pass" und auf „Person/Blick"-Angaben
einzelner Szenen) und eine Zeile in „Harte Regeln" („Sound/Schärfe/Licht NUR auf
Messwert-/Bild-Fakten-Basis" — im V2-Pfad gibt es keine Bild-Fakten).

Direkte Ersparnis mit dem Widerruf zusammen: ~2.260 Zeichen. Der Gewinn an Klarheit ist größer als
die Zahl.

---

## B2 — Instruktion und Daten sind vermischt (der größte Strukturfehler)

Der Override ist 6.543 Zeichen groß. **Davon sind ~190 Zeichen lauf-spezifische Daten** (Dateiname,
Transkript, Sprachstatistik, Messwerte). Der Rest — rund **6.100 Zeichen — sind allgemeine Regeln**,
identisch in jedem Lauf:

| Block im Override | Zeichen | Gehört in den Skill? |
|---|---|---|
| HOOK-VERBESSERUNG (3-Ebenen-Framework) | 1.011 | ja, überlappt mit der Hook-Sektion |
| TIEFE & KONKRETHEIT (3 Punkte) | 711 | ja, eigenständige Regel |
| PFLICHT Bildaufbau und Bildqualität | 524 | ja |
| Widerruf des System-Prompts | 430 | entfällt |
| PFLICHT Blickkontakt | 415 | ja |
| SPRECHPAUSEN | 382 | teilweise, Regel steht schon im Skill |
| AUDIO-QUALITÄT | 367 | ja |
| PFLICHT Untertitel | 318 | ja |
| Geplante Texthook fehlt | 305 | bleibt (hängt an der Nutzereingabe) |
| BILDTEXT-Abgleich | 302 | ja |
| HOOK-REDUNDANZ-CHECK | 299 | nur Verweis, kann entfallen |
| FORMAT-Block | 294 | bleibt (Nutzerangabe) |
| EMPFEHLUNGEN | 282 | nur Verweis, kann entfallen |
| NUTZE deinen visuellen Vorteil | 213 | ja |
| Messwerte-Hinweis | 195 | bleibt |
| PROTAGONIST | 161 | ja |
| Einleitung „sieh es dir wirklich an" | 115 | ja |

Diese Blöcke stehen im Override, **weil der Skill für V1 geschrieben war** und im V2-Lauf ergänzt
werden musste. Drei Folgen:

1. **Der Skill ist nicht die Single Source of Truth**, obwohl CLAUDE.md das behauptet. Regeln
   verteilen sich über zwei Dateien — genau dort entstehen die Doppelungen, die der Hygiene-Test
   inzwischen für Pausen und Varianten abfängt.
2. **Instruktionen stehen im user-turn statt in `system_instruction`.** Anweisungen im user-turn
   binden schwächer als Systemanweisungen. 6.100 Zeichen Regeln liegen an der falschen Stelle.
3. Die Verweis-Blöcke („Es gilt die Regel aus dem System-Prompt") kosten Tokens und bringen null
   Information, sobald es nur noch einen Modus gibt.

**Zielbild:** `system_instruction` = alle Regeln, für den einzigen existierenden Modus geschrieben.
user-turn = nur Lauf-Daten (Video, Format, Transkript, Sprachstatistik, Pausenliste, Messwerte).

Netto-Ersparnis konservativ geschätzt **1.500–2.500 Zeichen** durch entfallene Verweise, den
Widerruf und die Auflösung der Überlappung bei HOOK-VERBESSERUNG. Der eigentliche Gewinn ist die
stärkere Bindung und eine Datei als Wahrheit.

---

## B3 — Die Hook-Sektion ist ein Drittel des Prompts

10.881 Zeichen in 15 Absätzen. Sie ist über vier Feedback-Runden gewachsen und enthält jetzt:
Definition, Prüfschritt Grafik-Text, Bewertungsmaßstab, Wortlaut-Feld, drei Untertitel-Absätze,
zwei Score-0-Fassungen, Gestaltung, Länge, Sprech-Hook, Vorschläge und 2.657 Zeichen Kalibrierung.

Die Reihenfolge folgt der Entstehungsgeschichte, nicht der Arbeitsweise des Lesers. Ein Modell,
das eine Texthook bewerten soll, muss aktuell durch drei Untertitel-Absätze und zwei Score-0-Fälle,
bevor es beim Bewertungsmaßstab ankommt.

**Vorschlag:** Umbau in eine Entscheidungsreihenfolge — erst „ist es überhaupt eine Text-Hook?"
(Untertitel, Grafik-Inhalt), dann „wie gut ist sie?" (Inhalt, Gestaltung, Länge), dann „was
empfehle ich?" (Varianten, Mängel). Inhaltlich unverändert, nur sortiert und die Übergänge gekürzt.
Erwartete Ersparnis 800–1.200 Zeichen, vor allem aber leichter befolgbar.

---

## B4 — Provenienz im Prompt

13 Stellen nennen Herkunft: „Lauf 26a1adbf", „Feedback 041770c1", „Referenz S3", zusammen
~560 Zeichen. Sieben Lauf-IDs stehen im Prompt.

Das Negativ-Beispiel braucht das Modell. Die Lauf-ID braucht es nicht — sie ist Dokumentation für
uns. Sie gehört in einen Kommentar oder in CLAUDE.md, nicht in den Prompt.

Ausnahme: „Referenz S3", „Referenz P9" verweisen auf die angehängte Referenzdatei und sind für das
Modell auflösbar. Die bleiben.

Ersparnis ~350 Zeichen, und der Prompt liest sich sauberer.

---

## B5 — Kleinere Punkte

- **„Sprache des Outputs" (1.741 Zeichen)** enthält drei Vorher/Nachher-Beispiele. Die sind gut
  investiert — Stilregeln ohne Beispiel greifen schlecht. **Nicht kürzen.**
- **OUTPUT_SCHEMA (4.195)** trägt inzwischen inhaltliche Regeln in den Feldbeschreibungen. Zwei
  Hygiene-Tests prüfen das für Pausen und Varianten. Beim Umbau mitprüfen, ob weitere Beschreibungen
  Regeln wiederholen, die im Skill stehen.
- **Referenz (4.692)** wird bei jedem Lauf mitgeschickt. Inhaltlich Kalibrierung, kein Format.
  Kein Handlungsbedarf, aber der Posten ist gut ein Zehntel des Prompts — beim nächsten
  Kostenthema die erste Frage.

---

## Reihenfolge für den Umbau

1. **B1** — Prämisse umschreiben, Widerruf entfernen, V1-Bezüge im Skill bereinigen.
   Kleinster Eingriff, größte Wirkung auf die Verlässlichkeit.
2. **B2** — Regeln aus dem Override in den Skill ziehen, Verweis-Blöcke auflösen. Danach enthält
   der user-turn nur noch Daten.
3. **B4** — Lauf-IDs raus.
4. **B3** — Hook-Sektion neu sortieren. Zuletzt, weil es der Eingriff mit dem größten Risiko ist:
   Hier stecken die teuer erarbeiteten Regeln aus vier Feedback-Runden.

Nach jedem Schritt: `pytest tests/test_analyst.py tests/test_analyst_chat.py -q` (190 Tests) und
ein Blick darauf, dass die Hygiene-Tests weiter greifen. `PROMPT_VERSION` einmal am Ende hochzählen.

**Erwartetes Gesamtergebnis:** 46.900 → ca. 42.000–43.000 Zeichen, eine Datei als Regelquelle,
keine Selbstwidersprüche. Die Zahl ist der kleinere Teil des Gewinns.

---

## Nicht Teil dieses Umbaus

| Weggelassen | Warum |
|---|---|
| V1-Code entfernen (`_run_v1`, `claude_service`, `evaluate()`) | Eigene Aufräumaktion mit eigener Prüfung. Der Prompt-Umbau setzt nur voraus, dass V1 nicht mehr *benutzt* wird. |
| Referenzdatei kürzen | Kalibrierung, teuer erarbeitet. Erst messen, ob sie wirkt, dann anfassen. |
| Score-Gewichte oder Schwellen ändern | Nichts am Verhalten, nur an der Struktur. |
