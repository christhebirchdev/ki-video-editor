# Hook-Regeln: Widersprüche auflösen, Grafik-Text abgrenzen

**Auslöser:** Feedback zu Lauf `5502bb37` (2026-07-30) plus zwei Hygiene-Verstöße, die bei der
ersten Reparatur derselben Sache entstanden sind.

**Ansatz:** Variante C — an Ort und Stelle reparieren, Zweitnennungen löschen, dazu ein
Hygiene-Test, der eine erneute Streuung der Hook-Regeln auffängt.

---

## Befund

### 1. Fachlicher Fehler im Lauf

Das Modell bewertete die Spaltenüberschrift einer Vergleichsgrafik („Inbound vs. Outbound") als
Text-Hook mit **Score 4**, Begründung: „macht den Kern des Videos sofort ohne Ton verständlich und
zieht Blicke an."

Zwei Ursachen, beide im Skill:

- Der Skill definierte `text_hook` rein **positionell** („das Text-Overlay der ERÖFFNUNG").
  Es gab keine Regel, dass eine Text-Hook Spannung erzeugen muss. Jedes Overlay qualifizierte sich.
- Die **Bewertungsmaßstäbe** für die Text-Hook waren nicht benannt. Das Modell setzte eigene:
  Lesbarkeit und Blickfang.

Chris' Einordnung: Das zählt **speziell bei diesem Vergleichsformat nicht als Text-Hook, weil es
Teil des grafischen Inhalts ist.** Das ist der tragende Trennstrich — er generalisiert über
Diagramme, Tabellen und Legenden hinweg, während eine Liste verbotener Formulierungen immer
lückenhaft bleibt.

### 2. Folgeschaden im Code

Weil der Score 4 war, unterdrückte `erzwinge_hook_empfehlungen` die `texthook_varianten` — im
Output stand **keine** Texthook-Empfehlung. Ein falscher Score, zwei Schäden.

### 3. Zwei Widersprüche aus der ersten Reparatur

**A — Wer zahlt bei Redundanz?**
`analyst_eval_skill.md:142` (bestand): „**Den Abzug bekommt im Zweifel die TEXT-HOOK**."
`analyst_eval_skill.md:79–88` (neu eingefügt): Abzug beim **Sprech-Hook**.
Gegenläufige Anweisungen zur selben Lage.

**B — Prompt gegen eigenen Code.**
Neue Negativliste: Beschriftungstext → `text_hook_score = 0`.
Neue Code-Klemme `bereinige_redundante_texthook`: bei Redundanz → Score **2**.
„Inbound vs. Outbound" ist beides — Grafik-Beschriftung *und* mitgesprochen. Unbestimmt, was gilt.

**C — Dreifache Streuung derselben Idee.**
„Text-Hook soll ergänzen, nicht wiederholen" steht in Zeile 99–101, 123–124 und 139–145, dazu in
der Referenz als S3 und P9. CLAUDE.md fordert „jede Regel genau einmal". Der vorhandene
Hygiene-Test deckt nur Pausen und Varianten ab — die Hook-Regeln laufen ungeprüft, deshalb ist
der Widerspruch durchgegangen.

---

## Design

### D1 — Grafik-Text ist kein Hook (ersetzt die Label-Liste)

Ein Prüfschritt **vor** allen anderen Text-Hook-Regeln:

> Gehört der Text zu einem grafischen Element des Inhalts — Vergleichstabelle, Diagramm, Liste,
> Zeitleiste, Chart —, dann ist er **Inhalt und keine Text-Hook**, unabhängig von Position und
> Größe. Eine Text-Hook ist Text, der ZUSÄTZLICH über das Video gelegt wird, um beim Scrollen zu
> stoppen.

Beispiele bleiben als Anker (Spaltenüberschrift, Achsen- und Legendenbeschriftung, Tabellenkopf),
aber sie illustrieren die Regel statt sie zu ersetzen.

Ergebnis: `text_hook_vorhanden=false`, `text_hook_score=0`.

**Eigener Begründungstext.** Der bestehende Satz „Es gibt keine Text-Hook im Bild" wäre hier
faktisch falsch — Text war ja sichtbar. Es braucht eine zweite Fassung, die benennt, dass Text da
war und warum er nicht zählt. Zwei Fassungen, ein Zweck: Score 0 bedeutet in beiden Fällen
„dir fehlt eine Text-Hook", die Erklärung unterscheidet sich.

**Vorrangregel gegen Widerspruch B:** Der Grafik-Test kommt zuerst. Ist der Text Grafik-Inhalt,
greift die Redundanz-Regel gar nicht mehr — 0 ist bereits das Minimum.

### D2 — Bewertungsmaßstab und Fehl-Kriterien (bleibt, gekürzt)

Gewertet wird ausschließlich Spannung/Neugier, Relevanz für die Zielgruppe, Konkretheit.
Ausdrücklich unzulässig als Begründung für Score 4/5: „ohne Ton verständlich", „zieht Blicke an",
„gut lesbar", „sorgt für Orientierung". Das ist Lesbarkeit, nicht Hook-Wirkung.

### D3 — Eine kanonische Redundanz-Regel (löst Widerspruch A)

Es sind **zwei Folgen einer Regel**, nicht zwei Regeln. Kanonische Fassung an **genau einer**
Stelle, im bestehenden Block „Hook-Kalibrierung" — dorthin verweist Zeile 99–101 bereits:

> Sagen Bildtext und gesprochener Einstieg dasselbe, ist eine Ebene verschenkt.
> Den Hauptabzug bekommt die **Text-Hook** — sie hat den knapperen Platz und muss liefern, was das
> Gesprochene nicht sagt.
> Verbraucht der gesprochene Einstieg zusätzlich die ersten Sekunden damit, den Bildtext
> **vorzulesen**, und setzt die Neugier erst danach ein, verliert auch der **Sprech-Hook** Punkte —
> für die verschenkten Sekunden, nicht für die Doppelung.

Trennscharf: Doppelung bestraft die Text-Hook, verschenkte Eröffnungssekunden bestrafen den
Sprech-Hook. Beide Code-Klemmen bleiben damit begründet.

**Zu löschen:** die Einfügung in Zeile 79–88 und der Halbsatz in Zeile 123–124
(„Am stärksten ERGÄNZT er die Text-Hook, statt sie zu wiederholen"). Zeile 99–101 verweist korrekt
nach unten und bleibt unverändert.

### D4 — `text_hook_wortlaut` bedeutet „Eröffnungs-Bildtext", nicht „Hook"

Beim Aufschreiben des Designs aufgefallen und ohne diese Korrektur ein stiller Verlust:

Wird Grafik-Text künftig als `text_hook_vorhanden=false` mit leerem Wortlaut gemeldet, hat der Code
nichts mehr zu vergleichen — und der **Sprech-Hook-Abzug für das Vorlesen entfällt**, obwohl genau
das Chris' zweiter Kritikpunkt war.

Deshalb: Das Feld nimmt den **Eröffnungs-Bildtext wörtlich auf, auch wenn er nicht als Hook zählt.**
`bereinige_redundante_texthook` verlangt dann nicht mehr `text_hook_vorhanden`, sondern nur ein
Zitat von mindestens zwei Wörtern. Die Text-Klemme greift nur bei Score > 2, die Sprech-Klemme
unabhängig davon.

### D5 — Hygiene-Test für die Hook-Regeln

Nach dem Muster von `test_pausen_und_varianten_werden_nicht_doppelt_erklaert`:

- Die Redundanz-Regel steht **genau einmal** im Skill: „Den Hauptabzug bekommt" / „verschenkt"
  darf nur in einem Abschnitt auftauchen.
- Der Skill enthält **keine** gegenläufige Zuweisung („Abzug bekommt der Sprech-Hook" ohne den
  Zusatz „für die verschenkten Sekunden").
- Die Fehl-Kriterien stehen nur an einer Stelle.

Der Test prüft Zeichenketten, nicht Semantik — mehr geht nicht, und weniger hätte den aktuellen
Fehler nicht gefangen.

---

## Nicht Teil dieser Änderung

| Weggelassen | Warum |
|---|---|
| Hook-Abschnitt komplett umbauen (Variante B) | Große Änderung an der Datei, die das Produktverhalten *ist*. Zu viel Risiko für eine Feedback-Runde. |
| „Gemma"-Benennung in Zeile 146 bereinigen | Historische Benennung, in CLAUDE.md als bekannt vermerkt. Eigenes Thema. |
| Score-Gewichte anpassen | Der Score war nicht falsch gewichtet, sondern falsch vergeben. |
| Redundanz-Erkennung über Ähnlichkeitsmaß statt Teilstring | Erst messen, ob der Teilstring-Vergleich in echten Läufen Fälle verpasst. |

---

## Verifikation

- Gegenprobe gegen Lauf `5502bb37` ohne API-Call: Text-Hook muss 0 werden, Sprech-Hook 3, und
  **beide** Hook-Empfehlungen müssen im Output stehen.
- `tools/replay_nachbearbeitung.py` über alle gespeicherten Läufe: Altläufe haben kein
  `text_hook_wortlaut` und dürfen sich durch die Klemme nicht ändern.
- `PROMPT_VERSION` hochzählen, sonst ist altes Feedback nicht von neuem unterscheidbar.
