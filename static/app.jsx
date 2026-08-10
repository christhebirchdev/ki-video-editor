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

/* ===== Admin-/Feedback-Ansicht =====
   Zwei Ansichten aus EINER Codebasis: Kunden-Ansicht (Standard, ohne Feedback-Felder) und
   Admin-Ansicht mit Feedback pro Output-Feld. Das Passwort wird SERVER-seitig geprüft
   (/api/analyst/admin/verify) — im Frontend steht es nie, sonst wäre es bei öffentlich
   erreichbarem Server wirkungslos. Gesammelt wird nur; kein Auto-Fix, kein Auto-Commit. */
const AdminCtx = React.createContext({ admin: false, password: "", runId: "" });

function AdminToggle({ admin, password, onLogin, onLogout }) {
  const [offen, setOffen] = useState(false);
  const [pw, setPw] = useState("");
  const [fehler, setFehler] = useState("");
  const [pruefe, setPruefe] = useState(false);

  async function submit(e) {
    e?.preventDefault();
    setPruefe(true); setFehler("");
    try {
      await api("POST", "/api/analyst/admin/verify", { password: pw });
      onLogin(pw); setOffen(false); setPw("");
    } catch (err) {
      setFehler(err.message || "Falsches Passwort");
    } finally { setPruefe(false); }
  }

  if (admin) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginRight: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "#fff", background: "#c0392b",
                       padding: "3px 9px", borderRadius: 999 }}>ADMIN</span>
        <button className="btn btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }}
                onClick={onLogout}>Verlassen</button>
      </div>
    );
  }
  return (
    <div style={{ position: "relative", marginRight: 10 }}>
      <button className="btn btn-ghost" style={{ padding: "5px 10px", fontSize: 12 }}
              onClick={() => setOffen((o) => !o)}>Admin</button>
      {offen && (
        <form onSubmit={submit}
              style={{ position: "absolute", right: 0, top: "calc(100% + 6px)", zIndex: 50,
                       background: "#fff", border: "1px solid var(--line-strong)", borderRadius: 8,
                       padding: 10, boxShadow: "0 6px 20px rgba(0,0,0,.12)", width: 220 }}>
          <input type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)}
                 placeholder="Passwort"
                 name="admin-feedback-code" autoComplete="off"
                 data-1p-ignore="true" data-lpignore="true" data-form-type="other"
                 style={{ width: "100%", padding: "7px 9px", fontSize: 13,
                          border: "1px solid var(--line-strong)", borderRadius: 6 }} />
          {fehler && <div style={{ color: "#c0392b", fontSize: 12, marginTop: 5 }}>{fehler}</div>}
          <button className="btn btn-primary" type="submit" disabled={pruefe || !pw}
                  style={{ width: "100%", marginTop: 8, padding: "7px", fontSize: 13 }}>
            {pruefe ? "Prüfe …" : "Feedback-Ansicht öffnen"}
          </button>
        </form>
      )}
    </div>
  );
}

/* Ein Feedback-Block pro Output-Feld. In der Kunden-Ansicht rendert er nichts. */
function Feedback({ field }) {
  const { admin, password, runId } = React.useContext(AdminCtx);
  const [verdict, setVerdict] = useState("");
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");   // "", "gespeichert", Fehlertext
  // Was zuletzt WIRKLICH gespeichert wurde. Ohne diesen Vergleich schreibt jeder Blur einen neuen
  // Eintrag, auch wenn sich nichts geändert hat — und ein Klick auf den Daumen löst zuerst den Blur
  // des Textfelds aus, also gleich zwei. In den Läufen vom 2026-07-29 waren dadurch von 61 Einträgen
  // rund 35 Duplikate (Run 093dc5a7: 7× derselbe Text). Die Historie bleibt bewusst append-only —
  // hier fallen nur die inhaltsgleichen Wiederholungen weg.
  const gespeichert = React.useRef({ verdict: "", text: "" });

  if (!admin || !runId) return null;

  async function speichern(v, t) {
    if (gespeichert.current.verdict === v && gespeichert.current.text === t) return;
    try {
      await api("POST", `/api/analyst/${runId}/feedback`,
                { password, field_id: field, verdict: v, text: t });
      gespeichert.current = { verdict: v, text: t };
      setStatus("gespeichert ✓");
      setTimeout(() => setStatus(""), 1800);
    } catch (e) { setStatus(e.message || "Fehler"); }
  }

  const daumen = (wert, symbol) => (
    <button type="button" onClick={() => { setVerdict(wert); speichern(wert, text); }}
            title={wert === "up" ? "gut" : "schlecht"}
            style={{ padding: "2px 8px", fontSize: 14, cursor: "pointer", borderRadius: 6,
                     border: "1px solid " + (verdict === wert ? "transparent" : "var(--line-strong)"),
                     background: verdict === wert ? (wert === "up" ? "#1e8e5a" : "#c0392b") : "transparent",
                     filter: verdict === wert ? "grayscale(1) brightness(3)" : "none" }}>
      {symbol}
    </button>
  );

  return (
    <div style={{ marginTop: 6, padding: "8px 10px", background: "#fffdf5",
                  border: "1px dashed #e0c98a", borderRadius: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
        {daumen("up", "👍")}{daumen("down", "👎")}
        <span style={{ fontSize: 11, color: "var(--muted, #777)" }}>{field}</span>
        <span style={{ fontSize: 11, color: "#1e8e5a", marginLeft: "auto" }}>{status}</span>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => { if (text.trim() || verdict) speichern(verdict, text); }}
        placeholder="Was genau ist gut/schlecht, warum — und wie wäre es besser?"
        rows={2}
        style={{ width: "100%", padding: "6px 8px", fontSize: 12.5, resize: "vertical",
                 border: "1px solid var(--line-strong)", borderRadius: 6 }} />
    </div>
  );
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
  // Marken-Logo: drei goldene Schräg-Balken wie im MEINFLUSS-Screenshot.
  // Die Wortmarke „MEINFLUSS" steht daneben als HTML-Textmarke (dunkel = auf der
  // hellen Kopfleiste sichtbar; die helle Screenshot-Variante wäre creme-auf-creme unsichtbar).
  return (
    <svg className="mark" width="40" height="34" viewBox="0 0 40 34" fill="none" aria-hidden="true">
      <g fill="#BD9F66">
        <path d="M6 28 L14 6 L18 6 L10 28 Z"/>
        <path d="M15 28 L23 6 L27 6 L19 28 Z"/>
        <path d="M24 28 L32 6 L36 6 L28 28 Z"/>
      </g>
    </svg>
  );
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
// Muss mit models.analyst.FORMATE übereinstimmen — die API validiert die Auswahl dagegen (422).
const FORMATE = ["Talking Head", "Reaction", "Sketch", "Tutorial", "Vlog", "Andere"];

const ANALYST_FEATURES = [
  { ico: "🎯", title: "Inhaltsanalyse",    desc: "Themen, Kernaussagen & Story-Struktur erkennen" },
  { ico: "🎙️", title: "Sprach-Qualität",   desc: "Füllwörter, Pausen, Sprechtempo & Verständlichkeit" },
  { ico: "✂️",  title: "Schnitt-Bewertung", desc: "Übergänge, Pacing & Engagement-Kurve" },
  { ico: "📊", title: "Performance-Score", desc: "Plattform-Potenzial & konkrete Optimierungstipps" },
];

// Bewertungs-Engines zum Vergleich. Gleiches Ergebnis-Layout, gleicher Bewertungsprompt —
// nur WER/WIE bewertet ändert sich.
// Nur eine produktive Engine (v2_split, intern „V1.2"). Frontend nennt keine Version, damit
// nichts über den Hintergrund preisgibt. Backend-Param bleibt `v2_split` — Server-Vertrag stabil.
const ANALYST_ENGINE = "v2_split";

// Stufen für die Fortschrittsbalken. est = geschätzte Dauer in Sekunden aus echten Läufen. Die
// Labels sind bewusst neutral — sie sollen nicht verraten, welches Werkzeug im Hintergrund läuft
// (Vorgabe Chris 2026-08-10: keine „Transkription"-o.Ä.-Meldungen mehr).
const STAGE_SET = [
  { key: "transcribe", label: "Vorbereitung",        est: 28 },
  { key: "quality",    label: "Prüfung",             est: 6 },
  { key: "evaluate",   label: "Analyse & Bewertung", est: 150 },
];
// Frontend-Fortschrittstext, unabhängig vom (technisch benannten) Backend-Detail. Ein einziger
// Text für alle Phasen — der visuelle Fortschritt kommt aus StageBars.
const ANALYSE_LAEUFT = "Analyse läuft …";

// Ein Stufen-Balken pro Bearbeitungsschritt: abgeschlossene Stufen 100 %, die aktive füllt sich zeitbasiert
// (gedeckelt bei 96 %, bis die Stufe wirklich fertig ist). So gibt es pro Stufe ein „voll"-Erlebnis.
function StageBars({ stages, activePhase, phaseStartMs, tick }) {
  const activeIdx = stages.findIndex((s) => s.key === activePhase);
  return (
    <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
      {stages.map((s, i) => {
        // Abgeschlossene Stufe → nur grüner Haken. Aktive Stufe → EIN Balken. Zukünftige → nicht anzeigen.
        if (activeIdx >= 0 && i < activeIdx) {
          return (
            <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ok-ink)" }}>
              <span style={{ fontWeight: 700 }}>✓</span> {s.label}
            </div>
          );
        }
        if (i === activeIdx) {
          const elapsed = phaseStartMs ? (Date.now() - phaseStartMs) / 1000 : 0;
          const pct = Math.min(96, (elapsed / s.est) * 100);
          return (
            <div key={s.key}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                <span style={{ fontWeight: 700, color: "var(--ink)" }}>{s.label}</span>
                <span className="muted" style={{ fontSize: 11 }}>{Math.round(pct)} %</span>
              </div>
              <div style={{ height: 8, background: "var(--line)", borderRadius: 999, overflow: "hidden" }}>
                <div style={{
                  width: `${Math.round(pct)}%`, height: "100%",
                  background: "var(--gold-deep)", borderRadius: 999, transition: "width 0.35s ease",
                }} />
              </div>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}

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
        {/* score === null heißt "nicht bewertbar" (z.B. Sprech-Hook in einem Video ohne Sprache).
            Dann gar keine Punkte zeigen — 0 von 5 gefüllten Punkten liest sich wie eine 0-Wertung. */}
        {score != null && <RatingDots value={score} />}
        <span className="sc-num">{score != null ? `${score}/5` : "–"}</span>
      </div>
    </div>
  );
}

// Handlungsempfehlungen: max 3, nach Priorität; die wichtigste hervorgehoben. Ruhige Liste, kein Häkchen-Zwang.
function Handlungsempfehlungen({ steps }) {
  const top = (steps || []).slice(0, 3);
  return (
    <div className="analyst-eval-block">
      <div className="analyst-eval-title">💡 Handlungsempfehlungen</div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
        Du musst nicht alles umsetzen — schon eine Änderung hilft.
      </div>
      {top.map((s, i) => (
        <div
          key={i}
          style={{
            display: "flex", gap: 10, alignItems: "flex-start",
            padding: i === 0 ? "12px 14px" : "8px 14px",
            marginBottom: 8, borderRadius: 12,
            background: i === 0 ? "var(--gold-tint-2)" : "transparent",
            border: i === 0 ? "1px solid var(--line-strong)" : "1px solid var(--line)",
          }}
        >
          <span style={{
            flex: "0 0 auto", fontSize: 12, fontWeight: 700, marginTop: 2, whiteSpace: "nowrap",
            color: i === 0 ? "var(--gold-deep)" : "var(--taupe)",
          }}>
            {i === 0 ? "★ Wichtigste" : `#${i + 1}`}
          </span>
          <span style={{ fontSize: i === 0 ? 15 : 14 }}>
            {s.zeitpunkt ? <b>{s.zeitpunkt} — </b> : null}{s.anweisung}
          </span>
        </div>
      ))}
    </div>
  );
}

// Ordnet den Performance-Score ermutigend ein (Label + Farbe) statt nackter Zahl.
// Der Score wird IMMER grün dargestellt — auch bei 20 von 100.
// Vorgabe Chris: Die Farbe soll nicht demotivieren. Jedes veröffentlichte Video ist besser als
// keines; wer nach dem ersten Versuch eine rote Zahl sieht, dreht das zweite womöglich nicht mehr.
// Die Zahl selbst differenziert weiterhin, ebenso der Einordnungstext — nur das Farbsignal ist
// bewusst neutralisiert. Rot bleibt dem vorbehalten, was der Reputation wirklich schaden kann;
// dafür gibt es die Warnhinweise in den Empfehlungen, nicht den Gesamtscore.
function scoreEinordnung(score) {
  if (score == null) return { label: "", color: "var(--taupe)" };
  const color = "var(--ok-ink)";
  if (score >= 75) return { label: "Stark — weiter so", color };
  if (score >= 55) return { label: "Gute Basis — mit 1–2 Änderungen richtig stark", color };
  if (score >= 35) return { label: "Solide Basis — ein paar Hebel bringen viel", color };
  return { label: "Guter Anfang — die Empfehlungen unten helfen am meisten", color };
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

/* ===== V1.1: Markdown-Teilmenge für Chat-Antworten =====
   Kein react-markdown: Das Frontend läuft ohne Build-Step (Babel im Browser), npm-Pakete gibt
   es hier nicht. Unterstützt wird bewusst nur, was der Chat-Prompt erlaubt: Absätze,
   "- "-Aufzählungen, "1. "-Listen und **fett**.

   WICHTIG: Muss UNVOLLSTÄNDIGES Markdown vertragen. Beim Streaming ist ein ** oft noch nicht
   geschlossen — ein naiver Renderer färbt dann den Rest der Antwort fett. Deshalb: nur PAARE
   werden fett, ein einzelnes ** bleibt sichtbarer Text.
   Kein dangerouslySetInnerHTML — der Text kommt vom Modell. */
function mdInline(text) {
  const teile = [];
  let rest = text;
  let key = 0;
  while (true) {
    const auf = rest.indexOf("**");
    if (auf === -1) { if (rest) teile.push(rest); break; }
    const zu = rest.indexOf("**", auf + 2);
    if (zu === -1) { teile.push(rest); break; }   // offenes ** → als Text stehen lassen
    if (auf > 0) teile.push(rest.slice(0, auf));
    teile.push(<strong key={"b" + key++}>{rest.slice(auf + 2, zu)}</strong>);
    rest = rest.slice(zu + 2);
  }
  return teile;
}

function MarkdownLite({ text }) {
  const zeilen = (text || "").split("\n");
  const bloecke = [];
  let liste = null;   // {geordnet:bool, items:[]}

  const listeSchliessen = () => {
    if (!liste) return;
    const Tag = liste.geordnet ? "ol" : "ul";
    bloecke.push(
      <Tag key={"l" + bloecke.length} className="chat-liste">
        {liste.items.map((it, i) => <li key={i}>{mdInline(it)}</li>)}
      </Tag>
    );
    liste = null;
  };

  for (const zeile of zeilen) {
    const auf = zeile.replace(/^\s+/, "");
    const punkt = auf.match(/^[-•]\s+(.*)$/);
    const zahl = auf.match(/^\d+\.\s+(.*)$/);
    if (punkt) {
      if (liste && liste.geordnet) listeSchliessen();
      liste = liste || { geordnet: false, items: [] };
      liste.items.push(punkt[1]);
    } else if (zahl) {
      if (liste && !liste.geordnet) listeSchliessen();
      liste = liste || { geordnet: true, items: [] };
      liste.items.push(zahl[1]);
    } else {
      listeSchliessen();
      if (auf.trim()) bloecke.push(<p key={"p" + bloecke.length}>{mdInline(zeile)}</p>);
    }
  }
  listeSchliessen();
  return <>{bloecke}</>;
}

/* ===== V1.1: Rückfragen-Chat zur fertigen Analyse =====
   Fragt nach, lässt sich erklären, bekommt konkretere Hinweise. Der Chat ÄNDERT die Bewertung
   nicht — das braucht Versionierung und ein Bestätigungs-Gate und kommt separat. */
function ChatPanel({ runId, filename, dauerSec = 0 }) {
  const [offen, setOffen] = useState(false);
  const [nachrichten, setNachrichten] = useState([]);
  const [eingabe, setEingabe] = useState("");
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState("");
  const [teilantwort, setTeilantwort] = useState("");
  const endeRef = useRef(null);
  const feldRef = useRef(null);

  // Verlauf laden, sobald eine (andere) Analyse vorliegt — nach einem Reload ist der Chat
  // sonst leer, obwohl serverseitig alles noch da ist.
  useEffect(() => {
    if (!runId) { setNachrichten([]); return; }
    let abgebrochen = false;
    api("GET", `/api/analyst/${runId}/chat`)
      .then((d) => { if (!abgebrochen) setNachrichten(d.nachrichten || []); })
      .catch(() => {});
    return () => { abgebrochen = true; };
  }, [runId]);

  // Immer ans Ende scrollen — auch während die Antwort noch wächst.
  // Nur wenn der Chat offen ist: sonst scrollt der Browser zu einem unsichtbaren Element
  // und reißt die Seite an eine Stelle, an der der Nutzer gar nicht ist.
  useEffect(() => {
    if (!offen) return;
    endeRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [nachrichten, teilantwort, offen]);

  function autoResize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }

  async function senden() {
    const frage = eingabe.trim();
    if (!frage || laeuft || !runId) return;
    setFehler("");
    setEingabe("");
    if (feldRef.current) feldRef.current.style.height = "auto";
    setNachrichten((n) => [...n, { rolle: "user", text: frage, ts: new Date().toISOString() }]);
    setLaeuft(true);
    setTeilantwort("");
    try {
      const res = await fetch(`/api/analyst/${runId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frage }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `HTTP ${res.status}`);
      }
      // Stück für Stück lesen — das ist der Punkt, an dem sich „schnell" anfühlt.
      // Auf die Komplettantwort zu warten würde denselben Server-Call langsam wirken lassen.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let voll = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        voll += decoder.decode(value, { stream: true });
        setTeilantwort(voll);
      }
      setNachrichten((n) => [...n, { rolle: "model", text: voll, ts: new Date().toISOString() }]);
      // Verlauf einmal nachladen: Die IDs vergibt der Server beim Schreiben in chat.jsonl.
      // Ohne das hätte die gerade eingetroffene Antwort keine id — und damit kein
      // Bewertungsfeld, bis man die Seite neu lädt.
      try {
        const frisch = await api("GET", `/api/analyst/${runId}/chat`);
        if (frisch.nachrichten?.length) setNachrichten(frisch.nachrichten);
      } catch (_) { /* Anzeige steht schon, ohne id fehlt nur die Bewertung */ }
    } catch (e) {
      setFehler(e.message);
    } finally {
      setTeilantwort("");
      setLaeuft(false);
    }
  }

  function beiTaste(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); senden(); }
  }

  const leer = nachrichten.length === 0 && !laeuft;

  return (
    <div className={"chat-card" + (offen ? " ist-offen" : "")}>
      {/* Kopf ist der Auf-/Zuklapp-Schalter. Als <button>, damit Tastatur und Screenreader
          ihn bedienen können — ein <div> mit onClick kann beides nicht. */}
      <button
        type="button"
        className="chat-head"
        onClick={() => setOffen((o) => !o)}
        aria-expanded={offen}
        aria-controls="chat-klapp"
      >
        <div className="chat-badge"><Ico.brain width="18" height="18" /></div>
        <div className="chat-head-text">
          <div className="chat-titel">Rückfragen zur Analyse</div>
          <div className="chat-sub">
            {offen
              ? (filename || "Bereit für deine erste Nachricht")
              : (nachrichten.length
                  ? `${nachrichten.length} Nachrichten — zum Öffnen klicken`
                  : "Frag nach, lass dir Stellen im Video erklären")}
          </div>
        </div>
        {/* Plus wird zu Minus: der senkrechte Strich fährt zusammen, während sich das
            Zeichen dreht. Ein Icon-Tausch würde springen, das hier läuft durch. */}
        <span className="chat-toggle" aria-hidden="true">
          <span className="chat-toggle-bar" />
          <span className="chat-toggle-bar chat-toggle-bar-v" />
        </span>
      </button>

      <div className="chat-klapp" id="chat-klapp">
       <div className="chat-klapp-inner">
      <div className="chat-stream">
        {leer && (
          <div className="chat-leer">
            <div className="chat-leer-badge"><Ico.brain width="22" height="22" /></div>
            <div className="chat-leer-titel">Frag nach, was du nicht verstehst.</div>
            <div className="chat-leer-sub">
              Warum ist ein Tipp wichtig, wie setzt du ihn um, worauf kommt es beim nächsten
              Video an — frag einfach.
            </div>
          </div>
        )}

        {nachrichten.map((n, i) => (
          <div key={n.id || i} className={"chat-zeile " + (n.rolle === "user" ? "ist-user" : "ist-ki")}>
            <div className={"chat-bubble " + (n.rolle === "user" ? "bubble-user" : "bubble-ki")}>
              {n.rolle === "user" ? n.text : <MarkdownLite text={n.text} />}
            </div>
            <div className="chat-zeit">{(n.ts || "").slice(11, 16)}</div>
            {/* Bewertung je KI-Antwort — nur in der Admin-Ansicht sichtbar (Feedback rendert
                sonst null). Die id kommt aus chat.jsonl und bleibt über Reloads stabil. */}
            {n.rolle === "model" && n.id && (
              <div className="chat-feedback"><Feedback field={"chat." + n.id} /></div>
            )}
          </div>
        ))}

        {laeuft && teilantwort && (
          <div className="chat-zeile ist-ki">
            <div className="chat-bubble bubble-ki"><MarkdownLite text={teilantwort} /></div>
          </div>
        )}

        {laeuft && !teilantwort && (
          <div className="chat-zeile ist-ki">
            <div className="chat-bubble bubble-ki chat-tippt">
              <span /><span /><span />
            </div>
          </div>
        )}

        {fehler && <div className="chat-fehler">{fehler}</div>}
        <div ref={endeRef} />
      </div>

      <div className="chat-eingabe">
        <textarea
          ref={feldRef}
          rows={1}
          value={eingabe}
          placeholder={'Frag nach — z.B. „schau dir die Untertitel nochmal an"'}
          onChange={(e) => { setEingabe(e.target.value); autoResize(e.target); }}
          onKeyDown={beiTaste}
          disabled={laeuft}
        />
        <button
          className="chat-senden"
          onClick={senden}
          disabled={laeuft || !eingabe.trim()}
          aria-label="Nachricht senden"
        >→</button>
      </div>
       </div>
      </div>
    </div>
  );
}

// `chat` ist per Default aus → die bestehende Analyst-Ansicht verhält sich unverändert.
// V1.1 mountet dieselbe Komponente ein zweites Mal mit chat={true}.
function VideoAnalystPage({ adminPw = "", chat = false }) {
  const [runId, setRunId] = useState("");   // für die Feedback-Zuordnung in der Admin-Ansicht
  const [analysisFile, setAnalysisFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);   // visueller Hover-Zustand beim Draggen
  // Bewertungs-Version fest = v2_split. Version-Auswahl im Frontend entfernt (2026-08-10):
  // Nur eine sichtbare Variante, damit der Kunde nicht mit „V1.1 vs. V1.2" konfrontiert wird.
  // Alter Kommentar: V1.1 = ein Call, V1.2 = zwei Calls (Eröffnung / Handwerk). Läuft bewusst
  // nebeneinander, damit sich vergleichen lässt, ob der Split die Bewertung verbessert.
  const engine = ANALYST_ENGINE;   // fest; kein Umschalter im Frontend
  const [plannedTextHook, setPlannedTextHook] = useState("");  // Freifeld: geplante Texthook (falls noch nicht im Video)
  // Format-Auswahl (Pflicht, genau eines). Muss zu models.analyst.FORMATE passen — die API validiert dagegen.
  const [format, setFormat] = useState("");
  const [phase, setPhase] = useState("idle");   // idle | running | done
  const [progress, setProgress] = useState("");
  const [queueInfo, setQueueInfo] = useState(null);  // {ahead,total} während "queued"
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [activePhase, setActivePhase] = useState("");   // aktuelle Bearbeitungsstufe (transcribe/quality/evaluate)
  const [phaseStartMs, setPhaseStartMs] = useState(0);  // Startzeitpunkt der aktuellen Stufe
  const [tick, setTick] = useState(0);                  // erzwingt zeitbasierte Neuberechnung der Balken
  const [videoUrl, setVideoUrl] = useState("");         // lokale Quelle für den Player
  const [videoFehler, setVideoFehler] = useState(false);// Format, das der Browser nicht abspielt
  const [mini, setMini] = useState(false);              // Player schwebt mit beim Scrollen
  const [miniAus, setMiniAus] = useState(false);        // vom Nutzer weggeklickt
  const [slotHoehe, setSlotHoehe] = useState(0);        // hält den Platz, wenn der Player schwebt
  const [seiten, setSeiten] = useState(0);              // Breite/Höhe des Videos, aus den Metadaten
  const [miniH, setMiniH] = useState(0);                // Höhe des schwebenden Players in px
  const [selbstGezogen, setSelbstGezogen] = useState(false);  // Nutzer hat die Größe gesetzt
  const slotRef = useRef(null);
  const videoRef = useRef(null);
  const fileRef = useRef(null);
  const cancelledRef = useRef(false);
  const activePhaseRef = useRef("");                    // stale-freier Vergleich im Poll-Loop

  useEffect(() => () => { cancelledRef.current = true; }, []);

  // Object-URL zur gewählten Datei — der Player liest direkt vom Rechner des Nutzers.
  // Bewusst KEIN Server-Endpoint: Die Datei liegt lokal vor; sie über das Backend
  // zurückzuholen würde dasselbe Video ein zweites Mal über einen VPS ziehen, der bewusst
  // auf ANALYST_MAX_CONCURRENT=1 gedrosselt ist — und Video-Streaming bräuchte zusätzlich
  // HTTP-Range-Handling, sonst spielt Safari gar nicht erst ab.
  // revokeObjectURL ist Pflicht: ohne das hält der Browser die Referenz bis zum Tab-Schluss.
  useEffect(() => {
    if (!analysisFile?.file) { setVideoUrl(""); setVideoFehler(false); return; }
    const url = URL.createObjectURL(analysisFile.file);
    setVideoUrl(url);
    setVideoFehler(false);
    return () => URL.revokeObjectURL(url);
  }, [analysisFile]);

  // Solange die Analyse läuft: regelmäßig neu rendern, damit die aktive Stufe weiterfüllt.
  useEffect(() => {
    if (phase !== "running") return;
    const id = setInterval(() => setTick((t) => t + 1), 300);
    return () => clearInterval(id);
  }, [phase]);

  function onPickFile(e) {
    const f = e.target.files?.[0];
    if (f) setAnalysisFile({ file: f, name: f.name, size: (f.size / 1024 / 1024).toFixed(1) + " MB" });
  }

  const canStart = !!analysisFile && !!format;

  async function pollUntilDone(runId) {
    while (!cancelledRef.current) {
      await new Promise((r) => setTimeout(r, 2000));
      const data = await api("GET", `/api/analyst/${runId}`);
      if (cancelledRef.current) return null;
      if (data.phase === "error") throw new Error(data.error || "Analyse fehlgeschlagen");
      setQueueInfo(data.phase === "queued" ? (data.queue || null) : null);
      // data.detail bewusst NICHT anzeigen — es enthält technische Phasennamen
      // („Transkription läuft…", „Audio-Messwerte…") und würde verraten, was intern läuft.
      setProgress(ANALYSE_LAEUFT);
      if (data.phase && data.phase !== activePhaseRef.current) {
        activePhaseRef.current = data.phase;
        setActivePhase(data.phase);
        setPhaseStartMs(Date.now());
      }
      if (data.done && data.result) return data.result;
    }
    return null;
  }

  async function startAnalysis() {
    if (!canStart || phase === "running") return;
    setError("");
    setPhase("running");
    setResult(null);
    setActivePhase("");
    activePhaseRef.current = "";
    setPhaseStartMs(0);
    try {
      setProgress("Video wird hochgeladen …");   // neutral; verrät nichts
      const fd = new FormData();
      fd.append("file", analysisFile.file);
      const up = await api("POST", "/api/analyst/upload", fd);
      setRunId(up.id);
      setProgress(ANALYSE_LAEUFT);
      await api("POST", `/api/analyst/${up.id}/start?engine=${engine}&planned_text_hook=${encodeURIComponent(plannedTextHook)}&format=${encodeURIComponent(format)}`);
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
    setPlannedTextHook("");
    setFormat("");
    setRunId("");
    setActivePhase("");
    activePhaseRef.current = "";
    setPhaseStartMs(0);
  }

  // Player erst nach der Analyse — vorher gibt es kein „analysiertes Video".
  const zeigePlayer = phase === "done" && !!videoUrl;

  // Scrollt der Player aus dem Bild, wandert er als kleiner Schwebe-Player mit nach unten.
  // Wichtig: Es ist DASSELBE <video>-Element, nur der Rahmen wechselt die Klasse. Würde man
  // ein zweites rendern, würde die Wiedergabe beim Umschalten von vorn starten.
  useEffect(() => {
    if (!zeigePlayer) { setMini(false); setMiniAus(false); return; }
    const el = slotRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      ([eintrag]) => {
        if (!eintrag.isIntersecting && eintrag.boundingClientRect.top < 0) {
          // Höhe festhalten, bevor der Rahmen aus dem Fluss geht — sonst springt die Seite.
          setSlotHoehe(el.offsetHeight || 0);
          setMini(true);
        } else if (eintrag.isIntersecting) {
          setMini(false);
          setMiniAus(false);   // wieder oben: Schwebe-Player darf beim nächsten Mal erneut kommen
        }
      },
      { threshold: 0 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [zeigePlayer]);

  const schwebt = mini && !miniAus;

  // Standardgröße des schwebenden Players: so groß, dass er den freien Rand rechts neben dem
  // Inhalt ausfüllt, aber keinen Text überdeckt. Der Inhalt ist auf 1040px + 2×28px Polsterung
  // begrenzt (.app), der Rest links und rechts ist freier Rand.
  // Über die HÖHE gesteuert, nicht über die Breite: Bei einem 9:16-Reel ist die Höhe die
  // bindende Größe, und die Breite folgt dem Seitenverhältnis.
  useEffect(() => {
    if (selbstGezogen || !seiten) return;
    function passeAn() {
      const rand = Math.max(0, (window.innerWidth - 1096) / 2 - 24);
      const ausRand = rand > 120 ? rand / seiten : 0;   // schmaler Rand → nicht daran ausrichten
      const grenze = window.innerHeight * 0.78;
      setMiniH(Math.round(Math.min(grenze, Math.max(220, ausRand || 300))));
    }
    passeAn();
    window.addEventListener("resize", passeAn);
    return () => window.removeEventListener("resize", passeAn);
  }, [seiten, selbstGezogen]);

  // Ziehen am Anfasser oben links verändert die Höhe. Nach oben ziehen = größer.
  function starteZiehen(e) {
    e.preventDefault();
    const startY = e.clientY;
    const startH = miniH;
    setSelbstGezogen(true);
    const bewegen = (ev) => {
      const neu = startH + (startY - ev.clientY);
      setMiniH(Math.round(Math.max(140, Math.min(window.innerHeight * 0.92, neu))));
    };
    const loslassen = () => {
      window.removeEventListener("pointermove", bewegen);
      window.removeEventListener("pointerup", loslassen);
    };
    window.addEventListener("pointermove", bewegen);
    window.addEventListener("pointerup", loslassen);
  }

  return (
    <AdminCtx.Provider value={{ admin: !!adminPw, password: adminPw, runId }}>
      {error && (
        <div className="alert alert-err">
          <Ico.x style={{ marginTop: 1, flex: "0 0 auto" }} />
          <div>{error}</div>
        </div>
      )}

      {/* Eingabe */}
      <Card
        icon={zeigePlayer ? <Ico.film /> : <Ico.upload />}
        title={zeigePlayer ? "Analysiertes Video" : "Video-Quelle"}
        sub={zeigePlayer ? (analysisFile?.name || "") : "Lade dein Video hoch"}
      >
        {/* Nach der Analyse verschwindet der Upload-Bereich komplett — es gibt nichts mehr
            hochzuladen. Für einen neuen Durchlauf gibt es unten „Neue Analyse". */}
        {!zeigePlayer && (
          <div className="analyst-quelle-eingabe">
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              style={{ display: "none" }}
              onChange={onPickFile}
            />
            <div
              className={"dropzone" + (analysisFile ? " has" : "") + (dragActive ? " drag-over" : "")}
              onClick={() => fileRef.current?.click()}
              onDragEnter={(e) => { e.preventDefault(); setDragActive(true); }}
              onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
              onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
              onDrop={(e) => {
                // Ohne preventDefault öffnet der Browser die Datei direkt in einem neuen Tab —
                // genau das war der gemeldete Bug. onPickFile erwartet das native change-Event,
                // aber { target: { files } } genügt: es liest nur target.files[0].
                e.preventDefault();
                setDragActive(false);
                if (e.dataTransfer?.files?.length) {
                  onPickFile({ target: { files: e.dataTransfer.files } });
                }
              }}
            >
              <div className="dz-ico"><Ico.upload /></div>
              {/* title: Der Name wird per CSS abgeschnitten — der volle Name bleibt im Tooltip. */}
              <div className="dz-title" title={analysisFile ? analysisFile.name : ""}>
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
        )}

        {/* Player nach der Analyse. <video controls> bringt Abspielen, Pausieren, Scrubbing,
            Lautstärke und Vollbild mit — inklusive Tastatur- und Screenreader-Bedienung. */}
        {zeigePlayer && (
          videoFehler ? (
            <div className="analyst-player-hinweis">
              Dieses Format kann der Browser nicht abspielen (z.B. MKV oder AVI).
              Die Analyse ist davon nicht betroffen.
            </div>
          ) : (
            <div
              className="analyst-player-slot"
              ref={slotRef}
              style={schwebt && slotHoehe ? { height: slotHoehe } : undefined}
            >
              <div className={"analyst-player-rahmen" + (schwebt ? " ist-mini" : "")}>
                <video
                  ref={videoRef}
                  src={videoUrl}
                  controls
                  playsInline
                  preload="metadata"
                  style={schwebt && miniH ? { height: miniH, width: "auto", maxHeight: "none" } : undefined}
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget;
                    if (v.videoWidth && v.videoHeight) setSeiten(v.videoWidth / v.videoHeight);
                  }}
                  onError={() => setVideoFehler(true)}
                />
                {schwebt && (
                  <>
                    {/* Anfasser oben links: nach oben ziehen macht größer. Oben links, weil
                        unten rechts die Video-Steuerelemente liegen. */}
                    <div
                      className="analyst-mini-griff"
                      onPointerDown={starteZiehen}
                      role="separator"
                      aria-label="Größe des Players ziehen"
                      title="Ziehen, um die Größe zu ändern"
                    >
                      <span /><span />
                    </div>
                    <button
                      type="button"
                      className="analyst-mini-zu"
                      onClick={() => setMiniAus(true)}
                      aria-label="Schwebenden Player ausblenden"
                    >
                      <Ico.x width="13" height="13" />
                    </button>
                  </>
                )}
              </div>
            </div>
          )
        )}
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
          {/* Format-Auswahl (Pflicht, genau eines). Der Nutzer kennt sein Video — seine Angabe ist für die
              Bewertung bindend und verhindert, dass die KI z.B. ein Reaction-Video als Talking Head liest. */}
          <div style={{ marginBottom: 12 }} role="radiogroup" aria-label="Format">
            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              Format <span style={{ color: "var(--danger, #c0392b)" }}>*</span>
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {FORMATE.map((f) => {
                const on = format === f;
                return (
                  <button
                    key={f}
                    type="button"
                    role="radio"
                    disabled={phase === "running"}
                    onClick={() => setFormat(f)}
                    aria-checked={on}
                    style={{
                      padding: "6px 12px", fontSize: 13, borderRadius: 999, cursor: "pointer",
                      border: `1px solid ${on ? "var(--accent, #2d6cdf)" : "var(--line-strong)"}`,
                      background: on ? "var(--accent, #2d6cdf)" : "transparent",
                      color: on ? "#fff" : "inherit",
                      fontWeight: on ? 600 : 400,
                    }}
                  >
                    {f}
                  </button>
                );
              })}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Wähl das Format, das am besten passt — es steuert, wie streng Schnitt und Sprechpausen
              bewertet werden. Passt nichts genau? Dann „Andere“.
            </div>
          </div>

          {/* Version-Auswahl entfernt (2026-08-10) — nur eine sichtbare Variante. */}

          {/* Freifeld: geplante Texthook (falls sie erst nach dem Upload ins Video kommt) */}
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
              Geplante Texthook (optional)
            </label>
            <input
              type="text"
              value={plannedTextHook}
              onChange={(e) => setPlannedTextHook(e.target.value)}
              placeholder="z. B. Mit über 46 nochmal Mutter"
              disabled={phase === "running"}
              style={{ width: "100%", padding: "8px 10px", fontSize: 14, border: "1px solid var(--line-strong)", borderRadius: 8 }}
            />
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Kommt die Texthook erst nach dem Upload ins Video? Trag sie hier ein — dann wird sie bewertet.
              Leer lassen, wenn die Texthook schon im Video zu sehen ist.
            </div>
          </div>

          <button
            className="btn btn-primary analyst-start-btn"
            disabled={!canStart || phase === "running"}
            onClick={startAnalysis}
          >
            {phase === "running" ? (
              <><span className="spinner" /> {progress || ANALYSE_LAEUFT}</>
            ) : (
              <><Ico.play /> Videoanalyse starten</>
            )}
          </button>
          {phase === "running" && (
            <StageBars
              stages={STAGE_SET}
              activePhase={activePhase}
              phaseStartMs={phaseStartMs}
              tick={tick}
            />
          )}
          {phase === "running" && queueInfo && queueInfo.total > 1 && (
            <div className="analyst-queue">
              ⏳ In Warteschlange — Platz {queueInfo.ahead + 1} von {queueInfo.total}. Deine Analyse startet automatisch, sobald sie an der Reihe ist.
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
          sub={[
            // Kein Engine-Label (verrät „V1.2"). Länge und Zeit reichen als Info fürs Ergebnis.
            result.filename,
            fmtSec(result.duration_sec),
            result.elapsed_sec ? `⏱ ${result.elapsed_sec}s` : null,
            result.scene_count ? `${result.scene_count} Szenen` : null,
          ].filter(Boolean).join(" · ")}
          action={
            <button className="btn btn-ghost" style={{ padding: "9px 14px", fontSize: 13 }} onClick={reset}>
              <Ico.refresh width="15" height="15" /> Neue Analyse
            </button>
          }
        >
          {result.evaluation && (() => {
            const ev = result.evaluation;
            const ez = scoreEinordnung(ev.performance_score);
            return (
            <div className="analyst-eval">
              {/* 1. Score + Einordnung */}
              <div className="analyst-score">
                <div className="analyst-score-num" style={{ color: ez.color }}>{ev.performance_score}</div>
                <div className="analyst-score-label">Performance-Score von 100</div>
                {ez.label && (
                  <div style={{ fontSize: 13, fontWeight: 600, color: ez.color, marginTop: 4 }}>{ez.label}</div>
                )}
                {ev.funnel && <span className="analyst-funnel">{ev.funnel}</span>}
                <Feedback field="performance_score" />
              </div>

              {ev.zielgruppe && (
                <>
                  <div className="analyst-zielgruppe">🎯 {ev.zielgruppe}</div>
                  <Feedback field="zielgruppe" />
                </>
              )}

              {/* 2. Positiv zuerst */}
              {ev.staerken?.length > 0 && (
                <div className="analyst-eval-block" style={{ background: "var(--ok-bg)", border: "1px solid var(--ok-line)", borderRadius: 12, padding: "12px 14px" }}>
                  <div className="analyst-eval-title" style={{ color: "var(--ok-ink)" }}>✅ Das läuft schon gut</div>
                  <ul className="analyst-list">
                    {ev.staerken.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                  <Feedback field="staerken" />
                </div>
              )}

              {/* 3. Handlungsempfehlungen (max 3, wichtigste groß) */}
              {ev.action_steps?.length > 0 && (
                <><Handlungsempfehlungen steps={ev.action_steps} /><Feedback field="action_steps" /></>
              )}

              {/* 3b. Erweiterte Handlungsempfehlungen (aufklappbar) */}
              {ev.weitere_empfehlungen?.length > 0 && (
                <details className="analyst-eval-block" style={{ marginTop: 4 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14, padding: "6px 0" }}>
                    Erweiterte Handlungsempfehlungen ({ev.weitere_empfehlungen.length})
                  </summary>
                  <ul className="analyst-list" style={{ marginTop: 8 }}>
                    {ev.weitere_empfehlungen.map((s, i) => (
                      <li key={i} style={{ marginBottom: 6 }}>
                        {s.zeitpunkt ? <b>{s.zeitpunkt} — </b> : null}{s.anweisung}
                      </li>
                    ))}
                  </ul>
                  <Feedback field="weitere_empfehlungen" />
                </details>
              )}

              {/* 4. Details-Aufklapper: ausführliches Feedback + alle Score-Dimensionen */}
              <details className="analyst-details" style={{ marginTop: 8 }}>
                <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14, padding: "6px 0" }}>
                  Detaillierte Analyse und Feedback
                </summary>

                {ev.top_tipps?.length > 0 && (
                  <div className="analyst-eval-block analyst-tipps" style={{ marginTop: 10 }}>
                    <div className="analyst-eval-title">💬 Feedback (ausführlich)</div>
                    <ul className="analyst-list">
                      {ev.top_tipps.map((t, i) => <li key={i}>{t}</li>)}
                    </ul>
                    <Feedback field="top_tipps" />
                  </div>
                )}

                <div className="sc-grid" style={{ marginTop: 10 }}>
                  <ScoreChip label="🎤 Sprech-Hook" score={ev.hook?.sprech_hook_score} detail={ev.hook?.sprech_hook_grund} />
                  <Feedback field="hook.sprech" />
                  <ScoreChip label={ev.hook?.text_hook_vorhanden ? "📝 Text-Hook" : "📝 Text-Hook (fehlt)"} score={ev.hook?.text_hook_score} detail={ev.hook?.text_hook_grund} />
                  <Feedback field="hook.text" />
                  <ScoreChip label="📖 Struktur" score={ev.struktur?.score} detail={strukturDetail(ev.struktur)} />
                  <Feedback field="struktur" />
                  <ScoreChip label="🎙️ Sprechqualität" score={ev.sprechqualitaet?.score} detail={problemeDetail(ev.sprechqualitaet)} />
                  <Feedback field="sprechqualitaet" />
                  <ScoreChip label="✂️ Schnitt & Pacing" score={ev.schnitt_pacing?.score} detail={ev.schnitt_pacing?.kommentar} />
                  <Feedback field="schnitt_pacing" />
                  <ScoreChip label="📈 Spannungsbogen" score={ev.spannungsbogen?.score} detail={ev.spannungsbogen?.kommentar} />
                  <Feedback field="spannungsbogen" />
                  <ScoreChip label="🎨 Visuelle Ästhetik" score={ev.visuelle_aesthetik?.score} detail={problemeDetail(ev.visuelle_aesthetik)} />
                  <Feedback field="visuelle_aesthetik" />
                </div>
              </details>
            </div>
            );
          })()}

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

          {result.scenes.length > 0 && (
          <details className="analyst-rawdump">
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
          )}
        </Card>
      )}

      {/* V1.1: Chat erscheint erst, wenn eine Analyse fertig ist — vorher gibt es nichts zu fragen. */}
      {chat && phase === "done" && result && (
        <div style={{ marginTop: 24 }}>
          <ChatPanel runId={runId} filename={result.filename} dauerSec={result.duration_sec} />
        </div>
      )}
    </AdminCtx.Provider>
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
// Nur noch eine Analyst-Ansicht (2026-08-10). Der Rückfragen-Chat bleibt als Feature erhalten
// und lebt in derselben Seite; er heißt intern „V1.1", wurde im Frontend aber entlabelt.
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
    desc: "Analysiere jedes Video auf Inhalt, Sprach-Qualität, Schnitt-Pacing & Plattform-Potenzial — lade einfach dein Video hoch. Nach der Analyse kannst du Rückfragen zur Bewertung stellen.",
    appTitle: "AI Video Analyst",
  },
};

// Vom Server gesetzt (templates/index.html). True → nur Analyst, Editor versteckt.
const ANALYST_ONLY = typeof window !== "undefined" && window.ANALYST_ONLY === true;

function App() {
  const [activePage, setActivePage] = useState(ANALYST_ONLY ? "analyst" : "editor");
  const meta = PAGE_META[activePage];
  // Admin-Ansicht: Passwort wurde server-seitig geprüft; es bleibt nur im State (nicht persistiert),
  // damit ein Reload zurück in die Kunden-Ansicht fällt.
  const [adminPw, setAdminPw] = useState("");

  return (
    <div className="app">
      {/* Topbar */}
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span className="wordmark">MEINFLUSS</span>
          <div className="brand-divider" />
          <div className="app-name">
            <span className="kicker">Studio</span>
            <span className="title">{meta.appTitle}</span>
          </div>
        </div>
        <div className="topbar-right" style={{ display: "flex", alignItems: "center" }}>
          <AdminToggle
            admin={!!adminPw}
            password={adminPw}
            onLogin={setAdminPw}
            onLogout={() => setAdminPw("")}
          />
          <div className="userchip">
            <span className="avatar">C</span>
            <span className="who">chris@meinfluss.de</span>
          </div>
        </div>
      </header>

      {/* Navigation — im Analyst-only-Deployment ohne den Editor, aber NICHT komplett weg:
          sonst wäre die V1.1-Ansicht auf dem Server nicht erreichbar. Bleibt nur eine Seite
          übrig, verschwindet die Leiste wie bisher. */}
      {(() => {
        const sichtbar = ANALYST_ONLY ? PAGES.filter((p) => p.id !== "editor") : PAGES;
        if (sichtbar.length < 2) return null;
        return (
          <nav className="main-nav">
            {sichtbar.map((p) => (
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
        );
      })()}

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
      {/* EINE Analyst-Ansicht mit Chat. Vor 2026-08-10 lief hier eine zweite Instanz derselben
          Komponente unter dem Label „V1.1" — Chris will nur noch V1.2 sichtbar, der Chat bleibt
          aber als Feature erhalten und ist damit in der einzigen Ansicht direkt verfügbar. */}
      <div className={activePage !== "analyst" ? "page-hidden" : ""}>
        <VideoAnalystPage adminPw={adminPw} chat />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
