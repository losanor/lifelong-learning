const state = { dashboard: null, requests: [], selectedId: null, filter: "pending", intakePreview: null };
const byId = (id) => document.getElementById(id);

function escapeText(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Falha na operacao.");
  return payload;
}

function toast(message, error = false) {
  const element = byId("toast");
  element.textContent = message;
  element.className = error ? "toast error" : "toast";
  element.hidden = false;
  window.setTimeout(() => { element.hidden = true; }, 3400);
}

function statusClass(status) {
  if (["applied_validated", "approved_for_apply", "rolled_back"].includes(status)) return "done";
  if (["rejected", "not_actionable"].includes(status)) return "blocked";
  return "";
}

function renderMetrics(metrics) {
  const values = [
    ["Runs", metrics.run_count],
    ["Acionaveis", `${Math.round(metrics.execution_ready_rate * 100)}%`],
    ["Escalacoes", `${Math.round(metrics.escalation_rate * 100)}%`],
    ["Agentes/run", metrics.average_agents_per_run],
  ];
  byId("metrics").innerHTML = values.map(([label, value]) =>
    `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`
  ).join("");
}

function visibleRequests() {
  if (state.filter === "all") return state.requests;
  const pendingIds = new Set((state.dashboard.pending.pending || []).map((item) => item.request_id));
  return state.requests.filter((request) => pendingIds.has(request.request_id));
}

function renderList() {
  const requests = visibleRequests();
  byId("pending-count").textContent = state.dashboard.pending.pending_count;
  const list = byId("request-list");
  if (!requests.length) {
    list.innerHTML = `<p class="muted">Nenhum item nesta fila.</p>`;
    return;
  }
  list.innerHTML = "";
  requests.forEach((request) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `request-item ${request.request_id === state.selectedId ? "active" : ""}`;
    button.innerHTML = `<strong>${escapeText(request.initiative_id || request.request_id)}</strong><span>${escapeText(request.status)} | ${escapeText(request.execution_policy.execution_tier || "sem tier")}</span>`;
    button.addEventListener("click", () => selectRequest(request.request_id));
    list.appendChild(button);
  });
}

function renderRuns() {
  const runs = state.dashboard.recent_runs.runs || [];
  byId("run-list").innerHTML = runs.slice(0, 10).map((run) =>
    `<div class="run"><strong>${escapeText(run.user_goal || run.run_id)}</strong><span>${escapeText(run.project_id)} / ${escapeText(run.initiative_id)}</span><span>${escapeText(run.active_flow)} | ${escapeText(run.execution_tier || "legacy")}</span><span class="run-status ${statusClass(run.status)}">${escapeText(run.status)}</span></div>`
  ).join("") || `<p class="muted">Sem runs registradas.</p>`;
}

function renderDemands() {
  const demands = state.dashboard.demands.demands || [];
  byId("demand-count").textContent = state.dashboard.demands.demand_count;
  byId("demand-list").innerHTML = demands.slice(0, 5).map((demand) =>
    `<div class="demand"><strong>${escapeText(demand.user_goal)}</strong><span>${escapeText(demand.project_id)} / ${escapeText(demand.status)}</span></div>`
  ).join("") || `<p class="muted">Sem entradas externas.</p>`;
}

function listItems(containerId, values) {
  byId(containerId).innerHTML = (values || []).map((value) => `<li>${escapeText(value)}</li>`).join("");
}

function renderActions(request) {
  const actions = byId("actions");
  actions.innerHTML = "";
  const add = (label, action, kind = "") => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `action ${kind}`;
    button.textContent = label;
    button.addEventListener("click", () => submitAction(action));
    actions.appendChild(button);
  };
  if (request.status === "pending_approval") {
    add("Aprovar preparacao", "approve", "primary");
    add("Rejeitar", "reject", "danger");
  }
  if (["approved_for_dry_run", "awaiting_patch"].includes(request.status)) {
    add("Validar patch", "prepare", "primary");
  }
  if (request.status === "awaiting_apply_approval") {
    add("Aprovar aplicacao", "approve-apply", "primary");
    add("Rejeitar patch", "reject-apply", "danger");
  }
  if (request.status === "approved_for_apply") {
    add("Aplicar e validar", "apply", "primary");
  }
  if (request.status === "applied_validated") {
    add("Reverter aplicacao", "rollback", "danger");
  }
}

async function selectRequest(requestId) {
  state.selectedId = requestId;
  const request = await api(`/api/requests/${encodeURIComponent(requestId)}`);
  const timeline = await api(`/api/threads/${encodeURIComponent(request.run_id)}/timeline`);
  const memory = await api(`/api/memory?namespace=${encodeURIComponent(request.operational_packet.memory_namespace || "")}`);
  byId("empty-detail").hidden = true;
  byId("request-detail").hidden = false;
  byId("detail-scope").textContent = [request.project_id, request.initiative_id].filter(Boolean).join(" / ") || request.run_id;
  byId("detail-title").textContent = request.request_id;
  const badge = byId("detail-status");
  badge.textContent = request.status;
  badge.className = `status ${statusClass(request.status)}`;
  byId("detail-reason").textContent = request.status_reason;
  byId("target-refs").innerHTML = request.target_refs.map((ref) => `<span class="tag">${escapeText(ref)}</span>`).join("");
  listItems("recommended-actions", request.recommended_actions);
  listItems("verification-steps", request.verification_steps);
  byId("timeline").innerHTML = timeline.checkpoints.map((item) =>
    `<li>${escapeText(item.stage)}<small>${escapeText(item.status)}</small></li>`
  ).join("");
  if (request.preparation?.patch_text && !byId("patch-text").value) {
    byId("patch-text").value = request.preparation.patch_text;
  }
  if (request.preparation?.workspace_root) byId("workspace-root").value = request.preparation.workspace_root;
  const evidence = request.application || request.preparation;
  byId("evidence").hidden = !evidence;
  byId("evidence-content").textContent = evidence ? JSON.stringify(evidence, null, 2) : "";
  byId("memory").hidden = false;
  byId("memory-content").textContent = memory.decision_log || memory.compact_memory || "Sem memoria especifica registrada.";
  byId("patch-result").textContent = request.preparation?.preparation_status || "";
  renderActions(request);
  renderList();
}

async function submitAction(action) {
  const payload = { by: "owner", workspace_root: byId("workspace-root").value.trim() || "." };
  if (action === "prepare") payload.patch_text = byId("patch-text").value;
  if (action === "apply") {
    payload.validations = ["git_diff_check"];
    if (byId("validate-compile").checked) payload.validations.push("python_compile");
    if (byId("validate-tests").checked) payload.validations.push("unit_tests");
  }
  if (action === "rollback") payload.notes = "Rollback requested from console.";
  try {
    await api(`/api/requests/${encodeURIComponent(state.selectedId)}/${action}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    toast("Operacao registrada.");
    await loadDashboard();
    await selectRequest(state.selectedId);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadDashboard() {
  try {
    state.dashboard = await api("/api/dashboard");
    const requestSnapshot = await api("/api/requests");
    state.requests = requestSnapshot.requests;
    byId("system-status").textContent = "Runtime local conectado";
    renderMetrics(state.dashboard.metrics);
    renderRuns();
    renderDemands();
    renderList();
  } catch (error) {
    byId("system-status").textContent = "Runtime indisponivel";
    toast(error.message, true);
  }
}

function runPayload() {
  return {
    user_goal: byId("run-goal").value.trim(),
    project_id: byId("run-project").value.trim(),
    initiative_id: byId("run-initiative").value.trim(),
    workspace_root: byId("run-workspace").value.trim() || ".",
  };
}

function clearPreview() {
  state.intakePreview = null;
  byId("intake-preview").hidden = true;
  byId("confirm-cost").checked = false;
  byId("start-run").disabled = true;
}

function renderPreview(preview) {
  state.intakePreview = preview;
  const policy = preview.execution_policy;
  byId("preview-flow").textContent = preview.active_flow;
  byId("preview-tier").textContent = policy.execution_tier;
  byId("preview-budget").textContent = `US$ ${Number(policy.max_cost_usd).toFixed(2)}`;
  byId("preview-agents").textContent = String(policy.planned_agent_count);
  byId("preview-note").textContent = `${preview.rationale} O teto e uma estimativa operacional, nao um bloqueio de cobranca do provedor. Efeitos no workspace permanecem sujeitos a aprovacao.`;
  byId("confirm-cost").checked = false;
  byId("start-run").disabled = true;
  byId("intake-preview").hidden = false;
}

async function previewRun(event) {
  event.preventDefault();
  try {
    const preview = await api("/api/intake/preview", {
      method: "POST",
      body: JSON.stringify(runPayload()),
    });
    renderPreview(preview);
  } catch (error) {
    clearPreview();
    toast(error.message, true);
  }
}

async function startRun() {
  if (!state.intakePreview || !byId("confirm-cost").checked) return;
  byId("start-run").disabled = true;
  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({ ...runPayload(), cost_confirmed: true }),
    });
    toast(`Run ${run.run_id} enfileirada.`);
    clearPreview();
    byId("run-form").reset();
    byId("run-workspace").value = ".";
    await loadDashboard();
  } catch (error) {
    byId("start-run").disabled = false;
    toast(error.message, true);
  }
}

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    renderList();
  });
});
byId("refresh").addEventListener("click", loadDashboard);
byId("run-form").addEventListener("submit", previewRun);
byId("start-run").addEventListener("click", startRun);
byId("confirm-cost").addEventListener("change", (event) => {
  byId("start-run").disabled = !event.target.checked;
});
["run-goal", "run-project", "run-initiative", "run-workspace"].forEach((id) => {
  byId(id).addEventListener("input", clearPreview);
});
loadDashboard();
window.setInterval(() => {
  const runs = state.dashboard?.recent_runs?.runs || [];
  if (runs.some((run) => ["queued", "running"].includes(run.status))) loadDashboard();
}, 5000);
