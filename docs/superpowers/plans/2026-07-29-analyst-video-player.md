# Video-Player nach der Analyse — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nach abgeschlossener Analyse wird das analysierte Video neben dem Upload-Feld in
angenehmer Größe angezeigt und lässt sich abspielen und stoppen.

**Architecture:** Rein im Frontend. Die Datei liegt beim Nutzer bereits als `File`-Objekt vor
(`analysisFile.file`); daraus wird per `URL.createObjectURL` eine lokale Quelle für ein
`<video controls>`-Element. **Kein Backend-Endpoint, kein Byte über den Server.**

**Tech Stack:** React 18 UMD, natives `<video>`-Element, CSS Grid.

---

## Zwei Befunde vorab

**1. Beide Versionen sind eine Codestelle.** `VideoAnalystPage` wird zweimal gemountet (einmal
ohne, einmal mit `chat`). Was hier eingebaut wird, erscheint automatisch in beiden Ansichten.
Es gibt nichts doppelt umzusetzen — das ist die Auszahlung der V1.1-Architektur.

**2. Kein Backend-Endpoint nötig — und das ist kein Sparen, sondern das Richtige.**
Naheliegend wäre `GET /api/analyst/{id}/video`. Dagegen sprechen drei Dinge:

- Der Browser hat die Datei bereits lokal. Sie über den Server zurückzuholen heißt, ein
  potenziell mehrere hundert MB großes Video ein zweites Mal über die Leitung zu ziehen — auf
  einem VPS, der laut `CLAUDE.md` bewusst auf `ANALYST_MAX_CONCURRENT=1` gedrosselt ist.
- Video-Streaming braucht HTTP-Range-Unterstützung, sonst spielt Safari gar nicht erst ab.
  Das ist eine eigene Fehlerquelle, die man sich hier vollständig spart.
- Der Endpoint würde nur nach einem Reload einen Vorteil bringen — und nach einem Reload ist
  das Analyse-Ergebnis ohnehin weg (es lebt nur im React-State). Es gäbe also nichts, wozu
  der Player gehören könnte.

**Falle:** Die Dropzone erlaubt `MP4, MOV, AVI, MKV`. Browser spielen **MKV und AVI nicht ab**
und zeigen dann einen schwarzen Kasten ohne Erklärung. Das muss abgefangen werden.

---

### Task 1: Object-URL mit sauberem Lebenszyklus

**Files:** Modify `static/app.jsx` (in `VideoAnalystPage`, bei den übrigen `useState`)

- [ ] **Step 1: State und Effekt einfügen**

```jsx
const [videoUrl, setVideoUrl] = useState("");
const [videoFehler, setVideoFehler] = useState(false);

// Object-URL zur gewählten Datei. Kein Server-Roundtrip: Die Datei liegt lokal vor, sie
// über das Backend zurückzuholen würde dasselbe Video ein zweites Mal übertragen.
// Aufräumen ist Pflicht — ohne revokeObjectURL hält der Browser die Referenz bis zum
// Schließen des Tabs.
useEffect(() => {
  if (!analysisFile?.file) { setVideoUrl(""); setVideoFehler(false); return; }
  const url = URL.createObjectURL(analysisFile.file);
  setVideoUrl(url);
  setVideoFehler(false);
  return () => URL.revokeObjectURL(url);
}, [analysisFile]);
```

- [ ] **Step 2: In `reset()` mit zurücksetzen**

`reset()` setzt `analysisFile` auf `null` — der Effekt räumt dadurch selbst auf. Nichts weiter
nötig; nur prüfen, dass `setAnalysisFile(null)` dort wirklich steht.

---

### Task 2: Player neben die Dropzone

**Files:** Modify `static/app.jsx` (Card „Video-Quelle")

- [ ] **Step 1: Container auf zwei Spalten umstellen**

Das äußere `<div style={{display:"flex", flexDirection:"column", gap:12}}>` innerhalb der Card
wird durch ein Grid ersetzt, das erst dann zweispaltig wird, wenn der Player sichtbar ist.

```jsx
<div className={"analyst-quelle" + (zeigePlayer ? " hat-player" : "")}>
  <div className="analyst-quelle-eingabe">
    {/* input + dropzone + „Datei entfernen" unverändert */}
  </div>

  {zeigePlayer && (
    <div className="analyst-player">
      <div className="analyst-player-titel">Analysiertes Video</div>
      {videoFehler ? (
        <div className="analyst-player-hinweis">
          Dieses Format kann der Browser nicht abspielen (z.B. MKV oder AVI).
          Die Analyse ist davon nicht betroffen.
        </div>
      ) : (
        <video
          src={videoUrl}
          controls
          playsInline
          preload="metadata"
          onError={() => setVideoFehler(true)}
        />
      )}
    </div>
  )}
</div>
```

- [ ] **Step 2: Sichtbarkeits-Bedingung definieren** (oberhalb des `return`)

```jsx
// Player erst nach der Analyse — vorher gibt es kein „analysiertes Video".
const zeigePlayer = phase === "done" && !!videoUrl;
```

---

### Task 3: Styles

**Files:** Modify `static/styles.css`

- [ ] **Step 1: Ans Ende anhängen**

```css
.analyst-quelle{display:flex; flex-direction:column; gap:12px;}
.analyst-quelle-eingabe{display:flex; flex-direction:column; gap:12px; min-width:0;}
@media (min-width:900px){
  .analyst-quelle.hat-player{display:grid; grid-template-columns:1fr 1fr; gap:20px; align-items:start;}
}
.analyst-player{display:flex; flex-direction:column; gap:8px; min-width:0;}
.analyst-player-titel{font-size:12px; font-weight:600; color:var(--taupe);}
.analyst-player video{
  width:100%; max-height:360px; border-radius:var(--radius-sm);
  background:#000; display:block; object-fit:contain;
}
.analyst-player-hinweis{
  font-size:13px; line-height:1.5; color:var(--taupe);
  background:var(--paper-alt); border:1px solid var(--line);
  border-radius:var(--radius-sm); padding:12px 14px;
}
```

`max-height:360px` plus `object-fit:contain` deckt beide Fälle ab: Ein 9:16-Reel würde sonst
die halbe Seite füllen, ein 16:9-Video wäre unnötig klein.

---

### Task 4: Von Hand prüfen

- [ ] Analyse mit einem **MP4 im Hochformat** → Player erscheint rechts neben der Dropzone,
      Video ist nicht übergroß, Abspielen und Pausieren funktioniert.
- [ ] Analyse mit einem **MP4 im Querformat** → Player füllt die Spalte sinnvoll.
- [ ] Fenster schmal ziehen (< 900 px) → Player rutscht unter die Dropzone, nichts wird gequetscht.
- [ ] **„Datei entfernen"** klicken → Player verschwindet.
- [ ] In **beiden** Tabs prüfen (Video Analyst und V1.1) — es ist dieselbe Komponente,
      aber einmal ansehen kostet nichts.
- [ ] Falls zur Hand: eine **MKV-Datei** → statt schwarzem Kasten der Hinweistext.

---

## Bewusst weggelassen

| Weggelassen | Warum |
|---|---|
| `GET /api/analyst/{id}/video` | Die Datei liegt lokal vor; der Endpoint würde dasselbe Video ein zweites Mal über einen bewusst gedrosselten VPS ziehen und HTTP-Range-Handling nötig machen. |
| Eigene Play/Pause-Buttons | `<video controls>` bringt Abspielen, Pausieren, Scrubbing, Lautstärke und Vollbild mit — inklusive Tastatur- und Screenreader-Unterstützung. |
| Sprung zu einem Zeitpunkt aus der Analyse | Naheliegender nächster Schritt (Action Steps nennen Sekunden), aber ein eigenes Feature. Erst den Player stehen haben. |
