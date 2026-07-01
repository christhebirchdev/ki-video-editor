const { useState, useRef, useEffect } = React;

/* ===== Konstanten ===== */
const PLATFORMS = [
  { value: "short", label: "Short Video" },
  { value: "long", label: "Long Video" },
];
const LS_LAST_PROJECT = "kveditor.lastProjectId";

// Untertitel-Schriftarten (Auswahl im Settings-Panel). Für den Burn-in muss die
// Schrift auf dem Mac installiert sein — sonst greift eine Ersatzschrift.
// Nur gebündelte Fonts: jede Auswahl ist als @font-face geladen UND liegt als
// Datei im fontsdir des Burn-ins → Vorschau und Export nutzen exakt dieselbe Schrift.
// Reihenfolge = fett→schlank. Familienname muss 1:1 zu @font-face + ASS passen.
const SUB_FONT_CHOICES = [
  "Montserrat Black",
  "Montserrat ExtraBold",
  "Montserrat SemiBold",
  "Montserrat",
  "Plus Jakarta Sans ExtraBold",
  "DM Sans",
  "DM Serif Display",
  "Fraunces Black",
];
const SUB_FONT_DEFAULT = "Montserrat Black";
const SUB_FONT_SIZE_DEFAULT = 24;   // = Backend DEFAULT_FONT_SIZE; vollbild-tauglich
const SUB_FONT_SIZES = [14, 16, 20, 24, 28, 32, 40, 48];
const LS_MULTICUT = "kveditor.multicutMode";

const CONTENT_TYPES = [
  { value: "tofu", label: "TOFU — kontroverse Statements (max. 15s)", short: "TOFU" },
  { value: "mofu", label: "MOFU — Insights & Takeaways (30–60s)", short: "MOFU" },
];

const POST_PROCESSING_OPTIONS = [
  { value: "segment", label: "Nur Segment-Schnitt (schnell)" },
  { value: "v52", label: "Segment + V5.2 Feinschnitt (Ähs & Pausen raus)" },
];

const SEGMENT_TYPE_LABELS = {
  content: "Content",
  silence: "Stille",
  breath: "Atmer",
  filler_word: "Füllwort",
  false_start: "Fehlstart",
  repetition: "Wiederholung",
};

/* ===== API helper ===== */
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body && !(body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  } else if (body instanceof FormData) {
    opts.body = body;
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/* ===== Icons ===== */
const Ico = {
  refresh: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" {...p}><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>,
  plus: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" {...p}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>,
  folder: (p) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>,
  upload: (p) => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>,
  film: (p) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="2" y="2" width="20" height="20" rx="2.5"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg>,
  search: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
  brain: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z"/></svg>,
  scissors: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>,
  check: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" {...p}><polyline points="20 6 9 17 4 12"/></svg>,
  download: (p) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>,
  caption: (p) => <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><line x1="7" y1="11" x2="11" y2="11"/><line x1="13" y1="11" x2="17" y2="11"/><line x1="7" y1="15" x2="15" y2="15"/></svg>,
  x: (p) => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" {...p}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>,
  link: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>,
  play: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none" {...p}><polygon points="5 3 19 12 5 21 5 3"/></svg>,
  chart: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="2" y1="20" x2="22" y2="20"/></svg>,
  sparkles: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg>,
  grid: (p) => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...p}><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>,
};

/* ===== MultiCut: Sound-Belohnung (WebAudio, erst nach User-Geste erlaubt) ===== */
let _audioCtx = null;
function playDoneChime() {
  try {
    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const ctx = _audioCtx;
    if (ctx.state === "suspended") ctx.resume();
    const t0 = ctx.currentTime;
    [[880, 0, 0.18], [1318.5, 0.12, 0.3]].forEach(([freq, delay, dur]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + delay);
      gain.gain.exponentialRampToValueAtTime(0.22, t0 + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + delay + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + delay);
      osc.stop(t0 + delay + dur + 0.05);
    });
  } catch (_) { /* Sound ist Bonus — nie die Pipeline stören */ }
}

function Logo() {
  // Echte Logo-Datei aus dem Screenshot. Chris legt sie als static/logo.svg ab
  // (die Wortmarke „//MEINFLUSS" ist im Bild bereits enthalten → separater Text entfällt).
  return <img className="mark" src="/static/logo.svg" alt="MEINFLUSS" style={{ height: 32, width: "auto", display: "block" }} />;
}

function Card({ num, icon, title, sub, action, children }) {
  return (
    <section className="card">
      <div className="card-head">
        {num != null && <div className="card-num">{num}</div>}
        {icon && <div className="card-ico">{icon}</div>}
        <div>
          <h2>{title}</h2>
          {sub && <div className="sub">{sub}</div>}
        </div>
        <div className="grow" />
        {action}
      </div>
      {children}
    </section>
  );
}

function Pill({ kind, children }) {
  return <span className={"pill pill-" + kind}>{children}</span>;
}

/* ===== Pipeline-Schritte ===== */
const STEPS = [
  { key: "analyze", icon: Ico.search,   title: "Originalvideo wird analysiert", desc: "Segmente, Sprache & Highlights erkennen" },
  { key: "plan",    icon: Ico.brain,    title: "Rohschnitt wird geplant",       desc: "Füllwörter, Pausen & Wiederholungen aussortieren" },
  { key: "cut",     icon: Ico.scissors, title: "Rohschnitt wird erstellt",      desc: "Schnitte rendern & finales Video exportieren" },
];

function fmtMs(ms) {
  if (ms == null) return "—";
  const s = ms / 1000;
  const mm = Math.floor(s / 60);
  const ss = (s - mm * 60).toFixed(1);
  return mm > 0 ? `${mm}:${ss.padStart(4, "0")}` : `${s.toFixed(2)}s`;
}

/* ===== Video Analyst Seite ===== */
const ANALYST_FEATURES = [
  { ico: "🎯", title: "Inhaltsanalyse",    desc: "Themen, Kernaussagen & Story-Struktur erkennen" },
  { ico: "🎙️", title: "Sprach-Qualität",   desc: "Füllwörter, Pausen, Sprechtempo & Verständlichkeit" },
  { ico: "✂️",  title: "Schnitt-Bewertung", desc: "Übergänge, Pacing & Engagement-Kurve" },
  { ico: "📊", title: "Performance-Score", desc: "Plattform-Potenzial & konkrete Optimierungstipps" },
];

function fmtSec(s) {
  if (s == null) return "—";
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s - mm * 60);
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

function RatingDots({ value }) {
  return (
    <span className="analyst-dots">
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={"analyst-dot" + (i <= value ? " on" : "")} />
      ))}
    </span>
  );
}

// Score-Chip mit Hover-Info (ⓘ): spart Platz, Detail nur bei Bedarf.
function ScoreChip({ label, score, detail }) {
  const has = detail && String(detail).trim().length > 0;
  return (
    <div className="sc-chip">
      <div className="sc-top">
        <span className="sc-label">{label}</span>
        {has && (
          <span className="sc-info" tabIndex={0} aria-label={detail}>
            ⓘ<span className="sc-tip">{detail}</span>
          </span>
        )}
      </div>
      <div className="sc-row">
        <RatingDots value={score || 0} />
        <span className="sc-num">{score ? `${score}/5` : "–"}</span>
      </div>
    </div>
  );
}

// Detail-Text für die Struktur: Kommentar + erkannte Bausteine.
function strukturDetail(struktur) {
  if (!struktur) return "";
  const e = struktur.elemente || {};
  const order = [["hook", "Hook"], ["bridge", "Bridge"], ["mid", "Mid"], ["peak", "Peak"], ["cta", "CTA"]];
  const bausteine = order.map(([k, lbl]) => `${lbl}${e[k] ? "✓" : "✗"}`).join(" · ");
  return [struktur.kommentar, "Bausteine: " + bausteine].filter(Boolean).join("  —  ");
}

function problemeDetail(block) {
  const p = block?.probleme || [];
  return p.length ? p.join(" · ") : "Keine Auffälligkeiten";
}

function VideoAnalystPage() {
  const [analysisFile, setAnalysisFile] = useState(null);
  const [phase, setPhase] = useState("idle");   // idle | running | done
  const [progress, setProgress] = useState("");
  const [queueInfo, setQueueInfo] = useState(null);  // {ahead,total} während "queued"
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const fileRef = useRef(null);
  const cancelledRef = useRef(false);

  useEffect(() => () => { cancelledRef.current = true; }, []);

  function onPickFile(e) {
    const f = e.target.files?.[0];
    if (f) setAnalysisFile({ file: f, name: f.name, size: (f.size / 1024 / 1024).toFixed(1) + " MB" });
  }

  const canStart = !!analysisFile;

  async function pollUntilDone(runId) {
    while (!cancelledRef.current) {
      await new Promise((r) => setTimeout(r, 2000));
      const data = await api("GET", `/api/analyst/${runId}`);
      if (cancelledRef.current) return null;
      if (data.phase === "error") throw new Error(data.error || "Analyse fehlgeschlagen");
      setQueueInfo(data.phase === "queued" ? (data.queue || null) : null);
      setProgress(data.detail || "Analyse läuft …");
      if (data.done && data.result) return data.result;
    }
    return null;
  }

  async function startAnalysis() {
    if (!canStart || phase === "running") return;
    setError("");
    setPhase("running");
    setResult(null);
    try {
      setProgress("Video wird hochgeladen …");
      const fd = new FormData();
      fd.append("file", analysisFile.file);
      const up = await api("POST", "/api/analyst/upload", fd);
      setProgress("Analyse startet …");
      await api("POST", `/api/analyst/${up.id}/start`);
      const res = await pollUntilDone(up.id);
      if (res) {
        setResult(res);
        setPhase("done");
      }
    } catch (e) {
      setError(e.message);
      setPhase("idle");
    }
  }

  function reset() {
    setPhase("idle");
    setAnalysisFile(null);
    setResult(null);
    setProgress("");
    setQueueInfo(null);
    setError("");
  }

  return (
    <>
      {error && (
        <div className="alert alert-err">
          <Ico.x style={{ marginTop: 1, flex: "0 0 auto" }} />
          <div>{error}</div>
        </div>
      )}

      {/* Eingabe */}
      <Card icon={<Ico.upload />} title="Video-Quelle" sub="Lade dein Video hoch">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <input
            ref={fileRef}
            type="file"
            accept="video/*"
            style={{ display: "none" }}
            onChange={onPickFile}
          />
          <div
            className={"dropzone" + (analysisFile ? " has" : "")}
            onClick={() => fileRef.current?.click()}
          >
            <div className="dz-ico"><Ico.upload /></div>
            <div className="dz-title">
              {analysisFile ? analysisFile.name : "Video auswählen oder hierher ziehen"}
            </div>
            <div className="dz-sub">
              {analysisFile ? analysisFile.size : "MP4, MOV, AVI, MKV · max. 4 GB"}
            </div>
          </div>
          {analysisFile && (
            <div>
              <button className="btn btn-ghost" style={{ padding: "9px 16px", fontSize: 13 }} onClick={() => setAnalysisFile(null)}>
                <Ico.x width="14" height="14" /> Datei entfernen
              </button>
            </div>
          )}
        </div>
      </Card>

      {/* Analyse-Umfang + Start-Button */}
      <Card icon={<Ico.chart />} title="Analyse-Umfang" sub="Was der AI Video Analyst prüft">
        <div className="analyst-features">
          {ANALYST_FEATURES.map((f) => (
            <div key={f.title} className="analyst-feature">
              <span className="analyst-feature-ico">{f.ico}</span>
              <div>
                <div className="analyst-feature-title">{f.title}</div>
                <div className="analyst-feature-desc">{f.desc}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="analyst-action">
          <button
            className="btn btn-primary analyst-start-btn"
            disabled={!canStart || phase === "running"}
            onClick={startAnalysis}
          >
            {phase === "running" ? (
              <><span className="spinner" /> {progress || "Analyse läuft …"}</>
            ) : (
              <><Ico.play /> Videoanalyse starten</>
            )}
          </button>
          {phase === "running" && queueInfo && queueInfo.total > 1 && (
            <div className="analyst-queue">
              ⏳ In Warteschlange — Platz {queueInfo.ahead + 1} von {queueInfo.total}. Deine Analyse startet automatisch, sobald sie an der Reihe ist.
            </div>
          )}
          {phase === "running" && (
            <div className="muted" style={{ fontSize: 13 }}>
              Dein Video wird transkribiert, Bild und Ton werden analysiert und anschließend bewertet. Je nach Videolänge kann das etwas dauern.
            </div>
          )}
          {!canStart && (
            <div className="muted" style={{ fontSize: 13 }}>
              Bitte wähle zuerst eine Videodatei aus.
            </div>
          )}
        </div>
      </Card>

      {/* Ergebnis */}
      {phase === "done" && result && (
        <Card
          icon={<Ico.check />}
          title="Analyse abgeschlossen"
          sub={`${result.filename} · ${fmtSec(result.duration_sec)} · ${result.scene_count} Szenen`}
          action={
            <button className="btn btn-ghost" style={{ padding: "9px 14px", fontSize: 13 }} onClick={reset}>
              <Ico.refresh width="15" height="15" /> Neue Analyse
            </button>
          }
        >
          {result.evaluation && (
            <div className="analyst-eval">
              <div className="analyst-score">
                <div className="analyst-score-num">{result.evaluation.performance_score}</div>
                <div className="analyst-score-label">Performance-Score von 100</div>
                {result.evaluation.funnel && (
                  <span className="analyst-funnel">{result.evaluation.funnel}</span>
                )}
              </div>

              {result.evaluation.zielgruppe && (
                <div className="analyst-zielgruppe">🎯 {result.evaluation.zielgruppe}</div>
              )}

              <div className="sc-grid">
                <ScoreChip
                  label="🎤 Sprech-Hook"
                  score={result.evaluation.hook?.sprech_hook_score}
                  detail={result.evaluation.hook?.sprech_hook_grund}
                />
                {result.evaluation.hook?.text_hook_vorhanden && (
                  <ScoreChip
                    label="📝 Text-Hook"
                    score={result.evaluation.hook?.text_hook_score}
                    detail={result.evaluation.hook?.text_hook_grund}
                  />
                )}
                <ScoreChip
                  label="📖 Struktur"
                  score={result.evaluation.struktur?.score}
                  detail={strukturDetail(result.evaluation.struktur)}
                />
                <ScoreChip
                  label="🎙️ Sprechqualität"
                  score={result.evaluation.sprechqualitaet?.score}
                  detail={problemeDetail(result.evaluation.sprechqualitaet)}
                />
                <ScoreChip
                  label="✂️ Schnitt & Pacing"
                  score={result.evaluation.schnitt_pacing?.score}
                  detail={result.evaluation.schnitt_pacing?.kommentar}
                />
                <ScoreChip
                  label="📈 Spannungsbogen"
                  score={result.evaluation.spannungsbogen?.score}
                  detail={result.evaluation.spannungsbogen?.kommentar}
                />
                <ScoreChip
                  label="🎨 Visuelle Ästhetik"
                  score={result.evaluation.visuelle_aesthetik?.score}
                  detail={problemeDetail(result.evaluation.visuelle_aesthetik)}
                />
              </div>

              {result.evaluation.top_tipps?.length > 0 && (
                <div className="analyst-eval-block analyst-tipps">
                  <div className="analyst-eval-title">🚀 Top-Tipps</div>
                  <ul className="analyst-list">
                    {result.evaluation.top_tipps.map((t, i) => <li key={i}>{t}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {!result.evaluation && (
            <div className="alert alert-info" style={{ marginBottom: 16 }}>
              Die Bewertung konnte nicht erstellt werden. Unten findest du die Detailanalyse des Videos.
            </div>
          )}

          {result.audio_overview && (
            <div className="analyst-eval-block" style={{ marginBottom: 16 }}>
              <div className="analyst-eval-title">🔊 Audio (ganzes Video)</div>
              <div className="analyst-eval-text">{result.audio_overview}</div>
            </div>
          )}

          {result.gaze_overview && (
            <div className="analyst-eval-block" style={{ marginBottom: 16 }}>
              <div className="analyst-eval-title">👀 Blickkontakt (ganzes Video)</div>
              <div className="analyst-eval-text">{result.gaze_overview}</div>
            </div>
          )}

          <details className="analyst-rawdump" open>
            <summary>🔬 Detailanalyse ({result.scenes.length} Segmente)</summary>
            <div className="analyst-scenes" style={{ marginTop: 12 }}>
              {result.scenes.map((s) => (
                <div key={s.index} className="analyst-scene">
                  <div className="analyst-scene-time">
                    Segment {s.index + 1}<br />{fmtSec(s.start)}–{fmtSec(s.end)}
                  </div>
                  <div className="analyst-scene-body">
                    {s.error ? (
                      <div className="analyst-scene-err">{s.error}</div>
                    ) : (
                      <>
                        <div><strong>Handlung:</strong> {s.handlung || s.beschreibung}</div>
                        {s.personen && <div className="analyst-scene-meta">👤 {s.personen}</div>}
                        {(s.texte && s.texte.length > 0) ? (
                          s.texte.map((t, i) => (
                            <div key={i} className="analyst-scene-meta">
                              📝 Text: <strong>„{t.wortlaut}"</strong>{t.darstellung ? <span> — {t.darstellung}</span> : null}
                            </div>
                          ))
                        ) : (
                          <div className="analyst-scene-meta">
                            📝 Text im Bild: {s.text_overlays ? <strong>„{s.text_overlays}"</strong> : "(keiner erkannt)"}
                          </div>
                        )}
                        {s.gesprochener_text && <div className="analyst-scene-meta">🗣️ Gesprochen: {s.gesprochener_text}</div>}
                        {s.kamera && <div className="analyst-scene-meta">🎥 {s.kamera}</div>}
                        {s.bild_fakten && (
                          <div className="analyst-scene-meta">
                            🖼️ Komposition: {s.bild_fakten.komposition} · Licht: {s.bild_fakten.licht} · Hintergrund: {s.bild_fakten.hintergrund}
                          </div>
                        )}
                        {s.effekte && <div className="analyst-scene-meta">✨ Effekte: {s.effekte}</div>}
                        {s.raw && (
                          <details className="analyst-rawjson">
                            <summary>Rohtext (1:1)</summary>
                            <pre>{s.raw}</pre>
                          </details>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </details>

          {result.transcript && (
            <details className="analyst-transcript">
              <summary>Transkript anzeigen</summary>
              <p>{result.transcript}</p>
            </details>
          )}
        </Card>
      )}
    </>
  );
}

/* ===== Video Editor Seite ===== */
/* ===== Untertitel-Overlay (Bold Pop, Wort-für-Wort synchron, per Drag verschiebbar) ===== */
function SubtitleOverlay({ videoRef, doc, onPositionChange }) {
  const [time, setTime] = useState(0);
  const [pos, setPos] = useState(doc.position || { x_pct: 50, y_pct: 65 });
  const [boxH, setBoxH] = useState(0);
  const [boxW, setBoxW] = useState(0);
  const overlayRef = useRef(null);
  const dragRef = useRef(null);
  // Refs für die Event-Handler: immer aktuelle Position + Callback, ohne dass die
  // window-Listener bei jedem Render neu registriert werden müssen.
  const posRef = useRef(pos);
  const onPositionChangeRef = useRef(onPositionChange);
  useEffect(() => { onPositionChangeRef.current = onPositionChange; }, [onPositionChange]);

  useEffect(() => {
    const p = doc.position || { x_pct: 50, y_pct: 65 };
    posRef.current = p;
    setPos(p);
  }, [doc]);

  // Player-Sync über requestAnimationFrame (flüssiger als timeupdate-Events)
  useEffect(() => {
    let raf;
    const tick = () => {
      const v = videoRef.current;
      if (v) setTime(v.currentTime);
      const o = overlayRef.current;
      if (o) { setBoxH(o.clientHeight); setBoxW(o.clientWidth); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videoRef]);

  // Drag: Box greifen → Position in % des Video-Bereichs, beim Loslassen speichern.
  // WICHTIG: Listener werden genau EINMAL registriert ([]-Deps) und das Speichern
  // passiert direkt im Event-Handler — NIE als Seiteneffekt in einem setState-Updater.
  // (Updater müssen pur sein; React darf sie bei interleaved Updates mehrfach ausführen —
  // das hat mit laufendem Video die Seite eingefroren.)
  useEffect(() => {
    function onMove(e) {
      if (!dragRef.current || !overlayRef.current) return;
      const r = overlayRef.current.getBoundingClientRect();
      const x = Math.max(5, Math.min(95, ((e.clientX - r.left) / r.width) * 100));
      const y = Math.max(5, Math.min(95, ((e.clientY - r.top) / r.height) * 100));
      const next = { x_pct: x, y_pct: y };
      posRef.current = next;
      setPos(next);
    }
    function onUp() {
      if (dragRef.current) {
        dragRef.current = null;
        onPositionChangeRef.current(posRef.current);
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  const style = doc.style_def || {};
  const phrase = (doc.phrases || []).find((p) => time >= p.start && time < p.display_end);
  const visible = phrase ? phrase.words.filter((w) => time >= w.start) : [];
  // Zeilenumbruch: line_break_after = Index des letzten Worts von Zeile 1 (vom Backend,
  // Fallback: einzeilig). Sichtbare Wörter werden auf die Zeilen verteilt.
  const lineBreakAfter = phrase && phrase.line_break_after != null ? phrase.line_break_after : null;
  const line1 = [];
  const line2 = [];
  visible.forEach((w, i) => {
    if (lineBreakAfter !== null && i > lineBreakAfter) line2.push({ w, i });
    else line1.push({ w, i });
  });
  // Größe: font_size (Default 16) = px bei 480p-Referenz, skaliert mit Player-Höhe.
  // Rand-Garantie: identische Formel wie build_ass — Maß ist die LÄNGSTE ZEILE
  // der vollen Phrase (kein Größen-Sprung während Wörter erscheinen).
  const basePx = Math.max(8, ((doc.font_size || 24) * boxH) / 480);
  let longestLine = 0;
  if (phrase) {
    const texts = phrase.words.map((w) => w.text);
    if (lineBreakAfter === null) {
      longestLine = texts.join(" ").length;
    } else {
      longestLine = Math.max(
        texts.slice(0, lineBreakAfter + 1).join(" ").length,
        texts.slice(lineBreakAfter + 1).join(" ").length
      );
    }
  }
  const maxPx = longestLine > 0 ? (boxW * 0.80) / (0.68 * longestLine) : basePx;
  const fontSize = Math.max(8, Math.min(basePx, maxPx));

  const renderWord = ({ w, i }, isLast) => (
    <span
      key={phrase.start + "-" + i}
      style={{
        display: "inline-block",
        marginRight: isLast ? 0 : "0.28em",
        color: w.highlight ? (doc.highlight_color || style.highlight_color || "#00D2FF") : undefined,
        animation: `subPop ${(style.pop_in_ms || 110) / 1000}s ease-out`,
      }}
    >
      {w.text}
    </span>
  );

  return (
    <div ref={overlayRef} style={{ position: "absolute", inset: 0, overflow: "hidden", pointerEvents: "none" }}>
      <style>{`@keyframes subPop { from { transform: scale(.9); } to { transform: scale(1); } }`}</style>
      {visible.length > 0 && (
        <div
          onMouseDown={(e) => { e.preventDefault(); dragRef.current = true; }}
          style={{
            position: "absolute",
            left: pos.x_pct + "%",
            top: pos.y_pct + "%",
            transform: "translate(-50%, -50%)",
            pointerEvents: "auto",
            cursor: "grab",
            whiteSpace: "nowrap",
            textAlign: "center",
            userSelect: "none",
            // Genau die gewählte (gebündelte) Familie — KEIN synthetisches Bold,
            // das Gewicht steckt in der Datei. So rendert der Browser identisch zu libass.
            fontFamily: `"${doc.font_family || "Montserrat Black"}", "Montserrat Black", sans-serif`,
            fontWeight: "normal",
            textTransform: "uppercase",
            fontSize: fontSize + "px",
            letterSpacing: "0.5px",
            lineHeight: 1.15,
            color: doc.text_color || style.text_color || "#FFFFFF",
            textShadow: "0 2px 14px rgba(0,0,0,.85), 0 1px 4px rgba(0,0,0,.7)",
          }}
        >
          <div>{line1.map((x, k) => renderWord(x, k === line1.length - 1))}</div>
          {line2.length > 0 && (
            <div>{line2.map((x, k) => renderWord(x, k === line2.length - 1))}</div>
          )}
        </div>
      )}
    </div>
  );
}

/* ===== MultiCut: KPI-Badge (1-5) ===== */
function KpiBadge({ score }) {
  return (
    <span className={"kpi-badge kpi-" + score} title={`KPI-Erwartung ${score}/5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={"kpi-dot" + (i <= score ? " on" : "")} />
      ))}
      <b>{score}/5</b>
    </span>
  );
}

/* ===== MultiCut: Konfetti-Burst bei fertigem Short ===== */
function ConfettiBurst() {
  const pieces = Array.from({ length: 14 }, (_, i) => i);
  const colors = ["#BD9F66", "#A6863F", "#5E7A47", "#00D2FF", "#E8B84B"];
  return (
    <div className="confetti" aria-hidden="true">
      {pieces.map((i) => (
        <span
          key={i}
          style={{
            left: 8 + Math.random() * 84 + "%",
            background: colors[i % colors.length],
            animationDelay: Math.random() * 0.25 + "s",
            "--cx": (Math.random() * 120 - 60).toFixed(0) + "px",
            "--cr": (Math.random() * 540 - 270).toFixed(0) + "deg",
          }}
        />
      ))}
    </div>
  );
}

function fmtDur(sec) {
  if (sec == null) return "—";
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60);
  return `${m}:${String(Math.round(sec - m * 60)).padStart(2, "0")} min`;
}

/* ===== MultiCut: Kachel ===== */
function ShortTile({ projectId, short, locked, celebrating, onToggle }) {
  const [expanded, setExpanded] = useState(false);
  const isDone = short.status === "done";
  const isCutting = short.status === "cutting";
  const isError = short.status === "error";
  const previewUrl = `/api/shorts/${projectId}/${short.id}/preview`;

  return (
    <div className={
      "mc-tile" + (short.selected ? "" : " off") + (isDone ? " done" : "") +
      (isCutting ? " cutting" : "") + (celebrating ? " celebrate" : "")
    }>
      {celebrating && <ConfettiBurst />}
      <div className="mc-tile-head">
        <label className="mc-check" title={short.selected ? "Abwählen" : "Auswählen"}>
          <input
            type="checkbox"
            checked={short.selected}
            disabled={locked || isDone}
            onChange={(e) => onToggle(short.id, e.target.checked)}
          />
        </label>
        <div className="mc-title">
          <b>{short.index}. {short.title}</b>
          <div className="mc-meta">
            <span className="mc-dur">{fmtDur(short.duration_sec)}</span>
            <span className="mc-range">{short.start_sec.toFixed(0)}s → {short.end_sec.toFixed(0)}s</span>
          </div>
        </div>
        <KpiBadge score={short.kpi_score} />
      </div>

      <div className={"mc-transcript" + (expanded ? " open" : "")} onClick={() => setExpanded(!expanded)}>
        „{short.transcript}"
      </div>
      {short.rationale && <div className="mc-rationale">{short.rationale}</div>}

      <div className="mc-tile-foot">
        {isCutting && <span className="mc-status cutting"><span className="spinner" /> Wird geschnitten …</span>}
        {isError && <span className="mc-status error" title={short.error}>✗ Fehler — beim nächsten Lauf erneut</span>}
        {isDone && <span className="mc-status done"><Ico.check width="14" height="14" /> Fertig</span>}
        {!isCutting && !isError && !isDone && (
          <span className="mc-status pending">{short.selected ? "Bereit" : "Abgewählt"}</span>
        )}
      </div>

      {isDone && (
        <div className="mc-preview">
          <video controls preload="metadata" src={previewUrl} />
          <a className="btn btn-soft mc-dl" href={previewUrl} download={`${short.id}.mp4`}>
            <Ico.download width="14" height="14" /> Download
          </a>
        </div>
      )}
    </div>
  );
}

/* ===== MultiCut: kompletter Flow (Analyse → Vorschläge → Auswahl → Batch) ===== */
function MultiCutFlow({ project, onProjectChanged }) {
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);       // Analyse + Planung läuft
  const [busyMsg, setBusyMsg] = useState("");
  const [polling, setPolling] = useState(false); // Batch läuft → 2s-Polling
  const [postProcessing, setPostProcessing] = useState("segment");
  const [error, setError] = useState("");
  const [celebrating, setCelebrating] = useState({});
  const prevStatusRef = useRef({});

  const hasFiles = project.files && project.files.length > 0;

  // Plan-Update zentral: erkennt frisch fertige Shorts → Chime + Konfetti
  function applyPlan(p) {
    const prev = prevStatusRef.current;
    const fresh = (p.shorts || []).filter((s) => s.status === "done" && prev[s.id] && prev[s.id] !== "done");
    if (fresh.length > 0) {
      playDoneChime();
      setCelebrating((c) => {
        const next = { ...c };
        fresh.forEach((s) => { next[s.id] = true; });
        return next;
      });
      fresh.forEach((s) => {
        setTimeout(() => setCelebrating((c) => { const n = { ...c }; delete n[s.id]; return n; }), 1800);
      });
    }
    prevStatusRef.current = Object.fromEntries((p.shorts || []).map((s) => [s.id, s.status]));
    setPlan(p);
    if (p.post_processing) setPostProcessing(p.post_processing);
  }

  // Beim Projektwechsel: bestehenden Plan laden, ggf. Polling fortsetzen
  useEffect(() => {
    let cancelled = false;
    prevStatusRef.current = {};
    setPlan(null); setError(""); setCelebrating({});
    api("GET", `/api/shorts/${project.id}`)
      .then((p) => {
        if (cancelled) return;
        prevStatusRef.current = Object.fromEntries((p.shorts || []).map((s) => [s.id, s.status]));
        setPlan(p);
        if (p.post_processing) setPostProcessing(p.post_processing);
        if (p.batch_status === "running" && p.live !== false) setPolling(true);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [project.id]);

  // Batch-Polling alle 2s
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(async () => {
      try {
        const p = await api("GET", `/api/shorts/${project.id}`);
        applyPlan(p);
        if (p.batch_status === "done" || p.live === false) {
          setPolling(false);
          onProjectChanged && onProjectChanged();
        }
      } catch (_) {}
    }, 2000);
    return () => clearInterval(t);
  }, [polling, project.id]);

  async function findShorts() {
    setBusy(true); setError("");
    try {
      if (project.status === "created") {
        setBusyMsg("Langvideo wird analysiert … (Whisper — bei Podcast-Länge mehrere Minuten)");
        await api("POST", `/api/pipeline/${project.id}/analyse`);
      }
      setBusyMsg("Claude sucht die besten Stellen …");
      const p = await api("POST", `/api/shorts/${project.id}/plan`);
      prevStatusRef.current = Object.fromEntries((p.shorts || []).map((s) => [s.id, s.status]));
      setPlan(p);
      onProjectChanged && onProjectChanged();
    } catch (e) {
      setError("Shorts-Planung fehlgeschlagen: " + e.message);
    } finally {
      setBusy(false); setBusyMsg("");
    }
  }

  async function toggleShort(shortId, selected) {
    try {
      await api("PATCH", `/api/shorts/${project.id}/${shortId}`, { selected });
      setPlan((p) => ({
        ...p,
        shorts: p.shorts.map((s) => (s.id === shortId ? { ...s, selected } : s)),
      }));
    } catch (e) {
      setError("Auswahl speichern fehlgeschlagen: " + e.message);
    }
  }

  async function startBatch() {
    setError("");
    try {
      // AudioContext braucht eine User-Geste — hier ist sie (Klick auf Pipeline starten)
      try { if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) {}
      await api("POST", `/api/shorts/${project.id}/execute`, { post_processing: postProcessing });
      setPolling(true);
    } catch (e) {
      setError("Batch-Start fehlgeschlagen: " + e.message);
    }
  }

  const shorts = plan ? plan.shorts || [] : [];
  const nSelected = shorts.filter((s) => s.selected).length;
  const nDone = shorts.filter((s) => s.status === "done" && s.selected).length;
  const batchRunning = polling;
  const allDone = nSelected > 0 && nDone >= nSelected;
  const progressPct = nSelected ? Math.round((nDone / nSelected) * 100) : 0;
  const ctMeta = CONTENT_TYPES.find((c) => c.value === project.content_type);

  return (
    <Card
      num="3"
      title="MultiCut — Shorts aus dem Langvideo"
      sub={`Content-Typ: ${ctMeta ? ctMeta.label : project.content_type || "—"}`}
    >
      {error && (
        <div className="alert alert-err" style={{ marginBottom: 14 }}>
          <Ico.x style={{ marginTop: 1, flex: "0 0 auto" }} />
          <div>{error}</div>
        </div>
      )}

      {/* Schritt 1: Stellen finden */}
      {!plan && (
        <div className="mc-start">
          <p className="muted" style={{ margin: "0 0 14px", fontSize: 14 }}>
            Die KI analysiert das Langvideo und schlägt Stellen vor, die als eigenständige
            Shorts funktionieren — mit Transkript-Vorschau und KPI-Erwartung (1–5).
          </p>
          <button className="btn btn-primary" disabled={!hasFiles || busy} onClick={findShorts}>
            {busy ? <><span className="spinner" /> {busyMsg}</> : <><Ico.sparkles /> Shorts-Vorschläge finden</>}
          </button>
          {!hasFiles && (
            <div className="muted" style={{ fontSize: 13, marginTop: 10 }}>
              Lade zuerst dein Langvideo (z.B. Podcast) hoch.
            </div>
          )}
        </div>
      )}

      {/* Schritt 2+3: Kacheln + Batch */}
      {plan && (
        <>
          <div className="mc-toolbar">
            <div className="mc-progress-info">
              {batchRunning && <><span className="spinner" /> <b>{nDone}/{nSelected}</b>&nbsp;Shorts fertig</>}
              {!batchRunning && allDone && (
                <span style={{ color: "var(--ok-ink)", display: "inline-flex", alignItems: "center", gap: 7 }}>
                  <Ico.check width="16" height="16" /> Alle {nDone} Shorts fertig
                </span>
              )}
              {!batchRunning && !allDone && (
                <span className="muted">{shorts.length} Vorschläge · {nSelected} ausgewählt</span>
              )}
            </div>
            <div className="grow" />
            <button className="btn btn-ghost" style={{ padding: "9px 14px", fontSize: 13 }}
              disabled={busy || batchRunning} onClick={findShorts} title="Neu planen (Claude erneut fragen)">
              {busy ? <><span className="spinner" /> {busyMsg || "Plant …"}</> : <><Ico.refresh width="15" height="15" /> Neu planen</>}
            </button>
          </div>

          {(batchRunning || nDone > 0) && (
            <div className={"progress" + (batchRunning ? " running" : "")} style={{ marginBottom: 18 }}>
              <div className="bar" style={{ width: progressPct + "%" }} />
            </div>
          )}

          {plan.claude_reasoning && (
            <div className="mc-reasoning">{plan.claude_reasoning}</div>
          )}

          <div className="mc-grid">
            {shorts.map((s) => (
              <ShortTile
                key={s.id}
                projectId={project.id}
                short={s}
                locked={batchRunning}
                celebrating={!!celebrating[s.id]}
                onToggle={toggleShort}
              />
            ))}
          </div>

          <div className="mc-actions">
            <div className="field" style={{ flex: "0 1 340px", margin: 0 }}>
              <label className="label">Nachschnitt</label>
              <select className="select" value={postProcessing} disabled={batchRunning}
                onChange={(e) => setPostProcessing(e.target.value)}>
                {POST_PROCESSING_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="grow" />
            <button className="btn btn-primary" disabled={batchRunning || nSelected === 0 || busy} onClick={startBatch}>
              {batchRunning
                ? <><span className="spinner" /> Schneidet … ({nDone}/{nSelected})</>
                : allDone ? "Erneut schneiden" : <><Ico.scissors /> Pipeline starten ({nSelected} Shorts)</>}
            </button>
          </div>
        </>
      )}
    </Card>
  );
}

function VideoEditorPage() {
  const [projects, setProjects] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [newName, setNewName] = useState("");
  const [newPlatform, setNewPlatform] = useState("short");
  const [newEngine, setNewEngine] = useState("v3");
  const [multicutOn, setMulticutOn] = useState(() => localStorage.getItem(LS_MULTICUT) === "1");
  const [newContentType, setNewContentType] = useState("tofu");
  const [newSubsEnabled, setNewSubsEnabled] = useState(false);
  const [newSubStyle, setNewSubStyle] = useState("bold_pop_cyan");
  const [subStyles, setSubStyles] = useState([{ id: "bold_pop_cyan", label: "Bold Pop (Cyan)" }]);

  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);
  const [uploading, setUploading] = useState(false);

  const [phase, setPhase] = useState(-1);
  const [error, setError] = useState("");
  const [pipelineMsg, setPipelineMsg] = useState("");

  const [clips, setClips] = useState([]);
  const [claudeReasoning, setClaudeReasoning] = useState("");
  const [videoCacheBust, setVideoCacheBust] = useState(0);

  const [subtitling, setSubtitling] = useState(false);

  // Untertitel V1 (Bold Pop): JSON-Overlay + Drag + Burn-in
  const [subsDoc, setSubsDoc] = useState(null);
  const [subsBusy, setSubsBusy] = useState(false);
  const [burning, setBurning] = useState(false);
  const videoRef = useRef(null);

  const selected = projects.find((p) => p.id === selectedId);

  useEffect(() => { loadProjectList(); }, []);
  useEffect(() => {
    api("GET", "/api/subtitles/styles")
      .then((d) => d.styles && d.styles.length && setSubStyles(d.styles))
      .catch(() => {});
  }, []);

  // Preview zeigt IMMER den Rohschnitt + Live-Overlay — der Burn ist nur fürs
  // Downloaden. (Vorher wurde nach dem Download auf die eingebrannte Datei
  // umgeschaltet → Preview brach und Live-Einstellungen wären unsichtbar.)
  async function refreshSubsAndOutput(id, _status) {
    try {
      const doc = await api("GET", `/api/subtitles/${id}`);
      setSubsDoc(doc);
    } catch (_) { setSubsDoc(null); }
  }

  // Live-Einstellungen: lokal sofort anwenden (Overlay rendert direkt), dann speichern
  async function patchSubs(patch) {
    setSubsDoc((d) => (d ? { ...d, ...patch } : d));
    try { await api("PATCH", `/api/subtitles/${selectedId}/settings`, patch); } catch (_) {}
  }

  async function generateSubs() {
    if (!selectedId) return;
    setSubsBusy(true); setError("");
    try {
      const doc = await api("POST", `/api/subtitles/${selectedId}/generate`);
      setSubsDoc(doc);
    } catch (e) {
      setError("Untertitel-Generierung fehlgeschlagen: " + e.message);
    } finally { setSubsBusy(false); }
  }

  async function toggleSubs(enabled) { await patchSubs({ enabled }); }

  async function saveSubsPosition(pos) {
    // Funktionales Update — kein stale-subsDoc-Closure aus dem Drag-Listener
    setSubsDoc((d) => (d ? { ...d, position: pos } : d));
    try { await api("PATCH", `/api/subtitles/${selectedId}/settings`, { position: pos }); } catch (_) {}
  }

  function triggerDownload(url, filename) {
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
  }

  // Download mit integriertem Burn-in: sind Untertitel per Checkbox aktiv,
  // wird vor dem Download automatisch eingebrannt (subtitled_cut.mp4).
  async function downloadVideo() {
    if (!selected) return;
    if (subsDoc && subsDoc.enabled) {
      setBurning(true); setError("");
      try {
        await api("POST", `/api/subtitles/${selectedId}/burn`);
        // Preview bleibt auf dem Rohschnitt (Live-Overlay) — nur der Download
        // bekommt die eingebrannte Datei.
        triggerDownload(
          `/api/pipeline/${selectedId}/preview/subtitled_cut.mp4?t=${Date.now()}`,
          `${selected.name}_subtitled_cut.mp4`
        );
      } catch (e) {
        // Fallback: Download darf nie komplett blockieren — Rohschnitt liefern
        setError("Einbrennen fehlgeschlagen (" + e.message + ") — Rohschnitt ohne Untertitel wurde stattdessen heruntergeladen.");
        triggerDownload(
          `/api/pipeline/${selectedId}/preview/rough_cut.mp4?t=${Date.now()}`,
          `${selected.name}_rough_cut.mp4`
        );
      } finally { setBurning(false); }
    } else {
      triggerDownload(videoUrl, `${selected.name}_rough_cut.mp4`);
    }
  }

  async function loadProjectList(autoRestore = true) {
    setRefreshing(true);
    try {
      const data = await api("GET", "/api/projects/");
      const sorted = [...(data.projects || [])].sort(
        (a, b) => (b.created_at || "").localeCompare(a.created_at || "")
      );
      setProjects(sorted);
      if (autoRestore) {
        const lastId = localStorage.getItem(LS_LAST_PROJECT);
        const restoreId = lastId && sorted.find((p) => p.id === lastId) ? lastId : (sorted[0]?.id || "");
        setSelectedId(restoreId);
        if (restoreId) await loadProjectDetails(restoreId);
      }
    } catch (e) {
      setError("Projekt-Liste laden fehlgeschlagen: " + e.message);
    } finally {
      setTimeout(() => setRefreshing(false), 400);
    }
  }

  async function loadProjectDetails(id) {
    try {
      const proj = await api("GET", `/api/projects/${id}`);
      setProjects((ps) => ps.map((p) => (p.id === id ? { ...p, files: proj.files, status: proj.status, name: proj.name, platform: proj.platform, mode: proj.mode, content_type: proj.content_type } : p)));
      if (proj.mode === "multicut") {
        // MultiCut hat seinen eigenen Flow — Single-Pipeline-Phasen hier nicht anfassen
        setPhase(-1); setClips([]); setClaudeReasoning("");
        return;
      }
      if (proj.status === "cut" || proj.status === "subtitled") {
        setPhase(3);
        setVideoCacheBust(Date.now());
        await refreshSubsAndOutput(id, proj.status);
      } else if (proj.status === "planned") {
        try {
          const head = await fetch(`/api/pipeline/${id}/preview/rough_cut.mp4`, { method: "HEAD" });
          if (head.ok) {
            setPhase(3);
            setVideoCacheBust(Date.now());
            await refreshSubsAndOutput(id, proj.status);
          } else {
            setPhase(-1); setClips([]); setClaudeReasoning("");
          }
        } catch (_) {
          setPhase(-1); setClips([]); setClaudeReasoning("");
        }
      } else {
        setPhase(-1); setClips([]); setClaudeReasoning("");
      }
    } catch (e) {
      console.error("Projekt-Details laden fehlgeschlagen", e);
    }
  }

  async function onSelectProject(id) {
    setSelectedId(id);
    localStorage.setItem(LS_LAST_PROJECT, id);
    setFiles([]); setError(""); setPipelineMsg("");
    setSubsDoc(null); setOutputFile("rough_cut.mp4");
    await loadProjectDetails(id);
  }

  async function createProject() {
    if (!newName.trim()) { setError("Bitte gib einen Projektnamen ein."); return; }
    setError("");
    try {
      const p = await api("POST", "/api/projects/", {
        name: newName.trim().replace(/\s+/g, "_"),
        platform: multicutOn ? "short" : newPlatform,
        engine_version: newEngine,
        subtitles_enabled: multicutOn ? false : newSubsEnabled,
        subtitle_style: newSubStyle,
        mode: multicutOn ? "multicut" : "single",
        content_type: multicutOn ? newContentType : null,
      });
      localStorage.setItem(LS_LAST_PROJECT, p.id);
      setNewName(""); setPhase(-1); setClips([]); setClaudeReasoning(""); setFiles([]);
      await loadProjectList(false);
      setSelectedId(p.id);
    } catch (e) {
      setError("Projekt anlegen fehlgeschlagen: " + e.message);
    }
  }

  function onPick(e) {
    const picked = Array.from(e.target.files || []).map((f) => ({
      file: f, name: f.name, size: (f.size / 1024 / 1024).toFixed(1) + " MB",
    }));
    setFiles(picked);
  }
  function removeFile(i) { setFiles(files.filter((_, idx) => idx !== i)); }

  async function uploadFiles() {
    if (!files.length || !selectedId) return;
    setUploading(true); setError("");
    try {
      const form = new FormData();
      files.forEach((f) => form.append("files", f.file));
      await api("POST", `/api/projects/${selectedId}/upload`, form);
      setFiles([]);
      await loadProjectDetails(selectedId);
    } catch (e) {
      setError("Upload fehlgeschlagen: " + e.message);
    } finally {
      setUploading(false);
    }
  }

  async function runPipeline() {
    if (!selectedId) return;
    setError(""); setClips([]); setClaudeReasoning("");
    try {
      setPhase(0); setPipelineMsg("Originalvideo wird analysiert … (Whisper + Visual, 1-3 Min)");
      await api("POST", `/api/pipeline/${selectedId}/analyse`);

      setPhase(1); setPipelineMsg("Rohschnitt wird geplant …");
      const cutPlan = await api("POST", `/api/pipeline/${selectedId}/plan-cuts`);
      setClips(cutPlan.clips || []);
      setClaudeReasoning(cutPlan.claude_reasoning || "");

      setPhase(2); setPipelineMsg("Rohschnitt wird gerendert (~30s) …");
      await api("POST", `/api/feedback/${selectedId}/confirm`, { confirmed: true });
      await api("POST", `/api/pipeline/${selectedId}/execute-cut`);

      setPhase(3); setPipelineMsg("");
      setVideoCacheBust(Date.now());
      await loadProjectDetails(selectedId);
    } catch (e) {
      setError("Pipeline-Fehler: " + e.message);
      setPhase(-1); setPipelineMsg("");
    }
  }

  async function addSubtitles() {
    if (!selectedId) return;
    setSubtitling(true); setError("");
    try {
      await api("POST", `/api/pipeline/${selectedId}/add-subtitles`);
      setVideoCacheBust(Date.now());
      await loadProjectDetails(selectedId);
    } catch (e) {
      setError("Untertitel-Schritt fehlgeschlagen: " + e.message);
    } finally {
      setSubtitling(false);
    }
  }

  function stepState(idx) {
    if (phase === -1) return "pending";
    if (phase === 3) return "done";
    if (idx < phase) return "done";
    if (idx === phase) return "running";
    return "pending";
  }

  const progress = phase === -1 ? 0 : phase === 3 ? 100 : Math.round(((phase + 0.5) / 3) * 100);
  const hasFiles = selected && selected.files && selected.files.length > 0;
  const pipelineRunning = phase >= 0 && phase < 3;
  const isDone = phase === 3;
  const videoUrl = isDone && selected
    ? `/api/pipeline/${selected.id}/preview/rough_cut.mp4?t=${videoCacheBust}`
    : null;
  const showSubsOverlay = subsDoc && subsDoc.enabled;

  return (
    <>
      {error && (
        <div className="alert alert-err">
          <Ico.x style={{ marginTop: 1, flex: "0 0 auto" }} />
          <div>{error}</div>
        </div>
      )}

      {/* ===== MultiCut-Schalter — ganz oben ===== */}
      <div className={"mc-switch-card" + (multicutOn ? " active" : "")}>
        <div className="mc-switch-ico"><Ico.grid width="22" height="22" /></div>
        <div className="mc-switch-text">
          <b>🎬 MultiCut</b>
          <span>Ein langes Video (z.B. Podcast) hochladen — die KI findet die besten Stellen und schneidet daraus mehrere eigenständige Shorts.</span>
        </div>
        <label className="mc-switch">
          <input
            type="checkbox"
            checked={multicutOn}
            onChange={(e) => {
              setMulticutOn(e.target.checked);
              localStorage.setItem(LS_MULTICUT, e.target.checked ? "1" : "0");
            }}
          />
          <span className="mc-slider" />
        </label>
      </div>

      <Card
        icon={<Ico.folder />}
        title="Bestehende Projekte"
        sub="Wähle ein Projekt, um daran weiterzuarbeiten"
        action={
          <button className="btn btn-ghost btn-icon" onClick={() => loadProjectList(false)} title="Aktualisieren">
            <Ico.refresh style={refreshing ? { animation: "spin .7s linear infinite" } : null} />
          </button>
        }
      >
        <div className="field">
          <select className="select" value={selectedId} onChange={(e) => onSelectProject(e.target.value)}>
            <option value="">— Projekt wählen —</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.platform} · {p.status} · {p.id}
              </option>
            ))}
          </select>
        </div>
        {selected && (
          <div className="proj-status">
            <span className="dot" style={selected.status === "cut" || selected.status === "subtitled" ? { background: "var(--ok-ink)" } : null} />
            <div className="meta">
              <span><Ico.folder style={{ verticalAlign: "-3px", marginRight: 6, color: "var(--gold-deep)" }} /><b>{selected.name}</b></span>
              <Pill kind="platform">{selected.platform}</Pill>
              <Pill kind="platform">Engine {selected.engine_version || "v1"}</Pill>
              <Pill kind={selected.status === "cut" || selected.status === "subtitled" ? "done" : "created"}>
                {selected.status}
              </Pill>
              <span className="muted">
                Dateien: {selected.files && selected.files.length ? selected.files.join(", ") : "—"}
              </span>
            </div>
          </div>
        )}
      </Card>

      <Card
        num="1"
        title={multicutOn ? "Neues MultiCut-Projekt" : "Neues Projekt"}
        sub={multicutOn ? "Name und Content-Typ wählen — das Langvideo kommt in Schritt 2" : "Name, Zielplattform und Schnitt-Engine wählen"}
      >
        <div className="row">
          <div className="field" style={{ flex: "2 1 220px" }}>
            <label className="label">Projektname</label>
            <input className="input" placeholder={multicutOn ? "z.B. Podcast_Folge_12" : "z.B. TikTok_Juni_01"} value={newName}
              onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createProject()} />
          </div>
          {multicutOn ? (
            <div className="field" style={{ flex: "2 1 280px" }}>
              <label className="label">Content-Typ</label>
              <select className="select" value={newContentType} onChange={(e) => setNewContentType(e.target.value)}>
                {CONTENT_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
          ) : (
            <>
              <div className="field" style={{ flex: "1 1 160px" }}>
                <label className="label">Plattform</label>
                <select className="select" value={newPlatform} onChange={(e) => setNewPlatform(e.target.value)}>
                  {PLATFORMS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
              <div className="field" style={{ flex: "1 1 160px" }}>
                <label className="label">Schnitt-Engine</label>
                <select className="select" value={newEngine} onChange={(e) => setNewEngine(e.target.value)}>
                  <option value="v1">v1 — Master-Prompt</option>
                  <option value="v2">v2 — Hybrid (Visual-Priorität)</option>
                  <option value="v3">v3 — Hybrid (Verständlichkeits-Priorität)</option>
                  <option value="v4">v4 — Hybrid + lokale Audio-Wahrheit (VAD)</option>
                  <option value="v5">v5 — Local-First (ohne Gemini)</option>
                  <option value="v5.2">v5.2 — Local-First + Feinschliff (Whisper small)</option>
                  <option value="v5.3">v5.3 — wie v5.2 · Transkript: Whisper large-v3</option>
                  <option value="v5.4">v5.4 — wie v5.2 · Transkript: CrisperWhisper</option>
                </select>
              </div>
              <div className="field" style={{ flex: "1 1 180px" }}>
                <label className="label">Untertitel-Stil</label>
                <select className="select" value={newSubStyle} onChange={(e) => setNewSubStyle(e.target.value)}>
                  {subStyles.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
            </>
          )}
          <div className="field" style={{ justifyContent: "flex-end" }}>
            <button className="btn btn-primary" onClick={createProject}><Ico.plus /> Projekt erstellen</button>
          </div>
        </div>
        {!multicutOn && (
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 12, fontSize: 14, cursor: "pointer" }}>
            <input type="checkbox" checked={newSubsEnabled} onChange={(e) => setNewSubsEnabled(e.target.checked)} />
            Untertitel automatisch nach dem Rohschnitt erstellen
          </label>
        )}
        <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          {multicutOn ? (
            <>TOFU = kurze, polarisierende Statements (max. 15s) für Reichweite. MOFU = Insights, How-tos
            und kompakte Takeaways (30–60s) für Vertrauen & Watchtime. Shorts werden 9:16 mittig gecroppt.</>
          ) : (
            <>v1 = Claude generiert Schnitt-Zeiten direkt. v2 = Code gruppiert Sätze + visuelle Priorität.
            v3 = Verständlichkeit als Hauptkriterium. v4 = wie v3, plus lokale Audio-Wahrheit (Silero VAD).
            v5 = komplett ohne Gemini (~3 Min schnellere Analyse, keine Quota). v5.2 = v5 + Feinschliff (MVP).
            v5.3 / v5.4 = identisch zu v5.2, nur anderes Transkript-Modell (large-v3 bzw. CrisperWhisper) — zum A/B-Vergleich der Transkript-Qualität. Hinweis: large-v3 und CrisperWhisper laden beim ersten Lauf ein paar GB herunter und sind auf CPU spürbar langsamer als small.</>
          )}
        </div>
      </Card>

      {selectedId && (
        <Card num="2" title="Dateien hochladen" sub="Rohdateien — Videos, ggf. eigene Sounds & Animationen">
          <input ref={fileRef} type="file" multiple accept="video/*,audio/*" style={{ display: "none" }} onChange={onPick} />
          <div className={"dropzone" + (files.length ? " has" : "")} onClick={() => fileRef.current && fileRef.current.click()}>
            <div className="dz-ico"><Ico.upload /></div>
            <div className="dz-title">{files.length ? files.length + " Datei(en) ausgewählt" : "Dateien auswählen"}</div>
            <div className="dz-sub">MP4, MOV, WAV · mehrere Dateien möglich</div>
          </div>
          {files.length > 0 && (
            <>
              <div className="filelist">
                {files.map((f, i) => (
                  <div className="fileitem" key={i}>
                    <span className="fi-ico"><Ico.film /></span>
                    <span className="fi-name">{f.name}</span>
                    <span className="fi-size">{f.size}</span>
                    <button className="fi-rm" onClick={() => removeFile(i)}><Ico.x /></button>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 18 }}>
                <button className="btn btn-primary" onClick={uploadFiles} disabled={uploading}>
                  {uploading ? <><span className="spinner" /> Wird hochgeladen …</> : <><Ico.upload width="18" height="18" /> Hochladen</>}
                </button>
              </div>
            </>
          )}
        </Card>
      )}

      {/* MultiCut-Projekte bekommen ihren eigenen Flow — Single-Pipeline bleibt unberührt */}
      {selectedId && selected && selected.mode === "multicut" && (
        <MultiCutFlow project={selected} onProjectChanged={() => loadProjectDetails(selectedId)} />
      )}

      {selectedId && (!selected || selected.mode !== "multicut") && (
        <Card num="3" title="Pipeline" sub="KI-Schnitt in drei Schritten — automatisch">
          <div className="steps">
            {STEPS.map((s, i) => {
              const st = stepState(i);
              return (
                <div className={"step " + st} key={s.key}>
                  <div className="st-ico">{st === "done" ? <Ico.check /> : s.icon()}</div>
                  <div className="st-body">
                    <div className="st-title">{s.title}</div>
                    <div className="st-desc">{s.desc}</div>
                  </div>
                  {st === "running" && <span className="spinner" />}
                  <span className="st-state">
                    {st === "pending" ? "Wartet" : st === "running" ? "Läuft…" : "Fertig"}
                  </span>
                </div>
              );
            })}
          </div>
          <div className={"progress" + (pipelineRunning ? " running" : "")}>
            <div className="bar" style={{ width: progress + "%" }} />
          </div>
          <div className="pipeline-foot">
            <div className="pipeline-status">
              {phase === -1 && <span className="muted" style={{ fontWeight: 500 }}>Bereit zum Start</span>}
              {pipelineRunning && <><span className="spinner" /> {pipelineMsg || "Video wird verarbeitet …"}</>}
              {isDone && <span style={{ color: "var(--ok-ink)", display: "inline-flex", alignItems: "center", gap: 8 }}><Ico.check width="16" height="16" /> Schnitt abgeschlossen</span>}
            </div>
            <button className="btn btn-primary" disabled={!hasFiles || pipelineRunning} onClick={runPipeline}>
              {isDone ? "Erneut schneiden" : "Pipeline starten"}
            </button>
          </div>
          {!hasFiles && phase === -1 && (
            <div className="muted" style={{ fontSize: 13, marginTop: 12 }}>
              Lade zuerst Rohmaterial hoch, um die Pipeline zu starten.
            </div>
          )}
        </Card>
      )}

      {isDone && selected && videoUrl && selected.mode !== "multicut" && (
        <Card icon={<Ico.film />} title="Fertiges Video" sub="Vorschau, Download & Untertitel">
          <div className="result">
            <div className="result-preview" style={{ position: "relative" }}>
              <video ref={videoRef} controls preload="metadata" src={videoUrl} />
              {showSubsOverlay && (
                <SubtitleOverlay videoRef={videoRef} doc={subsDoc} onPositionChange={saveSubsPosition} />
              )}
            </div>
            <div className="result-info">
              <h3>{selected.name}_rough_cut.mp4</h3>
              <div className="muted" style={{ fontSize: 14 }}>
                {clips.length} Schnitt-Clips · {selected.platform} · Status: {selected.status}
              </div>
              <div className="ri-meta">
                <Pill kind="done">Schnitt fertig</Pill>
                <Pill kind="platform">{selected.platform}</Pill>
                {subsDoc && <Pill kind="platform">UT: {(subsDoc.style_def && subsDoc.style_def.label) || subsDoc.style}</Pill>}
                {selected.status === "subtitled" && <Pill kind="done">mit Untertiteln</Pill>}
              </div>
              <div className="ri-actions">
                <button className="btn btn-primary" onClick={downloadVideo} disabled={burning}>
                  {burning
                    ? <><span className="spinner" /> Untertitel werden eingebrannt …</>
                    : <><Ico.download /> Herunterladen{subsDoc && subsDoc.enabled ? " (mit Untertiteln)" : ""}</>}
                </button>
                {!subsDoc && (
                  <button className="btn btn-soft" onClick={generateSubs} disabled={subsBusy}>
                    {subsBusy ? <><span className="spinner" /> Whisper synchronisiert …</> : <><Ico.caption /> Untertitel erstellen</>}
                  </button>
                )}
              </div>
              {subsDoc && (
                <div style={{ marginTop: 14, padding: 14, border: "1px solid var(--line, #e3ddd2)", borderRadius: 10, display: "flex", flexDirection: "column", gap: 12 }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>Untertitel-Einstellungen <span className="muted" style={{ fontWeight: 400 }}>— live in der Vorschau</span></div>

                  <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 14, cursor: "pointer" }}>
                    <input type="checkbox" checked={!!subsDoc.enabled} onChange={(e) => toggleSubs(e.target.checked)} />
                    Untertitel anzeigen ({subsDoc.phrases.length} Phrasen)
                  </label>

                  <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14 }}>
                      Schriftart
                      <select
                        className="select"
                        style={{ width: 190, padding: "4px 8px", fontFamily: `"${subsDoc.font_family}", sans-serif` }}
                        value={subsDoc.font_family || SUB_FONT_DEFAULT}
                        onChange={(e) => patchSubs({ font_family: e.target.value })}
                      >
                        {SUB_FONT_CHOICES.map((f) => (
                          <option key={f} value={f} style={{ fontFamily: `"${f}", sans-serif` }}>{f}</option>
                        ))}
                      </select>
                    </label>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14 }}>
                      Größe
                      <select
                        className="select"
                        style={{ width: 72, padding: "4px 8px" }}
                        value={subsDoc.font_size || SUB_FONT_SIZE_DEFAULT}
                        onChange={(e) => patchSubs({ font_size: parseInt(e.target.value, 10) })}
                      >
                        {SUB_FONT_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </label>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14 }}>
                      Textfarbe
                      <input type="color" value={subsDoc.text_color || "#FFFFFF"}
                        onChange={(e) => patchSubs({ text_color: e.target.value })}
                        style={{ width: 36, height: 26, border: "none", cursor: "pointer", background: "transparent" }} />
                    </label>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14 }}>
                      Highlight-Farbe
                      <input type="color" value={subsDoc.highlight_color || "#00D2FF"}
                        onChange={(e) => patchSubs({ highlight_color: e.target.value })}
                        style={{ width: 36, height: 26, border: "none", cursor: "pointer", background: "transparent" }} />
                    </label>
                    <button className="btn btn-ghost" style={{ padding: "5px 10px", fontSize: 13 }}
                      onClick={() => patchSubs({ position: { x_pct: 50, y_pct: 65 } })}>
                      Position zurücksetzen
                    </button>
                  </div>

                  <span className="muted" style={{ fontSize: 12 }}>
                    Untertitel in der Vorschau mit der Maus verschieben — alle Einstellungen werden gespeichert
                    und beim Download exakt so eingebrannt. Hinweis: Die Schriftart muss auf dem Mac installiert
                    sein, sonst nutzt das Rendering eine Ersatzschrift.
                  </span>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}
    </>
  );
}

/* ===== Hauptkomponente ===== */
const PAGES = [
  { id: "editor",  label: "Video Editor",  Icon: Ico.scissors },
  { id: "analyst", label: "Video Analyst", Icon: Ico.brain },
];

const PAGE_META = {
  editor: {
    title: "Reels & Shorts automatisch schneiden",
    desc: "Lade dein Rohmaterial hoch — Gemini analysiert, Claude plant den Schnitt und der Editor rendert dein fertiges Video für die Plattform deiner Wahl.",
    appTitle: "KI Video Editor",
  },
  analyst: {
    title: "AI Video Analyst",
    desc: "Analysiere jedes Video auf Inhalt, Sprach-Qualität, Schnitt-Pacing & Plattform-Potenzial — lade einfach dein Video hoch.",
    appTitle: "AI Video Analyst",
  },
};

// Vom Server gesetzt (templates/index.html). True → nur Analyst, Editor versteckt.
const ANALYST_ONLY = typeof window !== "undefined" && window.ANALYST_ONLY === true;

function App() {
  const [activePage, setActivePage] = useState(ANALYST_ONLY ? "analyst" : "editor");
  const meta = PAGE_META[activePage];

  return (
    <div className="app">
      {/* Topbar */}
      <header className="topbar">
        <div className="brand">
          <Logo />
          <div className="brand-divider" />
          <div className="app-name">
            <span className="kicker">Studio</span>
            <span className="title">{meta.appTitle}</span>
          </div>
        </div>
        <div className="topbar-right">
          <div className="userchip">
            <span className="avatar">C</span>
            <span className="who">chris@meinfluss.de</span>
          </div>
        </div>
      </header>

      {/* Navigation — im Analyst-only-Deployment ausgeblendet */}
      {!ANALYST_ONLY && (
        <nav className="main-nav">
          {PAGES.map((p) => (
            <button
              key={p.id}
              className={"nav-tab" + (activePage === p.id ? " active" : "")}
              onClick={() => setActivePage(p.id)}
            >
              <p.Icon width="15" height="15" />
              {p.label}
            </button>
          ))}
        </nav>
      )}

      {/* Page Header */}
      <div className="page-head">
        <h1>{meta.title}</h1>
        <p>{meta.desc}</p>
      </div>

      {/* Seiten — immer gemountet, nur versteckt → State bleibt erhalten.
          Im Analyst-only-Deployment wird der Editor gar nicht gemountet (kein /api/projects-Call). */}
      {!ANALYST_ONLY && (
        <div className={activePage !== "editor" ? "page-hidden" : ""}><VideoEditorPage /></div>
      )}
      <div className={activePage !== "analyst" ? "page-hidden" : ""}><VideoAnalystPage /></div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
