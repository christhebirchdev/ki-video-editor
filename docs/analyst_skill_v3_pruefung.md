# Prüfung des V3-Skills: Widersprüche, Dopplungen, Ballast

Arbeitsdokument, **kein Prompt-Bestandteil.** Geprüft wurde `services/analyst_eval_skill_v3.md`
(1205 Zeilen, 85.800 Zeichen) am 2026-09-16, in drei Durchgängen: Zahlen/Schwellen,
Zuständigkeiten, Wiederholungen.

Zeilenangaben beziehen sich auf den Stand `ffadbde`.

**Warum das zählt:** Der Skill geht bei JEDER Analyse zweimal ans Modell. Jedes Zeichen kostet
doppelt, und jeder Widerspruch erzeugt eine Bewertung, die vom Zufall abhängt — davon, welchen Satz
das Modell zuerst liest.

**Einordnung der Funde in vier Klassen.** Die Unterscheidung ist wichtig, weil nicht jeder
Widerspruch im Prompt auch ein falsches Ergebnis erzeugt:

| Klasse | Bedeutung | Handlungsdruck |
|---|---|---|
| **A** | Der Widerspruch schlägt bis ins Ergebnis durch — kein Code fängt ihn ab | hoch |
| **B** | Der Prompt widerspricht sich, aber der Code entscheidet die Sache ohnehin | mittel (Tokens + Fehlkalibrierung) |
| **C** | Der Prompt behauptet etwas über das System, das nicht stimmt | hoch, weil es das Urteil verzerrt |
| **D** | Reine Wiederholung, kein inhaltlicher Konflikt | niedrig (nur Kosten) |

---

## C — Der Skill beschreibt das eigene System falsch

### C1 Die Gewichtsangabe ist sachlich falsch (Z. 1131–1132)

> „Den `performance_score` rechnet der Code aus deinen Einzel-Scores mit festen Gewichten aus; am
> stärksten zählen **die beiden Hooks sowie Ton- und Bildqualität**, danach Spannungsbogen,
> Struktur und Schnitt."

Die tatsächlichen Gewichte aus `SCORE_GEWICHTE_JE_ZIEL`:

| Dimension | TOFU | MOFU | BOFU |
|---|---|---|---|
| sprech_hook / text_hook | 16 / 16 | 14 / 14 | 13 / 13 |
| visuell_hook | 11 | 7 | 6 |
| spannungsbogen | 5 | **11** | **10** |
| **visuelle_aesthetik** | 6 | **3** | **3** |
| **audioqualitaet** | 5 | **3** | **2** |

Ton- und Bildqualität sind bei MOFU und BOFU die **am schwächsten** gewichteten Dimensionen
überhaupt — der Skill nennt sie als die stärksten. Der Spannungsbogen, laut Skill „danach", wiegt
dort dreimal so viel. Dazu: „die **beiden** Hooks" — es sind drei bewertete Hook-Ebenen, die dritte
(`visuell_hook`) wiegt bei TOFU 11 Punkte.

**Wirkung:** Das Modell kalibriert, wie streng es urteilt, an der wahrgenommenen Wichtigkeit. Es
wird also ausgerechnet dort besonders streng, wo es am wenigsten zählt — und lässt den
Spannungsbogen laufen.

### C2 „zu leise" steht noch im Leitsatz (Z. 103)

> „Technik und Auftreten sind Hygienefaktoren — schlechtes Audio, **zu leise**, schlechte
> Belichtung … zerstören Verständlichkeit"

Gegen Z. 887–892: „**Die LAUTSTÄRKE wird NICHT bewertet — von niemandem.** … kein ‚zu leise', kein
‚zu laut', kein Abzug dafür." Das ist ein Rest aus der Lautheits-Entfernung vom 2026-09-13 —
mein Fehler, der Leitsatz ganz oben erlaubt genau den Abzug, den der Fachabschnitt verbietet.

### C3 Funnel steuert den Score / steuert ihn nicht (Z. 1085 gegen Z. 1135)

Überschrift: „## Funnel — genaue Definitionen (zuerst bestimmen, **steuert den Score**)"
25 Zeilen später: „Der `funnel` ist weiterhin wichtig als Einordnung für den Nutzer, **steuert den
Score aber nicht mehr**."

Richtig ist das Zweite: Der Funnel steuert die Gewichte, nicht den Score direkt. Die Überschrift
muss gekürzt werden.

### C4 Der Gesamtscore wird angefordert und verworfen (Z. 96 gegen Z. 1133–1134)

> Z. 96: „Hook und Watchtime wiegen am schwersten — **gewichte sie im performance_score (0–100) am
> höchsten**."
> Z. 1133: „Deine Aufgabe ist deshalb NICHT der Gesamtwert … Gib trotzdem eine Zahl an (reiner
> Fallback) — **sie wird überschrieben**."

---

## A — Widersprüche, die bis ins Ergebnis durchschlagen

### A1 Untertitel-Qualität: der Skill schickt sie dorthin, wo er sie verbietet

- Z. 256–257: „Ihre QUALITÄT bewertest du **unter Schnitt & Pacing**."
- Z. 627 (im Abschnitt Schnitt & Pacing): „UNTERTITEL gehören **NICHT hierher**."
- Z. 830–831: „beurteile sie in `untertitel` und **NICHT in `schnitt_pacing`** oder `visuelle_aesthetik`."
- Z. 816 (visuelle Ästhetik): „Untertitel gehören NICHT hierher" — während Z. 762 im selben
  Abschnitt verlangt, die Safe Zone für „Texthook, **Untertitel**, Grafiken, Gesicht" zu prüfen.

Eine Anweisung, drei Verbote derselben Sache, und ein Abschnitt, der sich selbst widerspricht.

### A2 Empfehlung ist gleichzeitig Pflicht und verboten

Z. 1160: „Zu **JEDER** Dimension mit Score 3 oder schlechter gehört eine eigene Empfehlung."

Dem stehen sechs ausdrückliche Verbote gegenüber — jeweils, weil das System den Schritt selbst baut:
`text_hook` (Z. 381), `sprech_hook`/`visuell_hook` (Z. 416), `untertitel_vorhanden` (Z. 824),
Sprechpausen (Z. 731), `einblendungen` (Z. 984, 1171), Blickkontakt (Z. 1008).

Für ein schwaches Video ist beides gleichzeitig gefordert. Die angedrohte Folge (Z. 1161) tritt
dann garantiert ein: „setzt das System einen allgemeinen Standardsatz ein" — genau der Notnagel,
den wir selten haben wollten.

### A3 Mengenangaben passen nicht zu 16 Dimensionen

- `empfehlungen`: „EINE flache Liste (**3–10**)" (Z. 1140) gegen die Pflicht aus A2 bei bis zu 16
  schwachen Dimensionen.
- `staerken`: „1–2 je **Bereich**, höchstens **6** insgesamt" (Z. 1201). „Bereich" ist nicht
  definiert — bei 4 Kategorien ergibt 2 je Bereich schon 8, bei 16 Dimensionen 32. Der Code
  (`filtere_staerken`) rechnet mit 4 Kategorien × 2.

### A4 `betrifft` kennt Blick, Energie und Pausen nicht

Z. 1010: „Liegt der Blick in der Linse, **darf das als Stärke auftauchen**." Z. 722: eine gute
dramaturgische Pause „darf in `staerken`".

Die Enum (Z. 1164, Z. 1203) listet 16 Dimensionsnamen — `blickkontakt`, `energie`, `dynamik` und
Pausen sind keiner davon. Das Modell muss also entweder die Enum verletzen oder das erlaubte Lob
weglassen. Und: Lob ohne gültiges `betrifft` wirft `filtere_staerken` weg.

### A5 Monotone Stimme wird dreimal bestraft

`sprechqualitaet` Anker 3 (Z. 563 „durchgehend monoton"), `energie` = flach (Z. 991),
`protagonist_auftreten` Anker 2 (Z. 1059 „monotone Betonung"). Derselbe Befund, drei Abzüge, drei
Empfehlungen — gegen die eigene Regel „JEDE BEOBACHTUNG NUR EINMAL" (Z. 442).

Dasselbe Muster bei: unverständlicher Sprache (Z. 538–540 dämpft ausdrücklich Hook, Skript **und**
Struktur), nicht eingelöstem Eröffnungs-Versprechen (Hook, Struktur, Spannungsbogen), Safe-Zone-
Verstößen (visuelle Ästhetik, Texthook, Untertitel, Einblendungen).

### A6 Kollidierende Score-Anker

Je Dimension beschreiben zwei Anker denselben Fall:

| Dimension | Kollision |
|---|---|
| `visuell_hook` | Mini-Zoom ist Anker 3 (Z. 191) und „nichts stört = 5" (Z. 195) |
| `schnitt_pacing` | eine Einstellung ohne Schnitt ist 3 (Z. 619), „kein Schnitttempo nötig = 5" (Z. 624) |
| `soundeffekte` | Musik ohne Gestaltung ist 3 (Z. 700) und 2 (Z. 701) und „nichts fällt auf = 5" (Z. 704) |
| `visuelle_aesthetik` | „nichts gestaltet = 3" (Z. 803) gegen „ein deutlicher Mangel = 3" (Z. 808) |
| `audioqualitaet` | Grundrauschen ist 4 (Z. 917) und „leise Umgebung ist normal = 5" (Z. 924) |
| `spannungsbogen` | „kippt vor dem Ende" = 3 (Z. 745) und „Schluss läuft leer" = 2 (Z. 746) |

Muster: Der Satz „die Abwesenheit von Mängeln genügt für eine 5" steht unter **14** Ankerblöcken und
kollidiert dort jeweils mit einem Anker, der für dieselbe Lage eine 3 vorsieht.

### A7 Geometrie des Bildaufbaus geht nicht auf

- Kopfraum 20 % (Z. 771) + Gesicht ⅓ der Bildhöhe (Z. 779) ⇒ Kinn bei 53 %, also **unter** der
  Bildmitte — Z. 778 verlangt „auf der Mitte oder knapp darüber".
- „Brustbild bis Taille" (Z. 769) gegen Gesicht = ⅓ der Bildhöhe (Z. 779): bei ⅓ reicht der
  Ausschnitt bis Brustmitte, nie bis zur Taille.
- „Unteres Drittel frei für Handgesten" (Z. 780) gegen Sperrzone unten 21 % (Z. 760): zwei Drittel
  dieses Bereichs sind von der App überdeckt.

---

## B — Der Prompt widerspricht sich, der Code entscheidet

Kosten Tokens und verwirren die Kalibrierung, erzeugen aber kein falsches Ergebnis.

- **Untertitel-Score:** Anker „1 = so gut wie nichts zu lesen" (Z. 860) gegen „fehlen sie: schreib
  `null`" (Z. 877). `setze_untertitel_scores` regelt beide Fälle selbst — die Anweisung ans Modell
  ist trotzdem widersprüchlich.
- **Wortzahl der Text-Hook:** Das Modell soll zählen und deckeln (Z. 327), das System zählt
  ebenfalls und verwirft (Z. 381).
- **Anlauf vor dem ersten Wort:** Pflicht-Tipp (Z. 427) gegen Empfehlungsverbot (Z. 731).
- **Felder, die garantiert überschrieben werden:** `performance_score` (Z. 1134), `verbesserung`
  bei Score 5 (Z. 157), `funnel_wirkung_empfehlung` (Z. 61), beide Untertitel-Scores (Z. 877),
  Lautstärke-Empfehlungen (Z. 892), Lob ohne Score-Deckung (Z. 1204).

---

## D — Wiederholungen und Ballast (~11.500 Zeichen, ~13 %)

**Risikolos streichbar (~7.000 Zeichen):**

| Fund | Zeichen |
|---|---|
| Herkunftsbelege: „Anlass (Lauf d9988b7d)", Lauf-IDs, Nutzerzitate, Datumsangaben — 16 Stellen | ~3.200 |
| Funnel-Definitionen stehen dreimal (Z. 19–24, 931–936, 1085–1116) | ~1.100 |
| Lob/Kritik-Block buchstabiert die eigene Tabelle aus (Z. 144–160) | ~450 |
| Blickkontakt-Regeln wortgleich zweimal (Z. 1002 und 1029) | ~450 |
| Referenz-Abschnitt wiederholt S1–S4, die schon ausführlicher dastehen (Z. 584–587) | ~400 |
| CTA-`null`-Regel dreimal (Z. 937, 946, 961) | ~350 |
| Untertitel-Mängelliste dreimal in Varianten | ~300 |
| Bildausschnitt-Formatregel doppelt (Z. 767 und 1121) | ~300 |
| Kopfraum-Geometrie-Herleitung (Z. 771–774) | ~290 |
| Reaction-Blick-Ausnahme dreimal | ~250 |
| 16 Dimensionsnamen zweimal ausgeschrieben | ~230 |
| „höchstens 2" viermal im Hook-Abschnitt C | ~200 |
| Laiensprache-Regel in „Harte Regeln" wiederholt | ~200 |
| Testreel-Tipp wortgleich zweimal (Z. 266 und 272) | ~150 |
| Sonstiges (Soundeffekt-Liste, „halbe Sekunde vorbei") | ~250 |

**Zu entscheiden (~4.500 Zeichen)** — bewusste Wiederholung am Anwendungsort, wirkt gegen die
Mitte-Tendenz des Modells:

- „Die Abwesenheit von Mängeln genügt" unter 14 Ankerblöcken. Nicht streichen — aber der
  identische Mittelteil ließe sich kappen, der dimensionsspezifische Nachsatz („kein Studio",
  „keine Cliffhanger-Dramaturgie") ist die eigentliche Information. ~900 Zeichen.
- „Das System baut den Schritt — schreib keine eigene Empfehlung" an 10 Stellen. Ließe sich zu
  einem Spiegelstrich in der kanonischen Empfehlungs-Regel bündeln. ~700 Zeichen.
- Abgrenzung Sprech-/Audioqualität viermal — drei davon sind kurze Erinnerungen genau dort, wo die
  Verwechslung passiert. Behalten.

**Toter Output:** `top_tipps` (3–5 Hebel, Z. 1200) wird weiterhin erzeugt, seit dem 2026-09-13 aber
nirgends mehr angezeigt.

---

## Fehlende Schwellen

Stellen, an denen eine Zahl nötig wäre und keine steht: Kopfraum-Toleranz (nur „ca. 20 %"),
„deutlich nach 0 s" beim Sprechbeginn, Mindeststandzeit einer Einblendung, WPM- und
Füllwort-Korridore (obwohl gemessen), Pausen-Messschwelle, „drei Hinweise" in der visuellen
Ästhetik, Texthook unter drei Wörtern, BOFU-Längenkorridor (TOFU und MOFU haben einen, BOFU nicht).

Dazu die Überlappung TOFU 7–60 s / MOFU 30–90 s: Im Bereich 30–60 s entscheidet die Länge nichts,
und was stattdessen entscheidet, steht nirgends.

---

# Nachprüfung vor der Umsetzung (2026-09-16)

Chris hat vor der Umsetzung eine Gegenprüfung verlangt: Woher weiß ich bei jedem Widerspruch,
welche Seite die richtige ist? Antwort je Fund unten. Drei Autoritäten:

- **Code** — im Quelltext nachgelesen, nicht erinnert. Der Code ist die Wahrheit, weil er das
  Ergebnis erzeugt. Kein Rückfragebedarf.
- **Festlegung** — eine Entscheidung von Chris aus dem Verlauf. Zitiert, damit er widersprechen kann.
- **Offen** — weder Code noch Festlegung entscheidet. Braucht seine Antwort.

## Dabei neu gefunden (stand nicht im ersten Bericht)

### N1 Die Lautheits-Entfernung hat den Messwert-Block nie erreicht

`services/analyst_eval.py` Z. 50–55 (`_AUDIO_GUIDE`) geht bei JEDER Analyse in den Prompt und sagt
dem Modell: „Erst deutlich unter {LOUDNESS_TOO_QUIET} LUFS ist der Ton wirklich zu leise."
Zusammen mit dem Leitsatz Z. 103 im Skill sind das ZWEI Stellen, die „zu leise" erlauben, gegen
eine (Z. 887), die es verbietet. `verwirf_lautstaerke_empfehlungen` fängt nur die Empfehlung ab —
den Score-Abzug nicht.

`_AUDIO_GUIDE` ist mit V2 geteilt. Eine V3-Fassung über den vorhandenen `result`-Parameter an
beiden Aufrufstellen (`analyst_gemini_eval.py` Z. 54, `analyst_eval.py` Z. 2487) lässt V2 Byte für
Byte unverändert.

### N2 Der angedrohte Standardsatz existiert für 11 der 16 Dimensionen nicht

Z. 1160–1161 verspricht: „Fehlt sie, setzt das System einen allgemeinen Standardsatz ein."
`_DIMENSIONEN` (Z. 1248–1264) kennt genau fünf: `sprechqualitaet`, `visuelle_aesthetik`,
`spannungsbogen`, `struktur`, `schnitt_pacing`.

Die sechs Empfehlungsverbote aus A2 betreffen Dimensionen mit EIGENER Erzwingung
(`erzwinge_hook_empfehlungen`, `erzwinge_untertitel_empfehlung`, `baue_pausen_schritt`,
`baue_einblendungs_schritt`, `erzwinge_blick_empfehlung`). Dort ist der Widerspruch also nur
sprachlich — A2 ist milder als im Bericht behauptet.

Der echte Schaden liegt woanders: `skript`, `cta`, `untertitel_gestaltung`, `soundeffekte`,
`audioqualitaet` und `protagonist_auftreten` haben WEDER eine Erzwingung NOCH ein Verbot. Schreibt
das Modell dort nichts, verschwindet der Mangel aus den Empfehlungen — ohne Standardsatz, ohne
Warnung.

### N3 Der Rückfalltext widerspricht der 20-%-Festlegung

`_DIMENSIONEN` Z. 1253–1254 sagt dem Nutzer wörtlich „etwa 10–15 % Luft". Der Skill sagt seit der
Festlegung 20 %. Der Satz erscheint unverändert im Output, sobald `visuelle_aesthetik` ≤ 3 steht
und das Modell keine eigenen Probleme benannt hat. Nicht V3-begrenzt — eine Korrektur ändert auch
V2-Ergebnisse (nicht den V2-Prompt).

## Entscheidungstabelle

| Fund | Richtig ist | Autorität |
|---|---|---|
| C1 Gewichte | Die Tabelle aus `SCORE_GEWICHTE_JE_ZIEL`, nachgerechnet: Summe je Ziel 100, 16 Dimensionen. Satz muss neu geschrieben werden | Code |
| C2 + N1 „zu leise" | Lautstärke wird nicht bewertet. Z. 103 kürzen UND `_AUDIO_GUIDE` für V3 entschärfen | Festlegung („die lautstärke ist wirklich super … nimm die bewertung raus") |
| C3 Funnel-Überschrift | „steuert die Gewichte", nicht „den Score" | Code (`SCORE_GEWICHTE_JE_ZIEL` je Ziel) |
| C4 Gesamtscore | `berechne_performance_score` überschreibt. Z. 96 streichen | Code |
| A1 Untertitel | `untertitel_vorhanden` und `untertitel_gestaltung` sind eigene Dimensionen mit eigenem Gewicht. Z. 256–257 ist die falsche Seite | Code |
| A2/N2 Empfehlungspflicht | Die sechs Verbote gelten. Die Pflicht auf die fünf Dimensionen aus `_DIMENSIONEN` begrenzen — und für die sechs ungedeckten entscheiden, ob Erzwingung dazukommt | Code + **offen** |
| A3 `staerken`-Menge | `MAX_LOB_PRO_KATEGORIE` = 2 × 4 Kategorien = 8, nicht 6. „Bereich" durch „Kategorie" ersetzen | Code |
| A4 `betrifft` | Die Enum ist verbindlich (`filtere_staerken` verwirft alles andere). Blickkontakt und Energie gehören zu `protagonist_auftreten`, Pausen zu `sprechqualitaet` | Code + **offen** (Zuordnung bestätigen) |
| A5 Dreifachabzug | Regel „jede Beobachtung nur einmal" gilt. Welche Dimension die monotone Stimme führt: **offen** | offen |
| A6 Anker | `deckle_score_auf_probleme` deckt nur `sprechqualitaet`, `visuelle_aesthetik`, `audioqualitaet`, `protagonist_auftreten` ab. Bei `visuell_hook`, `schnitt_pacing`, `soundeffekte`, `spannungsbogen` entscheidet nichts | offen |
| A7 Geometrie | Kopfraum 20 % steht fest. Kinnhöhe (53 % statt „Mitte") und „Brustbild bis Taille" gegen „Gesicht = ⅓" sind **offen** | Festlegung + offen |
| N3 Rückfalltext | 20 %. Offen ist nur, ob V2 mitgeändert wird | Festlegung + offen |

## Was gestrichen werden darf — und was nicht

**Darf weg, ohne dass sich eine Bewertung ändert (~7.000 Zeichen):** Herkunftsbelege (Lauf-IDs,
Datumsangaben, Nutzerzitate, „Anlass …") — sie begründen eine Regel gegenüber dem Leser, nicht
gegenüber dem Modell; die Regel selbst bleibt jedes Mal stehen. Dazu die wortgleichen Dopplungen:
zweite und dritte Funnel-Definition, Blickkontakt-Regeln, Testreel-Tipp, CTA-`null`-Regel zweimal,
Bildausschnitt-Formatregel, 16 Dimensionsnamen beim zweiten Mal.

**Bleibt stehen:** „Die Abwesenheit von Mängeln genügt für eine 5" unter allen 14 Ankerblöcken —
gekürzt auf den dimensionsspezifischen Nachsatz, aber nicht entfernt; die Wiederholung am
Anwendungsort ist das, was gegen die Mitte-Tendenz des Modells arbeitet. Ebenso die drei kurzen
Erinnerungen zur Abgrenzung Sprech-/Audioqualität.

**Nicht ohne Entscheidung:** `top_tipps` aus dem Vertrag nehmen (wird erzeugt, seit dem 2026-09-13
nirgends angezeigt) — das ändert das Schema, nicht nur den Text.
