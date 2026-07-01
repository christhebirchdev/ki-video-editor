# AI Video Analyst — Menschliche Beobachtungs-Regeln

Chris' Beobachtungen am echten Material, um die KI-Bewertung zu schärfen. Start des Feedback-Loops.

**Arbeitsteilung (festgelegt 2026-06-16):**
- **Gemma + Whisper + Messwerte = Beschreibung.** Erfassen objektiv ALLE wichtigen visuellen (Gemma)
  und auditiven (Whisper-Transkript, Sprachstatistik, Audio-Messwerte) Elemente. KEINE Wertung, keine Einordnung.
- **Claude = Bewertung/Interpretation.** Entscheidet auf Basis der Beschreibung (z.B. ob Bildtext ein
  Hook oder nur ein Untertitel ist).

## Format pro Regel
- **Beobachtung:** was am Video real war
- **Konsequenz für die KI:** wie Beschreibung/Bewertung reagieren soll
- **Status:** wo umgesetzt (Gemma-Prompt / Claude-Skill / Code)

---

## R1 — Dynamische Untertitel ≠ Text-Hook  (2026-06-16)

- **Beobachtung:** Im Testvideo (Lauf `9e2e7e42`) gab es in den ersten 10 Sekunden KEINEN statischen Text
  im Bild — nur dynamische Untertitel, die das gerade Gesprochene anzeigen. Gemma hatte diese Untertitel
  fälschlich als statische Overlays/Titel gemeldet (zudem OCR-Fehler: „Ich bin kein Geld", „Gläubssatz").
- **Konsequenz für die KI:**
  - *Gemma* beschreibt Bildtext nur objektiv: Wortlaut (`text_overlays`) + `text_darstellung`
    (Position/Stil + ob er sich zwischen den Frames ändert). KEINE Einordnung als Titel/Untertitel/Hook.
  - *Claude* wertet: Bildtext, der den gesprochenen Worten entspricht bzw. als wechselnde Zeile in der
    unteren Bildhälfte beschrieben ist = laufender Untertitel = KEIN Text-Hook. Ein Text-Hook ist nur
    bewusst gesetzter, statischer Grafik-/Titeltext.
- **Status:** Gemma-Prompt ✅ (`text_darstellung`), Claude-Skill ✅ (Hook-Regel), Code –

---

## R2 — OCR unvollständig: Transkript ist Wortlaut-Quelle für Gesprochenes  (2026-06-16)

- **Beobachtung:** Im Bild stand „Ich bin kein Geldmensch", Gemma las nur „Ich bin kein Geld".
- **Konsequenz:** Bei gesprochenem Text (Untertitel) gilt das WHISPER-TRANSKRIPT als Wortlaut, nicht Gemmas Bild-OCR.
  Claude nutzt für Hook-/Untertitel-Wortlaut das Transkript. (Tesseract-OCR-Fallback bleibt nur für STATISCHEN
  Grafiktext relevant, der NICHT gesprochen wird.)
- **Status:** Claude-Skill ✅ (Wortlaut-Quelle), Code (OCR-Fallback) – Backlog

## R3 — Sprecherwechsel / leisere Stimme am Anfang  (2026-06-16)  ⚠️ schwer detektierbar

- **Beobachtung:** Am Anfang sprach NICHT der Protagonist, sondern eine andere Person mit leiserer Stimme.
- **Konsequenz (gewollt):** Sprecherwechsel + Lautstärke-Einbruch erkennen und im Hook/Audio berücksichtigen.
- **Limitation (ehrlich):** Whisper macht keine Sprecher-Trennung (Diarisation). „Andere Person" ist aktuell
  NICHT zuverlässig erkennbar. „Leiser" wäre über per-Segment-RMS messbar (Editor hat RMS-Code) — Backlog.
- **Status:** offen / Backlog (Diarisation oder RMS-Lautstärkeprofil)

## R4 — Einfache Sprache ist Pflicht  (2026-06-16)

- **Beobachtung:** „Geldmensch" negativ konnotiert + insgesamt zu komplexe Sprache, die die breite Masse nicht versteht.
- **Konsequenz:** Skript IMMER in einfacher Sprache. Komplexe/abstrakte Begriffe = Schwäche → Claude benennt sie
  in top_tipps mit einfacher Alternative und dämpft Relevanz-Scores, wenn die Zielgruppe es nicht versteht.
- **Status:** Claude-Skill ✅ (Abschnitt „Sprache & Verständlichkeit"). Offen: eigener sichtbarer Score? (Chris entscheidet)

## R5 — Visuelle Dynamik (Perspektivwechsel) ist positiv  (2026-06-16)

- **Beobachtung:** Kamera wechselt manchmal die Perspektive, Protagonist bleibt zentriert → mehr Unterhaltungswert
  durch Abwechslung, wahrscheinlich besser als ohne Dynamik.
- **Konsequenz:** Kamera-/Perspektivwechsel (Subjekt bleibt zentriert), B-Roll, Settingwechsel = positiv für
  Watchtime → bei Schnitt/Pacing höher bewerten als monotone statische Einstellung.
- **Status:** Claude-Skill ✅ (Schnitt & Pacing)

## R6 — Schwacher Hook-Typ: leeres Hype-Adjektiv  (2026-06-16)  [Gold-Beispiel]

- **Beobachtung:** Sprech-Hook „Das ist ein unfassbar spannender Glaubenssatz" ist NICHT gut.
- **Konsequenz:** Leere Hype-Wörter ohne konkreten Inhalt = schwacher Hook (Score ~2): sagt statt zeigt, kein
  konkreter Open Loop. Dient als Negativ-Beispiel (Few-Shot) für die Hook-Bewertung.
- **Status:** Claude-Skill ✅ (Hook-Kalibrierung)

## R7 — Genau EIN CTA am Ende  (2026-06-16)

- **Beobachtung:** Video hatte 2 CTAs am Ende.
- **Konsequenz:** Mehrere CTAs = Entscheidungslast für den Viewer → auf genau einen klaren CTA reduzieren.
- **Status:** Claude-Skill ✅ (Struktur/CTA)

## R8 — Blickrichtung wird nicht erfasst (Gemini riet „in die Kamera", real abgelesen)  (2026-06-22)  [Wita]

- **Beobachtung:** Video 3 — Gemini meldete durchgehend „blickt direkt in die Kamera", real schaute die Sprecherin
  die ganze Zeit nach unten/rechts und las den laufenden Text ab. Das wirkt geskriptet/unsicher und zerstört Authentizität.
- **Konsequenz:** *Gemini* beschreibt im Feld `personen` objektiv die Blickrichtung (in die Linse / nach unten / zur Seite /
  wechselnd) — keine Deutung. *Claude* wertet dauerhaften Blick weg von der Linse als abgelesen/unsicher (Auftreten-Schwäche).
- **Status:** Gemini-Prompt ✅ (`personen`: Blickrichtung), Claude-Referenz ✅ (A3/T4)

## R9 — Audio-Aufnahmequalität fehlte in der Beschreibung  (2026-06-22)  [Wita]

- **Beobachtung:** V3 Störgeräusche + sehr leise Stimme; V2 Anfang akustisch unverständlich (schlecht aus Podcast geclippt).
  Gemini erfasste Aufnahmequalität/Verständlichkeit bisher nicht (nur Lautstärke/Energie/Tempo).
- **Konsequenz:** *Gemini* (AUDIO_PROMPT) beschreibt zusätzlich Aufnahmequalität: Störgeräusche/Rauschen/Hall,
  Verständlichkeit (klar/undeutlich/nuschelig), abgeschnittene Wortanfänge. *Claude* wertet in sprechqualitaet/Hook.
- **Status:** Gemini-Prompt ✅ (AUDIO_PROMPT), Claude-Referenz ✅ (A3/T1, T2)

## R10 — SFX-Lautstärke relativ erfassen  (2026-06-22)  [Wita]

- **Beobachtung:** V2 — SFX/Einblendungs-Sound zu laut ggü. der Stimme.
- **Konsequenz:** *Gemini* erfasst SFX mit relativer Lautstärke (dezent/präsent/zu laut ggü. Stimme). *Claude* wertet
  zu laute SFX als Mix-Schwäche.
- **Status:** Gemini-Prompt ✅ (AUDIO_PROMPT), Claude-Referenz ✅ (A3/T2)

## R8 — NACHTRAG (2026-06-30): Prompt-Fix reichte nicht → dedizierter Blick-Pass

- **Befund:** Lauf a29f3c20 (73,5 s) — Gemini meldete in ALLEN Hook-Segmenten „blickt direkt in die Linse",
  real schaut die Person wiederholt nach unten aufs Skript. Mit Zahlen: 120 Segmente, nur 4 verschiedene
  „personen"-Texte, „nach unten/zur Seite/wechselnd" in 0 Segmenten. Bei 120 gebündelten Frames liest Gemini
  den Blick faktisch nicht — es produziert Boilerplate. Der R8-Prompt-Satz war strukturell chancenlos.
- **Konsequenz:** Blickrichtung kommt jetzt aus einem DEDIZIERTEN Whole-Video-Pass (`GAZE_PROMPT` in
  analyst_vlm.py, gleicher Upload wie der Audio-Pass) → `AnalystResult.gaze_overview`. Claude nutzt diesen
  Block als Primärquelle (Skill: „BLICKKONTAKT (ganzes Video, dedizierter Gemini-Pass)"); Per-Szene-`personen`
  nur noch Fallback. Bei Ablesen → top_tipp inkl. Zeitfenster: Stellen rausschneiden / Blick in die Linse.
- **Status:** Gemini dedizierter Blick-Pass ✅ · Eval-Primärquelle ✅ · Skill ✅ · Frontend-Block ✅
  — Realtest offen (greift der Pass den Blick nach unten wirklich? Quotenkosten +1 Call/Lauf).

## R11 — Text-Hook ≈ Sprech-Hook (Redundanz) wurde nicht bestraft  (2026-06-30)  [a29f3c20]

- **Beobachtung:** Sprech-Hook „Die Gesundheit eines Kindes beginnt vor der Schwangerschaft" und Text-Overlay
  „Die Gesundheit deines Kindes beginnt lange vor der Schwangerschaft" sind nahezu wortgleich. Claude bewertete
  den Text-Hook trotzdem mit 3 (HÖHER als Sprech-Hook 2, „konkreter") und nannte die Redundanz nicht.
- **Konsequenz:** Redundanter Sprech-=Text-Hook ist eine SCHWÄCHE (Referenz S3 — Open Loop sofort doppelt
  geschlossen, Overlay verschenkt seine zweite Ebene). *Claude:* text_hook_score nicht höher als Sprech-Hook,
  Redundanz im Grund benennen, top_tipp „Text-Overlay für eine zweite Ebene nutzen statt den Satz zu doppeln".
- **Status:** Claude-Skill ✅ (Hook-Kalibrierung, mit Negativ-Beispiel)

<!-- Neue Beobachtungen hier anhängen: R12, R13, … -->
