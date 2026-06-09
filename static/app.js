let currentProjectId = null;
let currentCutPlan = null;

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
        setStatus("project-info", `✅ Projekt erstellt — ID: ${p.id}`);
        document.getElementById("upload-section").style.display = "block";
        document.getElementById("pipeline-section").style.display = "block";
    } catch(e) { setStatus("project-info", `Fehler: ${e.message}`, false); }
}

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

function renderTakeReview(plan) {
    const container = document.getElementById("take-decisions");
    container.innerHTML = "";
    for (const d of plan.decisions) {
        const card = document.createElement("div");
        card.className = `take-card ${d.keep ? "" : "rejected"}`;
        card.innerHTML = `
            <span class="take-badge ${d.keep ? "badge-keep" : "badge-reject"}">${d.keep ? "✓ Behalten" : "✗ Verwerfen"}</span>
            <strong>${d.take_id}</strong>
            <span class="take-meta">[${d.in_point}s – ${d.out_point}s]</span>
            <div class="take-reason">${d.reason}</div>`;
        container.appendChild(card);
    }
    document.getElementById("claude-reasoning").textContent = `Claude: ${plan.claude_reasoning}`;
    document.getElementById("take-review").style.display = "block";
}

async function confirmPlan() {
    try {
        await api("POST", `/api/feedback/${currentProjectId}/confirm`, { confirmed: true });
        setStatus("pipeline-status", "⏳ Video wird geschnitten…");
        await api("POST", `/api/pipeline/${currentProjectId}/execute-cut`);
        setStatus("pipeline-status", "✅ Rohschnitt fertig");
        document.getElementById("take-review").style.display = "none";
        document.getElementById("post-cut").style.display = "block";
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
            <label>${d.take_id}</label>
            <input type="checkbox" id="keep_${d.take_id}" ${d.keep ? "checked" : ""} />
            <span style="color:#aaa;font-size:0.8rem">behalten</span>
            <input type="text" id="reason_${d.take_id}" placeholder="Begründung (optional)" style="flex:1"/>`;
        inputs.appendChild(row);
    }
    form.style.display = "block";
}

async function submitCorrections() {
    const corrections = currentCutPlan.decisions.map(d => ({
        take_id: d.take_id,
        user_keep: document.getElementById(`keep_${d.take_id}`).checked,
        user_reason: document.getElementById(`reason_${d.take_id}`).value
    })).filter(c => {
        const orig = currentCutPlan.decisions.find(d => d.take_id === c.take_id);
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
        setStatus("pipeline-status", "✅ Rohschnitt fertig");
        document.getElementById("take-review").style.display = "none";
        document.getElementById("post-cut").style.display = "block";
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
