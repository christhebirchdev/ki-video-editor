# Video-Analyst V4 — Design

Stand: 2026-10-30. Brainstorming mit Chris auf Basis von Kundenfeedback (Andrea, 1:1 vom 2026-10-23;
Katharina und ein weiterer Kunde aus der Community; Kundenwünsche zu Speicherung und Effekten).

## 1. Ziel und Rahmen

V4 läuft **neben** V3. Umschalten über das bestehende Feld „Version" in der Admin-Ansicht
(V4 / V3 / V2). Default bleibt V3, bis Chris umstellt (eine Konstante: `ANALYST_ENGINE`).

Der Analyst soll später komplett ins KI-System der Agentur umziehen (dort per Vibe Coding gebaut,
Tech-Stack unbekannt). Deshalb gilt: **Alle Funktionen sind vor dem Umzug fertig.** Die
Integration darf nur noch an genau einer Stelle Code brauchen: wer eingeloggt ist (Abschnitt 6).

Nicht Teil von V4 (bewusst gestrichen): Views-Eingabe zur Score-Kalibrierung, Hook→Demografie.

## 2. Bewertung (Engine `v4`)

`v4` = V3-Pipeline (Split, Zielpflicht, Gewichte, Nachbearbeitung) plus die Änderungen unten.
Schalter in der Nachbearbeitung ist `result.engine == "v4"`. Damit das funktioniert, setzt
`_run_v2` die Engine schon beim Anlegen des Ergebnisses (bisher erst danach — deshalb hing V3 an
`gewaehltes_ziel`). V3-Verhalten bleibt byte-gleich; Nachweis über die bestehende Testsuite.

Prompt: V3-Skill + `V4_SKILL_ERSETZUNGEN` (Anker → Ersatz, mit Import-Assert, gleiches Muster wie
`V3_VERTRAG_ERSETZUNGEN`) + ein V4-Nachsatz am Ende. Vertrag: V3-Vertrag + V4-Ersetzungen.
Eine Skill-Datei, keine zweite Kopie.

### 2.1 Aufwand je Empfehlung (Andrea: „geht davon aus, dass ich neu drehe")
- `Empfehlung.aufwand`: `schnitt` (im Schnitt/Editing lösbar) | `dreh` (braucht neue Aufnahme).
  Das Modell liefert ihn; erzwungene Schritte des Codes bekommen ihn aus `AUFWAND_JE_DIMENSION`.
- V4-Verteilung: `action_steps` (Top-3) nur aus `schnitt`. `dreh`-Schritte landen in der neuen
  Liste `naechstes_video` (max. 3, nach Schwere). Rest wie bisher in `weitere_empfehlungen`.
- Frontend: Top-3 „Das kannst du jetzt im Schnitt verbessern", darunter
  „Fürs nächste Video" (eingeklappt nur, wenn leer → dann gar nicht angezeigt).

### 2.2 Effekte (nicht immer Woosh)
Ursache belegt: Die Beispiele im Skill nennen fast immer Whoosh, und der Ersatztext in
`baue_effekt_schritt` schreibt „ein Whoosh" fest. V4:
- `EffektVorschlag.optionen`: 1–2 konkrete, zum Zweck passende Effekte. Zwei, wenn mehrere
  passen. Whoosh nur bei echten Bildwechseln/Übergängen.
- Skill-Ersetzungen neutralisieren die Whoosh-Beispiele; Nachsatz enthält eine Zuordnung
  Zweck → passende Effektarten (Übergang, Pointe, Zahl/Aufzählung, Spannung, Betonung, Ironie).
- `baue_effekt_schritt` (V4-Zweig) baut den Text aus `optionen`: „bei Sek. 12 (Pointe): ein
  kurzer Ding oder ein Plopp — beides passt, nimm, was zu deinem Stil passt." Kein fester Effekt.

### 2.3 Neubewertung eines verbesserten Videos
- Upload mit `vorgaenger=<run_id>` (gleicher Nutzer, fertige Analyse). Steht in `meta.json`
  und im Cache-Key.
- Der Handwerk-Call bekommt die Empfehlungen des Vorgängers (Top-3 + nächstes Video + weitere)
  und liefert `vorgaenger_abgleich`: je alter Empfehlung `status` (umgesetzt | teilweise | offen)
  + ein Satz.
- Score-Veränderung zeigt das Frontend nur bei gleichem Ziel; bei anderem Ziel ein Hinweis, dass
  die Zahlen nicht vergleichbar sind.

### 2.4 Anderes Ziel ohne neuen Upload
`POST /api/analyst/{id}/neues-ziel` legt einen neuen Lauf mit derselben Videodatei an
(Hardlink, sonst Kopie) und gibt dessen ID zurück; danach normaler Start. Video schon gelöscht
(siehe 4) → 410.

## 3. Nutzer und Speicherung

- **Nutzer-ID**: eine Nahtstelle `services/analyst_nutzer.aktueller_nutzer(request)`. Heute liest
  sie den Header `X-Analyst-Nutzer`, den das Frontend aus einer im Browser erzeugten ID schickt
  (`localStorage`, `crypto.randomUUID()`). Format geprüft (`[A-Za-z0-9_-]{8,64}`).
- `meta.json` bekommt `nutzer`. Zugriffsprüfung: Hat ein Lauf einen Nutzer und die Anfrage auch,
  müssen sie gleich sein (sonst 404). Anfragen ohne Nutzer bleiben erlaubt (V3-Frontend, Altläufe).
  `ANALYST_NUTZER_PFLICHT=1` macht den Nutzer zur Pflicht — für den Betrieb nach der Integration.
- Speicher bleibt das Lauf-Verzeichnis (`analyst_runs/<id>/`). Kein Umbau auf eine Datenbank:
  Der Analyst bleibt ein eigenständiger Dienst mit eigenem Volume (Abschnitt 6).
- Endpunkte:
  - `GET /api/analyst/meine` — eigene Läufe, neueste zuerst (id, Datei, Datum, Ziel, Score,
    Vorgänger, Status, Video noch vorhanden).
  - `GET /api/analyst/fortschritt` — Level (Abschnitt 5).
  - `DELETE /api/analyst/{id}` — eigenen Lauf löschen.
  - `DELETE /api/analyst/meine` — alle eigenen Läufe löschen (Account-Löschung).
  - `GET /api/analyst/meine/export` — alle eigenen Analysen als JSON (Datenauskunft, Umzug).

Einschränkung der Übergangslösung (bewusst akzeptiert): Die Browser-ID ist gerätegebunden und
keine Zugriffskontrolle. Wer die Lauf-ID kennt und keinen Header schickt, sieht den Lauf — wie heute.

## 4. Aufbewahrung

Analysen bleiben dauerhaft. Videodateien (`raw/`) werden nach 30 Tagen gelöscht
(`ANALYST_VIDEO_TAGE`, Default 30). Aufgeräumt wird beim Serverstart und bei jedem Upload.
Danach: Analyse, Chatverlauf und Level bleiben; Chat-Rückfragen mit Video und „anderes Ziel"
gehen nicht mehr (klare Meldung).

## 5. Gamification

Grundlage: die Einzelwerte der Dimensionen (1–5) aus `dimensions_scores`, gemittelt je
Kategorie (Hook, Mittelteil, Editing, Auftreten/Bild/Ton). Nicht der Umsetzungs-Score — der
hängt am Ziel und ist zwischen Videos nicht vergleichbar.

Gezählt werden nur eigene, fertige Läufe, die NICHT aus dem Cache übernommen wurden (sonst levelt
man durch mehrfaches Hochladen desselben Videos).

Level je Kategorie (1–10), sequentiell — Level k erfordert alle Bedingungen bis k:

| Level | Bedingung | Bar-Position |
|---|---|---|
| 1 | 1 Analyse | 5 % |
| 2 | 3 Analysen | 12 % |
| 3 | 1 Neubewertung ODER 5 Analysen | 20 % |
| 4 | Ø ≥ 3,0 | 28 % |
| 5 | Ø ≥ 3,3 | 36 % |
| 6 | Ø ≥ 3,6 | 48 % |
| 7 | Ø ≥ 3,9 | 60 % |
| 8 | Ø ≥ 4,2 | 73 % |
| 9 | Ø ≥ 4,5 | 86 % |
| 10 | Ø ≥ 4,8 | 100 % |

- Ø = Mittel der Kategoriewerte der letzten 5 gezählten Läufe (gleitend — glättet das
  Rauschen der Modellbewertung).
- Ein erreichtes Level sinkt nie (höchster Stand über die Historie). Der aktuelle Trend
  (letzte 5 gegen die 5 davor) wird getrennt angezeigt.
- Gesamtlevel = abgerundeter Mittelwert der vier Kategorie-Level.
- Kalibrierung an 98 Altläufen (Einzellauf je Kategorie): Median 3,3–4,0, p90 4,0–5,0.
  Die Skill-Stufen beginnen deshalb bei 3,0 — darunter wären sie beim ersten Video geschenkt.
- Zusätzlich „Dein Fokus": die Dimension, die in den letzten 5 Läufen am häufigsten ≤ 3 lag.

### 5.1 Änderung 2026-11-01 (Chris)

Kategorien zeigen kein Level mehr, sondern den Schnitt-Score (1–5) der letzten 5 Videos mit Trend.
Das Gesamtlevel steigt auf zwei Wegen, der höhere zählt: Nutzung (1/3/5/8/12/17/23/30/40/50
Analysen; Level 3 auch für die erste Neubewertung) oder Können (Gesamtschnitt der letzten 5 Videos
≥ 3,0/3,3/3,6/3,9/4,2/4,5/4,8 für Level 4–10, ab 3 Analysen). Es sinkt nie.
Frühere Analysen spielen ihr Video über `GET /{id}/video` ab, solange es gespeichert ist.
„Anderes Ziel" ist aus der Oberfläche entfernt (Endpunkt bleibt).

## 6. Integration ins Agentursystem

Empfehlung: Der Analyst zieht als **eigener Container** um (Dockerfile und Compose liegen vor)
und bleibt Python. Das Agentursystem bindet ihn per Proxy/Link ein und muss nur zwei Dinge tun:
1. Bei jeder Anfrage an den Analysten den Header `X-Analyst-Nutzer: <stabile User-ID>` setzen
   (serverseitig im Proxy, nicht im Browser) und `ANALYST_NUTZER_PFLICHT=1` setzen.
2. Bei Account-Löschung `DELETE /api/analyst/meine` mit diesem Header aufrufen.
Das Frontend schickt seine Browser-ID immer doppelt: als `X-Analyst-Nutzer` und als
`X-Analyst-Geraet`. Der Proxy überschreibt nur `X-Analyst-Nutzer` mit der Konto-ID. Weichen beide
ab und ist `ANALYST_NUTZER_PFLICHT=1`, schreibt `GET /api/analyst/meine` die Läufe der
Browser-ID einmalig auf die Konto-ID um. So nimmt jeder Kunde seine Übergangs-Historie ohne
Zusatzcode mit.
Vollständige Anleitung: `INTEGRATION.md`. Abnahme: Testsuite grün.

Den Analysten in eine andere Sprache umzuschreiben ist ausdrücklich nicht vorgesehen — das wäre
das größte Risiko des Umzugs.

## 7. Frontend (nur bei Version V4)

- Score heißt „Umsetzungs-Score" (+ eine Zeile Erklärung: misst die Umsetzung, nicht die Views).
- Reihenfolge für weniger Scrollen: Score → Vorgänger-Abgleich → Top-3 (Schnitt) →
  Fürs nächste Video → „Mehr Details" (eingeklappt: Zielgruppe, Funnel, Stärken, Kategorien,
  weitere Empfehlungen).
- Messenger-Chat: fester runder Button unten rechts, sobald eine Analyse da ist. Öffnet ein
  Chatfenster unten rechts (Desktop ca. 380×560 px, Analyse bleibt bedienbar). Unter 640 px
  Breite fährt es als Panel von unten hoch (85 % Höhe). Kein Modal-Popup.
- „Frag nach" an jeder Empfehlung öffnet den Chat mit vorbefüllter Frage.
- Bereich „Dein Fortschritt": Gesamtlevel + vier Balken mit Stufenmarken, Trend, „Dein Fokus".
- Bereich „Meine Analysen": Liste mit Öffnen, „Verbesserte Version hochladen" (setzt Vorgänger),
  „Mit anderem Ziel bewerten", Löschen.

## 8. Tests

TDD je Baustein. Unit: Aufwand-Verteilung, Effekt-Text, Vorgänger-Kontext, Nutzerprüfung,
Fortschrittsberechnung, Aufbewahrung. API: meine/fortschritt/löschen/export/neues-ziel/Zugriff.
Regression: gesamte bestehende Suite (Stand 548 grün) bleibt grün; V3-Prompt byte-gleich.
Frontend: Syntaxprüfung mit Babel (kein Build-Schritt vorhanden).
