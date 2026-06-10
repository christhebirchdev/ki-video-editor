let currentProjectId = null;
let currentCutPlan = null;

const LS_LAST_PROJECT = "kveditor.lastProjectId";

async function api(method, path, body = null) {
    const opts = { method, headers: {} };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
    return res.json();
}

function setStatus(id, msg, ok = true) {
    const el = document.getElementById(id);
    if (el) { el.textContent = msg; el.className = "status " + (ok ? "ok" : "err"); }
}

async function createProject() {
    const name = document.getElementById("project-name").value.trim();
    const platform = document.getElementById("platform").value;
    if (!name) { alert("Bitte Projektname eingeben"); return; }
    try {
        const p = await api("POST", "/api/projects/", { name, platform });
        currentProjectId = p.id;
        localStorage.setItem(LS_LAST_PROJECT, p.id);
        setStatus("project-info", `✅ Projekt erstellt — ID: ${p.id}`);
        document.getElementById("upload-section").style.display = "block";
        document.getElementById("pipeline-section").style.display = "block";
        await loadProjectList();  // Dropdown aktualisieren
    } catch(e) { setStatus("project-info", `Fehler: ${e.message}`, false); }
}

async function loadProjectList() {
    try {
        const data = await api("GET", "/api/projects/");
        const select = document.getElementById("project-picker");
        const previousValue = select.value;
        select.innerHTML = '<option value="">— bestehendes Projekt auswählen oder unten neu anlegen —</option>';
        // Neueste zuerst (created_at absteigend)
        const sorted = [...data.projects].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
        for (const p of sorted) {
            const opt = document.createElement("option");
            opt.value = p.id;
            const date = p.created_at ? p.created_at.slice(0, 16).replace("T", " ") : "";
            opt.textContent = `${p.name} · ${p.platform} · ${p.status} · ${p.id} · ${date}`;
            select.appendChild(opt);
        }
        // Letztes Projekt automatisch wiederherstellen (außer Picker hat schon eine andere Auswahl)
        const lastId = localStorage.getItem(LS_LAST_PROJECT);
        const restoreId = previousValue || lastId;
        if (restoreId && sorted.find(p => p.id === restoreId)) {
            select.value = restoreId;
            await loadSelectedProject();
        }
    } catch(e) {
        console.error("Projekt-Liste laden fehlgeschlagen", e);
        setStatus("picker-info", `Konnte Projekt-Liste nicht laden: ${e.message}`, false);
    }
}

async function loadSelectedProject() {
    const id = document.getElementById("project-picker").value;
    if (!id) {
        currentProjectId = null;
        setStatus("picker-info", "");
        return;
    }
    try {
        const proj = await api("GET", `/api/projects/${id}`);
        currentProjectId = proj.id;
        localStorage.setItem(LS_LAST_PROJECT, proj.id);
        const fileList = proj.files.length ? proj.files.join(", ") : "(noch keine Dateien)";
        setStatus("picker-info", `📂 ${proj.name} · ${proj.platform} · Status: ${proj.status} · Dateien: ${fileList}`);
        document.getElementById("upload-section").style.display = "block";
        document.getElementById("pipeline-section").style.display = "block";
        restoreButtonState(proj.status, proj.files.length > 0);
    } catch(e) {
        setStatus("picker-info", `Fehler beim Laden: ${e.message}`, false);
    }
}

function restoreButtonState(status, hasFiles) {
    const btnAnalyse = document.getElementById("btn-analyse");
    const btnPlan = document.getElementById("btn-plan");
    const postCut = document.getElementById("post-cut");
    const takeReview = document.getElementById("take-review");

    // Defaults
    btnAnalyse.disabled = !hasFiles;
    btnPlan.disabled = true;
    if (postCut) postCut.style.display = "none";
    if (takeReview) takeReview.style.display = "none";

    // Status-basierte Freigabe (kumulativ)
    const advanced = ["analysed", "planned", "cut", "subtitled"];
    if (advanced.includes(status)) {
        btnPlan.disabled = false;
        setStatus("pipeline-status", `✓ Analyse vorhanden — Schritt 2 (Claude) verfügbar`);
    }
    if (status === "cut" || status === "subtitled") {
        showRoughCutPreview();
    }
}

window.addEventListener("DOMContentLoaded", loadProjectList);

async function uploadFiles() {
    const files = document.getElementById("file-input").files;
    if (!files.length || !currentProjectId) return;
    const form = new FormData();
    for (const f of files) form.append("files", f);
    try {
        const res = await fetch(`/api/projects/${currentProjectId}/upload`, { method: "POST", body: form });
        const data = await res.json();
        setStatus("upload-status", `✅ Hochgeladen: ${data.uploaded.join(", ")}`);
    } catch(e) { setStatus("upload-status", `Fehler: ${e.message}`, false); }
}

async function runAnalysis() {
    setStatus("pipeline-status", "⏳ Gemini analysiert das Video… (kann 1–2 Min. dauern)");
    document.getElementById("btn-analyse").disabled = true;
    try {
        await api("POST", `/api/pipeline/${currentProjectId}/analyse`);
        setStatus("pipeline-status", "✅ Analyse abgeschlossen");
        document.getElementById("btn-plan").disabled = false;
    } catch(e) {
        setStatus("pipeline-status", `Fehler: ${e.message}`, false);
        document.getElementById("btn-analyse").disabled = false;
    }
}

async function runCutPlanning() {
    setStatus("pipeline-status", "⏳ Claude erstellt Schnittplan…");
    document.getElementById("btn-plan").disabled = true;
    try {
        currentCutPlan = await api("POST", `/api/pipeline/${currentProjectId}/plan-cuts`);
        renderTakeReview(currentCutPlan);
        setStatus("pipeline-status", "✅ Schnittplan bereit — bitte prüfen und bestätigen");
    } catch(e) {
        setStatus("pipeline-status", `Fehler: ${e.message}`, false);
        document.getElementById("btn-plan").disabled = false;
    }
}

function fmtMs(ms) {
    if (ms === null || ms === undefined) return "—";
    return (ms / 1000).toFixed(2) + "s";
}

function typeLabel(type) {
    const labels = {
        content: "🎙️ Content",
        silence: "🔇 Stille",
        breath: "🫁 Atmer",
        filler_word: "💬 Füllwort",
        false_start: "↩️ Fehlstart",
        repetition: "🔁 Wiederholung",
    };
    return labels[type] || type;
}

function renderTakeReview(plan) {
    const container = document.getElementById("take-decisions");
    container.innerHTML = "";
    const keptCount = plan.decisions.filter(d => d.keep).length;
    const totalCount = plan.decisions.length;
    const totalDurationMs = plan.decisions
        .filter(d => d.keep && d.in_point_ms !== null && d.out_point_ms !== null)
        .reduce((sum, d) => sum + (d.out_point_ms - d.in_point_ms), 0);
    const summary = document.createElement("div");
    summary.className = "hint";
    summary.innerHTML = `📊 <strong>${keptCount}/${totalCount}</strong> Segmente behalten — Cut-Dauer: <strong>${(totalDurationMs/1000).toFixed(1)}s</strong>`;
    container.appendChild(summary);

    for (const d of plan.decisions) {
        const card = document.createElement("div");
        card.className = `take-card ${d.keep ? "" : "rejected"}`;
        const inOut = d.keep
            ? `<span class="take-meta">[${fmtMs(d.in_point_ms)} – ${fmtMs(d.out_point_ms)}]</span>`
            : `<span class="take-meta" style="opacity:0.5">verworfen</span>`;
        card.innerHTML = `
            <span class="take-badge ${d.keep ? "badge-keep" : "badge-reject"}">${d.keep ? "✓ Behalten" : "✗ Verwerfen"}</span>
            <strong>#${d.segment_id}</strong>
            <span style="margin-left:0.5rem;color:#888;font-size:0.85rem">${typeLabel(d.type)}</span>
            ${inOut}
            <div class="take-reason">${d.reason}</div>`;
        container.appendChild(card);
    }
    document.getElementById("claude-reasoning").textContent = `Claude: ${plan.claude_reasoning}`;
    document.getElementById("take-review").style.display = "block";
}

async function rerunCut() {
    if (!currentProjectId) return;
    setStatus("pipeline-status", "⏳ Video wird neu geschnitten (Re-Encoding ~30s)…");
    const btn = document.getElementById("btn-rerun-cut");
    if (btn) btn.disabled = true;
    try {
        await api("POST", `/api/pipeline/${currentProjectId}/execute-cut`);
        setStatus("pipeline-status", "✅ Rohschnitt aktualisiert");
        showRoughCutPreview();
    } catch(e) {
        setStatus("pipeline-status", `Fehler: ${e.message}`, false);
    } finally {
        if (btn) btn.disabled = false;
    }
}

function showRoughCutPreview() {
    const video = document.getElementById("rough-cut-video");
    if (!video || !currentProjectId) return;
    const ts = Date.now();
    // Cache-Bust mit Timestamp, falls neu geschnitten wurde
    video.src = `/api/pipeline/${currentProjectId}/preview/rough_cut.mp4?t=${ts}`;
    video.load();
    // Download-Link aktualisieren
    const download = document.getElementById("download-rough-cut");
    if (download) {
        download.href = `/api/pipeline/${currentProjectId}/preview/rough_cut.mp4?t=${ts}`;
        download.download = `rohschnitt_${currentProjectId}.mp4`;
        download.style.display = "inline-block";
    }
    document.getElementById("post-cut").style.display = "block";
}

async function confirmPlan() {
    try {
        await api("POST", `/api/feedback/${currentProjectId}/confirm`, { confirmed: true });
        setStatus("pipeline-status", "⏳ Video wird geschnitten…");
        await api("POST", `/api/pipeline/${currentProjectId}/execute-cut`);
        setStatus("pipeline-status", "✅ Rohschnitt fertig — schau dir die Preview an");
        document.getElementById("take-review").style.display = "none";
        showRoughCutPreview();
    } catch(e) { setStatus("pipeline-status", `Fehler: ${e.message}`, false); }
}

function showCorrectionForm() {
    const form = document.getElementById("correction-form");
    const inputs = document.getElementById("correction-inputs");
    inputs.innerHTML = "";
    for (const d of currentCutPlan.decisions) {
        const row = document.createElement("div");
        row.className = "correction-row";
        row.innerHTML = `
            <label>#${d.segment_id} · ${typeLabel(d.type)}</label>
            <input type="checkbox" id="keep_${d.segment_id}" ${d.keep ? "checked" : ""} />
            <span style="color:#aaa;font-size:0.8rem">behalten</span>
            <input type="text" id="reason_${d.segment_id}" placeholder="Begründung (optional)" style="flex:1"/>`;
        inputs.appendChild(row);
    }
    form.style.display = "block";
}

async function submitCorrections() {
    const corrections = currentCutPlan.decisions.map(d => ({
        segment_id: d.segment_id,
        user_keep: document.getElementById(`keep_${d.segment_id}`).checked,
        user_reason: document.getElementById(`reason_${d.segment_id}`).value
    })).filter(c => {
        const orig = currentCutPlan.decisions.find(d => d.segment_id === c.segment_id);
        return orig.keep !== c.user_keep || c.user_reason;
    });

    try {
        if (corrections.length > 0) {
            await api("POST", `/api/feedback/${currentProjectId}/corrections`, { corrections });
        } else {
            await api("POST", `/api/feedback/${currentProjectId}/confirm`, { confirmed: true });
        }
        setStatus("pipeline-status", "⏳ Video wird mit deinen Korrekturen geschnitten…");
        await api("POST", `/api/pipeline/${currentProjectId}/execute-cut`);
        setStatus("pipeline-status", "✅ Rohschnitt fertig — schau dir die Preview an");
        document.getElementById("take-review").style.display = "none";
        showRoughCutPreview();
    } catch(e) { setStatus("pipeline-status", `Fehler: ${e.message}`, false); }
}

async function addSubtitles() {
    setStatus("pipeline-status", "⏳ Transkribiere und brenne Untertitel ein…");
    document.getElementById("btn-subtitles").disabled = true;
    try {
        await api("POST", `/api/pipeline/${currentProjectId}/add-subtitles`);
        setStatus("pipeline-status", "✅ Fertig! Video mit Untertiteln bereit.");
        const video = document.getElementById("result-video");
        video.src = `/api/pipeline/${currentProjectId}/preview/with_subtitles.mp4`;
        document.getElementById("result-section").style.display = "block";
    } catch(e) { setStatus("pipeline-status", `Fehler: ${e.message}`, false); }
}
