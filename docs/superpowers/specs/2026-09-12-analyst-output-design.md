# Analyst-Output: Zielsteuerung, Vier-Kategorien-Struktur, Priorisierung nach Schwere

Stand 2026-09-12. Entscheidungen von Chris in der Brainstorming-Session vom 2026-09-11/12.
Betrifft: Eingabemaske vor der Analyse, Bewertungsschema, Nachbearbeitung, Frontend-Darstellung.

## Problem

Der heutige Output hat vier Schwachstellen, die im Code belegbar sind:

1. **Die Top-3-Handlungsempfehlungen sind nach Zeitpunkt sortiert, nicht nach Schwere.**
   `verteile_empfehlungen()` in `services/analyst_eval.py` sortiert `schritte.sort(key=lambda t: t[0])`
   — das ist `zeitpunkt_sek`. Ein kosmetischer Tipp bei Sekunde 2 verdraengt einen gravierenden
   Mangel bei Sekunde 20.
2. **Der Analyst kennt das Ziel des Videos nicht.** `funnel` ist heute ein Ausgabefeld: Das Modell
   raet die Funnel-Stufe. Ein Video, das Reichweite erzeugen soll, wird nach denselben Massstaeben
   bewertet wie eines, das Anfragen erzeugen soll.
3. **Der Output hat keine durchgaengige Gliederung.** Lob ist eine flache Liste, Empfehlungen eine
   zweite, die Einzelscores liegen in einem Sammel-Aufklapper unten. Drei Ordnungen im selben Ergebnis.
4. **Audioqualitaet wird gemessen, aber nicht bewertet.** `lufs_integrated`, `true_peak_db` und
   `loudness_range` liegen in `QualityMetrics`, `audio_overview` als Freitext — es gibt keinen Score,
   kein Gewicht und keine Empfehlungslogik dafuer.

## Nicht-Ziele

- Keine Aenderung an der Pipeline (Whisper, Gemini-Frames, Messwerte). Nur Eingabe, Bewertung,
  Nachbearbeitung und Darstellung.
- Keine Zeitachsen-Visualisierung der Empfehlungen. Separat entscheiden, wenn die Struktur steht.
- Keine Vorher/Nachher-Vergleiche zwischen Laeufen desselben Creators.

---

## 1. Eingabe vor der Analyse

| Feld | Status | Pflicht |
|---|---|---|
| Video | vorhanden | ja |
| Format (`FORMATE`) | vorhanden | ja |
| Geplante Texthook | vorhanden | nein |
| **Videoziel** | neu | **ja** |
| **Brand-/Zielgruppen-Datei** | neu | nein |

### 1.1 Videoziel

Dropdown in Nutzersprache, Funnel-Stufe nur intern:

| Anzeige | intern |
|---|---|
| Neue Menschen erreichen | TOFU |
| Vertrauen und Expertenstatus aufbauen | MOFU |
| Kundenanfragen gewinnen | BOFU |

Kein Eintrag "Mischung". Wer alles auswaehlen kann, bekommt kein scharfes Urteil, und das Modell
bekommt eine Ausrede, sich nicht festzulegen.

Neues Feld `AnalystResult.gewaehltes_ziel`. Wie `gewaehltes_format` ist die Nutzerangabe fuer die
Bewertung bindend; das Modellfeld `funnel` wird vom Code ueberschrieben.

**Altlaeufe:** `gewaehltes_ziel == ""` → die heutigen Gewichte aus `SCORE_GEWICHTE` gelten
unveraendert. Dasselbe Muster wie bei `visuell_hook`: Gespeicherte Ergebnisse aendern sich
rueckwirkend nicht, und `tools/replay_nachbearbeitung.py` behaelt seine Basis.

### 1.2 Brand-/Zielgruppen-Datei

Optionaler Upload. Zweck, in dieser Reihenfolge:

1. **Texthook-Varianten in der Sprache des Nutzers.** `texthook_varianten` (max. 9 Woerter) sind ohne
   Kontext generisch. Das ist der konkreteste Nutzen.
2. **Zielgruppen-Abgleich** — siehe 3.2.
3. **Sprachlevel-Pruefung.** Zu viel Fachjargon fuer eine kalte Zielgruppe oder zu flach fuer eine
   fortgeschrittene ist ohne Zielgruppenwissen nicht beurteilbar.
4. **CTA-Passung bei BOFU.** Passt der Call zum Angebot, das in der Datei steht.
5. **Positionierungs-Konflikte.** Aussagen im Video, die der eigenen Positionierung widersprechen.

**Geltungsbereich — verbindlich:** Der Kontext wirkt NUR auf Zielgruppe, Relevanz, Ansprache,
Texthook-Varianten und CTA-Passung. Er wirkt NICHT auf Hook-Mechanik, Schnitt, Pacing, Licht, Ton,
Untertitel-Gestaltung oder Bildqualitaet. Grund: Eine hochglanzpolierte Brand-Datei darf handwerkliche
Urteile nicht verwaessern ("passt ja zur Marke"). Technisch: eigener Prompt-Abschnitt mit explizit
formuliertem Geltungsbereich.

**Formate:**

| Format | Weg | Aufwand |
|---|---|---|
| `.md`, `.txt` | direkt einlesen | keiner |
| `.pdf` | als File-Part an Gemini durchreichen | keiner, `google-genai` ist installiert |
| `.docx` | `python-docx`, Text extrahieren | eine kleine Dependency |

Hinweistext am Feld: "Am besten .md oder .txt — PDF und Word gehen auch."
Limit 2.000 Woerter, danach abschneiden mit sichtbarem Hinweis im Ergebnis. Ein 40-seitiges
Brand-Manual verdraengt sonst im Prompt die Videodaten.

### 1.3 Cache-Key

`services/analyst_cache.py`: Der Key waechst um das Ziel und den Hash der Brand-Datei.

    sha256(Datei) + format + ziel + brand_hash + planned_text_hook + engine + PROMPT_VERSION

Ohne diese Erweiterung liefert derselbe Clip mit anderem Ziel das alte Ergebnis — bei
zielabhaengigen Gewichten ein sichtbarer Fehler. `brand_hash` ist leer, wenn keine Datei anliegt.

---

## 2. Die vier Kategorien

Eine Gliederung ordnet Lob, Empfehlungen und Detailscores. Keine konkurrierenden Ordnungen mehr.

| Kategorie | Bewertete Dimensionen |
|---|---|
| **1. Hook** | Sprech-Hook, Text-Hook, Visuelle Hook |
| **2. Mittelteil** (Aufmerksamkeit halten) | Spannungsbogen, Struktur, Untertitel *vorhanden* |
| **3. Editing** | Schnitt & Pacing, Untertitel-*Gestaltung* |
| **4. Auftreten, Bild & Ton** | Sprechqualitaet, Bildqualitaet, Audioqualitaet |
| (ausserhalb der Bloecke) | CTA — nur bei BOFU gewichtet, angezeigt im Mittelteil |

Score-freie Urteile (`energie`, `blickkontakt`, `dynamik`) bleiben wie heute: kein Score, koennen aber
eine Empfehlung ausloesen. `energie` und `blickkontakt` erscheinen in Kategorie 4, `dynamik` in
Kategorie 3.

### 2.1 Untertitel: zwei Fragen, zwei Dimensionen

Dasselbe Wort, zwei getrennte Urteile:

- **Untertitel vorhanden** → Mittelteil. Retention: Leute schauen ohne Ton.
- **Untertitel-Gestaltung** (Platzierung, Wortanzahl, Lesbarkeit, Timing) → Editing. Handwerk am
  Schnittplatz. Speist sich aus `UNTERTITEL_MANGEL_ARTEN`.

**Aenderung gegenueber heute:** Der Docstring von `UntertitelEval` sagt derzeit *"vorhanden=False ist
KEIN Mangel: Ohne Untertitel zu arbeiten ist eine Formatentscheidung."* Diese Entscheidung wird
umgedreht, mit Bedingung:

| Zustand | untertitel_vorhanden | Folge |
|---|---|---|
| `speech_stats.wort_anzahl == 0` | `None` | nicht bewertbar, Gewicht verteilt sich proportional |
| gesprochen, keine Untertitel | `1` | **kritischer Mangel**, erzwungene Empfehlung |
| gesprochen, Untertitel vorhanden | Modellurteil 1–5 | Gestaltung wird separat bewertet |

Die Bedingung wird im Code geprueft, nicht im Prompt. `wort_anzahl` ist ein deterministischer
Whisper-Messwert; eine Prompt-Regel waere wieder nur eine Bitte.

Untertitel-Gestaltung ist `None`, wenn keine Untertitel vorhanden sind — es gibt nichts zu gestalten.

### 2.2 Audioqualitaet (neu)

Neue Dimension `audioqualitaet: ScoreProbleme`. Grundlage: `QualityMetrics.lufs_integrated`,
`true_peak_db`, `loudness_range` plus `audio_overview`. `None`, wenn kein Audio vorliegt.

### 2.3 CTA (neu)

Neue Dimension `cta: ScoreKommentar`. Heute steckt der CTA als Bool in `StrukturElemente` — damit
laesst sich nicht unterscheiden, ob eine schwache Struktur oder ein fehlender CTA den Score gedrueckt
hat, und die erzwungene Empfehlung wird unscharf. `StrukturElemente.cta` bleibt als Beobachtung
bestehen; die Bewertung zieht um.

Gewicht 0 bei TOFU und MOFU: Die Dimension wird dort weder bewertet noch angezeigt.

---

## 3. Bewertung

### 3.1 Gewichte je Ziel

`SCORE_GEWICHTE` wird zu `SCORE_GEWICHTE_JE_ZIEL: dict[str, dict[str, int]]`. Summe je Ziel = 100.

| Dimension | Kategorie | TOFU | MOFU | BOFU |
|---|---|---|---|---|
| Sprech-Hook | Hook | 16 | 15 | 14 |
| Text-Hook | Hook | 16 | 15 | 14 |
| Visuelle Hook | Hook | 13 | 7 | 6 |
| **Hook gesamt** | | **45** | **37** | **34** |
| Spannungsbogen | Mittelteil | 7 | 15 | 13 |
| Struktur | Mittelteil | 7 | 10 | 9 |
| Untertitel vorhanden | Mittelteil | 8 | 10 | 9 |
| **Mittelteil gesamt** | | **22** | **35** | **31** |
| Schnitt & Pacing | Editing | 7 | 6 | 5 |
| Untertitel-Gestaltung | Editing | 3 | 3 | 3 |
| **Editing gesamt** | | **10** | **9** | **8** |
| Sprechqualitaet | Auftreten | 8 | 10 | 9 |
| Bildqualitaet | Auftreten | 10 | 6 | 6 |
| Audioqualitaet | Auftreten | 5 | 3 | 3 |
| **Auftreten gesamt** | | **23** | **19** | **18** |
| CTA | — | 0 | 0 | 9 |
| **Summe** | | **100** | **100** | **100** |

Herleitung aus den Vorgaben: Bei TOFU ist die Hook am wichtigsten, alle drei Ebenen (45, hoechste
Summe, Ebenen dicht beieinander), der Spannungsbogen am unwichtigsten (7), die Bildqualitaet hoch (10).
Bei MOFU faellt die visuelle Hook (13 → 7), Sprech- und Text-Hook bleiben hoch (15/15), der
Spannungsbogen steigt deutlich (7 → 15), die Bildqualitaet sinkt (10 → 6). BOFU entspricht MOFU,
ergaenzt um den CTA (9).

**Nonverbale Videos brauchen keine Sonderregel.** `berechne_performance_score()` verteilt das Gewicht
nicht bewertbarer Dimensionen bereits proportional auf den Rest. Faellt bei einem stummen Video der
Sprech-Hook (16), die Sprechqualitaet (8) und beide Untertitel-Dimensionen (11) weg, steigt die
Bildqualitaet von 10 auf rund 15. Das ist der gewuenschte Effekt ohne zusaetzliche Logik. Erst
nachschaerfen, wenn ein echter Lauf zeigt, dass es nicht reicht.

### 3.2 Zielgruppen-Abgleich

`zielgruppe` bleibt: Wen spricht dieses Video an — unabhaengig von der hochgeladenen Datei. Neue
Formulierung im Frontend: "Dieses Video spricht an: …".

Neues Feld `zielgruppen_abgleich: str` mit Werten aus einer festen Liste:

| Wert | Anzeige |
|---|---|
| `trifft_kern` | kein Hinweis |
| `teilweise` | Hinweis |
| `breiteres_publikum` | Hinweis |
| `andere_zielgruppe` | Hinweis |

Enum statt Freitext, gleiches Muster wie `BlickEval` und `DynamikEval`. Freitext laedt zum
Schmeicheln ein ("spricht die Zielgruppe sehr gut an"); ein Enum-Wert nicht. Das Feld bleibt leer,
wenn keine Brand-Datei hochgeladen wurde.

### 3.3 Lob ohne Halluzination

Prompt-Anweisungen wie "erfinde kein Lob" halten nicht — belegt in `docs/offene-fixes-analyst.md`,
P2: Der Redundanz-Check wurde zum Boilerplate, weil er Pflicht ohne Schwelle war. Deshalb im Code:

> Eine Staerke in Kategorie X wird nur angezeigt, wenn mindestens eine Dimension dieser Kategorie
> einen Score >= 4 hat. Hoechstens 2 Staerken pro Kategorie. Das Modell muss zusaetzlich angeben,
> auf welche Dimension sich die Staerke bezieht (Feld `betrifft`, wie bei `Empfehlung`).

`staerken: list[str]` wird zu `staerken: list[Staerke]` mit `{text, betrifft}`. Der Code gruppiert
nach Kategorie und filtert, was die Schwelle reisst. Ist die Hook auf allen drei Ebenen <= 3,
verschwindet der Hook-Block aus dem Lob — ohne dass das Modell sich zusammenreissen muss.

### 3.4 Priorisierung der Handlungsempfehlungen

**Schwere = Gewicht der Dimension x (1 - normalisierter Score).**

Beispiel: Sprechqualitaet bei MOFU, Gewicht 10, Score 2 → normalisiert (2-1)/4 = 0,25 → 10 x 0,75 = 7,5.

Sortierung in `verteile_empfehlungen()`:

1. Kritische Maengel (Definition unten) — immer zuerst
2. Hook-Schritte — behalten Vorrang (bestehende Vorgabe)
3. Uebrige nach Schwere absteigend
4. Bei gleicher Schwere: frueherer Zeitpunkt zuerst

`MAX_SAMMEL_OBEN` und `NUR_UNTEN` bleiben unveraendert.

**Kritischer Mangel** — deterministisch, im Code pruefbar:

- Score <= 2 in einer Dimension mit Gewicht >= 10 beim gewaehlten Ziel, **oder**
- ein Hook-Score <= 2, **oder**
- Text-Hook fehlt vollstaendig, **oder**
- es wird gesprochen und es gibt keine Untertitel.

Ein kritischer Mangel erzwingt einen Platz in den Top 3. Gibt es mehr als drei, rutscht der mit der
geringsten Schwere nach "Erweitert".

**Weniger als drei Schritte sind erlaubt.** Gibt es keinen kritischen Mangel und liegt der Score
bei >= 85, zeigt das Frontend nur die Schritte, die es wirklich gibt. Heute erzwingt der Code drei —
Lauf 56748c94 (Score 92, alle Dimensionen 4–5) hatte als Schritt 1 "Ersetze das Text-Overlay
'16 LITER'", also den Umbau des staerksten Elements im Video.

### 3.5 PROMPT_VERSION

Betriebsregel aus `project_analyst_cache.md` gilt: Nach der Prompt-Aenderung die `PROMPT_VERSION` in
`services/analyst_eval.py` hochzaehlen. Sonst liefert der Cache Ergebnisse der alten Bewertungslogik.

---

## 4. Frontend

    (1) 78 · gemessen an: Neue Menschen erreichen
    (2) Dieses Video spricht an: [1-2 Saetze]
        ! Zielgruppen-Hinweis  (nur bei Brand-Datei und Wert != trifft_kern)
    (3) Das laeuft schon gut
        Hook · Mittelteil · Editing · Auftreten   — je 0-2 Punkte, Score >= 4 Pflicht
    (4) Deine 3 wichtigsten Schritte
        [Sek. 2] ...   [Sek. 14] ...   [Sek. 27] ...
        "Mehr Optimierungen? Frag unten im Chat."
    (5) Hook >   Mittelteil >   Editing >   Auftreten, Bild & Ton >
        (je Kategorie: Scores der Dimensionen + ausfuehrliches Feedback)

**Score-Label:** "78 · gemessen an: <Ziel>" statt "Performance-Score von 100". Mit zielabhaengigen
Gewichten sind zwei Videos mit unterschiedlichem Ziel nicht mehr vergleichbar; ohne das Label
vergleicht der Nutzer sie trotzdem und merkt es nicht.

**Entfaellt:** Der Sammel-Aufklapper "Detaillierte Analyse und Feedback". Seine sieben `ScoreChip`
werden auf die vier Kategorie-Aufklapper verteilt und um Untertitel (2x), Audioqualitaet und CTA
ergaenzt. `top_tipps` wird nach Kategorie aufgeteilt und erscheint im jeweiligen Aufklapper.

**Bleibt:** `weitere_empfehlungen` als eigener Aufklapper, Chat-Panel, `Feedback`-Komponente pro
Block, Cache-Hinweis, Audio- und Blick-Uebersicht, Segment-Dump.

**Chat:** Der Hinweis unter den drei Schritten zeigt auf das bestehende Chat-Panel. Es hat bereits
Zugriff auf die Analyse; keine Aenderung noetig.

---

## 5. Schema-Aenderungen im Ueberblick

Neu in `models/analyst.py`:

| Feld | Typ | Quelle |
|---|---|---|
| `AnalystResult.gewaehltes_ziel` | `str` | Nutzer |
| `AnalystResult.brand_kontext_datei` | `str` | Nutzer (Dateiname) |
| `AnalystResult.brand_kontext_gekuerzt` | `bool` | Code |
| `AnalystEvaluationV2.zielgruppen_abgleich` | `str` (Enum) | Modell |
| `AnalystEvaluationV2.audioqualitaet` | `ScoreProbleme` | Modell + Messwerte |
| `AnalystEvaluationV2.cta` | `ScoreKommentar` | Modell |
| `UntertitelEval.score` | `Optional[int]` | Modell/Code |
| `UntertitelEval.gestaltung_score` | `Optional[int]` | Modell |
| `Staerke` | `{text, betrifft}` | Modell |
| `ZIELE` | Konstante | — |
| `SCORE_GEWICHTE_JE_ZIEL` | Konstante | — |

Alle neuen Felder haben Defaults, damit gespeicherte Altlaeufe unveraendert laden.

## 6. Grenze Modell / Code

Unveraendertes Prinzip: Das Modell urteilt, der Code rechnet.

| Modell | Code |
|---|---|
| Scores je Dimension | Performance-Score aus Scores x Gewichten |
| `betrifft` je Staerke und Empfehlung | Zuordnung zu Kategorien |
| `zielgruppen_abgleich` (Enum) | ob ein Hinweis erscheint |
| `gruppe` je Empfehlung | Buendeln, sortieren, Top-3 abtrennen |
| Urteile zu Pausen, Blick, Energie, Dynamik | Schwere, kritische Maengel, Lob-Schwelle |
| — | Untertitel-Pflicht aus `wort_anzahl` |

## 7. Tests

- `tests/test_analyst_gewichte.py`: Summe je Ziel = 100; Altlauf ohne Ziel nutzt die alten Gewichte;
  nonverbales Video verteilt Gewicht proportional.
- `tests/test_analyst_prioritaet.py`: Schwere-Sortierung; kritischer Mangel erzwingt Platz; weniger
  als drei Schritte bei Score >= 85 ohne kritischen Mangel.
- `tests/test_analyst_lob.py`: Staerke unter Schwelle wird gefiltert; max. 2 pro Kategorie.
- `tests/test_analyst_untertitel.py`: `wort_anzahl == 0` → `None`; gesprochen ohne Untertitel →
  kritischer Mangel.
- `tests/test_analyst_cache.py` erweitern: unterschiedliches Ziel = unterschiedlicher Key.
- `tools/replay_nachbearbeitung.py` gegen alle gespeicherten Laeufe: Score-Diff muss 0 sein, solange
  kein Ziel gesetzt ist.

Umgebungshinweis aus `project_analyst_cache.md`: Tests laufen in der Linux-VM der Geraete-Shell, nicht
ueber das Mac-venv.

## 8. Offene Punkte

Keine. Die Gewichtstabelle ist eine begruendete Setzung aus den Vorgaben, kein Messwert — sie wird
nach den ersten Echtlaeufen ueberprueft.

## 9. Betroffene Dateien

| Datei | Aenderung |
|---|---|
| `models/analyst.py` | neue Felder, `ZIELE`, `SCORE_GEWICHTE_JE_ZIEL`, `Staerke` |
| `services/analyst_eval.py` | Gewichte je Ziel, Schwere-Sortierung, kritische Maengel, Lob-Filter, `PROMPT_VERSION` |
| `services/analyst_eval_skill.md` | Prompt: Ziel als Fakt, Brand-Kontext mit Geltungsbereich, neue Dimensionen, `betrifft` bei Staerken |
| `services/analyst_cache.py` | Ziel und Brand-Hash im Cache-Key |
| `api/analyst.py` | Ziel-Pflichtfeld, Datei-Upload, Validierung gegen `ZIELE` |
| `static/app.jsx` | Ziel-Dropdown, Upload-Feld, vier Kategorie-Aufklapper statt Sammelblock |
| `static/styles.css` | Kategorie-Bloecke |
| `tests/` | siehe Abschnitt 7 |

## 10. Umsetzung in drei Stufen

Der Umfang ist zu gross fuer einen Plan. Vorgeschlagene Reihenfolge, jede Stufe fuer sich lauffaehig:

1. **Bewertungslogik.** Ziel als Eingabe, `SCORE_GEWICHTE_JE_ZIEL`, Schwere-Sortierung, kritische
   Maengel, Lob-Schwelle, Cache-Key. Frontend nur minimal: Dropdown und neues Score-Label.
   Danach messbar ueber `tools/replay_nachbearbeitung.py`.
2. **Neue Dimensionen.** Untertitel gesplittet, Audioqualitaet, CTA. Schema, Prompt, Gewichte aktiv.
3. **Darstellung und Kontext.** Vier Kategorie-Bloecke im Frontend, Brand-Datei-Upload,
   Zielgruppen-Abgleich.


---

## 11. Versionierung: V3 neben V2, nicht statt V2

Vorgabe Chris (2026-09-12): Der alte Stand darf nicht ueberschrieben werden, beide Versionen muessen
am selben Video vergleichbar bleiben.

### 11.1 Kein neuer Mechanismus

Die Infrastruktur existiert und ist genau dafuer gebaut:

- `meta.json` traegt `engine`; `analyst_engine._run()` verzweigt darauf; `api/analyst.ENGINES` ist die
  Whitelist.
- `engine` ist Teil des Cache-Keys (`services/analyst_cache.py`). Beide Versionen desselben Videos
  koexistieren damit im Cache, ohne sich zu verdraengen.
- Praezedenzfall `v2_split`: eine Variante, die nur den Bewertungsschritt aendert, damit der Vergleich
  mit der Vorversion gueltig bleibt.

Neu ist nur der Wert **`v3`** in `ENGINES` und im Dispatcher. `v2_hybrid` bleibt unveraendert lauffaehig.

### 11.2 Was geforkt wird und was nicht

| Ebene | Vorgehen | Begruendung |
|---|---|---|
| Prompt | **Fork:** `services/analyst_eval_skill_v3.md` | Inhaltlich andere Bewertung; ein gemeinsamer Prompt mit Verzweigungen waere unlesbar |
| Schema `models/analyst.py` | **additiv**, kein `AnalystEvaluationV3` | Alle neuen Felder haben Defaults; Altlaeufe laden unveraendert. Etabliertes Muster im Projekt |
| Nachbearbeitung `analyst_eval.py` | **verzweigen**, nicht kopieren | 1.494 Zeilen zu duplizieren heisst, jeden kuenftigen Fix zweimal zu machen |
| Frontend `static/app.jsx` | nach `result.engine` rendern | V3 → vier Kategorien; alles andere → heutige Darstellung |
| `PROMPT_VERSION` | eigene Zaehlung fuer V3 | Der Cache-Key enthaelt Engine und Version getrennt |

### 11.3 Regel fuer die verzweigende Nachbearbeitung

Betroffen sind drei Funktionen: `berechne_performance_score()`, `verteile_empfehlungen()` und der neue
Lob-Filter. Die Verzweigung steht am **Anfang** der Funktion; der V2-Pfad bleibt Zeile fuer Zeile
unveraendert.

**Abnahmekriterium:** Die Ausgabe von `tools/replay_nachbearbeitung.py` ist VOR und NACH der
Aenderung byte-gleich.

ACHTUNG, gemessen am 2026-09-12: Das Werkzeug meldet als Ausgangslage bereits
„66 von 93 Laeufen aendern sich durch die aktuelle Nachbearbeitung" — die gespeicherten Altlaeufe
sind gegenueber der heutigen Nachbearbeitung ohnehin gedriftet (frueher ergaenzte Untertitel- und
Texthook-Schritte, Score-Deckelungen). Eine absolute 0 ist deshalb KEIN sinnvolles Kriterium.
Der Nachweis ist der A/B-Vergleich: Werkzeug einmal mit der Datei aus HEAD laufen lassen, einmal mit
der geaenderten Fassung, beide Ausgaben diffen. Ein Unterschied heisst, die Verzweigung greift auch
ohne Ziel — genau der Fehler, den dieser Test faengt.

### 11.4 Vergleichbarkeit

- Beide Engines laufen ueber dieselbe Vorverarbeitung (Whisper, Frames, Messwerte). Nur der
  Bewertungsschritt und die Nachbearbeitung unterscheiden sich — dieselbe Regel wie bei `v2_split`.
- `tools/vergleiche_laeufe.py` vergleicht die `prompt_log.md` beider Laeufe.
- **Bekannter Unterschied:** V3 bekommt mit dem Videoziel einen Input, den V2 nicht hat. Das ist der
  zu messende Effekt, kein Messfehler — beim Vergleich aber mitzudenken.

### 11.5 Sichtbarkeit

Die Engine-Auswahl war am 2026-08-10 bewusst aus dem Frontend entfernt worden ("nur eine sichtbare
Variante"). Sie kommt **nur in der Admin-Ansicht** zurueck, wie die Checkbox "Cache umgehen":
Endnutzer sehen weiterhin genau eine Variante. Solange V3 nicht freigegeben ist, bleibt `v2_hybrid`
der Default; die Umstellung des Defaults ist eine eigene, spaetere Entscheidung.

---

## 12. Funnel-Wirkung: was der Nutzer WOLLTE gegen das, was das Video TUT

Vorgabe Chris (2026-09-12, nach dem ersten echten V3-Lauf). Die wertvollste Aussage des Analysten
ist nicht der Score, sondern die Luecke zwischen Absicht und Wirkung.

**Befund aus Lauf dc5c0a3d:** `funnel` steht dort auf `MOFU` — exakt dem gewaehlten Ziel. Kein
Zufall: Der V3-Skill sagt woertlich „Trag das Ziel unveraendert in das Feld funnel ein." Das Modell
schaetzt also nichts ein, es schreibt ab. Damit ist ein Abgleich unmoeglich.

**Aenderung:** `funnel` traegt weiterhin das gewaehlte Ziel (Fakt, vom Code gesetzt). Die
Einschaetzung des Modells kommt in ein eigenes Feld:

| Feld | Quelle | Inhalt |
|---|---|---|
| `funnel` | Code (Nutzerziel) | TOFU / MOFU / BOFU — die Absicht |
| `funnel_wirkung` | Modell | TOFU / MOFU / BOFU — auf welche Stufe das Video tatsaechlich einzahlt |
| `funnel_wirkung_grund` | Modell | EIN Satz, woran das Modell das festmacht |

Der Prompt-Abschnitt „Videoziel" im V3-Skill wird entsprechend umgeschrieben: Das Ziel ist der
Massstab fuer die BEWERTUNG, aber die Einschaetzung der tatsaechlichen Wirkung erfolgt **unabhaengig
davon** — ausdruecklich mit der Erlaubnis zu widersprechen.

**Darstellung** (nur wenn `funnel_wirkung != funnel`), direkt unter dem Score:

> ⚠ Du wolltest **Vertrauen und Expertenstatus aufbauen**. Dieses Video wirkt eher wie ein Video,
> das **Kundenanfragen gewinnen** soll — <Grund in einem Satz>.

Stimmen beide ueberein, erscheint nichts. Kein Lob fuer Uebereinstimmung: Das waere Fuelltext, und
der Nutzer soll den Hinweis als Signal lesen, nicht als Routine.

**Verhaeltnis zum Zielgruppen-Abgleich (Abschnitt 3.2):** Dieselbe Mechanik auf einer anderen Achse
— dort „wen sprichst du an", hier „was bewirkt das Video". Der Zielgruppen-Abgleich braucht die
Brand-Datei, dieser hier nicht: Das Ziel ist Pflichteingabe, der Abgleich also immer moeglich.

---

## 13. Fixes aus dem ersten echten V3-Lauf (dc5c0a3d, Ziel MOFU)

Alle drei folgen demselben Muster wie der Rest des Projekts: Wo ein Messwert vorliegt, urteilt der
Code, nicht der Prompt.

### 13.1 Videoende — Nachlauf messen statt raten

**Befund:** Handlungsschritt 3 lautete „Kuerze den letzten Satz leicht ab und beende das Video direkt
nach dem Wort 'weiter', um einen unnoetigen Leerlauf am Ende zu vermeiden." Tatsaechlich:
`duration_sec` 24,92 s, Sprechende bei 24,68 s → **0,24 s Nachlauf**. Es gab keinen Leerlauf. Chris:
*„das video endet schon direkt nach dem wort weiter. vielleicht muss da ein puffer rein. 1-2 sekunden
leerlauf waeren in ordnung, alles darueber waere zu lang."*

**Regel (Code):** `nachlauf = duration_sec - (sprechbeginn_sec + sprech_dauer_sec)`

| Nachlauf | Urteil |
|---|---|
| < 1,0 s | Mangel: 1–2 s Puffer anhaengen, damit der Schluss nicht abgehackt wirkt |
| 1,0 – 2,0 s | in Ordnung, kein Schritt |
| > 2,0 s | Mangel: auf 1–2 s kuerzen |

Modell-Empfehlungen zum Videoende werden wie bei den Pausen verworfen; den Satz baut der Code
(`baue_videoende_schritt`). Bei Videos ohne gesprochenes Wort entfaellt die Pruefung.

### 13.2 Lautstaerke — Zielkorridor statt Schaetzung

**Befund:** Gemessen `-35,8 LUFS` / True Peak `-18,0 dBFS`. Die Empfehlung lautete „Hebe die
Lautstaerke der gesamten Tonspur um ca. 3 Dezibel an" — es fehlen rund 22 LU. Der Messwert stand im
Prompt; das Modell hat ihn nicht in eine Zahl uebersetzt.

**Zielwerte (Vorgabe Chris):** `-14 LUFS (±3 LU)`, True Peak `<= -1 dBTP`.

**Regel (Code):** Liegt `lufs_integrated` ausserhalb `-17 … -11`, baut der Code die Empfehlung mit
Ist- und Sollwert und der konkreten Differenz („dein Ton liegt bei -35,8 LUFS, Ziel sind -14 LUFS —
hebe die Tonspur um etwa 22 LU an"). Liegt `true_peak_db` ueber `-1`, kommt ein Hinweis auf
Uebersteuerung dazu. Innerhalb des Korridors: kein Schritt, keine Erwaehnung.
Modell-Empfehlungen zur Lautstaerke werden verworfen.

### 13.3 Hoechstens eine Empfehlung je Dimension in den Top 3

**Befund:** Zwei der drei Schritte betrafen `sprech_hook` — „Formuliere deinen ersten gesprochenen
Satz um" und „Starte direkt mit der steilen These des Experten". Dieselbe Handlung, zweimal Platz
belegt. Die Buendelung griff nicht, weil `gruppe` einmal `"sprechhook"` und einmal leer war.

**Regel (Code):** In `verteile_empfehlungen` darf je Wert von `betrifft` hoechstens EIN Schritt in
die Top N. Der schwerere gewinnt, der andere rutscht nach „Erweitert". Empfehlungen ohne `betrifft`
sind davon nicht betroffen — sie sind videospezifisch und meinen verschiedene Stellen.

Das ist strenger als die bestehende `gruppe`-Buendelung und ersetzt sie nicht: `gruppe` fasst
dieselbe Handlung an mehreren Zeitpunkten zu EINEM Schritt zusammen, diese Regel verhindert, dass
zwei verschiedene Formulierungen desselben Mangels beide oben stehen.
