# Wissensdatenbank: Short-Form-Video-Bewertung

<!-- ENTWURF, noch nicht verdrahtet. Extrahiert aus services/analyst_eval_skill.md (2026-08-10c).
     Hier stehen die inhaltlichen MASSSTÄBE. Ablauf, Feldnamen und Ausgabeformat stehen in
     analyst_skill.md. Wortlaut wurde 1:1 übernommen, geändert sind nur die Querverweise.
     Überschneidet sich derzeit mit services/analyst_video_reference.md (P/S/T-Prinzipien) — dort
     genannte Prinzipien sind als „Referenz Sx/Px/Tx" verlinkt statt dupliziert. -->

Dies ist die Urteilsgrundlage, kein Ausgabe-Template. Die Beispiele und Anker kalibrieren dein
Urteil; wie du das Ergebnis schreibst, regelt ausschließlich der Skill. Widersprechen sich beide,
gilt der Skill — er kennt das Ausgabe-Schema.

---

## KB 0 — Leitprinzip
Short-Form-Performance = CTR × Watchtime.
- CTR entscheidet sich am HOOK (auditiv/visuell/Text), erste ~1–5 s.
- Watchtime entscheidet sich am SPANNUNGSBOGEN + SCHNITT: Hält die Spannung
  bis zum Ende? Endet das Video zeitnah, wenn die Spannung kippt?
Hook und Watchtime wiegen am schwersten.

Drei Leitsätze über allem (Referenz):
1. Der Input ist entscheidend — starker Input braucht Feinschliff, schwacher Input braucht
   Kompensation. Dieselbe Beobachtung ist je nach Input unterschiedlich zu werten.
2. Ein Video ist nie besser als sein Skript. Post-Produktion belebt kein totes Skript.
3. Technik und Auftreten sind Hygienefaktoren — schlechtes Audio, zu leise, schlechte Belichtung, ständiges Ablesen
   zerstören Verständlichkeit, Authentizität und Qualitätsempfinden.

---

## KB 1 — Hook

### KB 1.1 Die drei Hook-Ebenen
Alle drei werden getrennt bewertet (Referenz S1: „Hook auf 3 Ebenen"):
- **sprech_hook** = die ersten 1–2 Sätze, die der PROTAGONIST sagt.
- **text_hook** = Texteinblendung, die ZUSÄTZLICH über die Eröffnung gelegt wird.
- **visuell_hook** = was in den ersten Sekunden OPTISCH passiert: Bewegung der Person, ein Zoom,
  ein harter Schnitt, ein Objekt/Einblendung die ins Bild kommt, ein Settingwechsel. Bewertet wird, ob das den
  Daumen stoppt — nicht, ob es aufwendig produziert ist.
  Anker: 5 = etwas passiert sofort und zieht Aufmerksamkeit 3 = leichte Dynamik, aber nichts stark
  Auffälliges. 1 = reines Standbild & keine Einblendungen, die Person sitzt still im Bild.

Eine schwache Text- oder Sprechhook kann durch eine starke visuelle Ebene teilweise getragen
werden. Die Kombination aus allen 3 ist optimal und immer das Ziel. 

### KB 1.2 Was als Text-Hook zählt
**Erster Prüfschritt — gehört der Text zum grafischen Inhalt?**
Gehört der Text zu einem grafischen Element des Videos — Vergleichstabelle, Diagramm, Chart,
Liste, Zeitleiste —, dann ist er INHALT und keine Text-Hook. Unabhängig von Position und Größe,
und unabhängig davon, ob er auch gesprochen wird. Er wäre auch ohne Hook-Absicht da.
Anker: Spaltenüberschrift einer Vergleichsgrafik („Inbound vs. Outbound"), Tabellenkopf,
Achsen- und Legendenbeschriftung, Rubrik-/Kapiteltitel („Tipp 3", „Teil 1/3"), Namens- und
Rollenschilder, Produktnamen.
Dieser Prüfschritt geht allen anderen Text-Hook-Regeln VOR: Ist der Text grafischer Inhalt, ist
0 bereits das Minimum und die Redundanz-Regel wird nicht mehr angewandt.

**UNTERTITEL sind KEIN Text-Hook.** Erkenne sie an ZWEI Merkmalen, die BEIDE zutreffen müssen:
1. Der Wortlaut kommt (nahezu) genauso im TRANSKRIPT vor, UND
2. der Text LÄUFT MIT: über das Video hinweg kommen laufend neue Blöcke, die das jeweils gerade
   Gesagte mitschreiben.
Trifft beides zu, ist es eine Untertitelspur — auch wenn die Blöcke statisch stehen bleiben, groß sind
oder oben im Bild stehen. Untertitel müssen weder wechseln noch unten sitzen.

Trifft nur Punkt 1 zu, ist es eine TEXT-HOOK: ein einzelner Textblock am Anfang, der danach nicht
weiterläuft, bleibt eine Text-Hook — auch wenn er fast wörtlich wiederholt, was gesprochen wird.
Woran du sie erkennst (positiver Test, geht der Wortlaut-Prüfung vor): Eine Text-Hook steht
durchgehend an derselben Position im Bild, ODER sie verschwindet nach einer Weile und taucht danach
nicht wieder auf. Untertitel dagegen werden laufend durch neue Blöcke ersetzt. Beides kann
gleichzeitig im Bild sein: eine stehende Text-Hook oben und eine mitlaufende Untertitelspur.
Dann ist sie NICHT „nicht vorhanden", sondern REDUNDANT (KB 1.8).

Stehen Untertitelspur UND ein eigener Titeltext im Bild, ist allein der Titeltext die Text-Hook.
Die Untertitelspur wiederholt das Gesprochene per Definition, daraus folgt kein Redundanz-Vorwurf.

### KB 1.3 Der Kerntest: Öffnet er, oder beschreibt er?
Ein Hook öffnet eine Lücke, die der Zuschauer geschlossen haben will. Eine Zusammenfassung des
Inhalts schließt sie sofort. Es muss Neugierde erzeugt werden.
- beschreibt (schwach): „In diesem Video zeige ich dir drei Fehler beim Lead-Kontakt."
- öffnet (stark): „Der dritte Fehler kostet dich am meisten Geld — und fast alle machen ihn."

Lässt sich keine offene Frage formulieren, gibt es keinen Haken. Referenz S3: Der Open Loop muss
offen BLEIBEN — ein verratender Folgesatz tötet die Spannung sofort.
Referenz S4: Promise → Payoff. Das Eröffnungs-Statement muss eingelöst werden, sonst bleibt der
Zuschauer unbefriedigt. Value = die Antwort auf das Versprechen, nicht das Wiederholen der Relevanz.

### KB 1.4 Mechaniken
Womit arbeitet der Hook? Genau einer dieser Werte:
`provokation` (widerspricht dem, was die Zielgruppe glaubt) · `neugierluecke` · `zahl` (konkrete
Zahl oder konkreter Pain Point) · `erwartungsbruch` · `pov` · `konflikt` · `versprechen` · `keine`.
`keine` bedeutet: Es ist eine Aussage und kein Hook.

### KB 1.5 Kriterien
- **Einsatz:** Was gewinnt oder verliert der Zuschauer? Ohne erkennbaren Einsatz bleibt es
  Information statt Sog. „Verlierst du tausende Euro Umsatz" hat Einsatz, „geht es um Leads" nicht.
- **Trennschärfe:** Ein Hook für alle stoppt niemanden. Er muss die Zielgruppe treffen UND andere
  aussortieren. Je klarer erkennbar ist, für wen das gilt, desto stärker.
- **Konkretheit:** Leere Hype-Wörter ohne Inhalt sind schwach. Negativ-Beispiel: „Das ist ein
  unfassbar spannender Glaubenssatz" — sagt statt zu zeigen, reines Adjektiv-Hype → ~2.
- Referenz S5: Konkretheit, Emotion, Relatability; Vergleiche und Analogien in einfacher Sprache.

### KB 1.6 Score-Anker
- **5** = öffnet klar, Mechanik trägt, Einsatz und Zielgruppe erkennbar.
- **4** = stark, ein Punkt schwächer.
- **3** = ein Haken ist DA, wirkt aber generisch.
- **2** = kein Haken formulierbar, oder reine Beschreibung, oder leere Hype-Wörter.
- **1** = schreckt ab.

**Kein Haken in den ersten zwei Sätzen = höchstens 2.** Beginnt das Video mit Kontext, Begrüßung
oder Themenankündigung, hookt es nicht — „verständlich gesprochen" und „passt zum Thema" sind
keine Hook-Kriterien.

NUR FÜR DIE TEXT-HOOK: Der Score bewertet Inhalt UND GESTALTUNG (KB 1.9). **Für eine 4 oder 5 müssen
BEIDE Seiten tragen.** Ist der Wortlaut stark, die Gestaltung aber schwach — zu groß, grell, schlecht
lesbar, zu kurz, am Rand klebend —, höchstens 3. Lässt sich die Gestaltung nicht positiv belegen,
ist sie nicht gut, sondern unbeurteilt: dann 3.
NICHT zulässig als Begründung für 4 oder 5: „ohne Ton verständlich", „zieht Blicke an",
„gut lesbar", „sorgt für Orientierung", „passt zum Thema" — das ist Lesbarkeit, nicht Hook-Wirkung.

### KB 1.7 Länge
**Text-Hook** — gilt für die Bewertung der vorhandenen genauso wie für jeden Vorschlag:
3–9 Wörter (ideal 3–6), höchstens 2 Zeilen. Wer scrollt, liest nur einen Blick lang. Eine vorhandene
Text-Hook über 9 Wörter ist in dieser Zeit nicht erfassbar → höchstens Score 3, und die Länge ist zu
benennen. Ganze Sätze oder Erklärungen sind keine Text-Hooks.

**Sprech-Hook** — eigene Maßstäbe, NICHT die der Text-Hook. Ein Sprech-Hook ist in der Regel deutlich
LÄNGER. Die 9-Wörter-Grenze gilt für ihn NICHT — er ist nie „zu lang", nur weil er ein ganzer Satz ist.
Stark ist er, wenn er mindestens eines davon tut: Neugier wecken, emotional treffen, oder den
Zuschauer direkt ansprechen („du").

### KB 1.8 Redundanz Sprech-Hook = Text-Hook
REDUNDANZ ist eine SCHWÄCHE, keine Stärke (Referenz S3). Dies ist die kanonische Fassung dieser Regel.
Sie gilt NUR für echte Text-Hooks: Untertitel sind ausgenommen (KB 1.2), grafischer Inhalt ebenfalls
(dort ist der Score bereits 0).
Sagen beide (nahezu) dasselbe, schließt sich der Open Loop sofort doppelt und eine Ebene ist verschenkt.
Zwei Folgen, klar getrennt:

**(1) Für die Doppelung zahlt die TEXT-HOOK.** Sie hat den knapperen Platz und muss genau das liefern,
was das Gesprochene noch nicht abdeckt. Die Doppelung ist ausdrücklich zu benennen.
**(2) Für verschenkte Eröffnungssekunden zahlt der SPRECH-HOOK.** Verbraucht der gesprochene
Einstieg die ersten Sekunden damit, den Bildtext VORZULESEN, und setzt die Neugier erst danach
ein, verliert auch der Sprech-Hook Punkte — nicht für die Doppelung, sondern dafür, dass die
wertvollsten Sekunden ohne Gegenwert weggehen. Zu empfehlen ist dann ein Einstieg, der direkt sagt,
was auf dem Spiel steht.

Negativ-Beispiel zu (1): Sprech-Hook „Die Gesundheit eines Kindes beginnt vor der Schwangerschaft" +
fast identischer Text-Overlay „Die Gesundheit deines Kindes beginnt lange vor der Schwangerschaft".
Negativ-Beispiel zu (2): Bildtext „Inbound vs. Outbound" + gesprochener Start „Inbound vs. Outbound,
wer beide gleich behandelt, verliert am Ende beide" → die ersten drei Wörter sind verschenkt.
Besser: „Wenn du Inbound- und Outbound-Leads gleich behandelst, verlierst du tausende Euro Umsatz."

### KB 1.9 Gestaltung der Text-Hook
Eine inhaltlich gute Hook, die gestalterisch nicht funktioniert, stoppt niemanden.
- **Größe:** Sie muss ins Bild passen, ohne das Gesicht zu überdecken. Bildschirmfüllender Text
  wirkt laut und unprofessionell, nicht auffällig. Als sichtbarer Anhaltspunkt: Eine Textzeile
  sollte nicht höher sein als der Kopf des Sprechers im Bild.
  Die Größe wird ausschließlich an dem beurteilt, was im Bild SICHTBAR ist.
- **Farbe und Kontrast:** lesbar, aber im Gesamtbild ruhig. Grelles Neon ohne Bezug zum Look des
  Videos wirkt billig.
- **Einblendungsdauer:** mindestens 5 Sekunden. Wer scrollt, braucht Zeit zum Lesen — kürzer ist
  die Hook praktisch nicht vorhanden.
- **Position:** im oberen Bereich, aber innerhalb der SAFE ZONE (KB 8.1). Ganz oben überdeckt die
  App den Text.

### KB 1.10 Legitimation & Hook-Start
- **Superhook/Legitimation (Referenz S2):** Eine UNBEKANNTE Person braucht sie früh und konkret, eine
  bekannte nicht — Bekanntheit legitimiert. Späte oder vage Legitimation ist eine Schwäche.
  ABER: Ob die Person prominent ist, ist im Lauf NICHT bekannt (es wird keine Identität bestimmt).
  Behandle sie als unbekannt — eine Legitimations-Hook darf als Chance genannt werden, rechtfertigt
  aber KEINEN harten Score-Abzug bei Hook oder Struktur; Bekanntheit könnte sie überflüssig machen.
- **Hook-Start (Referenz P3):** Der Hook gehört ab Sekunde 1. Beginnt das Sprechen laut Sprachstatistik
  deutlich nach 0 s (Atmen, Anlauf, Denkpause vor dem ersten Wort), ist der Hook verzögert — der
  Anlauf gehört weggeschnitten.

---

## KB 2 — Struktur & Skript
Sinnvolle Storyline aus Hook → Bridge → Mid → Peak → (optional CTA)? Bewertet wird, wie schlüssig
die Bausteine ineinandergreifen — nicht bloßes Abhaken.
Referenz S6: Hook → (Legitimation) → Value → Payoff; zu lang oder ausschweifend → straffen, Kern in
3–5 s.

- Fehlender CTA ist KEIN Mangel, wenn das Format ihn nicht braucht. Das wichtigste: Der CTA ist nur die Aufforderung das im Video entstandene Bedürfnis umzusetzen. Das Bedürfnis selbst wird NICHT durch den CTA erzeugt. Das Video selbst MUSS das Bedürfnis wecken. Jedes Video hat ein anderes Ziel und kann unterschiedliche Bedürfnisse wecken. Ein TOF Video soll zum Teilen anregen oder Diskussion in den Kommentaren fördern. Das Video noch ein Zweites mal anzuschauen kann auch ein Bedürfnis im TOF sein. Bei MOF Videos soll das Bedürfnis erzeugt werden, das Video zu speichern oder dem Account zu folgen. Speichern ist der beste Indikator dafür. In BOF Videos soll der Inhalt eine Kontaktaufnahme fördern wie beispielsweise der Klick auf einen angekündigten Link, eine Direktnachricht oder eine Interessenssignal in den Kommentaren. 
- MEHRERE CTAs am Ende sind eine Schwäche (zwingen den Zuschauer zur Entscheidung) → auf genau EINEN
  klaren CTA reduzieren.
- **Weitschweifigkeit ist ein Struktur-Mangel.** Wird eine Aussage mit mehr Worten getroffen als nötig,
  oder wiederholt sich der Inhalt, kostet das Watchtime. Die konkrete Passage ist zu benennen —
  pauschales „straffe das Skript" hilft nicht.
- Referenz S4: Wird das Eröffnungs-Statement nie beantwortet, ist der Value ≈ 0, unabhängig davon wie
  gut das Editing ist.

---

## KB 3 — Sprache & Verständlichkeit
Das Skript muss in EINFACHER Sprache verständlich sein. Es muss für die dümmsten Person innerhalb der Zielgruppe verständlich sein. Beispiel
Negativ (zu komplex):
„Die Implementierung effektiver Zeitmanagement-Strategien erfordert eine grundlegende Rekalibrierung der eigenen Prioritätensetzung, um langfristig produktivitätssteigernde Verhaltensmuster zu etablieren."

Positiv (einfach):
„Du hast keine Zeit? Stimmt nicht. Du setzt die falschen Prioritäten. So änderst du das."

Regeln, die aus dem Positiv-Beispiel extrahierbar sind (für den Prompt):

Sätze unter 8 Wörtern
keine Nominalisierungen (Implementierung, Rekalibrierung, Etablierung → raus)
keine Fremdwörter/Fachbegriffe ohne Not
aktive Verben statt Substantivketten
ein Gedanke pro Satz, keine Schachtelsätze
konkrete Alltagssprache statt AbstraktaVersteht die Zielgruppe die Sprache wahrscheinlich nicht, dämpft
das auch Skript und Struktur-Bewertung.

---

## KB 4 — Sprechqualität & Ton
LEITFRAGE ZUERST: Versteht man jedes Wort ohne Anstrengung? Muss man sich konzentrieren oder
zurückspulen, ist das der Mangel — alles andere ist Detail.

Tempo, Deutlichkeit und TONQUALITÄT ergeben EINEN Score. Energie gehört nicht dazu (KB 10).
Urteilsgrundlage sind die gemessenen Werte (WPM, Füllwörter, Pausen). Auffällig sind nur STARKE
Abweichungen: monoton, viele Füllwörter, undeutlich.

TON — das muss GEHÖRT werden, die Messwerte sagen darüber nichts:
- **Störgeräusche:** Rauschen, Brummen, Hall, Übersteuerung, Klopfen, Wind. Entscheidend ist nicht,
  ob etwas da ist, sondern ob es beim Zuhören STÖRT. Eine leise Umgebung im Hintergrund ist normal;
  ein Kratzen mitten im Satz nicht.
- **Mikrofonabstand:** Klingt es dumpf und übersteuert, sitzt das Mikro zu nah am Mund oder es wird
  zu laut hineingesprochen. Klingt es hallig und fern, ist es zu weit weg.
- **Hintergrundmusik neben Sprache:** Sie muss deutlich LEISER liegen als die Stimme — hörbar, aber
  klar untergeordnet. Liegt sie auf gleicher Lautstärke, kämpft sie mit dem Gesprochenen: Problem.
  Wird nicht gesprochen, darf die Musik normal laut sein und trägt das Video — dann ist Lautstärke
  kein Mangel.
- Referenz T1: Die ersten Worte müssen verständlich sein. Schlecht geclippte Anfänge starten besser
  eine halbe Sekunde später.

---

## KB 5 — Schnitt, Pacing & Dynamik
Der Schnitt wird gegen das FORMAT bewertet (KB 12), konservativ: grober Score, keine Behauptungen
über einzelne Schnitte. Lieber vorsichtig als falsch.

Visuelle Abwechslung wirkt POSITIV auf Watchtime: Kamera-/Perspektivwechsel (Subjekt bleibt
zentriert), B-Roll oder Settingwechsel erzeugen Unterhaltungswert — höher zu bewerten als eine
statische, monotone Einstellung, solange das Subjekt klar erkennbar bleibt.

**B-Roll und Einblendungen sind ein starker, mehrfach wirkender Hebel** — aktiv zu empfehlen, wo sie
passen: (1) sie überdecken die Stellen, an denen der Sprecher wegschaut; (2) sie verstärken das
Gesagte visuell, das Video wird leichter verständlich und der Zuschauer nimmt mehr mit;
(3) sie bringen Dynamik ins Bild und erhöhen die Chance, dass Zuschauer dranbleiben.
Fehlen sie in einem statischen Video, ist das eine konkrete Chance.

Referenz P5: SFX subtil, unterstützend, mehrkanalig — nicht überladen.
Referenz P7: Emotion an Schlüsselmomenten durch Schnitt verstärken (Zoom, Farbe, Sound), dosiert.
Referenz P10: So viele Reize wie nötig, keine Reizüberflutung.
Referenz P8: Länge kürzen, ohne Inhalt zu kürzen.

**Dynamik-Stufen** (reine Beschreibung, kein Qualitätsurteil):
- `gering` — ein Kamerawinkel, kaum oder keine Schnitte, kein Zoom, keine Einblendungen, das Bild
  steht im Wesentlichen still.
- `mittel` — gelegentliche Schnitte, ein Zoom, vereinzelte Einblendungen.
- `hoch` — häufige Wechsel von Einstellung oder Perspektive, B-Roll, sichtbare Bewegung.
Ein ruhiges Talking Head kann für sein Thema genau richtig sein.

**Effekt-Stellen** (kurzer Soundeffekt wie Whoosh oder Klick, kleiner visueller Effekt wie Flash beim
Übergang oder kurzer Punch-In) lohnen vor allem dann, wenn die Dynamik gering ist — dort ersetzen
Effekte die fehlende Abwechslung — oder wenn das Video sonst kaum Schwächen hat (letzter Feinschliff).

---

## KB 6 — Sprechpausen: nach FUNKTION, nicht nach Länge
Gemeldet werden nur Pausen oberhalb der Messschwelle; alles darunter fällt beim Zuschauen nicht auf
und ist nie eine Empfehlung wert. Die Länge allein sagt aber auch oberhalb der Schwelle NICHTS über
die Qualität: Eine lange Pause vor einer Pointe ist stark, eine kurze Stockung mitten im Satz ist ein
Loch. Nicht zählen — die FUNKTION bestimmen, an ihrer Position (was passiert davor, was danach?).

- **Stockung/Denkpause** — sucht nach Worten, Satz bricht ab, Blick geht weg → `raus`.
  (Referenz P4: Denkpausen und Skript-Blicke sind Retention-Killer.)
- **Anlauf** vor dem ersten Wort — Atmen, Einrichten, „ähm" → `raus`.
- **Dramaturgische Pause** — steht nach einer starken Aussage, vor einer Pointe, oder lässt eine Frage
  wirken → `lassen`. Wenn sie gut sitzt, ist sie eine Stärke.
- **Übergangspause** — Szenen-, Format- oder Sprecherwechsel; z.B. das Ende eines eingeblendeten
  Fremdvideos in einer Reaction, bevor der Protagonist einsteigt → `lassen`, außer sie bricht den
  Rhythmus spürbar.
- **Funktion nicht bestimmbar** → `unklar`. Keine Erwähnung, kein Abzug.

Im Zweifel gilt IMMER `unklar`. Eine falsche Schnitt-Empfehlung kostet den Nutzer mehr als eine
fehlende: Er schneidet eine Pause raus, die sein Video getragen hat.

---

## KB 7 — Spannungsbogen (Watchtime)
Hält die Spannung über die Länge des Videos? Wo kippt sie, und endet das Video zeitnah danach? Der Spannungsbogen kann dadurch gehalten werden, indem das Versprechen am Anfang des Videos erst am Ende aufgelöst wird. 
Dramaturgischer Leerlauf am Ende = niedriger Score.

---

## KB 8 — Visuelle Ästhetik
LEITFRAGE ZUERST: Erkennt man das Gesicht klar? Ist die Antwort ja, ist die Bildqualität in Ordnung —
unabhängig davon, ob professionell ausgeleuchtet wurde. Erst wenn nein, gibt es einen echten Mangel.

### KB 8.1 Safe Zone
**Die App legt ihre Oberfläche über das Video.** Gilt für Instagram und TikTok gleichermaßen
(konservative Faustregel, deckt beide ab). Zu meiden sind, gemessen an der Bildhöhe bzw. -breite:
**oben 13 %, unten 21 %, rechts 15 %, links 4 %.** Alles darin wird von Profilname, Caption, Buttons
oder der Like-Spalte überdeckt.
Zu prüfen für JEDEN wichtigen Bildinhalt — Texthook, Untertitel, eingeblendete Grafiken, das Gesicht.
Ragt er hinein, ist er für den Zuschauer teilweise unsichtbar. Liegt alles innerhalb, ist das eine
Stärke und kein Thema.

### KB 8.2 Bildausschnitt (nur Talking Head, siehe KB 12)
- Einstellung: Brustbild bis Taille (Medium Close-up). Zu weit weg (Totale) oder zu nah (nur Gesicht)
  = Abzug.
- Kamera auf Augenhöhe und frontal. Blick von oben/unten wirkt distanziert.
- Kopfraum: ca. 10–15 % Luft über dem Kopf — genug Platz für eine Texthook, die NICHT auf der Stirn
  klebt. Deutlich MEHR Luft (Kopf sitzt tief im Bild) ist genauso ein Mangel wie zu wenig: Das Gesicht
  wird klein, das Bild wirkt zufällig statt komponiert. Angeschnittener Kopf oder halbes leeres Bild
  darüber = Abzug.
- Person mittig; das Kinn liegt auf der vertikalen Bildmitte oder knapp darüber, damit die Untertitel
  direkt darunter Platz haben. Das Gesicht füllt etwa ein Drittel der Bildhöhe.
- Unteres Drittel bleibt frei genug, dass Handgesten sichtbar sind.

### KB 8.3 Licht
Weiches, gleichmäßiges Licht von vorn/seitlich, keine harten Schatten unter Augen und Nase,
keine ausgebrannten Stellen auf der Haut. Farbige Akzente im Hintergrund geben Tiefe. Die Person muss
sich klar vom Hintergrund abheben; ein leicht unscharfer Hintergrund hilft dabei.
Referenz T3: Ein authentischer „Vibe" kann schwache Bildqualität teilweise ausgleichen.

### KB 8.4 Technische Bildqualität
Scharf (mindestens 1080p — Haare und Stoffstruktur erkennbar), rauschfrei auch in dunklen Bereichen,
flüssige Bewegung ohne Schlieren bei Gesten. Ist der Schärfe-Messwert niedrig, ist die Unschärfe
aktiv zu benennen statt zu übergehen.

### KB 8.5 Deutlicher Mangel vs. Hinweis
Die wichtigste Unterscheidung in dieser Dimension. Ein MANGEL ist nur, was einem Zuschauer beim
ERSTEN Sehen sofort auffällt und die Wirkung messbar schwächt. Alles andere ist ein HINWEIS und
senkt den Score nicht.
Es gibt einen Toleranzbereich: Nicht jedes Video muss ein Studio sein. Natürlich gefilmt ist NICHT
schlecht. Ein normaler Wohnraum als Hintergrund, leichtes Kamerawackeln aus der Hand, ein etwas
schlichter Hintergrund — das sind höchstens Hinweise, oft gar nichts. Leichtes Wackeln kann sogar
Dynamik erzeugen.
Ist der Bildaufbau in Ordnung, ist das als Stärke zu sagen, statt nach einem Makel zu suchen.
Anlässe (Läufe 30d6b472, 82bda700): „kameraeinstellung und licht ist nicht schlecht",
„der raum zwischen kopf und rand ist nahezu perfekt groß", „natürlich ist nicht schlecht und muss
nicht negativ bewertet werden". Zuvor galt eine Pflicht, Auffälliges zu melden — das Modell
fand daraufhin in 5 von 5 Läufen zwei Mängel und der Score war jedes Mal exakt 3.

### KB 8.6 Score-Anker
Die ganze Skala nutzen, nach oben wie nach unten:
- **5** — komponiert: Kopfraum stimmt, ruhiger Hintergrund, scharf und sauber belichtet.
- **4** — gut, ein Punkt schwächer (z.B. leicht unruhiger Hintergrund).
- **3** — funktional: nichts stört massiv, aber auch nichts ist bewusst gestaltet.
- **2** — MEHRERE deutliche Mängel: klar zu viel oder zu wenig Kopfraum, unscharfe oder rauschige
  Aufnahme, stark ablenkender Hintergrund, schiefe Kamera.
- **1** — das Bild schadet dem Video: sehr unscharf, stark unter- oder überbelichtet, Motiv
  angeschnitten.
Zwei DEUTLICHE Mängel sind eine 2. Zwei Hinweise sind keine 2 — und auch keine 3.

---

## KB 9 — Untertitel
Mitlaufende Untertitel sind essentiell, nicht optional. Wird im Video gesprochen, muss praktisch
jedes gesprochene Wort auch als Untertitel lesbar sein — ein großer Teil der Zuschauer sieht das
Video ohne Ton. Fehlen sie, ist das ein MANGEL.
Dass vereinzelte Wörter nicht angezeigt werden, ist dagegen KEIN Mangel — entscheidend ist, dass
die Spur durchgehend mitläuft.

Mängel-Kategorien:
- `position` — sie sitzen am unteren Bildrand statt direkt unter dem Kinn, oder ragen unten aus der
  SAFE ZONE (KB 8.1) heraus, wo Caption und Buttons sie überdecken.
- `statisch` — lange Textblöcke bleiben stehen, statt synchron zum Gesprochenen in kurze Blöcke
  geschnitten zu sein.
- `wortzahl` — mehr als 4 Wörter pro Block.
- `groesse` — auf einem Handydisplay mühsam zu lesen.
- `lesbarkeit` — zu wenig Kontrast zum Hintergrund, keine Kontur.
- `timing` — Text und gesprochenes Wort laufen auseinander.

Sind sie in Ordnung, ist das eine Stärke. Referenz P9: Redundanz vermeiden — eine Hook plus
Untertitel reicht, doppelnde Texttafeln gehören weg.

---

## KB 10 — Auftreten

### KB 10.1 Energie
`traegt` | `flach` | `uebertrieben`. Gemeint ist die PASSUNG zum Inhalt, nicht Lautstärke: Ein
ernstes Thema ruhig vorgetragen ist `traegt`. `flach` heißt, die Stimme lässt den Zuschauer kalt,
obwohl der Inhalt mehr hergäbe.
Referenz T5: Reine Ich-Perspektive ohne Nutzen ist schwach — Mehrwert heißt, der Zuschauer lernt
oder nimmt etwas mit.

### KB 10.2 Blickkontakt
`in_der_linse` | `abgelesen` | `unklar`.
`abgelesen` nur, wenn der Blick wiederholt oder dauerhaft nach unten oder zur Seite geht und dabei
erkennbar Text abgelesen wird (die Augen wandern zeilenweise). Ein einzelner kurzer Blick zur Seite
ist `in_der_linse`. Lässt es sich nicht sicher sehen, ist es `unklar` — nicht raten.
Referenz T4: Blick in die Linse wirkt sicher, Blick nach unten oder zur Seite wirkt geskriptet und
unsicher; echt und ungestellt ist positiv.
Format-Ausnahmen: KB 12.

---

## KB 11 — Funnel
Zuerst bestimmen. Einordnung für den Nutzer.
Das sind die Videomerkmale in den unterschiedlichen Funnelstufen:
- **TOFU:** Breitere Personenansprache; ohne stark thematische
  Tiefe/Erklärungen: weniger fachlich-edukativ dafür emotional-identifikatorisch; relatable Alltagsszenarien der Zielgruppe in denen Sie sich Wiedererkennen. Das Problem muss NICHT zwingend direkt benannt
  werden — es genügt, die Emotion dahinter sichtbar zu machen. Auch Meinungsvideos die ein emotionales Statement enthalten. Die Videos sind oft kürzer als MOFU Videos. Zwischen 7 und 60 Sekunden. TOFU Videos haben oft einen massentauglichen Videoeinstieg oder sind mit einem massentauglichen Thema verknüpft das für viele Menschen relevant oder bekannt ist. Wenn es gerade beispielsweise ein relevantes politisches Thema gibt kann ein Video im TOF dieses Thema als Einstieg wählen und anschließend mit seinem Kernthema Bezug darauf nehmen. TOFU-Content funktioniert, wenn er den Scroll stoppt, Neugier weckt und sofort relevant wirkt — auch für jemanden, der die Person noch nie gesehen hat; er fokussiert auf Inspiration, Bildung oder Unterhaltung statt auf Verkauf. TOFU wird über Impressions und Reichweite bei nicht Followern gemessen. Ziel von TOFU ist nicht die Konversion, sondern im Gedächtnis der Zielperson präsent zu sein für den Moment, in dem der Bedarf entsteht.  Ziel = Viele neue Menschen erreichen (Zielgruppe muss enthalten sein) und Emotionen wecken. Das Bedürfnis erwecken, das Video zu teilen oder in den Kommentaren zu diskutieren. 
- **MOFU:** ~30–90 s + gezieltere Ansprache der Zielgruppe; mehr thematische Tiefe mit Erklärungen;
  baut Vertrauen und Expertenstatus auf; Protagonisten-Story = Beziehungsvertrauen/Nahbarkeit, Case Study mit konkretem Vorher/Nachher = Kompetenzbeweis, reine Wissensvermittlung ohne Narrativ = Expertenstatus über Substanz. MOFU baut Vertrauen auf, verkauft aber noch nicht — kein harter CTA/Angebot. KPI's: Engagement-Rate, Savas, Follower Ziel = Zielgruppe soll Vertrauen in die Expertise des Creators gewinnen. Zielgruppe entwickelt das Bedürfnis der Person für weiteren Mehrwert zu folgen und die Beiträge zu speichern weil sie so wertvoll sind.
- **BOFU:** konkreter Pitch auf Produkt/Angebot. Der Creator verkauft sich selbst oder ein Angebot innerhalb des Videos und fordert aktiv zur Kontaktaufnahme auf. KPI's: DMS, Terminbuchungen, Klicks auf den Angebotslink. Ziel = Kontaktanfragen.
- **Mischung:** wenn Merkmale mehrerer Stufen klar zusammenfallen.

---

## KB 12 — Formatabhängige Maßstäbe
Das Format ist im Lauf vorgegeben (Nutzerauswahl beim Upload). Mögliche Werte:
Talking Head · Reaction · Sketch · Tutorial · Vlog · Andere.

Was heute formatabhängig geregelt ist:

- **Talking Head** — der Bildausschnitt-Standard in KB 8.2 gilt NUR hier.
- **Reaction** — der Blick auf den eingeblendeten Clip ist funktional und zählt als `in_der_linse`
  (KB 10.2). Der Protagonist spricht oft erst später; davor läuft fremdes Audio, das ist NICHT sein
  Sprech-Hook. Übergangspausen beim Wechsel vom Fremdvideo zum Protagonisten sind normal (KB 6).
- **Reaction · Sketch · Tutorial · Vlog · Andere** — der Bildausschnitt wird nach dem bewertet, was
  das Format braucht; aus KB 8.2 wird hier NICHTS abgezogen.

<!-- AUSBAUSTELLE: Hier gehören die formatspezifischen Prinzipien hin, die heute noch fehlen —
     was ein gutes Tutorial vom guten Vlog unterscheidet, welche Hook-Mechaniken je Format tragen,
     welche Pacing-Erwartung gilt. Bewusst leer gelassen statt geraten. -->
