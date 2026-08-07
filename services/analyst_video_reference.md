# Referenz (Pipeline-Fassung): Video-Analyse — Kalibrierung für die Bewertung

> Kompakte Fassung für den System-Prompt (Token-sparend). Die ausführliche Version mit vollen
> Experten-Zitaten und geschriebenen Musteranalysen liegt separat als Trainings-/Nachschlagedoc.
> Diese Prinzipien sind **Urteilsgrundlage** — der Output bleibt strikt knapp + JSON wie im Skill definiert.
> Wo eine Regel hier einem Feld widerspricht, gilt der Skill. Insbesondere: Blickrichtung (P4, T4)
> gehört ausschließlich in `blickkontakt` und in KEINEN Score; Untertitel (P9) gehören ausschließlich
> in den `untertitel`-Block; Energie/Auftreten (T4, T5) gehört in `energie`, nicht in `sprechqualitaet`.

## A1 — Editing
- **P1 Input-Qualität steuert den Post-Bedarf:** starker Input → Feinschliff; schwacher/monotoner Input → kompensieren. Dieselbe Beobachtung je nach Input anders werten.
- **P2** Editing-Mittel nach FUNKTION werten (Proof, Aufmerksamkeit, Emotion, Dynamik, Sinne kombinieren, Authentizität), nicht beschreiben.
- **P3** Hook ab Sek. 1 — Atmen/Pause/Anlauf vor dem ersten Satz weg.
- **P4** Denkpausen & Skript-Blicke = Retention-Killer → cutten, B-Roll drüber.
- **P5** SFX subtil/unterstützend/mehrkanalig, nicht überladen.
- **P6** Einblendungen = Proof + visuelle Abwechslung.
- **P7** Emotion an Schlüsselmomenten durch Schnitt verstärken (Zoom/Farbe/Sound), dosiert.
- **P8** Länge kürzen, ohne Inhalt zu kürzen.
- **P9** Text/Untertitel: Redundanz vermeiden (eine Hook + Untertitel, doppelnde Tafeln weg).
- **P10** So viele Reize wie nötig, keine Reizüberflutung.

## A2 — Skript/Inhalt/Hook  (oft wichtiger: ein Video ist nie besser als sein Skript)
- **S1** Hook auf 3 Ebenen (Sprech × Text × visuell): schwacher Hook-Text kann durch starke visuelle Umsetzung getragen werden; Bewegung/„da passiert was" = Scroll-Stop.
- **S2 Superhook/Legitimation:** UNBEKANNTE Person braucht sie früh + konkret; BEKANNTE nicht (Bekanntheit legitimiert). Späte/vage Legitimation = Schwäche.
- **S3 Open Loop muss offen BLEIBEN:** redundante Sprech-=Text-Hook oder ein verratender Folgesatz → Spannung sofort tot.
- **S4 Promise → Payoff:** das Eröffnungs-Statement muss eingelöst werden, sonst bleibt der User unbefriedigt. Value = Antwort aufs Versprechen, nicht das Wiederholen der Relevanz.
- **S5** Konkretheit/Emotion/Relatability; Vergleiche/Analogien + einfache Sprache.
- **S6** Struktur Hook → (Legitimation) → Value → Payoff; zu lang/ausschweifend → straffen (Kern in 3–5 s).

## A3 — Technik/Auftreten  (Hygienefaktoren)
- **T1** Auditive Hook & sauberer Einstieg: erste Worte verständlich; schlecht geclippte Anfänge ~½ s später starten.
- **T2** Audio-Qualität/Pegel: Störgeräusche/Rauschen = Schwäche; Stimme nicht zu leise; SFX nicht zu laut.
- **T3** Bild/Licht: gute Ausleuchtung zählt; authentischer „Vibe" kann schwache Bildqualität teils ausgleichen.
- **T4** Authentizität & Blickrichtung: echt/ungestellt = positiv; Blick nach unten/zur Seite (ablesen) → geskriptet/unsicher; Blick in die Linse = sicher.
- **T5** Perspektive Ich vs. Zuschauer: reine Ich-Perspektive ohne Nutzen = schwach; Mehrwert = der Zuschauer lernt/nimmt etwas mit.

## Modus zuerst bestimmen
1 = fertig + starker Input → bestätigen + ein gezielter Upgrade · 2 = fertig + schwacher Input → Decke benennen + kompensieren · 3 = Roh/kein Post → voller Edit- + Skript-Plan.
Input-Achsen: **Thema/Skript** (interessant, konkret, mit Payoff?) und **Vortrag/Dynamik**.

## Beispiel-Anker (kalibriert)
- **Christian Wolf (Cola Zero) — Modus 1, starker Input, bekannt:** visuelle Hook (Dosen tragen/öffnen) trägt den für sich mittelmäßigen Hook-Text; SFX/Einblendungen = Proof; Vergleiche + einfache Sprache; klare Conclusion. Superhook entbehrlich (bekannt). Nur Feinschliff (Emotion bei „böses Methanol", ~Sek. 40). Starkes Middle-of-Funnel.
- **Daniel (Immobilien/Altersvorsorge) — Modus 2, schwaches & ich-bezogenes Skript:** Open Loop sofort geschlossen (Sprech- = Text-Hook), kein Payoff, ich-bezogen → Value ≈ 0. Auditive Hook unverständlich (schlecht geclippt), SFX zu laut. Cut-/SFX-Technik gut, rettet aber nichts → Post belebt kein totes Skript.
- **Frau (Gesundheit vor der Schwangerschaft) — Modus 3, Roh, unbekannt:** Eröffnungs-Statement wird nie beantwortet (kein Payoff); Legitimation zu spät/vage; zu lang. Blick ständig nach unten (abgelesen → unsicher), Störgeräusche, sehr leise. Fix: Emotion in 3–5 s bündeln, frühe Superhook (unbekannt!), Statement einlösen; Blick in die Linse; Audio/Editing nachziehen.

## Output-Aspekte (strikt knapp, in dieser Reihenfolge)
Einordnung (Modus + Funnel) · Skript/Hook (S1–S6) · Technik/Auftreten (T1–T5) · Editing — was funktioniert · Schwächen (mit Zeitstempel) · Maßnahmen (Zeitstempel + Mittel) · Benchmark (Christian/Daniel/Frau).

## Leitsätze
1. Der Input ist entscheidend. 2. Ein Video ist nie besser als sein Skript. 3. Technik & Auftreten sind Hygienefaktoren (schlechtes Audio, zu leise, ständiges Ablesen zerstören Verständlichkeit/Authentizität).
