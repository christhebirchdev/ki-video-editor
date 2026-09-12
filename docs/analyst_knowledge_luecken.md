# Lückenanalyse der Wissensbasis

Arbeitsdokument, **kein Prompt-Bestandteil.** Es prüft `docs/analyst_knowledge.md` (428 Zeilen,
KB 0 bis KB 12) daraufhin, ob sie jede Dimension trägt, die die Analyse tatsächlich abfragt.
Stand: nach der Übertragung in `services/analyst_eval_skill_v3.md` (PROMPT_VERSION `2026-09-13a`).

Zeilenverweise „KB:nnn" meinen `docs/analyst_knowledge.md`, „V3:nnn" den V3-Skill.

---

## a) Was die Analyse überhaupt abfragt

### Bewertete Dimensionen (Score, gehen in den `performance_score` ein)

Maßgeblich sind `dimensions_scores()` in `services/analyst_eval.py` (Feld → Dimensionsname) und
`SCORE_GEWICHTE_JE_ZIEL` in `models/analyst.py` (Gewicht je Videoziel). Es sind **zwölf**:

| # | Dimension | TOFU | MOFU | BOFU |
|---|---|---|---|---|
| 1 | `sprech_hook` | 16 | 15 | 14 |
| 2 | `text_hook` (Skala 0–5, 0 = fehlt komplett) | 16 | 15 | 14 |
| 3 | `visuell_hook` | 13 | 7 | 6 |
| 4 | `struktur` | 7 | 10 | 9 |
| 5 | `spannungsbogen` | 7 | 15 | 13 |
| 6 | `schnitt_pacing` | 7 | 6 | 5 |
| 7 | `sprechqualitaet` | 8 | 10 | 9 |
| 8 | `visuelle_aesthetik` | 10 | 6 | 6 |
| 9 | `untertitel_vorhanden` | 8 | 10 | 9 |
| 10 | `untertitel_gestaltung` | 3 | 3 | 3 |
| 11 | `audioqualitaet` | 5 | 3 | 3 |
| 12 | `cta` | 0 | 0 | 9 |

### Score-freie Urteile
`energie.urteil` (`traegt` \| `flach` \| `uebertrieben`), `blickkontakt.urteil`
(`in_der_linse` \| `abgelesen` \| `unklar`), `dynamik.urteil` (`gering` \| `mittel` \| `hoch`).

### Einschätzungs- und Freitextfelder
`funnel_wirkung` (+ `_grund`), `zielgruppe`, `format`, `pausen_urteile`, `texthook_varianten`,
`texthook_maengel`, `effekt_vorschlaege`, `einblendungen`, `staerken`, `top_tipps`, `empfehlungen`.

---

## b) Abdeckung durch die Wissensbasis

Maßstab für **solide**: es gibt einen eigenen Abschnitt, Score-Anker über die ganze Skala,
mindestens ein Beispiel und eine Negativabgrenzung.
**dünn** = ein Absatz da, aber mindestens eines davon fehlt. **gar nicht** = kein Absatz.

| Dimension | KB-Fundstelle | Abdeckung | Was fehlt, damit ein Modell danach zuverlässig urteilt |
|---|---|---|---|
| `sprech_hook` | KB 1.1, 1.3–1.8, 1.10 | **solide** | — |
| `text_hook` | KB 1.1, 1.2, 1.6–1.9 | **solide** | — |
| `visuell_hook` | KB 1.1 (KB:37–41) | **dünn** | Anker nur für 5, 3, 1 — 4 und 2 fehlen. Kein Beispiel, keine Negativabgrenzung: zählt ein langsamer Push-In? Zählt die Untertitelspur, die in Sek. 1 aufpoppt, als „Einblendung"? Zählt Bewegung, die vom Fremdvideo kommt (Reaction)? Nötig: 5 Anker, je ein Positiv-/Negativ-Beispiel, und die Abgrenzung zu `dynamik` (KB:255–260 beschreibt fast dieselben Merkmale für das ganze Video). |
| `struktur` | KB 2 (KB:176–189) | **dünn** | Keine Score-Anker. KB nennt nur drei Einzelregeln (CTA, Weitschweifigkeit, Promise→Payoff) und die Bausteinkette. Nötig: Anker 5–1 („alle Bausteine greifen" bis „Reihung ohne Bogen"), ein Beispiel für eine schlüssige und eine unschlüssige Abfolge, und eine Aussage dazu, wie stark Weitschweifigkeit allein den Score drückt. |
| `spannungsbogen` | KB 7 (KB:289–291) | **dünn** | Drei Sätze, keine Anker, kein Beispiel. Nötig: Anker 5–1, eine Operationalisierung von „wo kippt sie" (woran sieht man das im Video?), und die Abgrenzung zu `struktur` — beide messen heute „hält der Bogen". |
| `schnitt_pacing` | KB 5 (KB:236–264) | **dünn** | Zirkulär: KB:237 sagt, der Schnitt werde „gegen das FORMAT bewertet (KB 12)", aber KB 12 enthält keinerlei Pacing-Erwartung je Format. Damit hat die Dimension faktisch keinen Maßstab. Nötig: je Format eine Schnittfrequenz-/Abwechslungserwartung, Anker 5–1, und die Abgrenzung zu `untertitel_gestaltung` und `visuell_hook`. |
| `sprechqualitaet` | KB 4 (KB:213–232) | **dünn** | Leitfrage und Ton-Kriterien sind gut, **Score-Anker fehlen komplett.** Nötig: Anker 5–1 entlang der Leitfrage („jedes Wort mühelos" bis „muss zurückspulen"), und eine Schwelle, ab wann WPM/Füllwörter „stark auffällig" sind — KB:218–219 sagt nur „STARKE Abweichungen", ohne Zahl. |
| `visuelle_aesthetik` | KB 8.1–8.6 | **solide** | (Aber: zwei innere Widersprüche, siehe c-2 und c-3.) |
| `untertitel_vorhanden` | KB 9 (KB:358–363) | **dünn** | Inhaltlich klar (Pflicht, vereinzelte Lücken kein Mangel), aber **die Wissensbasis kennt die Zwei-Score-Aufteilung gar nicht.** Nötig: die Anker, die heute nur im Skill stehen (5 = lückenlos, 3 = in Teilen, 1 = so gut wie gar nicht) plus eine Schwelle für „in Teilen". |
| `untertitel_gestaltung` | KB 9 (KB:365–373) | **dünn** | Mängel-Katalog vorhanden, aber keine Anker und keine Verrechnung: Wie viele der sechs Mängel ergeben welchen Score? Ist `timing` so schwer wie `groesse`? Nötig: Anker 5–1 mit Mängelzahl, und je Kategorie ein sichtbares Beispiel. |
| `audioqualitaet` | KB 4 (KB:221–230) | **dünn** | Die Inhalte (Störgeräusche, Mikrofonabstand, Musikbalance) sind da — aber in KB 4 **als Teil des `sprechqualitaet`-Scores** (KB:217: „Tempo, Deutlichkeit und TONQUALITÄT ergeben EINEN Score"). Die Wissensbasis kennt `audioqualitaet` als eigene Dimension nicht. Nötig: ein eigener Abschnitt mit Anker 5–1 und eine ausdrückliche Trennung, welcher Befund wohin gehört (siehe c-1). |
| `cta` | KB 2 (KB:182–184) | **dünn** | Der neue Bedürfnis-Absatz erklärt hervorragend, **wozu** ein CTA da ist, sagt aber nichts darüber, **wie man ihn bewertet.** Keine Anker, kein Beispiel für konkret vs. unkonkret (das steht nur im Skill, V3:581–582), und ein direkter Widerspruch zur Score-Definition (siehe c-4). Nötig: Anker 5–1, Positiv-/Negativ-Beispiel je Funnelstufe, und eine Regel für den Fall „Format braucht keinen CTA". |
| `energie` | KB 10.1 (KB:382–387) | **dünn** | `traegt` und `flach` sind definiert, **`uebertrieben` überhaupt nicht** — der dritte zulässige Wert hat keine Definition und kein Beispiel. Nötig: Definition und Beispiel für `uebertrieben`, plus die Abgrenzung zu `sprechqualitaet` (die steht heute nur im Skill). |
| `blickkontakt` | KB 10.2 (KB:389–396) | **solide** | — (klare Regel, Unklar-Default, Format-Ausnahme verlinkt) |
| `dynamik` | KB 5 (KB:255–260) | **solide** | — (drei Stufen konkret beschrieben, ausdrücklich kein Qualitätsurteil) |
| `funnel_wirkung` | KB 11 (KB:400–409) | **solide** | — nach der Überarbeitung inklusive Videolängen und KPIs. Ein Rest bleibt: kein Tiebreaker für den Überlappungsbereich 30–60 s (siehe c-6). |
| `zielgruppe` | — | **gar nicht** | Die Wissensbasis sagt nirgends, **woran** man die Zielgruppe im Video erkennt oder wie der eine Satz aussehen soll. „Zielgruppe" kommt nur als Bezugsgröße anderer Regeln vor (KB:95 Trennschärfe, KB:194 Sprachniveau, KB:405 TOFU). Nötig: die Signale (Ansprache, Vokabular, gezeigtes Problem, Setting), zwei bis drei Musterformulierungen, und die Regel, wie breit/eng der Satz sein darf. |
| `format` | KB 12 (KB:413–428) | **dünn** | Nur drei Regeln (Bildausschnitt, Reaction-Blick, Reaction-Pausen). Die Ausbaustelle ist im Dokument selbst markiert (KB:426–428). |
| `pausen_urteile` | KB 6 (KB:268–285) | **solide** | — |
| `texthook_varianten` | KB 1.4 + 1.7 | **solide** | — (Mechanik-Liste plus Längenregel) |
| `einblendungen` / `effekt_vorschlaege` | KB 5 (KB:244–248, 262–264) | **dünn** | Wann eine Einblendung lohnt, steht da; **wie viele und wie oft** nicht. Nötig: eine Dichte-Erwartung („nicht mehr als X pro 30 s") — heute steht dem nur das vage Referenz-P10 „keine Reizüberflutung" gegenüber. |

**Kurzfassung:** 4 von 12 bewerteten Dimensionen sind solide (`sprech_hook`, `text_hook`,
`visuelle_aesthetik` und — score-frei — `blickkontakt`/`dynamik`). 8 von 12 sind dünn, davon sieben
**ohne jeden Score-Anker**: `struktur`, `spannungsbogen`, `schnitt_pacing`, `sprechqualitaet`,
`untertitel_vorhanden`, `untertitel_gestaltung`, `audioqualitaet`, `cta`. Genau die drei neuen
Dimensionen `cta`, `audioqualitaet` und `untertitel_gestaltung` sind die am schwächsten getragenen.
Ein Freitextfeld (`zielgruppe`) ist gar nicht abgedeckt.

---

## c) Widersprüche, Überschneidungen und zu vage Stellen

### c-1 `audioqualitaet` und `sprechqualitaet` beurteilen dieselben Befunde — und der Skill verbietet das
KB:217 sagt: „Tempo, Deutlichkeit und TONQUALITÄT ergeben EINEN Score." Darunter (KB:221–230)
stehen Störgeräusche, Mikrofonabstand und Musikbalance. Exakt dieselben drei Punkte definieren im
Skill die eigene Dimension `audioqualitaet` (V3:551–564, und die Schema-Zeile `"audioqualitaet"`).
Gleichzeitig gilt die Regel „JEDE BEOBACHTUNG NUR EINMAL — in genau der Dimension, zu der sie am
besten passt" (V3:311).
**Folge:** Für „es hallt" gibt es zwei zuständige Felder und keine Entscheidungsregel. Das Modell
schreibt den Befund entweder doppelt (dann baut das System zwei Empfehlungen für eine Sache — der
im Skill dokumentierte Fehler aus Lauf 26a1adbf) oder gar nicht.
**Vorschlag zur Entscheidung durch den Nutzer:** `sprechqualitaet` = alles, was die *Person* macht
(Tempo, Deutlichkeit, Füllwörter, Betonung); `audioqualitaet` = alles, was die *Aufnahme* macht
(Störgeräusche, Hall, Mikrofonabstand, Musikbalance). Dann müsste KB:217 umformuliert werden —
das habe ich **nicht** getan, weil es eine inhaltliche Entscheidung ist und nicht aus der
Wissensbasis ableitbar.

### c-2 KB 8.5 und KB 8.6 lassen zwei Fälle offen (die vermutete Überschneidung ist keine, die Lücke schon)
KB 8.5 (KB:331–343) definiert die **Schwelle** („was ist ein Mangel"), KB 8.6 (KB:345–354) die
**Menge** („wie viele Mängel ergeben welchen Score"). Das greift sauber ineinander — ein
Widerspruch ist es nicht. Zwei Fälle fallen aber durch:
- **Genau EIN deutlicher Mangel.** Anker 2 verlangt „MEHRERE deutliche Mängel", Anker 3 verlangt
  „nichts stört massiv". Ein einzelner massiver Mangel passt in keinen der beiden.
- **Zwei Hinweise.** KB:354 sagt ausdrücklich: „Zwei Hinweise sind keine 2 — und auch keine 3",
  also mindestens 4. Anker 4 erlaubt aber nur „**ein** Punkt schwächer" und nennt als Beispiel
  „leicht unruhiger Hintergrund" — das ist genau ein Hinweis. Zwei Hinweise erfüllen damit weder
  Anker 4 noch Anker 5 („ruhiger Hintergrund").
**Nötig:** Anker 4 auf „ein bis zwei Hinweise" erweitern und einen expliziten Satz für den
Einzelmangel ergänzen („ein einzelner deutlicher Mangel = 3").

### c-3 Safe Zone (KB 8.1) und Kopfraum (KB 8.2) lassen der Text-Hook geometrisch keinen Platz
- KB:302: oben **13 %** der Bildhöhe sind zu meiden.
- KB:313: Kopfraum ca. **10–15 %** Luft über dem Kopf, ausdrücklich begründet mit „genug Platz für
  eine Texthook, die NICHT auf der Stirn klebt".
- KB:161–162: die Text-Hook gehört „im oberen Bereich, aber innerhalb der SAFE ZONE".
Bei 10–15 % Kopfraum liegt der freie Streifen über dem Kopf vollständig **innerhalb** der oberen
13 %-Sperrzone. Die Text-Hook müsste also gleichzeitig über dem Kopf und unterhalb von 13 % sitzen —
das geht nicht. Entweder muss der geforderte Kopfraum steigen (Richtung 20–25 %) oder die Text-Hook
darf unter die Kopfoberkante rutschen. **Das ist eine Entscheidung für den Nutzer, nicht für mich.**

### c-4 `cta`: „kein Mangel" (KB 2) gegen „Fehlt er ganz: 1" (Skill/Schema)
KB:182 und V3:300–302: „Fehlender CTA ist KEIN Mangel/Abzug, wenn das Format ihn nicht braucht."
V3:580 und die Schema-Zeile `"cta"`: „Gibt es einen? Fehlt jede Aufforderung, ist das eine 1."
Beides steht heute nebeneinander im selben Prompt. Entschärft ist es nur rechnerisch: `cta` hat bei
TOFU und MOFU Gewicht 0 (`SCORE_GEWICHTE_JE_ZIEL`), der Score fließt dort also nicht ein. Sichtbar
bleibt er trotzdem — der Nutzer eines TOFU-Videos sieht eine 1 für etwas, das laut demselben Prompt
kein Mangel ist. **Nötig:** eine Regel wie „braucht das Format keinen CTA, ist `cta` = null" oder
ein ausdrücklicher Einordnungssatz im `kommentar`, der die 1 relativiert.

### c-5 Die `betrifft`-Listen im Skill kennen nur 7 bzw. 8 der 12 Dimensionen
- `empfehlungen[].betrifft` (V3:710–711) nennt 7 Namen — es fehlen `visuell_hook`,
  `untertitel_vorhanden`, `untertitel_gestaltung`, `audioqualitaet`, `cta`.
- `staerken[].betrifft` in den Harten Regeln (V3:748–751) nennt 8 — es fehlen die vier
  Stufe-2-Dimensionen.
- Die JSON-Schema-Zeile für `staerken` nennt dagegen alle **12**.
**Folge:** Die Regel „Zu JEDER Dimension mit Score 3 oder schlechter gehört eine eigene Empfehlung"
(V3:707) ist für `cta`, `audioqualitaet` und die beiden Untertitel-Dimensionen nicht erfüllbar —
das Modell hat keinen zulässigen `betrifft`-Wert dafür und das System setzt seinen schwächeren
Standardsatz ein. Das ist eine **Skill-Baustelle, keine Lücke der Wissensbasis**, aber sie trifft
genau die Dimensionen, die oben als dünn markiert sind. `models/analyst.py:485` akzeptiert die
Namen bereits („Name aus SCORE_GEWICHTE_JE_ZIEL"), es fehlt nur die Aufzählung im Prompt.

### c-6 Funnel: Längenkorridore überlappen, `Mischung` hat kein Feld
- KB:405 (neu): TOFU „zwischen 7 und 60 Sekunden". KB:406: MOFU „~30–90 s". Zwischen 30 und 60
  Sekunden entscheidet die Länge nichts; einziges Unterscheidungsmerkmal bleibt die thematische
  Tiefe. Das ist verwendbar, sollte aber ausdrücklich dastehen, sonst begründet das Modell
  `funnel_wirkung` mit der Länge, obwohl sie in diesem Korridor nichts trägt.
- KB:409 führt **`Mischung`** als vierten Wert. Der Skill lässt in `funnel_wirkung` aber nur
  TOFU/MOFU/BOFU zu (V3:44–45), und `funnel` ist die unveränderte Nutzerangabe. `Mischung` hat
  damit kein Feld, in das es geschrieben werden könnte. Beim Übertragen habe ich einen klarstellenden
  Halbsatz ergänzt („das Feld `funnel_wirkung` lässt weiterhin nur TOFU, MOFU oder BOFU zu") —
  falls `Mischung` ein echter Ausgabewert werden soll, ist das eine Modell- und Code-Änderung.
- Die alte Skill-Fassung sagte TOFU „kürzer als ~20 s", die neue KB-Fassung „7 bis 60 s". Das ist
  die deutlichste inhaltliche Verschiebung der ganzen Überarbeitung: Ein 45-Sekunden-Video ohne
  Tiefe kippt damit von MOFU nach TOFU — und mit ihm das gesamte Gewichtsprofil
  (`visuell_hook` 7→13, `spannungsbogen` 15→7). Wer A/B-Läufe vergleicht, muss das wissen.

### c-7 Zu vage für eine Bewertung
- **KB:218–219** „Auffällig sind nur STARKE Abweichungen: monoton, viele Füllwörter, undeutlich."
  Ohne Schwelle ist „viele" nicht prüfbar. Die Sprachstatistik liefert Zahlen — es fehlt der
  Korridor (z.B. WPM-Band, Füllwörter pro Minute).
- **KB:238** „Lieber vorsichtig als falsch" beim Schnitt-Score. In Kombination mit dem fehlenden
  Format-Maßstab (c, Zeile `schnitt_pacing`) ist das eine Einladung, immer 3 zu vergeben — genau
  das Muster, das KB 8.5 (KB:340–343) für die visuelle Ästhetik dokumentiert und dort bewusst
  abgestellt wurde. Dieselbe Gegenmaßnahme fehlt bei `schnitt_pacing`.
- **KB:290** „Wo kippt sie, und endet das Video zeitnah danach?" — „zeitnah" ist nicht
  operationalisiert (Sekunden? Anteil der Gesamtlänge?).
- **KB:159** Einblendungsdauer der Text-Hook „mindestens 5 Sekunden" gegen **KB:16–17** CTR
  entscheidet sich in den „ersten ~1–5 s". Kein Widerspruch, aber es heißt faktisch: die Text-Hook
  muss über das gesamte entscheidende Fenster hinaus stehen bleiben. Das sollte dastehen, sonst
  liest das Modell die 5 s als Obergrenze.

### c-8 Formfehler in der Wissensbasis (beim Übertragen repariert, in der Quelle noch offen)
- **KB:208**: „konkrete Alltagssprache statt AbstraktaVersteht die Zielgruppe die Sprache…" — hier
  sind zwei Sätze ohne Zeilenumbruch verklebt. Im Skill sauber getrennt.
- **KB:194**: „für die dümmsten Person" (Grammatik). Im Skill als „die dümmste Person" übernommen.
- **KB:407**: „Savas" (gemeint: Saves), „KPI's". Im Skill korrigiert.
- **KB:194** ändert außerdem den Maßstab von „breite Masse" (alte Skill-Fassung) auf „dümmste Person
  **innerhalb der Zielgruppe**". Das ist eine echte inhaltliche Verengung und wurde als solche
  übernommen.
- Die alte Skill-Fassung dämpfte bei unverständlicher Sprache die „Hook-/Struktur-Relevanz", die
  KB-Fassung „Skript und Struktur-Bewertung". Beim Übertragen zu „Hook-, Skript- und
  Struktur-Bewertung" zusammengeführt, damit die Hook-Wirkung nicht verloren geht.

---

## Empfohlene Reihenfolge für den Ausbau

1. **c-1 entscheiden** (`sprechqualitaet` vs. `audioqualitaet`) — betrifft zwei Dimensionen mit
   zusammen 11–13 Gewichtspunkten und kollidiert mit einer harten Prompt-Regel.
2. **Score-Anker für die sieben ankerlosen Dimensionen** — das ist die größte Einzellücke; ohne
   Anker urteilt das Modell nachweislich zur Mitte (der 3er-Befund aus KB:340–343).
3. **c-4 CTA-Widerspruch auflösen** und die CTA-Bewertungsanker nachziehen — `cta` wiegt bei BOFU 9.
4. **`zielgruppe` überhaupt beschreiben** — das Feld steht im Output ganz oben und hat null Regeln.
5. **KB 12 füllen** (die im Dokument markierte Ausbaustelle) — damit fällt zugleich der zirkuläre
   Format-Verweis in KB 5 weg.
6. **c-3 Geometrie** und **c-2 Anker-Lücken** in der visuellen Ästhetik.
7. **c-5** ist eine reine Skill-Korrektur und unabhängig von der Wissensbasis erledigbar.

## Offene Fragen an den Nutzer

1. `sprechqualitaet` vs. `audioqualitaet`: Gilt der Vorschlag aus c-1 (Person vs. Aufnahme)?
   Falls ja, muss KB:217 geändert werden.
2. Kopfraum vs. Safe Zone (c-3): Soll der Kopfraum steigen oder darf die Text-Hook tiefer sitzen?
3. CTA bei Formaten ohne Verkaufsabsicht (c-4): Score `null` oder weiterhin 1 mit Einordnungssatz?
4. `Mischung` als Funnel-Wert (c-6): rein gedankliche Kategorie oder soll das ein Ausgabewert werden?
