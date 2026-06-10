const { useState, useRef, useEffect } = React;

/* ===== Konstanten ===== */
const PLATFORMS = [
  { value: "short", label: "Short Video" },
  { value: "long", label: "Long Video" },
];
const LS_LAST_PROJECT = "kveditor.lastProjectId";

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
};

function Logo() {
  return (
    <svg className="mark" width="40" height="34" viewBox="0 0 40 34" fill="none" aria-hidden="true">
      <g>
        <path d="M6 28 L14 6 L18 6 L10 28 Z" fill="#BD9F66"/>
        <path d="M15 28 L23 6 L25 6 L17 28 Z" fill="#221E18"/>
        <path d="M24 28 L32 6 L36 6 L28 28 Z" fill="#BD9F66"/>
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

function App() {
  const [projects, setProjects] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [newName, setNewName] = useState("");
  const [newPlatform, setNewPlatform] = useState("short");
  const [newEngine, setNewEngine] = useState("v3");

  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);
  const [uploading, setUploading] = useState(false);

  // pipeline: -1 idle, 0..2 running step index, 3 done
  const [phase, setPhase] = useState(-1);
  const [error, setError] = useState("");
  const [pipelineMsg, setPipelineMsg] = useState("");

  // Cut-Plan-Daten
  const [clips, setClips] = useState([]);
  const [claudeReasoning, setClaudeReasoning] = useState("");
  const [videoCacheBust, setVideoCacheBust] = useState(0);

  // Untertitel
  const [subtitling, setSubtitling] = useState(false);

  const selected = projects.find((p) => p.id === selectedId);

  /* ----- Init: Projekte laden ----- */
  useEffect(() => { loadProjectList(); }, []);

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
      setProjects((ps) => ps.map((p) => (p.id === id ? { ...p, files: proj.files, status: proj.status, name: proj.name, platform: proj.platform } : p)));
      // Wenn schon geschnitten → cut_plan + Video laden
      if (proj.status === "cut" || proj.status === "subtitled" || proj.status === "planned") {
        try {
          const planRes = await fetch(`/api/projects/${id}`);
          // Wir holen den cut_plan via separater Datei nicht — der GET endpoint hat ihn nicht.
          // Pragmatisch: wenn status >= planned, wir wissen es gibt einen cut_plan,
          // aber wir haben ihn nicht im Frontend bis runPipeline läuft. Skip.
        } catch (_) {}
      }
      // Preview anzeigen wenn Status "cut" oder "subtitled".
      // Bei "planned" prüfen ob rough_cut.mp4 noch existiert (z.B. nach fehlgeschlagenem Re-Plan).
      if (proj.status === "cut" || proj.status === "subtitled") {
        setPhase(3);
        setVideoCacheBust(Date.now());
      } else if (proj.status === "planned") {
        // HEAD-Request: prüfen ob rough_cut.mp4 noch vom letzten erfolgreichen Cut da ist
        try {
          const head = await fetch(`/api/pipeline/${id}/preview/rough_cut.mp4`, { method: "HEAD" });
          if (head.ok) {
            setPhase(3);
            setVideoCacheBust(Date.now());
          } else {
            setPhase(-1);
            setClips([]);
            setClaudeReasoning("");
          }
        } catch (_) {
          setPhase(-1);
          setClips([]);
          setClaudeReasoning("");
        }
      } else {
        setPhase(-1);
        setClips([]);
        setClaudeReasoning("");
      }
    } catch (e) {
      console.error("Projekt-Details laden fehlgeschlagen", e);
    }
  }

  /* ----- Projekt-Wechsel ----- */
  async function onSelectProject(id) {
    setSelectedId(id);
    localStorage.setItem(LS_LAST_PROJECT, id);
    setFiles([]);
    setError("");
    setPipelineMsg("");
    await loadProjectDetails(id);
  }

  /* ----- Neues Projekt anlegen ----- */
  async function createProject() {
    if (!newName.trim()) { setError("Bitte gib einen Projektnamen ein."); return; }
    setError("");
    try {
      const p = await api("POST", "/api/projects/", {
        name: newName.trim().replace(/\s+/g, "_"),
        platform: newPlatform,
        engine_version: newEngine,
      });
      localStorage.setItem(LS_LAST_PROJECT, p.id);
      setNewName("");
      setPhase(-1);
      setClips([]);
      setClaudeReasoning("");
      setFiles([]);
      await loadProjectList(false);
      setSelectedId(p.id);
    } catch (e) {
      setError("Projekt anlegen fehlgeschlagen: " + e.message);
    }
  }

  /* ----- Upload ----- */
  function onPick(e) {
    const picked = Array.from(e.target.files || []).map((f) => ({
      file: f, name: f.name, size: (f.size / 1024 / 1024).toFixed(1) + " MB",
    }));
    setFiles(picked);
  }
  function removeFile(i) { setFiles(files.filter((_, idx) => idx !== i)); }

  async function uploadFiles() {
    if (!files.length || !selectedId) return;
    setUploading(true);
    setError("");
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

  /* ----- Pipeline auto-progressing ----- */
  async function runPipeline() {
    if (!selectedId) return;
    setError("");
    setClips([]);
    setClaudeReasoning("");

    try {
      // Step 1: Analyse (Whisper-Wörter + Gemini-Visual-Phasen)
      setPhase(0);
      setPipelineMsg("Originalvideo wird analysiert … (Whisper + Visual, 1-3 Min)");
      await api("POST", `/api/pipeline/${selectedId}/analyse`);

      // Step 2: Schnittplan
      setPhase(1);
      setPipelineMsg("Rohschnitt wird geplant …");
      const cutPlan = await api("POST", `/api/pipeline/${selectedId}/plan-cuts`);
      setClips(cutPlan.clips || []);
      setClaudeReasoning(cutPlan.claude_reasoning || "");

      // Step 3: Auto-confirm + execute-cut
      setPhase(2);
      setPipelineMsg("Rohschnitt wird gerendert (~30s) …");
      await api("POST", `/api/feedback/${selectedId}/confirm`, { confirmed: true });
      await api("POST", `/api/pipeline/${selectedId}/execute-cut`);

      // Done
      setPhase(3);
      setPipelineMsg("");
      setVideoCacheBust(Date.now());
      await loadProjectDetails(selectedId);
    } catch (e) {
      setError("Pipeline-Fehler: " + e.message);
      setPhase(-1);
      setPipelineMsg("");
    }
  }

  async function addSubtitles() {
    if (!selectedId) return;
    setSubtitling(true);
    setError("");
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
    ? `/api/pipeline/${selected.id}/preview/${selected.status === "subtitled" ? "with_subtitles.mp4" : "rough_cut.mp4"}?t=${videoCacheBust}`
    : null;

  return (
    <div className="app">
      {/* Top bar */}
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span className="wordmark">MEINFLUSS</span>
          <div className="brand-divider" />
          <div className="app-name">
            <span className="kicker">Studio</span>
            <span className="title">KI Video Editor</span>
          </div>
        </div>
        <div className="topbar-right">
          <div className="userchip">
            <span className="avatar">C</span>
            <span className="who">chris@meinfluss.de</span>
          </div>
        </div>
      </header>

      <div className="page-head">
        <h1>Reels & Shorts automatisch schneiden</h1>
        <p>Lade dein Rohmaterial hoch — Gemini analysiert, Claude plant den Schnitt und der Editor rendert dein fertiges Video für die Plattform deiner Wahl.</p>
      </div>

      {error && (
        <div className="alert alert-err">
          <Ico.x style={{ marginTop: 1, flex: "0 0 auto" }} />
          <div>{error}</div>
        </div>
      )}

      {/* Existing projects */}
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

      {/* New project */}
      <Card num="1" title="Neues Projekt" sub="Name, Zielplattform und Schnitt-Engine wählen">
        <div className="row">
          <div className="field" style={{ flex: "2 1 220px" }}>
            <label className="label">Projektname</label>
            <input className="input" placeholder="z.B. TikTok_Juni_01" value={newName}
              onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createProject()} />
          </div>
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
            </select>
          </div>
          <div className="field" style={{ justifyContent: "flex-end" }}>
            <button className="btn btn-primary" onClick={createProject}><Ico.plus /> Projekt erstellen</button>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          v1 = Claude generiert Schnitt-Zeiten direkt. v2 = Code gruppiert Sätze + visuelle Priorität.
          v3 = Verständlichkeit als Hauptkriterium: Inhalt schlägt Bild/Audio, bei Wiederholungen gewinnt die klarere Formulierung.
        </div>
      </Card>

      {/* Upload */}
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

      {/* Pipeline */}
      {selectedId && (
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

      {/* Result */}
      {isDone && selected && videoUrl && (
        <Card icon={<Ico.film />} title="Fertiges Video" sub="Vorschau, Download & Untertitel">
          <div className="result">
            <div className="result-preview">
              <video controls preload="metadata" src={videoUrl} />
            </div>
            <div className="result-info">
              <h3>{selected.name}_{selected.status === "subtitled" ? "with_subtitles" : "rough_cut"}.mp4</h3>
              <div className="muted" style={{ fontSize: 14 }}>
                {clips.length} Schnitt-Clips · {selected.platform} · Status: {selected.status}
              </div>
              <div className="ri-meta">
                <Pill kind="done">Schnitt fertig</Pill>
                <Pill kind="platform">{selected.platform}</Pill>
                {selected.status === "subtitled" && <Pill kind="done">mit Untertiteln</Pill>}
              </div>
              <div className="ri-actions">
                <a className="btn btn-primary" href={videoUrl} download={`${selected.name}_${selected.status === "subtitled" ? "with_subtitles" : "rough_cut"}.mp4`}>
                  <Ico.download /> Herunterladen
                </a>
                {selected.status !== "subtitled" && (
                  <button className="btn btn-soft" onClick={addSubtitles} disabled={subtitling}>
                    {subtitling ? <><span className="spinner" /> Wird transkribiert …</> : <><Ico.caption /> Untertitel hinzufügen</>}
                  </button>
                )}
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
