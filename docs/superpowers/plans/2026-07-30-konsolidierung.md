# Konsolidierung — vier Strukturfehler aus vier Runden Flickwerk

**Anlass.** Fünf Läufe auf `2026-07-30d`. Chris: „manche fehler spitzen sich zu und doppeln sich
auch langsam." Die Messung bestätigt das, und die Ursache liegt in meinen eigenen Eingriffen.

## Diagnose

**D1 — Doppelte Tipps sind garantiert, nicht zufällig.** In 4 von 5 Läufen wiederholt Tipp 3 die
Tipps 1/2. `erzwinge_empfehlungen_bei_schwachen_scores` baut aus `visuelle_aesthetik.probleme` einen
Sammeltipp — aus denselben Problemen, zu denen das Modell schon konkrete Empfehlungen geschrieben
hat. Geprüft wird das nicht, weil die Stichwort-Erkennung wegen Fehlalarmen entfernt wurde.
Die Absicherung feuert also genau dann, wenn sie nicht gebraucht wird.

**D2 — Der Ästhetik-Score ist informationslos.** 5 von 5 Läufen exakt 3. Kette: Pflicht-Checkliste
→ Modell findet immer zwei Probleme → Score-Deckel (2 Probleme = max 3) → immer 3. Der Deckel kennt
keine Schwere: „Textbox klebt an den Haaren" zählt wie „Bild ist unscharf".

**D3 — Feste Textbausteine im Code sind zu starr.** `_texthook_anweisung` nennt alle
Gestaltungspunkte, auch die intakten. Chris: „bei tipp 2 hätte nur die textinhaltsanpassung
gereicht. optisch ist die texthook in ordnung."

**D4 — Der Prompt wuchs um 41 % (21.513 → 30.405 Zeichen)**, vier Runden nur Addition.
Ein 30-KB-Prompt verwässert; Regeln greifen schlechter, je mehr daneben steht.

Gemeinsames Muster: Jedes Feedback wurde als fehlende Regel behandelt. Die Lösung ist diesmal
Konsolidierung und Rücknahme, nicht Ergänzung.

---

## F1 — `Empfehlung.betrifft`: exakte Zuordnung statt Raten

Neues Feld an `Empfehlung`: `betrifft` mit festen Werten (`sprech_hook`, `text_hook`,
`sprechqualitaet`, `visuelle_aesthetik`, `spannungsbogen`, `struktur`, `schnitt_pacing`, leer).
Das Modell ordnet jede eigene Empfehlung einer Dimension zu.

`erzwinge_empfehlungen_bei_schwachen_scores` überspringt eine Dimension, wenn dazu schon eine
Empfehlung existiert — **deterministisch, ohne Stichwörter**. Damit verschwindet D1 vollständig.

Dies ist derselbe Mechanismus, der bei `text_hook_wortlaut` und `pausen_urteile` funktioniert hat:
ein strukturiertes Feld statt einer Formatierungsbitte. Vier Versuche mit Textmustern
(`bild`, `sprech`, `hintergrund`, Wortmengen-Überlappung) sind vorher gescheitert.

## F2 — Schwere ins Schema: `probleme` vs. `hinweise`

`probleme` führt nur **deutliche** Mängel — sie zählen für den Score-Deckel und erzeugen
Empfehlungen. Neues Feld `hinweise` für leichte Auffälligkeiten: werden erwähnt, wirken NICHT auf
Score oder Empfehlungen.

Bewusst zwei Listen statt eines Schwere-Attributs pro Eintrag: `probleme: list[str]` bleibt
typgleich, Altläufe und Frontend lesen weiter dasselbe Feld. Ein Typwechsel auf Objekte hätte
`analysis.json` aller 40 Altläufe und die Frontend-Anzeige gebrochen.

In `82bda700` wären „Hintergrund schlicht" und „Textbox klebt an den Haaren" Hinweise gewesen —
Score also 4 statt 3.

## F3 — Pflicht heißt PRÜFEN, nicht KRITISIEREN

Der V2-Override sagt heute: Kopfraum und Bildqualität beurteilen, Auffälliges MUSS in `probleme`.
Neu: beurteilen ja — aber ein Toleranzbereich wird ausdrücklich benannt, und gute Umsetzung soll
als gut gesagt werden. Nur was einem Zuschauer beim ersten Sehen auffällt, ist ein deutlicher
Mangel; alles andere ist Hinweis oder gar nichts.

Chris' Beispiele als Anker: „natürlich ist nicht schlecht und muss nicht negativ bewertet werden",
„der raum zwischen kopf und rand ist nahezu perfekt groß", Kamerawackeln kann Dynamik sein.

## F4 — Texthook-Empfehlung modular aus benannten Mängeln

Neues Feld `texthook_maengel: list[str]` mit festen Werten: `wortlaut`, `laenge`, `groesse`,
`farbe`, `lesbarkeit`, `dauer`, `position`, `redundanz`.
`_texthook_anweisung` baut den Satz **nur** aus den gemeldeten Mängeln. Ist der Inhalt gut und nur
die Schrift schlecht lesbar, steht auch nur das drin.

## F5 — Gestaltung wird Teil der Score-Definition

`e9f69518`: `text_hook_score = 4`, Begründung nur über den Inhalt („zeigt einen spannenden
Widerspruch"), obwohl die Hook laut Chris „grottig aussieht". Die Gestaltungsregel steht als
eigener Absatz weit von der Score-Vergabe entfernt und wird überlesen.

Neu: Die Score-Definition selbst nennt Inhalt UND Gestaltung. Kannst du die Gestaltung nicht
positiv belegen, höchstens 3. Dazu ist `texthook_maengel` Pflicht — was benannt werden muss, kann
nicht übersprungen werden.

## F6 — Empfehlungen kürzen: Handlung zuerst

Chris: „kürze die textblöcke so das die handlungsempfehlung vordergründig ist und die erklärung
dazu nur kurz und knapp." Regel in den Empfehlungs-Kanon: erster Satz = die Handlung, höchstens ein
Satz Begründung danach.

## F7 — Prompt-Diät

Systematisch nach Dopplungen und Widersprüchen durchgehen und kürzen. Ziel: unter 27 KB, ohne eine
inhaltliche Regel zu verlieren. Hygiene-Tests gegen erneute Streuung.

---

## Reihenfolge

1. Schema (F1, F2, F4) — alles andere hängt daran
2. Code: Deckel auf `probleme`, Erzwingung mit `betrifft`, modulare Texthook-Anweisung
3. Prompt: F3, F5, F6, dann F7
4. Gegenprobe gegen alle fünf Läufe, `PROMPT_VERSION` → `2026-07-31`
