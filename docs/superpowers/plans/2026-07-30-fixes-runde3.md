# Fixes Runde 3 — Läufe 26a1adbf und 041770c1

Beide auf `PROMPT_VERSION = 2026-07-30c`, die ≤3-Regel war aktiv und hat in `26a1adbf`
viermal gefeuert. Damit zeigen sich zwei Baufehler von mir und drei Lücken.

---

## F1 — Rahmenformulierung raus (mein Fehler, trivial)

**Befund.** Zwei Tipps untereinander begannen identisch:
„Diese Punkte fallen sofort auf und gehören zuerst behoben: …". Feedback: „Diese Formulierung ist
unnötig und sollte nicht mit drinstehen."

**Änderung.** In `erzwinge_empfehlungen_bei_schwachen_scores` nur noch die Probleme selbst
ausgeben, ohne Vorspann. Mehrere Punkte mit „ " verbinden, jeder als eigener Satz.

---

## F2 — Dieselbe Beobachtung darf nicht zwei Tipps erzeugen

**Befund.** Das Modell schrieb den Blickkontakt in ZWEI Dimensionen:
- `sprechqualitaet`: „…wirkst abgelenkt, weil dein **Blick abschweift**"
- `visuelle_aesthetik`: „Dein **Blick wandert** häufig nach unten oder zur Seite"

Mein Code macht aus jeder Dimension einen Schritt — aus einer Beobachtung wurden zwei Tipps.
Feedback: „ähnliche tipps sollen sich in den empfehlungen nicht doppeln sondern gebündelt und
vereinfacht beschrieben werden."

**Änderung.** Beim Bauen der erzwungenen Schritte werden die Problemtexte über ALLE Dimensionen
hinweg gesammelt und Doppler übersprungen. Maß: Überlappung der Inhaltswörter (Wörter ab 4 Zeichen,
ohne Füllwörter). Ab 50 % gemeinsamer Inhaltswörter gilt ein Problem als schon genannt.

**Bewusst kein Stichwortmuster.** Drei Fehlalarme in Folge (`bild`, `sprech`, `hintergrund`) haben
gezeigt, dass thematische Schlagwörter hier nicht tragen. Wortmengen-Überlappung ist messbar und
testbar, nicht geraten.

---

## F3 — Benannte Probleme deckeln den Score (Entscheidung Chris)

**Befund.** `041770c1`: `visuelle_aesthetik` = **4** und trotzdem zwei benannte Probleme
(„Kopfraum recht groß", „Bildausschnitt unruhig"). Die Score-Anker aus Runde 2 greifen nicht.
Damit feuert die ≤3-Regel nicht, obwohl Mängel dokumentiert sind. Der Score ist also der falsche
Auslöser, solange er den Problemen nicht folgt.

**Änderung.** Neue Funktion `deckle_score_auf_probleme(parsed)`, VOR
`erzwinge_empfehlungen_bei_schwachen_scores` und vor `berechne_performance_score`:

- ein benanntes Problem → Score höchstens **4**
- zwei oder mehr → Score höchstens **3**

Gilt nur für die Dimensionen mit einer echten `probleme`-Liste (`sprechqualitaet`,
`visuelle_aesthetik`). Die anderen führen nur `kommentar` — ein Kommentar ist nicht zwingend ein
Mangel, daraus einen Abzug zu machen wäre erfunden.

Nur deckeln, nie anheben. Dadurch fällt der Score von selbst in den Bereich, in dem die ≤3-Regel
greift — kein zweiter, parallel laufender Auslöser.

---

## F4 — Höchstens zwei Sammel-Tipps in den Top 3 (Entscheidung Chris)

**Befund.** In `26a1adbf` belegten Anlauf-Schnitt plus zwei erzwungene Dimensions-Tipps alle drei
Plätze. Die konkreten Tipps mit echter Sekundenangabe (Sek. 10, 15, 20) rutschten komplett in die
erweiterten Empfehlungen.

**Änderung.** In `verteile_empfehlungen`: Unter den Top 3 stehen höchstens **zwei** Schritte aus den
Dimensions-Gruppen (`sprechqualitaet`, `aesthetik`, `spannungsbogen`, `struktur`, `schnitt`).
Der dritte Platz bleibt für einen videospezifischen Schritt frei; überzählige Sammel-Tipps wandern
nach `weitere_empfehlungen`.

Hook- und Anlauf-Schritte zählen NICHT zum Deckel — sie betreffen die ersten Sekunden und behalten
laut früherer Vorgabe Vorrang.

Die Sortierung nach frühestem Zeitpunkt bleibt unverändert; der Deckel wirkt erst bei der Auswahl.

---

## F5 — Untertitel werden bewertet (in `schnitt_pacing`)

**Befund.** Der Skill sagt über Untertitel nur, dass sie keine Text-Hook sind. Eine Bewertung ihrer
Qualität gibt es nicht. Feedback: „es fehlt die kritik an den untertiteln. diese sind sehr statisch
und wenig dynamisch. zu viele wörter pro textblock. eher auf 2-4 reduzieren."

**Änderung.** Im Skill unter `schnitt_pacing`:
- Laufen Untertitel mit, gehören sie zum Pacing. Prüfe: **2–4 Wörter pro Block** (mehr ist beim
  Mitlesen zu träge), Wechsel im Takt der Sprache statt langer statischer Blöcke.
- Zu lange oder statische Blöcke sind ein Mangel und gehören in `schnitt_pacing`.

Dazu im V2-Override eine Pflichtzeile, analog zu Kopfraum und Blickkontakt — sonst wird die Regel
übersprungen, wie es bei Kopfraum vor Runde 2 passiert ist.

**Bewusst hier und nicht in `visuelle_aesthetik`** (Entscheidung Chris). Folge, einmal benannt:
`schnitt_pacing` hat Gewicht 10, die Ästhetik 17 — die Untertitel-Kritik bewegt den Gesamtscore
dadurch weniger.

---

## F6 — Sprech-Hook-Kalibrierung

**Befund.** `041770c1`: Sprech-Hook **3**, Chris: „würde ich eher auf eine 2 ranken … es hookt fast
garnicht." Der Anker für 3 lautet „funktional aber generisch" — ein Einstieg, der gar nicht hookt,
ist darunter.

**Änderung.** Im Skill bei der Hook-Kalibrierung ergänzen: Beginnt das Video mit Kontext,
Begrüßung oder Aufwärmen — kein Konflikt, keine Zahl, keine offene Frage in den ersten zwei
Sätzen —, ist der Sprech-Hook höchstens **2**, auch wenn der Satz sauber formuliert ist.
„Sauber gesprochen" ist kein Hook-Kriterium.

---

## F7 — Zu viel Text im Skript ist ein Struktur-Mangel

**Befund.** `041770c1`, Feedback unter `struktur`: „ich finde hier könnte man das skript anpassen.
an vielen stellen könnte man text reduzieren." Struktur-Score war 4.

**Änderung.** Im Skill unter Struktur: Weitschweifigkeit ist ein Struktur-Mangel. Nennt der Nutzer
Passagen, die kürzer könnten, gehört das in den Struktur-Kommentar UND als Empfehlung mit der
konkreten Stelle.

---

## Abschluss

- `PROMPT_VERSION` auf `2026-07-30d`.
- Gegenprobe gegen beide Läufe ohne API-Call: In `26a1adbf` darf der Blick-Tipp nur EINMAL
  auftauchen und ein konkreter Zeit-Tipp muss in die Top 3 zurückkommen. In `041770c1` muss die
  Ästhetik durch die Deckelung auf 3 fallen und einen Schritt erzeugen.
- `tools/replay_nachbearbeitung.py` über alle Läufe: Die Deckelung ändert Altläufe dort, wo
  Probleme benannt sind — das ist gewollt und muss sichtbar sein.
