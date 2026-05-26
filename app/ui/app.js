const state = {
  dashboard: null,
  requests: [],
  selectedId: null,
  filter: "pending",
  intakePreview: null,
  board: null,
  decisions: null,
  costs: null,
  ideas: null,
  activeView: "inbox",
};
const byId = (id) => document.getElementById(id);

function escapeText(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;");
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Falha na operacao.");
  return payload;
}

function toast(message, error = false) {
  const element = byId("toast");
  element.textContent = message;
  element.className = error ? "toast error" : "toast";
  element.hidden = false;
  window.setTimeout(() => { element.hidden = true; }, 3600);
}

function statusClass(status) {
  if (["applied_validated", "approved_for_apply", "rolled_back", "resolved", "ended"].includes(status)) return "done";
  if (["rejected", "not_actionable", "failed"].includes(status)) return "blocked";
  return "";
}

function money(value) {
  return `US$ ${Number(value || 0).toFixed(2)}`;
}

function renderMetrics(metrics) {
  const values = [
    ["Runs", metrics.run_count],
    ["Acionaveis", `${Math.round(metrics.execution_ready_rate * 100)}%`],
    ["Escalacoes", `${Math.round(metrics.escalation_rate * 100)}%`],
    ["Agentes/run", metrics.average_agents_per_run],
  ];
  byId("metrics").innerHTML = values.map(([label, value]) =>
    `<div class="metric"><span>${label}</span><strong>${escapeText(value)}</strong></div>`
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
    `<div class="run"><strong>${escapeText(run.user_goal || run.run_id)}</strong><span>${escapeText(run.project_id || "sem projeto")} / ${escapeText(run.initiative_id || "sem iniciativa")}</span><span>${escapeText(run.active_flow)} | ${escapeText(run.execution_tier || "legacy")}</span><span class="run-status ${statusClass(run.status)}">${escapeText(run.status)}</span></div>`
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
  if (["approved_for_dry_run", "awaiting_patch"].includes(request.status)) add("Validar patch", "prepare", "primary");
  if (request.status === "awaiting_apply_approval") {
    add("Aprovar aplicacao", "approve-apply", "primary");
    add("Rejeitar patch", "reject-apply", "danger");
  }
  if (request.status === "approved_for_apply") add("Aplicar e validar", "apply", "primary");
  if (request.status === "applied_validated") add("Reverter aplicacao", "rollback", "danger");
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
  if (request.preparation?.patch_text && !byId("patch-text").value) byId("patch-text").value = request.preparation.patch_text;
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
      method: "POST", body: JSON.stringify(payload),
    });
    toast("Operacao registrada.");
    await loadAll();
    await selectRequest(state.selectedId);
  } catch (error) {
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
  byId("preview-budget").textContent = money(policy.max_cost_usd);
  byId("preview-agents").textContent = String(policy.planned_agent_count);
  byId("preview-note").textContent = `${preview.rationale} Orcamento estimado; efeitos exigem approval.`;
  byId("confirm-cost").checked = false;
  byId("start-run").disabled = true;
  byId("intake-preview").hidden = false;
}

async function previewRun(event) {
  event.preventDefault();
  try {
    renderPreview(await api("/api/intake/preview", { method: "POST", body: JSON.stringify(runPayload()) }));
  } catch (error) {
    clearPreview();
    toast(error.message, true);
  }
}

async function startRun() {
  if (!state.intakePreview || !byId("confirm-cost").checked) return;
  byId("start-run").disabled = true;
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify({ ...runPayload(), cost_confirmed: true }) });
    toast(`Run ${run.run_id} enfileirada.`);
    clearPreview();
    byId("run-form").reset();
    byId("run-workspace").value = ".";
    await loadAll();
  } catch (error) {
    byId("start-run").disabled = false;
    toast(error.message, true);
  }
}

const boardLabels = {
  planned: "A iniciar",
  in_progress: "Em andamento",
  human_decision: "Decisao humana",
  approval: "Aprovacao",
  done: "Concluido",
  blocked: "Bloqueado",
};

function renderBoard(board) {
  byId("kanban").innerHTML = Object.entries(boardLabels).map(([key, label]) => {
    const cards = board.columns[key] || [];
    return `<section class="kanban-column"><header class="kanban-head"><span>${label}</span><span class="count">${cards.length}</span></header>${cards.map((card) =>
      `<article class="work-card"><strong>${escapeText(card.user_goal)}</strong><span>${escapeText(card.project_id || "sem projeto")} / ${escapeText(card.initiative_id || "sem iniciativa")}</span><span>${escapeText(card.execution_tier || card.status)}</span></article>`
    ).join("") || `<p class="muted">Vazio</p>`}</section>`;
  }).join("");
}

async function renderFlow(runId = "") {
  const runs = state.dashboard.recent_runs.runs.filter((run) => run.agent_count > 0);
  const select = byId("flow-run-select");
  select.innerHTML = runs.map((run) => `<option value="${escapeText(run.run_id)}">${escapeText(run.project_id)} / ${escapeText(run.initiative_id)} - ${escapeText(run.active_flow)}</option>`).join("");
  const selectedId = runId || select.value || runs[0]?.run_id || "";
  if (selectedId) select.value = selectedId;
  const flow = await api(`/api/flow${selectedId ? `?run_id=${encodeURIComponent(selectedId)}` : ""}`);
  if (!flow.run) {
    byId("flow-meta").innerHTML = `<p class="muted">Sem runs executadas.</p>`;
    byId("agent-flow").innerHTML = "";
    return;
  }
  byId("flow-meta").innerHTML = [
    flow.run.project_id || "sem projeto", flow.run.initiative_id || "sem iniciativa", flow.run.active_flow, flow.run.execution_tier || "legacy", flow.run.cos_decision || flow.run.status,
  ].map((value) => `<span class="pill neutral">${escapeText(value)}</span>`).join("");
  byId("agent-flow").innerHTML = flow.agents.map((agent) =>
    `<article class="agent-node"><strong>${escapeText(agent.agent_name.replaceAll("_", " "))}</strong><span class="ece ${agent.ece === "C3" ? "c3" : ""}">${escapeText(agent.ece)}</span><span>${escapeText(agent.artifact_type || "artefato")}</span><span>${agent.schema_valid ? "Schema valido" : "Revisar schema"}</span></article>`
  ).join("") || `<p class="muted">Sem interacoes registradas.</p>`;
}

function renderDecisions(snapshot) {
  byId("decision-badge").textContent = snapshot.pending_count;
  byId("decision-pending-count").textContent = snapshot.pending_count;
  byId("decision-list").innerHTML = snapshot.decisions.map((decision) => {
    const pending = decision.status === "pending";
    return `<article class="panel decision-card">
      <div class="section-head">
        <div><strong>${escapeText(decision.project_id || "sem projeto")} / ${escapeText(decision.initiative_id || "sem iniciativa")}</strong><p class="muted">${escapeText(decision.source_agent)} | ${escapeText(decision.run_id)}</p></div>
        <span class="pill ${pending ? "warning" : "done"}">${escapeText(decision.status)}</span>
      </div>
      <p class="reason">${escapeText(decision.reason)}</p>
      <p class="question">${escapeText(decision.question)}</p>
      ${pending ? `<form class="decision-form" data-decision-id="${escapeText(decision.decision_id)}">
        <label class="objective-field"><span>Resposta humana</span><textarea name="response" required maxlength="4000"></textarea></label>
        <div class="decision-actions">
          <label class="select-field"><span>Direcao</span><select name="resolution"><option value="continue">Continuar</option><option value="request_revision">Pedir revisao</option><option value="close">Encerrar</option></select></label>
          <button class="action primary" type="submit">Registrar decisao</button>
        </div>
      </form>` : `<div class="resolved-response"><strong>${escapeText(decision.resolution)}</strong><p>${escapeText(decision.response)}</p></div>`}
    </article>`;
  }).join("") || `<div class="panel empty-detail"><h3>Sem escaladas humanas</h3></div>`;
  document.querySelectorAll(".decision-form").forEach((form) => form.addEventListener("submit", respondDecision));
}

async function respondDecision(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api(`/api/decisions/${encodeURIComponent(form.dataset.decisionId)}/respond`, {
      method: "POST",
      body: JSON.stringify({ response: form.elements.response.value, resolution: form.elements.resolution.value, by: "owner" }),
    });
    toast("Decisao humana registrada.");
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderBars(containerId, items, labelKey) {
  const max = Math.max(...items.map((item) => item.estimated_budget_usd), 1);
  const widthClass = (amount) => {
    const band = Math.max(0, Math.min(100, Math.ceil((Number(amount) / max) * 10) * 10));
    return `w-${band}`;
  };
  byId(containerId).innerHTML = items.map((item) =>
    `<div class="bar-row"><div class="bar-label"><span>${escapeText(item[labelKey])}</span><strong>${money(item.estimated_budget_usd)}</strong></div><div class="bar-track"><div class="bar-fill ${widthClass(item.estimated_budget_usd)}"></div></div><span class="muted">${item.runs} runs</span></div>`
  ).join("") || `<p class="muted">Sem dados.</p>`;
}

function renderCosts(costs) {
  byId("cost-overview").innerHTML = [
    ["Runs contabilizadas", costs.total_runs],
    ["Orcamento acumulado", money(costs.estimated_budget_usd)],
    ["Tracing amostrado", costs.tracing_enabled ? `${Math.round(costs.sampling_rate * 100)}%` : "Inativo"],
    ["Validacoes identificadas", money(costs.validation_estimated_budget_usd)],
    ["Custo real", costs.real_cost_available ? "Integrado" : "Via LangSmith"],
  ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeText(value)}</strong></div>`).join("");
  renderBars("cost-by-tier", costs.by_tier, "tier");
  renderBars("cost-by-project", costs.by_project, "project_id");
}

function renderIdeas(snapshot) {
  byId("idea-list").innerHTML = snapshot.ideas.map((idea) =>
    `<article class="panel idea-card">
      <div class="section-head"><strong>${escapeText(idea.title)}</strong><span class="pill ${idea.status === "candidate" ? "neutral" : "done"}">${escapeText(idea.status)}</span></div>
      <span class="muted">${escapeText(idea.project_id || "sem projeto")} / ${escapeText(idea.initiative_id || "sem iniciativa")} | ${escapeText(idea.priority)}</span>
      <p>${escapeText(idea.context)}</p>
      <div class="idea-card-footer">
        <span class="muted">${escapeText(idea.source_run_id || "Registro humano")}</span>
        ${idea.status === "candidate" ? `<button type="button" class="action promote-idea" data-idea-id="${escapeText(idea.idea_id)}">Promover para inbox</button>` : ""}
      </div>
    </article>`
  ).join("") || `<div class="panel empty-detail"><h3>Parking lot vazio</h3></div>`;
  document.querySelectorAll(".promote-idea").forEach((button) => button.addEventListener("click", promoteIdea));
}

async function createIdea(event) {
  event.preventDefault();
  try {
    await api("/api/ideas", {
      method: "POST",
      body: JSON.stringify({
        title: byId("idea-title").value.trim(),
        context: byId("idea-context").value.trim(),
        project_id: byId("idea-project").value.trim(),
        initiative_id: byId("idea-initiative").value.trim(),
        priority: byId("idea-priority").value,
      }),
    });
    byId("idea-form").reset();
    toast("Ideia registrada.");
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function promoteIdea(event) {
  try {
    await api(`/api/ideas/${encodeURIComponent(event.currentTarget.dataset.ideaId)}/promote`, { method: "POST", body: "{}" });
    toast("Ideia promovida para o inbox.");
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  }
}

function switchView(view) {
  state.activeView = view;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".app-view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  if (view === "flow") renderFlow().catch((error) => toast(error.message, true));
}

async function loadAll() {
  try {
    const [dashboard, requestSnapshot, board, decisions, costs, ideas] = await Promise.all([
      api("/api/dashboard"), api("/api/requests"), api("/api/board"),
      api("/api/decisions"), api("/api/costs"), api("/api/ideas"),
    ]);
    state.dashboard = dashboard;
    state.requests = requestSnapshot.requests;
    state.board = board;
    state.decisions = decisions;
    state.costs = costs;
    state.ideas = ideas;
    byId("system-status").textContent = "Runtime local conectado";
    renderMetrics(dashboard.metrics);
    renderRuns();
    renderDemands();
    renderList();
    renderBoard(board);
    renderDecisions(decisions);
    renderCosts(costs);
    renderIdeas(ideas);
    if (state.activeView === "flow") await renderFlow(byId("flow-run-select").value);
  } catch (error) {
    byId("system-status").textContent = "Runtime indisponivel";
    toast(error.message, true);
  }
}

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll(".segment").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.filter = button.dataset.filter;
  renderList();
}));
byId("refresh").addEventListener("click", loadAll);
byId("run-form").addEventListener("submit", previewRun);
byId("start-run").addEventListener("click", startRun);
byId("confirm-cost").addEventListener("change", (event) => { byId("start-run").disabled = !event.target.checked; });
["run-goal", "run-project", "run-initiative", "run-workspace"].forEach((id) => byId(id).addEventListener("input", clearPreview));
byId("flow-run-select").addEventListener("change", (event) => renderFlow(event.target.value).catch((error) => toast(error.message, true)));
byId("idea-form").addEventListener("submit", createIdea);
loadAll();
window.setInterval(() => {
  const runs = state.dashboard?.recent_runs?.runs || [];
  if (runs.some((run) => ["queued", "running"].includes(run.status))) loadAll();
}, 5000);
