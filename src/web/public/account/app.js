const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) =>
  Number(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
const number = (value) => Number(value).toLocaleString("en-US");
const day = (value) =>
  new Date(`${value}T12:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
const navigate = (url) =>
  (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);
const paths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
  database:
    '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2m10 0h2a2 2 0 0 1 2 2v2m0 10v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10M7 8h6M7 16h4"/>',
  archive:
    '<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v14h14V7M10 11h4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m18 6-12 12M6 6l12 12"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5"/>',
  upload:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  check: '<path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  shield:
    '<path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4M8 13h.01M16 13h.01M9 17h6"/>',
  play: '<path d="m7 5 12 7-12 7Z"/>',
  workflow:
    '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] ?? paths.file}</svg>`;
for (const el of document.querySelectorAll("[data-icon]"))
  el.innerHTML = icon(el.dataset.icon);
let companies = [],
  jobs = [],
  openExceptions = [],
  records = null,
  datasets = {},
  wizard = null,
  files = [],
  role = "viewer",
  view = ["overview", "sources", "findings", "agents", "runs", "recordings", "workflows"].includes(
    new URLSearchParams(location.search).get("view"),
  )
    ? new URLSearchParams(location.search).get("view")
    : "overview",
  recordingFocus = new URLSearchParams(location.search).get("recording"),
  busy = false,
  generation = 0,
  agents = null,
  synthetic = [],
  reports = [],
  agentFilter = { kind: "all", status: "open", agent: "all" };
// Canonical datasets as GET /deals/{id}/records returns them.
const RECORD_SETS = {
  customers: "Customers",
  invoices: "Invoices / AR",
  vendors: "Vendors",
  purchases: "Vendor purchases",
  subscriptions: "Software subscriptions",
  policies: "Policies",
  purchaseOrders: "Purchase orders",
  purchaseOrderLines: "Purchase-order lines",
  inventory: "Inventory balances",
};
const AGENTS = [
  {
    key: "recording_reviewer",
    name: "Recording Reviewer",
    layer: "Ingestion",
    detail:
      "Explains low-confidence stretches of screen recordings employees submit from the recorder.",
    trigger: "Runs when an employee submits a recording for review.",
  },
  {
    key: "file_reviewer",
    name: "File Reviewer",
    layer: "Ingestion",
    detail:
      "Reads this company's imported canonical records (or a synthetic division) and records evidence-linked observations.",
    trigger: "Review imported records.",
  },
  {
    key: "report_generator",
    name: "Pipeline & Report Generator",
    layer: "Reporting",
    detail:
      "Summarizes employee-agent findings into a company report with verified facts kept apart from hypotheses.",
    trigger: "Generate a company summary.",
  },
  {
    key: "sector_merger",
    name: "Sector Merger",
    layer: "Portfolio",
    detail:
      "Proposal-only Portfolio Analyst comparing sister companies in the same sector for consolidation opportunities.",
    trigger: "Analyze the company's sector.",
  },
  {
    key: "computer_use",
    name: "Computer Use Agent",
    layer: "Execution",
    detail:
      "Carries out an approved sandbox workflow one bounded step at a time — through an employee's recorder for browser and desktop steps — pausing before anything irreversible and verifying the result by reading it back.",
    trigger: "Starts only from Workflows → Run in sandbox.",
  },
];
const agentName = (key) =>
  AGENTS.find((a) => a.key === key)?.name ?? key ?? "Agent";
const KINDS = {
  observed_fact: "Observed fact",
  inefficiency: "Inefficiency",
  proposed_automation: "Proposed automation",
};
const cost = (value) =>
  `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
const stamp = (value) => (value ? new Date(value).toLocaleString() : "—");
const canEdit = () => role === "owner" || role === "member";
const company = () => companies.find((c) => c.id === $("company").value);
const isMeridian = () => /meridian risk partners/i.test(company()?.name ?? "");
let workflows = [];
function message(text = "") {
  $("message").textContent = text;
  $("message").hidden = !text;
}
function importError(text = "") {
  $("import-error").textContent = text;
  $("import-error").hidden = !text;
}
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Vista-Request": "1",
      ...options.headers,
    },
  });
  if (response.status === 401) {
    navigate("/signin/");
    throw new Error("Sign in to continue");
  }
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      /* non-JSON proxy response */
    }
    throw new Error(
      response.status === 404 && (!body?.detail || body.detail === "Not Found")
        ? "Data imports are not available yet. Ask your workspace administrator to finish enabling imports."
        : typeof body?.detail === "string"
          ? body.detail
          : response.status === 422
            ? "The import could not be accepted. Check the file format and required fields."
            : "The workspace could not complete this request. Please try again.",
    );
  }
  return response.status === 204 ? null : response.json();
}
function action(fn) {
  return async (e) => {
    try {
      message();
      await fn(e);
    } catch (error) {
      if (error.message !== "Sign in to continue") message(error.message);
    }
  };
}
const statusTag = (status) =>
  `<span class="tag ${["reviewed", "completed", "actioned", "resolved"].includes(status) ? "success" : ["open", "mapping_review", "validating", "ready_to_import"].includes(status) ? "warning" : status === "failed" ? "danger" : ""}">${esc(
    {
      open: "Needs review",
      reviewed: "Reviewed",
      actioned: "Actioned",
      dismissed: "Dismissed",
      resolved: "Decided",
      completed: "Imported",
      mapping_review: "Mapping needed",
      validating: "Exceptions to decide",
      ready_to_import: "Ready to approve",
      importing: "Importing",
      failed: "Failed",
      uploaded: "Uploaded",
    }[status] ?? status,
  )}</span>`;
const completedJobs = () => jobs.filter((j) => j.status === "completed");
const pendingJobs = () =>
  jobs.filter((j) => ["mapping_review", "validating", "ready_to_import"].includes(j.status));
const recordSets = () =>
  Object.entries(RECORD_SETS)
    .map(([key, label]) => ({ key, label, rows: records?.[key] ?? [] }))
    .filter((r) => r.rows.length);
const recordCount = () => recordSets().reduce((n, r) => n + r.rows.length, 0);
function render() {
  const count =
    openExceptions.length +
    agentFindings().filter((f) => f.status === "open").length;
  $("source-count").textContent = completedJobs().length;
  $("finding-count").textContent = count;
  $("run-count").textContent = agentRuns().length;
  $("report-count").textContent = companyReports().length;
  const latest = completedJobs().at(-1);
  $("as-of").textContent = latest
    ? `Last import ${stamp(latest.completedAt ?? latest.createdAt)}`
    : "";
  $("workflow-count").textContent = workflows.length;
  $("view-name").textContent = {
    overview: "Overview",
    sources: "Data sources",
    findings: "Findings",
    agents: "Agents",
    runs: "Runs",
    recordings: "Recordings",
    workflows: "Workflows",
  }[view];
  document.title = `Vista · ${$("view-name").textContent}`;
  $("import-top").disabled = !company() || !canEdit();
  document.querySelectorAll("[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  });
  if (!company()) {
    $("content").innerHTML =
      '<div class="empty"><h2>No company assigned.</h2><p>Ask your workspace administrator to add your company.</p></div>';
    return;
  }
  $("content").innerHTML =
    view === "sources"
      ? sourcesView()
      : view === "findings"
        ? findingsView()
        : view === "agents"
          ? agentsView()
          : view === "runs"
            ? runsView()
            : view === "recordings"
              ? recordingsView()
              : view === "workflows"
                ? workflowsView()
                : recordCount()
                  ? overviewView()
                  : welcomeView();
  bindContent();
}
const runTag = (status) =>
  `<span class="tag ${status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "running" || status === "queued" || status.startsWith("waiting") ? "warning" : ""}">${esc({ queued: "Queued", running: "Running", succeeded: "Succeeded", failed: "Failed", waiting: "Waiting", waiting_for_harness: "Waiting for recorder", waiting_for_human: "Needs your decision", stopped: "Stopped" }[status] ?? status)}</span>`;
const agentRuns = () =>
  (agents?.runs ?? []).filter(
    (r) => r.deal_id === company()?.id || r.deal_id === null,
  );
function agentFindings() {
  const visible = new Set(agentRuns().map((r) => r.id));
  return (agents?.findings ?? []).filter((f) => visible.has(f.run_id));
}
const lastRun = (key) => agentRuns().find((r) => r.agent_key === key) ?? null;
const syntheticCompany = () =>
  synthetic.find((c) => c.name === company()?.name) ?? null;
async function loadAgents(current) {
  const id = company().id;
  const since = new Date();
  since.setUTCDate(1);
  since.setUTCHours(0, 0, 0, 0);
  try {
    const [runs, findings, usage, companies] = await Promise.all([
      api("/runs?limit=100"),
      api("/findings?limit=200"),
      api(
        `/usage?deal_id=${id}&since=${since.toISOString()}&group_by=agent_key`,
      ),
      api("/synthetic/companies"),
    ]);
    if (current !== generation) return;
    agents = { runs, findings, usage };
    synthetic = companies;
  } catch {
    if (current === generation) agents = null;
  }
}
function agentOverviewPanel() {
  if (!agents) return "";
  const open = agentFindings().filter((f) => f.status === "open");
  const byKind = Object.keys(KINDS).map(
    (k) =>
      `<div class="metric"><span class="metric-label">${KINDS[k]}</span><strong>${number(open.filter((f) => f.kind === k).length)}</strong><span class="small">open</span></div>`,
  );
  return `<section class="panel section-gap"><div class="panel-heading"><h2>Agents on this company</h2><button data-go="agents" class="text-button">All agents ${icon("arrow")}</button></div><div class="table-wrap"><table><thead><tr><th>Agent</th><th>Last run</th><th>Status</th><th class="num">Open findings</th></tr></thead><tbody>${AGENTS.map(
    (a) => {
      const r = lastRun(a.key);
      return `<tr><td><strong>${esc(a.name)}</strong><small>${esc(a.layer)}</small></td><td>${r ? `<button data-run="${esc(r.id)}" class="text-button">${esc(stamp(r.created_at))}</button>` : '<span class="muted">Never</span>'}</td><td>${r ? runTag(r.status) : "—"}</td><td class="num">${number(open.filter((f) => f.agent_key === a.key).length)}</td></tr>`;
    },
  ).join(
    "",
  )}</tbody></table></div><div class="metrics spacing-3">${byKind.join("")}<div class="metric"><span class="metric-label">Model spend this month</span><strong>${cost(agents.usage.total_cost_usd)}</strong><span class="small">${number(agents.usage.total_input_tokens + agents.usage.total_output_tokens)} tokens · ${number(agents.usage.runs)} runs</span></div></div><p class="spacing-3 small">Findings are evidence-backed observations and proposals. Spend counts workspace model calls only; explanations made on an employee's own computer are not included.</p></section>`;
}
function agentsView() {
  const sc = syntheticCompany();
  const open = agentFindings().filter((f) => f.status === "open");
  const cards = AGENTS.map((a) => {
    const r = lastRun(a.key);
    const spend = agents?.usage.groups.find((g) => g.key.agent_key === a.key);
    let control = "";
    if (a.key === "file_reviewer")
      control = recordCount()
        ? `<button class="primary" data-review ${canEdit() ? "" : "disabled"}>${icon("play")}Review imported records</button>${sc ? `<label class="small">Synthetic division <select data-division="${a.key}">${sc.divisions.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("")}</select></label><button data-start="${a.key}" ${canEdit() ? "" : "disabled"}>Run discovery</button>` : ""}`
        : sc
          ? `<label class="small">Division <select data-division="${a.key}">${sc.divisions.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("")}</select></label><button class="primary" data-start="${a.key}" ${canEdit() ? "" : "disabled"}>${icon("play")}Run discovery</button>`
          : '<span class="small muted">Import company records first; the File Reviewer reads canonical rows.</span>';
    else if (a.key === "report_generator")
      control = `<button class="primary" data-start="${a.key}" ${canEdit() ? "" : "disabled"}>${icon("play")}Generate summary</button>`;
    else if (a.key === "sector_merger")
      control = sc
        ? `<button class="primary" data-start="${a.key}" ${canEdit() ? "" : "disabled"}>${icon("play")}Analyze ${esc(sc.sector.replace("_", " "))}</button>`
        : '<span class="small muted">Sector unknown for this company.</span>';
    else control = `<span class="small muted">${esc(a.trigger)}</span>`;
    return `<section class="panel"><div class="panel-heading"><h2>${esc(a.name)}</h2>${r ? runTag(r.status) : '<span class="tag">Never run</span>'}</div><span class="eyebrow">${esc(a.layer)}</span><p>${esc(a.detail)}</p><dl class="small"><dt>Last run</dt><dd>${r ? `<button data-run="${esc(r.id)}" class="text-button">${esc(stamp(r.started_at ?? r.created_at))}</button>` : "—"}</dd><dt>Open findings</dt><dd>${number(open.filter((f) => f.agent_key === a.key).length)}</dd><dt>Spend this month</dt><dd>${spend ? `${cost(spend.cost_usd)} · ${number(spend.runs)} runs` : "$0.00"}</dd></dl><div class="actions">${control}</div></section>`;
  });
  return `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Agents at <i>work.</i></h1><p>Five agents read this company's data, propose, report — and, for approved workflows, act in a sandbox. Every run leaves a trace you can inspect.</p></div></div>${agents ? "" : '<p class="quiet-note">Agent activity is unavailable right now.</p>'}<div class="overview-grid">${cards.join("")}</div><p class="spacing-4 small">Runs only start when you ask; agents never change source systems. The Computer Use Agent acts only in the sandbox, on an approved version, through an employee's recorder, and pauses before anything irreversible. Owners of this company can start runs.</p>`;
}
// Published recording reports for this company: what employees chose to share
// from the desktop recorder after the Recording Reviewer analysed the upload.
const companyReports = () =>
  reports.filter(
    (r) => r.workspace?.id === company()?.id || r.canonical_company_id === company()?.id,
  );
async function loadReports(current) {
  try {
    const rows = await api("/recorder/reports?limit=100");
    if (current === generation) reports = rows;
  } catch {
    if (current === generation) reports = [];
  }
}
async function loadWorkflows(current) {
  try {
    const rows = await api("/workflows?limit=100");
    if (current === generation) workflows = rows;
  } catch {
    if (current === generation) workflows = [];
  }
  await loadWorkflowExecution(current);
}
// ---- Computer Use Agent: eligibility and runs per approved version --------------
// Eligibility is the approval gate plus whether a harness is connected right now
// (an employee's recorder for browser/desktop tools). Runs are listed per workflow.
let workflowRuns = {},
  eligibility = {},
  runConfirm = null,
  runPoll = null;
const REASONS = {
  execution_permission_required: "Only the workspace owner can run a workflow.",
  version_superseded: "A newer version exists; decide on it first.",
  version_not_approved: "This version is not approved.",
  unsupported_environment: "Only sandbox workflows can run.",
  definition_hash_mismatch: "The approved definition no longer matches the stored version.",
  no_harness_for_tools: "Some allowed tools have no harness yet.",
  harness_not_connected:
    "No employee's recorder is connected — an employee must open the Vista Recorder, connect this company and keep it running.",
  connection_missing: "No HTTP connection is configured for this company.",
};
const eligibilityReason = (code) => REASONS[code] ?? code;
async function loadWorkflowExecution(current) {
  const approved = workflows.filter((w) => w.latest_version?.status === "approved");
  const results = await Promise.allSettled(
    approved.flatMap((w) => [
      api(`/workflows/${w.id}/versions/${w.latest_version.id}/eligibility`).then((e) => ["eligibility", w.latest_version.id, e]),
      api(`/workflows/${w.id}/runs?limit=20`).then((r) => ["runs", w.id, r]),
    ]),
  );
  if (current !== generation) return;
  const nextEligibility = {},
    nextRuns = {};
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    const [kind, id, value] = r.value;
    if (kind === "eligibility") nextEligibility[id] = value;
    else nextRuns[id] = value;
  }
  eligibility = nextEligibility;
  workflowRuns = nextRuns;
}
const RUN_TERMINAL = new Set(["succeeded", "failed", "stopped"]);
function workflowRunControls(w) {
  const v = w.latest_version;
  if (v.status !== "approved") return "";
  const e = eligibility[v.id];
  if (!e) return '<span class="small muted">Checking whether this version can run…</span>';
  if (role !== "owner") return '<span class="small muted">Only the workspace owner can run a workflow.</span>';
  const reasons = [...e.reasons, ...(e.availability?.reasons ?? [])];
  if (!e.execution_available)
    return `<span class="small muted">Cannot run now: ${esc(reasons.map(eligibilityReason).join(" "))}${e.availability?.unmapped_tools?.length ? ` (${esc(e.availability.unmapped_tools.join(", "))})` : ""}</span>`;
  const d = v.definition ?? {};
  if (runConfirm === v.id)
    return `<div class="run-confirm"><p class="small">Runs <b>v${v.number}</b> in the sandbox with at most ${esc(d.limits?.max_steps ?? "?")} steps, ${esc(d.limits?.max_runtime_seconds ?? "?")} s and $${esc(d.limits?.max_cost_usd ?? "?")}. Inputs: ${esc((d.required_inputs ?? []).join(", ") || "none")}. Browser and desktop steps run on the connected employee's computer only after they accept; anything irreversible waits for you here.</p><button class="primary" data-run-confirm="${esc(w.id)}" data-version="${esc(v.id)}">${icon("play")}Start run</button><button data-run-cancel="1">Cancel</button></div>`;
  return `<button class="primary" data-run-workflow="${esc(v.id)}">${icon("play")}Run in sandbox</button>`;
}
function workflowRunsHtml(w) {
  const runs = workflowRuns[w.id] ?? [];
  if (!runs.length) return "";
  return `<ul class="run-list small">${runs
    .slice(0, 5)
    .map(
      (r) =>
        `<li>${runTag(r.status)} v${esc(r.version_number)} · ${esc(r.mode)} · ${number(r.steps_used)}/${number(r.limits?.max_steps ?? 0)} steps · ${cost(r.cost_usd)} · ${esc(stamp(r.created_at))} <button class="text-button" data-workflow-run="${esc(r.id)}">Open ${icon("arrow")}</button></li>`,
    )
    .join("")}</ul>`;
}
async function startWorkflowRun(workflowId, versionId) {
  const run = await api(`/workflows/${workflowId}/versions/${versionId}/runs`, {
    method: "POST",
    body: JSON.stringify({ mode: "sandbox" }),
  });
  runConfirm = null;
  workflowRuns = { ...workflowRuns, [workflowId]: [run, ...(workflowRuns[workflowId] ?? [])] };
  render();
  await showWorkflowRun(run.id);
}
// One executed step from the ledger: `tool_call` events carry the harness, the primitive,
// the target label and the *name* of the input a value came from — never the value.
function stepListHtml(events) {
  const steps = events.filter((e) => e.event_type === "tool_call" && e.data?.harness);
  if (!steps.length) return "";
  return `<ol class="step-list">${steps
    .map((e) => {
      const d = e.data;
      return `<li><span class="tag ${d.ok === false ? "danger" : d.executed ? "success" : ""}">${esc(d.tool ?? "step")}</span> <b>${esc(d.harness)}</b>${d.target ? ` → ${esc(d.target)}` : ""}${d.value_input ? ` <span class="muted">(value from input <code>${esc(d.value_input)}</code>)</span>` : ""}${d.description ? `<br><span class="small">${esc(d.description)}</span>` : ""}${d.error ? `<br><span class="small muted">${esc(typeof d.error === "string" ? d.error : JSON.stringify(d.error))}</span>` : ""}</li>`;
    })
    .join("")}</ol>`;
}
function pauseCardHtml(run) {
  const p = run.pending ?? {};
  const cands = p.candidates ?? [];
  return `<section class="pause-card"><h3>The agent wants to: ${esc(p.description ?? p.action ?? "act")}</h3><p class="small">${esc(p.harness ?? "")} · <b>${esc(p.action ?? "")}</b>${p.target ? ` on ${esc(p.target.role)} “${esc(p.target.label)}”` : ""}${p.value_from ? ` · value from input <code>${esc(p.value_from)}</code>` : ""} · irreversible ${Math.round((p.risk?.irreversible ?? 0) * 100)}% · ${esc({ irreversible: "paused before an irreversible action", no_action: "no safe next step could be chosen", ambiguous_target: "the target was ambiguous", missing_value: "a value was missing", dry_run: "dry run stopped before the first write", submit: "submit always waits for you" }[p.reason] ?? p.reason ?? "")}</p>${
    cands.length
      ? `<details open><summary class="small">What the agent could see (${cands.length})</summary><ul class="candidates small">${cands.map((c) => `<li class="${c.id === p.chosen ? "chosen" : ""}">${esc(c.label)}${c.p != null ? ` <span class="muted">${Math.round(c.p * 100)}%</span>` : ""}${c.id === p.chosen ? " ← chosen" : ""}</li>`).join("")}</ul></details>`
      : ""
  }<p class="small muted">${run.harness?.screenshots ? `<a href="/api/workflow-runs/${esc(run.id)}/steps/${esc(p.step_id)}/screenshot" target="_blank" rel="noopener">Screenshot evidence</a>` : "Screenshots stay on the employee's computer."}</p>${
    role === "owner"
      ? `<div class="actions"><button class="primary" data-decide-step="approve" data-run-id="${esc(run.id)}" data-step="${esc(p.step_id)}">${icon("check")}Approve step</button><button data-decide-step="deny" data-run-id="${esc(run.id)}" data-step="${esc(p.step_id)}">Deny</button><button data-stop-run="${esc(run.id)}">Stop run</button></div>`
      : '<p class="small muted">Only the workspace owner can decide.</p>'
  }</section>`;
}
function harnessWaitHtml(run) {
  const p = run.pending ?? {};
  if (p.kind === "offer")
    return `<section class="pause-card"><h3>Waiting for an employee's recorder</h3><p class="small">This run needs ${esc((p.harness_kinds ?? []).join(" and ") || "a harness")} on an employee's computer. ${p.harness?.connected ? "A recorder is connected; the employee must press Start and accept." : "The employee must open the Vista Recorder, go to Computer use and accept the offer."}</p>${role === "owner" ? `<div class="actions"><button data-stop-run="${esc(run.id)}">Stop run</button></div>` : ""}</section>`;
  return `<section class="pause-card"><h3>Running on the employee's computer</h3><p class="small">Step ${esc(p.seq ?? "?")}: ${esc(p.description ?? p.action ?? "")} (${esc(p.harness ?? "")})${run.harness?.connected === false ? " · the recorder is no longer connected" : ""}</p>${role === "owner" ? `<div class="actions"><button data-stop-run="${esc(run.id)}">Stop run</button></div>` : ""}</section>`;
}
function outcomeHtml(run) {
  const o = run.outcome ?? {};
  if (run.status === "stopped") return `<section class="pause-card"><h3>Stopped</h3><p class="small">${esc(run.error ?? "The run was stopped.")}</p></section>`;
  if (run.status === "failed" && !o.criteria?.length) return `<section class="pause-card"><h3>Failed</h3><p class="small">${esc(run.error ?? o.reason ?? "The run did not finish.")}</p></section>`;
  return `<section class="pause-card"><h3>${o.verified ? "Verified" : "Not verified"}: ${number(o.matched ?? 0)} of ${number(o.checked ?? 0)} criteria met</h3><p class="small">Independent read-back of the final state, goal met ${Math.round((o.p_goal ?? 0) * 100)}%.</p>${(o.criteria ?? []).length ? `<ul class="small">${o.criteria.map((c) => `<li>${c.met ? "✓" : "✗"} ${esc(c.criterion ?? c.text ?? "")}${c.p != null ? ` <span class="muted">${Math.round(c.p * 100)}%</span>` : ""}</li>`).join("")}</ul>` : ""}${run.error ? `<p class="small muted">${esc(run.error)}</p>` : ""}</section>`;
}
function workflowRunHtml(run, trace) {
  const head = `<p class="small"><b>${esc(run.workflow_name)}</b> v${esc(run.version_number)} · ${esc(run.mode)} · ${runTag(run.status)}<br>${number(run.steps_used)} of ${number(run.limits?.max_steps ?? 0)} steps · ${cost(run.cost_usd)} of $${esc(run.limits?.max_cost_usd ?? "?")} · started ${esc(stamp(run.started_at ?? run.created_at))}${run.finished_at ? ` · finished ${esc(stamp(run.finished_at))}` : ""}</p>`;
  const body =
    run.status === "waiting_for_human"
      ? pauseCardHtml(run)
      : run.status === "waiting_for_harness"
        ? harnessWaitHtml(run)
        : RUN_TERMINAL.has(run.status)
          ? outcomeHtml(run)
          : role === "owner"
            ? `<div class="actions"><button data-stop-run="${esc(run.id)}">Stop run</button></div>`
            : "";
  const steps = stepListHtml(trace?.events ?? []);
  return `${head}${body}${steps ? `<h3>Steps</h3>${steps}` : ""}${trace ? `<details><summary class="small">Full run trace</summary>${traceHtml(trace, { steps: false })}</details>` : ""}`;
}
async function showWorkflowRun(id) {
  clearInterval(runPoll);
  $("run-body").innerHTML = '<p class="muted">Loading run…</p>';
  $("run-dialog").showModal();
  const refresh = async () => {
    const run = await api(`/workflow-runs/${id}`);
    let trace = null;
    try {
      trace = await api(`/runs/${run.agent_run_id}`);
    } catch {
      /* trace is optional */
    }
    if (!$("run-dialog").open) return clearInterval(runPoll);
    $("run-body").innerHTML = workflowRunHtml(run, trace);
    bindRunDialog();
    if (RUN_TERMINAL.has(run.status)) {
      clearInterval(runPoll);
      workflowRuns = { ...workflowRuns, [run.workflow_id]: (workflowRuns[run.workflow_id] ?? []).map((r) => (r.id === run.id ? run : r)) };
      if (view === "workflows") render();
    }
  };
  await refresh();
  runPoll = setInterval(() => refresh().catch(() => clearInterval(runPoll)), 3000);
}
function bindRunDialog() {
  document.querySelectorAll("#run-body [data-decide-step]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        await api(`/workflow-runs/${b.dataset.runId}/decision`, {
          method: "POST",
          body: JSON.stringify({ step_id: b.dataset.step, decision: b.dataset.decideStep, reason: "Decided from the company workspace" }),
        });
        await showWorkflowRun(b.dataset.runId);
      })),
  );
  document.querySelectorAll("#run-body [data-stop-run]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        await api(`/workflow-runs/${b.dataset.stopRun}/stop`, { method: "POST", body: JSON.stringify({ reason: "Stopped from the company workspace" }) });
        await showWorkflowRun(b.dataset.stopRun);
      })),
  );
}
const span = (session) =>
  session?.started_at
    ? `${new Date(session.started_at).toLocaleString()} – ${session.ended_at ? new Date(session.ended_at).toLocaleTimeString() : "…"}`
    : "—";
function recordingsView() {
  const rows = companyReports();
  return `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Recordings, <i>as shared.</i></h1><p>Employees upload activity metadata from the desktop recorder, review the Recording Reviewer's draft, answer its questions and publish. Only published reports appear here.</p></div></div><section class="panel">${
    rows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Session</th><th>Apps</th><th class="num">Switches</th><th class="num">Workflows</th><th class="num">Questions answered</th><th>Published</th><th></th></tr></thead><tbody>${rows
          .map(
            (r) =>
              `<tr><td><strong>${esc(span(r.session))}</strong><small>${esc(r.summary ? r.summary.slice(0, 120) : "No model interpretation")}</small></td><td>${esc((r.apps ?? []).join(" · "))}</td><td class="num">${number(r.switches ?? 0)}</td><td class="num">${number(r.workflows ?? 0)}</td><td class="num">${number((r.questions_total ?? 0) - (r.questions_open ?? 0))} / ${number(r.questions_total ?? 0)}</td><td>${esc(stamp(r.published_at))}</td><td><button data-report="${esc(r.id)}" class="text-button">Open ${icon("arrow")}</button></td></tr>`,
          )
          .join("")}</tbody></table></div>`
      : '<div class="empty"><h2>No published recordings yet.</h2><p>Reports appear once an employee uploads a session from the Vista recorder and publishes the reviewed draft.</p></div>'
  }</section><p class="spacing-4 small">Observed facts are computed from metadata (app names, timing, copy and paste). Window titles, URLs, typed text and screenshots never leave the employee's computer. The agent's reading is a hypothesis until someone verifies it.</p>`;
}
function reportHtml(r) {
  const o = r.observed ?? {};
  const i = r.interpretation ?? {};
  const apps = (o.apps ?? []).slice(0, 10);
  const li = (items, f) =>
    items.length ? `<ul class="small">${items.map(f).join("")}</ul>` : '<p class="small muted">None.</p>';
  return `<p class="small"><strong>${esc(span(o.session))}</strong> · ${number(o.events ?? 0)} interactions · ${number(o.switches ?? 0)} app switches · published ${esc(stamp(r.published_at))}</p><p class="quiet-note">${esc(r.coverage?.note ?? "")} Not observed: ${esc((r.coverage?.excluded ?? []).join(", "))}.</p><h3>Observed</h3><div class="table-wrap"><table><thead><tr><th>Application</th><th class="num">Share of active time</th><th class="num">Events</th><th class="num">Copies</th><th class="num">Pastes</th></tr></thead><tbody>${apps
    .map(
      (a) =>
        `<tr><td>${esc(a.app)}${appDetail(a)}</td><td class="num">${Math.round((a.share ?? 0) * 100)}%</td><td class="num">${number(a.events)}</td><td class="num">${number(a.copies)}</td><td class="num">${number(a.pastes)}</td></tr>`,
    )
    .join("")}</tbody></table></div>${li(o.transfers ?? [], (t) => `<li>Copied from <strong>${esc(t.from)}</strong> into <strong>${esc(t.to)}</strong> ${number(t.count)}× (about ${number(t.mean_latency_s)}s apart)${(t.samples ?? []).length ? `<br /><span class="muted">moved: ${t.samples.map((s) => `“${esc(s)}”`).join(", ")}</span>` : ""}</li>`)}<h3>Agent's reading <span class="tag">${esc(i.source === "stub" ? "no model" : "hypothesis")}</span></h3>${i.summary ? `<p>${esc(i.summary)}</p>` : '<p class="small muted">No model interpretation was produced.</p>'}${li(i.workflows ?? [], workflowItem)}${(i.automation_candidates ?? []).length ? `<h4>Automation candidates</h4>${li(i.automation_candidates, (c) => `<li><strong>${esc(c.title)}</strong>${c.rationale ? ` — ${esc(c.rationale)}` : ""}</li>`)}` : ""}${(i.documents ?? []).length ? `<h4>Shared documents</h4>${li(i.documents, (d) => `<li>${esc(d.filename)} <span class="muted">${esc(d.summary?.kind ?? "")}${d.summary?.rows != null ? ` · ${number(d.summary.rows)} rows` : ""}</span></li>`)}` : ""}<h3>Employee's answers</h3>${li(r.questions ?? [], (q) => `<li><em>${esc(q.question)}</em><br />${q.answer ? esc(q.answer) : '<span class="muted">Not answered</span>'}</li>`)}`;
}
// Under an application's name: what was on screen there (titles, pages, files) and samples
// of what was typed — present only when the upload carried detail (activity-full-v1).
function appDetail(a) {
  const rows = [
    ["On screen", [...(a.titles ?? []), ...(a.files ?? [])]],
    ["Pages", a.pages ?? []],
    ["Typed", (a.typed ?? []).map((t) => `“${t}”`)],
  ].filter(([, items]) => items.length);
  if (!rows.length) return "";
  return `<br /><span class="muted small">${rows.map(([label, items]) => `${esc(label)}: ${items.slice(0, 5).map(esc).join(" · ")}`).join("<br />")}</span>`;
}
// What to do about one judged workflow: the employee question it raised, the numbered
// steps, and — only when a prefilled draft exists and the viewer may act — a Draft button.
// The steps and the draft are templated by the backend from the facts; nothing is generated here.
function actionsHtml(actions, key) {
  if (!actions?.instructions?.length) return "";
  const steps = actions.instructions.map((s) => `<li>${esc(s)}</li>`).join("");
  const asked = actions.question ? `<p class="small">Asked the employee: <em>${esc(actions.question)}</em></p>` : "";
  const draft =
    actions.draft_definition && canEdit()
      ? `<p class="actions"><button class="primary" data-draft="${esc(key)}">${icon("workflow")}Draft workflow</button><span class="small muted">sandbox · draft · needs owner approval</span></p>`
      : "";
  return `<details class="what-to-do"><summary>What to do</summary>${asked}<ol class="small">${steps}</ol>${draft}<p class="small draft-result" data-draft-result="${esc(key)}" hidden></p></details>`;
}
function workflowItem(w) {
  return `<li><strong>${esc(w.name)}</strong> — ${esc(w.apps.join(", "))}${w.evidence ? ` · ${esc(w.evidence)}` : ""} · ${Math.round((w.confidence ?? 0) * 100)}%${actionsHtml(w.actions, w.candidate ?? "")}</li>`;
}
// Wire every Draft workflow button under `root`: POST the prefilled definition to the
// workspace's own workflows API, report the result in place, and refresh the Workflows nav.
function bindDrafts(root, lookup, after) {
  root.querySelectorAll("[data-draft]").forEach(
    (b) =>
      (b.onclick = async () => {
        const draft = lookup(b.dataset.draft);
        if (!draft) return;
        const out = root.querySelector(`[data-draft-result="${b.dataset.draft}"]`);
        b.disabled = true;
        try {
          const created = await api("/workflows", {
            method: "POST",
            body: JSON.stringify({ name: draft.name.slice(0, 255), definition: draft.definition }),
          });
          workflows = [created, ...workflows.filter((w) => w.id !== created.id)];
          $("workflow-count").textContent = workflows.length;
          if (out) {
            out.hidden = false;
            out.textContent = `Draft v${created.latest_version.number} created — awaiting owner approval under Workflows.`;
          }
          if (after) await after(created);
        } catch (e) {
          b.disabled = false;
          if (out) {
            out.hidden = false;
            out.textContent = e.message;
          }
        }
      }),
  );
}
async function showReport(id) {
  $("report-body").innerHTML = '<p class="muted">Loading report…</p>';
  $("report-dialog").showModal();
  const r = await api(`/recorder/reports/${id}`);
  $("report-body").innerHTML = reportHtml(r);
  bindDrafts($("report-body"), (key) => {
    const w = (r.interpretation?.workflows ?? []).find((x) => x.candidate === key);
    return w?.actions?.draft_definition ? { name: w.name, definition: w.actions.draft_definition } : null;
  });
}
function runsView() {
  const all = agentRuns();
  const focused = recordingFocus
    ? all.filter((r) => r.recording_id === recordingFocus)
    : [];
  const runs = focused.length ? focused : all;
  return `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Every run, <i>on record.</i></h1><p>What each agent did, when, and what it cost. Open a run for its step-by-step trace.</p></div></div>${focused.length ? '<p class="quiet-note">Showing the Recording Reviewer runs for one submitted recording.</p>' : ""}<section class="panel">${
    runs.length
      ? `<div class="table-wrap"><table><thead><tr><th>Started</th><th>Agent</th><th>Scope</th><th>Status</th><th class="num">Findings</th><th></th></tr></thead><tbody>${runs
          .map(
            (r) =>
              `<tr><td>${esc(stamp(r.started_at ?? r.created_at))}<small>${esc(r.run_type)}</small></td><td>${esc(agentName(r.agent_key))}</td><td>${esc([r.company, r.division, r.sector].filter(Boolean).join(" · ") || (r.deal_id ? company().name : "Portfolio"))}</td><td>${runTag(r.status)}${r.error ? `<small class="muted">${esc(r.error)}</small>` : ""}</td><td class="num">${number(agentFindings().filter((f) => f.run_id === r.id).length)}</td><td><button data-run="${esc(r.id)}">Trace</button></td></tr>`,
          )
          .join("")}</tbody></table></div>`
      : `<div class="empty"><h2>No agent runs yet.</h2><p>Start one from the Agents view.</p><button data-go="agents">Agents</button></div>`
  }</section>`;
}
function agentFindingRows(findings) {
  if (!findings.length)
    return '<p class="muted spacing-3">No agent findings match these filters.</p>';
  return findings
    .map(
      (f) =>
        `<article class="finding-row"><div><span class="eyebrow">${esc(KINDS[f.kind] ?? f.kind)}</span><h3>${esc(f.title)}</h3><p>${esc(f.detail)}</p><p class="spacing-2">${esc(agentName(f.agent_key))}${f.company ? ` · ${esc(f.company)}` : ""} · ${esc(stamp(f.created_at))}</p></div><section>${statusTag(f.status)}<button data-agent-finding="${esc(f.id)}">Review evidence ${icon("arrow")}</button></section></article>`,
    )
    .join("");
}
function agentFindingsBlock() {
  if (!agents) return "";
  const all = agentFindings();
  const chip = (group, value, label, count) =>
    `<button data-afilter="${group}" data-value="${esc(value)}" aria-pressed="${agentFilter[group] === value}" class="${agentFilter[group] === value ? "selected" : ""}">${esc(label)} · ${count}</button>`;
  const rows = all.filter(
    (f) =>
      (agentFilter.kind === "all" || f.kind === agentFilter.kind) &&
      (agentFilter.status === "all" || f.status === agentFilter.status) &&
      (agentFilter.agent === "all" || f.agent_key === agentFilter.agent),
  );
  return `<section class="panel section-gap"><div class="panel-heading"><h2>Agent findings</h2><span class="small">${number(all.length)} total</span></div><div class="filterbar" role="group" aria-label="Finding kind">${chip("kind", "all", "All kinds", all.length)}${Object.entries(
    KINDS,
  )
    .map(([k, l]) => chip("kind", k, l, all.filter((f) => f.kind === k).length))
    .join(
      "",
    )}</div><div class="filterbar" role="group" aria-label="Agent finding status">${[
    "open",
    "reviewed",
    "actioned",
    "dismissed",
    "all",
  ]
    .map((st) =>
      chip(
        "status",
        st,
        {
          open: "Needs review",
          reviewed: "Reviewed",
          actioned: "Actioned",
          dismissed: "Dismissed",
          all: "Any status",
        }[st],
        all.filter((f) => st === "all" || f.status === st).length,
      ),
    )
    .join(
      "",
    )}</div><div class="filterbar" role="group" aria-label="Source agent">${chip("agent", "all", "All agents", all.length)}${AGENTS.map((a) => chip("agent", a.key, a.name, all.filter((f) => f.agent_key === a.key).length)).join("")}</div>${agentFindingRows(rows)}</section>`;
}
function traceHtml(run, { steps = true } = {}) {
  const events = run.events ?? [];
  return `${steps && run.run_type === "workflow_execution" ? stepListHtml(events) : ""}<p class="small">${esc(agentName(run.agent_key))} · ${esc(run.run_type)} · ${runTag(run.status)}<br>Started ${esc(stamp(run.started_at ?? run.created_at))} · Finished ${esc(stamp(run.finished_at))}${run.error ? `<br><span class="muted">${esc(run.error)}</span>` : ""}</p>${
    events.length
      ? `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Event</th><th>Detail</th><th>At</th></tr></thead><tbody>${events
          .map(
            (e) =>
              `<tr><td class="num">${e.seq}</td><td><strong>${esc(e.event_type)}</strong></td><td><code class="small">${esc(JSON.stringify(e.data))}</code></td><td>${esc(stamp(e.created_at))}</td></tr>`,
          )
          .join("")}</tbody></table></div>`
      : '<p class="muted">No events recorded for this run.</p>'
  }`;
}
async function showRun(id) {
  $("run-body").innerHTML = '<p class="muted">Loading run trace…</p>';
  $("run-dialog").showModal();
  const run = await api(`/runs/${id}`);
  $("run-body").innerHTML = traceHtml(run);
}
async function showAgentFinding(id) {
  const f = agentFindings().find((f) => f.id === id);
  const ev = f.evidence ?? {};
  const rest = Object.fromEntries(
    Object.entries(ev).filter(([k]) => k !== "refs" && k !== "company"),
  );
  const refs = Array.isArray(ev.refs) ? ev.refs : [];
  $("evidence-body").innerHTML =
    `<span class="tag">${esc(KINDS[f.kind] ?? f.kind)}</span> ${statusTag(f.status)}<h2 id="evidence-title">${esc(f.title)}</h2><p class="evidence-detail">${esc(f.detail)}</p><p class="small">Found by ${esc(agentName(f.agent_key))}${f.company ? ` on ${esc(f.company)}` : ""} · ${esc(stamp(f.created_at))}</p><div class="filterbar" role="tablist"><button data-tab="evidence" role="tab" aria-selected="true" class="selected">Evidence</button><button data-tab="trace" role="tab" aria-selected="false">Run trace</button></div><div id="tab-evidence"><h3>Follow the evidence</h3>${
      refs.length
        ? `<ul>${refs.map((r) => `<li><code>${esc(typeof r === "string" ? r : JSON.stringify(r))}</code></li>`).join("")}</ul>`
        : '<p class="muted">No source references were recorded.</p>'
    }${
      Object.keys(rest).length
        ? `<details><summary>All evidence fields</summary><dl>${Object.entries(
            rest,
          )
            .map(
              ([k, v]) =>
                `<dt>${esc(k)}</dt><dd>${esc(typeof v === "string" ? v : JSON.stringify(v))}</dd></dl>`,
            )
            .join("")}</dl></details>`
        : ""
    }${ev.verification ? `<h3>Verification</h3><p class="small">${ev.verification.verified ? "Verified" : "Not verified"}: ${number(ev.verification.matched ?? 0)} of ${number(ev.verification.checked ?? 0)} criteria met (goal met ${Math.round((ev.verification.p_goal ?? 0) * 100)}%).</p>` : ""}${actionsHtml(ev.actions, f.id)}</div><div id="tab-trace" hidden><p class="muted">Loading run trace…</p></div>${
      canEdit()
        ? `<div class="actions section-gap">${[
            "reviewed",
            "actioned",
            "dismissed",
            "open",
          ]
            .filter((st) => st !== f.status)
            .map(
              (st) =>
                `<button data-set-status="${st}">${{ reviewed: "Mark reviewed", actioned: "Mark actioned", dismissed: "Dismiss", open: "Reopen" }[st]}</button>`,
            )
            .join("")}</div>`
        : ""
    }`;
  $("evidence-dialog").showModal();
  let trace = null;
  document.querySelectorAll("#evidence-body [data-tab]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        document.querySelectorAll("#evidence-body [data-tab]").forEach((x) => {
          const on = x === b;
          x.classList.toggle("selected", on);
          x.setAttribute("aria-selected", on);
        });
        $("tab-evidence").hidden = b.dataset.tab !== "evidence";
        $("tab-trace").hidden = b.dataset.tab !== "trace";
        if (b.dataset.tab === "trace" && !trace) {
          trace = await api(`/runs/${f.run_id}`);
          $("tab-trace").innerHTML = traceHtml(trace);
        }
      })),
  );
  document.querySelectorAll("#evidence-body [data-set-status]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const updated = await api(`/findings/${f.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: b.dataset.setStatus }),
        });
        Object.assign(f, updated);
        $("evidence-dialog").close();
        render();
      })),
  );
  bindDrafts(
    $("evidence-body"),
    () => (ev.actions?.draft_definition ? { name: f.title, definition: ev.actions.draft_definition } : null),
    async () => {
      if (f.status === "open" || f.status === "reviewed") {
        const updated = await api(`/findings/${f.id}`, { method: "PATCH", body: JSON.stringify({ status: "actioned" }) });
        Object.assign(f, updated);
      }
    },
  );
}
async function startReview() {
  const queued = await api(`/deals/${company().id}/review`, { method: "POST", body: "{}" });
  if (agents)
    agents.runs.unshift({
      id: queued.run_id,
      run_type: "canonical_review",
      agent_key: "file_reviewer",
      status: "queued",
      deal_id: company().id,
      created_at: new Date().toISOString(),
    });
  view = "runs";
  render();
  message("File Reviewer queued over the imported records. Findings appear under Findings when the run completes.");
}
async function startRun(key) {
  const sc = syntheticCompany();
  let run;
  if (key === "file_reviewer")
    run = await api("/synthetic/discovery", {
      method: "POST",
      body: JSON.stringify({
        company: sc.slug,
        division: document.querySelector(`[data-division="${key}"]`).value,
      }),
    });
  else if (key === "sector_merger")
    run = await api("/synthetic/analyze", {
      method: "POST",
      body: JSON.stringify({ sector: sc.sector }),
    });
  else if (key === "report_generator")
    run = await api("/summaries", { method: "POST", body: "{}" });
  if (run && agents) agents.runs.unshift(run);
  view = "runs";
  render();
  message(
    `${agentName(key)} queued. Refresh the Runs view to follow progress.`,
  );
}
function welcomeView() {
  const pending = pendingJobs();
  return `<div class="intro"><div><span class="eyebrow">${esc(company().name)}</span><h1>A clearer picture.<br>From the data<br>you already <i>have.</i></h1><p class="lede">Turn scattered exports into a shared view of the business. Bring in your records, confirm how they fit, and let the File Reviewer show what deserves attention.</p><div class="actions"><button class="primary" data-import ${!canEdit() ? "disabled" : ""}>${icon("upload")}Import company data</button>${isMeridian() && canEdit() ? '<button class="text-button" data-sample>Try Meridian sample data</button>' : ""}</div><p class="spacing-1 small">CSV and Excel workbooks · Up to 25 MB per file</p></div><div class="source-stack"><header><span class="eyebrow">Your first import</span><span class="tag">${isMeridian() ? "Synthetic demo available" : "Files you already use"}</span></header>${[
    ["Customers", "Client or account lists"],
    ["Invoices & receivables", "Invoices, due dates and balances"],
    ["Vendors, purchases & software", "Purchase lines and subscriptions"],
  ]
    .map(
      ([name, desc]) =>
        `<div class="sample-row"><span class="file-icon">${icon("file")}</span><div><strong>${name}</strong><small>${desc}</small></div><span class="tag">CSV / XLSX</span></div>`,
    )
    .join(
      "",
    )}<div class="stack-footer">${icon("shield")}Every record keeps its source file, row and original values.</div></div></div>${pending.length ? `<div class="quiet-note">${icon("clock")}<div><strong>${pending.length === 1 ? "An import is waiting" : `${pending.length} imports are waiting`} for your review.</strong><br>${pending.map((j) => esc(j.filename)).join(", ")}</div><button data-resume="${esc(pending[0].id)}" ${!canEdit() ? "disabled" : ""}>Resume import</button></div>` : ""}<div class="explain-grid"><article><span class="number">01 / INGEST</span><h3>Start with your exports.</h3><p>Upload the records from your systems and spreadsheets. Originals are retained with the import.</p></article><article><span class="number">02 / CONFIRM</span><h3>Make the connections clear.</h3><p>Confirm detected record types and field mappings before anything becomes a company record.</p></article><article><span class="number">03 / REVIEW</span><h3>See what needs attention.</h3><p>The File Reviewer reads the imported records and cites the rows behind every observation.</p></article></div><div class="quiet-note">${icon("scan")}<div><strong>Built for a defensible first look.</strong><br>Findings are review candidates with evidence; recovered revenue and realized savings require confirmation.</div></div>`;
}
function metrics() {
  const open = agentFindings().filter((f) => f.status === "open").length;
  return `<div class="metrics">${[
    ["Records imported", number(recordCount()), `${completedJobs().length} source files`],
    ["Datasets", number(recordSets().length), recordSets().map((r) => r.label).slice(0, 3).join(" · ") || "—"],
    ["Import exceptions", number(openExceptions.length), "Records awaiting a decision"],
    ["Findings to review", number(open), "Evidence-linked observations"],
  ]
    .map(
      ([label, value, note], i) =>
        `<div class="metric ${i === 3 ? "emphasis" : ""}"><span class="eyebrow">${label}</span><b>${value}</b><small>${esc(note)}</small></div>`,
    )
    .join("")}</div>`;
}
function datasetRows() {
  return `<div class="table-wrap"><table><thead><tr><th>Dataset</th><th class="num">Records</th><th>Source files</th><th></th></tr></thead><tbody>${recordSets()
    .map((r) => {
      const sources = [...new Set(r.rows.map((x) => x.provenance?.file).filter(Boolean))];
      return `<tr><td>${icon("file")} ${esc(r.label)}</td><td class="num">${number(r.rows.length)}</td><td>${esc(sources.join(", ") || "—")}</td><td><button data-source="${esc(r.key)}">Browse records</button></td></tr>`;
    })
    .join("")}</tbody></table></div>`;
}
function overviewView() {
  const open = agentFindings().filter((f) => f.status === "open");
  const latest = completedJobs().at(-1);
  return `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Your business, <i>in view.</i></h1><p>${number(recordCount())} imported records across ${number(recordSets().length)} datasets. ${open.length ? `${number(open.length)} observations worth a closer look.` : "No open observations yet — run the File Reviewer over the imported records."} Every conclusion starts with a source.</p></div><span class="tag success">${icon("check")}Import complete</span></div>${metrics()}<div class="overview-grid"><section class="panel"><div class="panel-heading"><h2>Where to focus</h2><button data-go="findings" class="text-button">All findings ${icon("arrow")}</button></div>${open.length ? agentFindingRows(open.slice(0, 3)) : `<div class="empty"><h3>No open findings.</h3><p>The File Reviewer reads the imported records and cites the rows behind each observation.</p>${canEdit() ? `<button class="primary" data-review>${icon("play")}Review imported records</button>` : ""}</div>`}</section><section class="panel"><div class="panel-heading"><h2>What was imported</h2><button data-go="sources" class="text-button">Data sources ${icon("arrow")}</button></div>${datasetRows()}<p class="spacing-3 small">Records were normalised deterministically at import; original values stay on every row. No automatic changes are made to your source systems.</p></section></div><div class="import-note"><span>Last import ${esc(stamp(latest?.completedAt ?? latest?.createdAt))} · ${completedJobs().length} files · ${openExceptions.length ? `${number(openExceptions.length)} exceptions still open.` : "No open exceptions."}</span>${canEdit() ? `<button data-review class="text-button">Run the File Reviewer ${icon("arrow")}</button>` : ""}</div>${agentOverviewPanel()}`;
}
function sourcesView() {
  return `<div class="page-heading"><div><span class="eyebrow">The evidence library</span><h1>Data with a <i>paper trail.</i></h1><p>Canonical records with the source file, row and original values behind each one, and every import that produced them.</p></div></div>${
    recordCount()
      ? `<section class="panel"><div class="panel-heading"><h2>Company records</h2><span class="small">${number(recordCount())} records</span></div>${datasetRows()}</section>`
      : '<div class="empty"><h2>Your source library starts here.</h2><p>Import your company exports to create canonical records.</p><button data-import>Import data</button></div>'
  }<section class="panel section-gap"><div class="panel-heading"><h2>Import history</h2><span class="small">${number(jobs.length)} files</span></div>${
    jobs.length
      ? `<div class="table-wrap"><table><thead><tr><th>File</th><th>Dataset</th><th class="num">Rows</th><th class="num">Imported</th><th>Status</th><th class="num">Open exceptions</th><th></th></tr></thead><tbody>${[...jobs]
          .reverse()
          .map(
            (j) =>
              `<tr><td>${icon("file")} ${esc(j.filename)}${j.sheet ? `<small>${esc(j.sheet)}</small>` : ""}<small>${esc(stamp(j.createdAt))}</small></td><td>${esc(datasets[j.dataset]?.label ?? j.dataset)}</td><td class="num">${number(j.recordsDetected ?? 0)}</td><td class="num">${number(j.recordsImported ?? 0)}</td><td>${statusTag(j.status)}${j.error ? `<small class="muted">${esc(j.error)}</small>` : ""}</td><td class="num">${number((j.exceptions ?? []).filter((x) => x.open).length)}</td><td>${["mapping_review", "validating", "ready_to_import"].includes(j.status) && canEdit() ? `<button data-resume="${esc(j.id)}">Resume</button>` : ""}</td></tr>`,
          )
          .join("")}</tbody></table></div>`
      : '<p class="muted">No files imported yet.</p>'
  }</section>`;
}
function exceptionRows(list) {
  if (!list.length)
    return '<p class="muted spacing-3">No import exceptions need a decision.</p>';
  return list
    .map(
      (x) =>
        `<article class="finding-row"><div><span class="eyebrow">${esc(x.type)} · ${esc(datasets[x.dataset]?.label ?? x.dataset ?? "")}</span><h3>${esc(x.left)}${x.right ? ` <span class="muted">vs</span> ${esc(x.right)}` : ""}</h3><p>${esc(x.description)}</p><p class="spacing-2">${x.leftRow ? `Source row ${esc(x.leftRow)}` : "This import"}${x.rightRow ? ` · compared with row ${esc(x.rightRow)}` : ""}${x.confidence != null ? ` · ${Math.round(x.confidence * 100)}% similar` : ""}</p></div><section>${x.open ? statusTag("open") : `${statusTag("resolved")}<span class="small">${esc(x.decision)}</span>`}${
          x.open && canEdit()
            ? x.actions.map((a) => `<button data-exception="${esc(x.importJobId)}:${esc(x.id)}" data-decide="${esc(a)}">${esc(a)}</button>`).join("")
            : ""
        }</section></article>`,
    )
    .join("");
}
const versionTag = (status) =>
  `<span class="tag ${status === "approved" ? "success" : status === "rejected" ? "danger" : "warning"}">${esc(status)}</span>`;
// ---- Task graph per version: what the recordings compiled, what runs added, what an admin may promote ----
// Loaded on demand from GET /workflows/{w}/versions/{v}/graph. The approved structure is immutable;
// runs only add statistics, code proposes promotions from those statistics, and only a person turns
// them into the next draft version (POST …/graph/draft), which then needs approval like any other.
let graphReviews = {};
const nodeLabel = (n) => `${n.app_role} · ${n.activity}${n.signature?.length ? ` · ${n.signature.join(" ")}` : ""}`;
const edgeLabel = (e) => `${e.action_class}${e.control ? ` ${e.control}` : ""}${e.slot ? ` ← ${e.slot}` : ""}`;
const policyTag = (p) => `<span class="tag ${p === "auto" ? "success" : p === "always_ask" ? "danger" : ""}">${esc(p)}</span>`;
const statsText = (s) =>
  `${number(s.support)} seen · ${number(s.executed)} run · ${number(s.verified_ok)} verified · ${number(s.approved)} approved · ${number(s.denied)} denied · ${number(s.effect_missing)} effect missing`;
const provenanceText = (p) => p.map((x) => `${x.source} ${x.id}${x.event_ids?.length ? ` (${number(x.event_ids.length)} events)` : ""}`).join(", ");
function graphReviewHtml(workflowId, review) {
  const g = review.graph;
  if (!g) return '<p class="small muted">This version has no task graph; it was written by hand rather than compiled from recordings.</p>';
  const nodes = Object.fromEntries(g.nodes.map((n) => [n.key, n]));
  const edges = Object.fromEntries(g.edges.map((e) => [e.id, e]));
  const name = (key) => esc(nodeLabel(nodes[key] ?? { app_role: "?", activity: key }));
  const start = new Set(g.start);
  const nodeRows = g.nodes
    .map((n) => `<tr><td>${esc(nodeLabel(n))}</td><td>${start.has(n.key) ? '<span class="tag">start</span>' : ""}${n.terminal ? '<span class="tag success">goal</span>' : ""}</td><td class="num">${number(g.edges.filter((e) => e.frm === n.key).length)}</td><td class="mono small">${esc(n.key)}</td></tr>`)
    .join("");
  const edgeRows = g.edges
    .map(
      (e) =>
        `<tr><td>${name(e.frm)}<br><span class="muted">→ ${name(e.to)}</span></td><td><b>${esc(edgeLabel(e))}</b>${e.produces?.length ? `<br><span class="small muted">produces ${esc(e.produces.join(", "))}</span>` : ""}</td><td>${policyTag(e.policy)}</td><td class="small">${esc(statsText(e.stats))}</td><td class="small muted">${esc(provenanceText(e.provenance))}</td></tr>`,
    )
    .join("");
  const runs = review.runs.length
    ? `<h4>Runs of this version (${number(review.runs.length)})</h4><ul class="run-list small">${review.runs
        .map(
          (r) =>
            `<li>${runTag(r.status)} ${r.verified === true ? '<span class="tag success">verified</span>' : r.verified === false ? '<span class="tag danger">not verified</span>' : ""} ${esc(r.mode)}${r.finished_at ? ` · ${esc(stamp(r.finished_at))}` : ""} <button class="text-button" data-workflow-run="${esc(r.run_id)}">Open ${icon("arrow")}</button><ol class="step-list">${r.steps
              .map((s) => `<li>${esc(edges[s.edge] ? edgeLabel(edges[s.edge]) : s.edge)} <span class="muted">from ${edges[s.edge] ? name(edges[s.edge].frm) : "?"}</span> · ${s.executed ? "executed" : "proposed"}${s.effect_seen === false ? " · <b>effect not seen</b>" : ""}${s.decision ? ` · ${esc(s.decision)}d by a person` : ""}</li>`)
              .join("")}</ol></li>`,
        )
        .join("")}</ul>`
    : '<p class="small muted">No finished run of this version yet.</p>';
  const changeRows = review.changes
    .map((c) =>
      c.kind === "policy"
        ? `<li>${esc(edges[c.edge_id] ? edgeLabel(edges[c.edge_id]) : c.edge_id)}: ${policyTag(c.before)} → ${policyTag(c.after)} <span class="muted">automatic — ${esc(c.reason)}</span></li>`
        : `<li>${esc(edges[c.edge_id] ? edgeLabel(edges[c.edge_id]) : c.edge_id)}: <span class="muted">${esc(statsText(c.before))}</span><br>→ ${esc(statsText(c.after))}</li>`,
    )
    .join("");
  const proposals = review.proposals
    .map(
      (p) =>
        `<li><label><input type="checkbox" data-promote="${esc(p.edge_id)}" ${role === "owner" && review.can_draft ? "" : "disabled"}> ${esc(edges[p.edge_id] ? edgeLabel(edges[p.edge_id]) : p.edge_id)}: ${policyTag(p.from_policy)} → ${policyTag(p.to_policy)} <span class="muted">${esc(p.reason)}</span></label></li>`,
    )
    .join("");
  const previous = review.against_previous.length
    ? `<h4>What approving v${review.version_number} changes against v${review.previous_version_number}</h4><ul class="small">${review.against_previous
        .map((c) => `<li>${esc(edges[c.edge_id] ? edgeLabel(edges[c.edge_id]) : c.edge_id)}: ${c.before ? policyTag(c.before) : '<span class="tag">new</span>'} → ${policyTag(c.after)}${c.reason ? ` <span class="muted">${esc(c.reason)}</span>` : ""}</li>`)
        .join("")}</ul>`
    : review.status === "draft" && review.previous_version_number
      ? `<p class="small muted">No policy differs from v${review.previous_version_number}; this draft carries statistics only.</p>`
      : "";
  const draft =
    review.changes.length || review.proposals.length
      ? `<h4>Since approval</h4>${changeRows ? `<ul class="small">${changeRows}</ul>` : ""}${proposals ? `<p class="small">Code proposes these promotions from the statistics; nothing changes until a person drafts and approves them.</p><ul class="small">${proposals}</ul>` : ""}${
          role === "owner" && review.can_draft
            ? `<div class="actions"><button class="primary" data-draft-runs="${esc(workflowId)}" data-version="${esc(review.version_id)}" data-expected="${esc(review.version_number)}">${icon("workflow")}Create draft v${review.version_number + 1} from these runs</button><span class="small muted">then approve it under Workflows</span></div>`
            : review.can_draft
              ? '<p class="small muted">Only the workspace owner can draft the next version.</p>'
              : ""
        }`
      : "";
  return `<p class="small">${number(g.nodes.length)} states · ${number(g.edges.length)} moves · compiled from ${number(g.trajectories)} recorded pass${g.trajectories === 1 ? "" : "es"}${g.truncated ? " · truncated" : ""} · <span class="mono">${esc(g.compiled_by)}</span></p>${previous}<h4>States</h4><div class="table-wrap"><table><thead><tr><th>State</th><th></th><th class="num">Moves out</th><th>Key</th></tr></thead><tbody>${nodeRows}</tbody></table></div><h4>Moves</h4><div class="table-wrap"><table><thead><tr><th>From → to</th><th>Move</th><th>Policy</th><th>Statistics</th><th>Provenance</th></tr></thead><tbody>${edgeRows}</tbody></table></div>${runs}${draft}<p class="small muted">Names only: a state says which fields hold a value and which facts are known, never the values. Statistics accumulate on every run; the structure and the policies change only through an approved version.</p>`;
}
async function loadGraphReview(workflowId, versionId, body) {
  body.innerHTML = '<p class="small muted">Loading task graph…</p>';
  try {
    const review = await api(`/workflows/${workflowId}/versions/${versionId}/graph`);
    graphReviews = { ...graphReviews, [versionId]: review };
    body.innerHTML = graphReviewHtml(workflowId, review);
    bindGraphReview(body, workflowId, versionId);
  } catch (e) {
    body.innerHTML = `<p class="small muted">${esc(e.message)}</p>`;
  }
}
function bindGraphReview(body, workflowId, versionId) {
  body.querySelectorAll("[data-workflow-run]").forEach((b) => (b.onclick = () => showWorkflowRun(b.dataset.workflowRun)));
  body.querySelectorAll("[data-draft-runs]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const promote = [...body.querySelectorAll("[data-promote]:checked")].map((c) => c.dataset.promote);
        const created = await api(`/workflows/${workflowId}/versions/${versionId}/graph/draft`, {
          method: "POST",
          body: JSON.stringify({ expected_version: Number(b.dataset.expected), promote }),
        });
        workflows = workflows.map((w) => (w.id === workflowId ? { ...w, latest_version: created } : w));
        graphReviews = {};
        render();
      })),
  );
}
function graphPanelHtml(w) {
  const v = w.latest_version;
  const g = v.definition?.graph;
  if (!g) return "";
  return `<details data-graph="${esc(w.id)}" data-version="${esc(v.id)}"><summary class="small">Task graph · ${number(g.nodes.length)} states · ${number(g.edges.length)} moves${v.status === "draft" && v.number > 1 ? " · draft changes" : ""}</summary><div class="graph-body"></div></details>`;
}
function workflowsView() {
  const heading = `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Workflows, <i>version by version.</i></h1><p>Drafts come from recorder findings (Findings → Proposed automation → Draft workflow) or from the firm's analysts. Every version is approved or rejected exactly as written; a new version starts unapproved again. An approved sandbox version can be run by the Computer Use Agent, one bounded step at a time.</p></div></div>`;
  if (!workflows.length)
    return `${heading}<div class="empty"><h2>No workflows yet.</h2><p>Open a published recording or a proposed-automation finding and press <b>Draft workflow</b> to create the first draft.</p></div>`;
  const rows = workflows
    .map((w) => {
      const v = w.latest_version;
      const d = v.definition ?? {};
      const decision = v.decision ? `${v.decision.decision} · ${v.decision.reason || "no reason given"}` : "awaiting a decision";
      const buttons =
        role === "owner" && v.status === "draft"
          ? `<button class="primary" data-decide="approved" data-workflow="${esc(w.id)}" data-version="${esc(v.id)}">${icon("check")}Approve v${v.number}</button><button data-decide="rejected" data-workflow="${esc(w.id)}" data-version="${esc(v.id)}">Reject</button>`
          : role === "owner"
            ? ""
            : '<span class="small muted">Only the workspace owner can approve or reject.</span>';
      return `<article class="finding-row"><div><span class="eyebrow">v${v.number} · ${esc(v.status)}</span><h3>${esc(w.name)}</h3><p>${esc(d.goal ?? "")}</p><details><summary class="small">Definition</summary><dl class="small"><dt>Inputs</dt><dd>${esc((d.required_inputs ?? []).join(", "))}</dd><dt>Tools</dt><dd>${esc((d.allowed_tools ?? []).join(", "))}</dd><dt>Success</dt><dd>${(d.success_criteria ?? []).map((c) => `<div>${esc(c)}</div>`).join("")}</dd><dt>Limits</dt><dd>${esc(`${d.limits?.max_steps ?? "?"} steps · ${d.limits?.max_runtime_seconds ?? "?"} s · $${d.limits?.max_cost_usd ?? "?"} per run · ${d.environment ?? "sandbox"}`)}</dd></dl></details>${graphPanelHtml(w)}<p class="spacing-2 small">${esc(decision)} · created ${esc(stamp(v.created_at))}</p>${workflowRunsHtml(w)}</div><section>${versionTag(v.status)}<div class="actions">${buttons}${workflowRunControls(w)}</div></section></article>`;
    })
    .join("");
  return `${heading}<section class="panel">${rows}</section>`;
}
function findingsView() {
  return `<div class="page-heading"><div><span class="eyebrow">From records to recommendations</span><h1>Attention, with <i>evidence.</i></h1><p>Decide the records Vista was unsure about, then review what the agents observed. Every finding cites the rows behind it.</p></div></div><section class="panel"><div class="panel-heading"><h2>Import exceptions</h2><span class="small">${number(openExceptions.length)} open</span></div>${exceptionRows(openExceptions)}</section><p class="spacing-4 small">Marking a finding reviewed records your assessment. It does not resolve the discrepancy or count it as realized savings.</p>${agentFindingsBlock()}`;
}
function bindContent() {
  document
    .querySelectorAll("[data-import]")
    .forEach((b) => (b.onclick = openImport));
  document.querySelectorAll("[data-sample]").forEach(
    (b) =>
      (b.onclick = () => {
        openImport();
        loadSample();
      }),
  );
  document.querySelectorAll("[data-go]").forEach(
    (b) =>
      (b.onclick = () => {
        view = b.dataset.go;
        render();
      }),
  );
  document
    .querySelectorAll("[data-review]")
    .forEach((b) => (b.onclick = action(startReview)));
  document.querySelectorAll("[data-exception]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const [jobId, ref] = b.dataset.exception.split(":");
        replaceJob(
          await api(`/deals/${company().id}/imports/${jobId}/exceptions/${ref}`, {
            method: "POST",
            body: JSON.stringify({ decision: b.dataset.decide }),
          }),
        );
        await refreshImports();
        render();
      })),
  );
  document
    .querySelectorAll("[data-agent-finding]")
    .forEach(
      (b) =>
        (b.onclick = action(() => showAgentFinding(b.dataset.agentFinding))),
    );
  document
    .querySelectorAll("[data-run]")
    .forEach((b) => (b.onclick = action(() => showRun(b.dataset.run))));
  document
    .querySelectorAll("[data-report]")
    .forEach((b) => (b.onclick = action(() => showReport(b.dataset.report))));
  document
    .querySelectorAll("[data-start]")
    .forEach((b) => (b.onclick = action(() => startRun(b.dataset.start))));
  document.querySelectorAll("[data-afilter]").forEach(
    (b) =>
      (b.onclick = () => {
        agentFilter[b.dataset.afilter] = b.dataset.value;
        render();
      }),
  );
  document
    .querySelectorAll("[data-source]")
    .forEach((b) => (b.onclick = () => showSource(b.dataset.source)));
  document.querySelectorAll("[data-resume]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const current = generation;
        const loaded = await api(`/deals/${company().id}/imports/${b.dataset.resume}`);
        if (current !== generation) return;
        wizard = { jobs: [loaded] };
        importError();
        openDialog($("import-dialog"));
        if (loaded.status === "mapping_review") renderMapping();
        else renderApprove();
      })),
  );
  document.querySelectorAll("[data-decide]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const updated = await api(`/workflows/${b.dataset.workflow}/versions/${b.dataset.version}/decision`, {
          method: "POST",
          body: JSON.stringify({ decision: b.dataset.decide, reason: "Decided from the company workspace" }),
        });
        workflows = workflows.map((w) => (w.id === b.dataset.workflow ? { ...w, latest_version: updated } : w));
        render();
      })),
  );
  document.querySelectorAll("[data-graph]").forEach((d) => {
    const body = d.querySelector(".graph-body");
    const cached = graphReviews[d.dataset.version];
    if (cached) {
      body.innerHTML = graphReviewHtml(d.dataset.graph, cached);
      bindGraphReview(body, d.dataset.graph, d.dataset.version);
      d.open = true;
    } else d.ontoggle = () => d.open && !body.dataset.loaded && ((body.dataset.loaded = "1"), loadGraphReview(d.dataset.graph, d.dataset.version, body));
  });
  document.querySelectorAll("[data-run-workflow]").forEach(
    (b) =>
      (b.onclick = () => {
        runConfirm = b.dataset.runWorkflow;
        render();
      }),
  );
  document.querySelectorAll("[data-run-cancel]").forEach(
    (b) =>
      (b.onclick = () => {
        runConfirm = null;
        render();
      }),
  );
  document
    .querySelectorAll("[data-run-confirm]")
    .forEach((b) => (b.onclick = action(() => startWorkflowRun(b.dataset.runConfirm, b.dataset.version))));
  document
    .querySelectorAll("[data-workflow-run]")
    .forEach((b) => (b.onclick = action(() => showWorkflowRun(b.dataset.workflowRun))));
}
function openDialog(dialog) {
  if (!dialog.open) dialog.showModal();
}
function setStep(stage) {
  ["upload", "map", "analyze"].forEach((name, i) => {
    $(`step-${name}`).className =
      i === stage ? "current" : i < stage ? "done" : "";
  });
}
function openImport() {
  if (!canEdit()) return;
  wizard = { jobs: [] };
  files = [];
  importError();
  setStep(0);
  openDialog($("import-dialog"));
  $("import-body").innerHTML =
    `<div id="dropzone" class="dropzone">${icon("upload")}<h3>A few files. A useful first look.</h3><p>Drop CSV or XLSX exports here, or choose files below.<br>Customers, invoices, vendors and purchases, software, policies, purchase orders, inventory.</p><input id="file-input" type="file" multiple accept=".csv,.xlsx" aria-label="Choose company export files"/></div><div id="selected-files" class="selected-files"></div>${isMeridian() ? '<div class="spacing-5 quiet-note"><div><strong>Presenting Meridian?</strong><br>Load three synthetic exports from the demo company.</div><button id="sample-files">Use sample files</button></div>' : ""}<div class="dialog-actions"><span class="small">Up to 12 files, 25 MB each. Files are saved to this company when you continue; nothing becomes a record until you approve.</span><button class="primary" id="upload-files" disabled>Detect and map fields ${icon("arrow")}</button></div>`;
  $("file-input").onchange = () => selectFiles([...$("file-input").files]);
  const drop = $("dropzone");
  drop.ondragover = (e) => {
    e.preventDefault();
    drop.classList.add("dragover");
  };
  drop.ondragleave = () => drop.classList.remove("dragover");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");
    selectFiles([...e.dataTransfer.files]);
  };
  if ($("sample-files")) $("sample-files").onclick = loadSample;
  $("upload-files").onclick = uploadFiles;
}
function selectFiles(selected) {
  files = selected.filter((f) => /\.(csv|xlsx)$/i.test(f.name));
  importError();
  if (!files.length || files.length > 12 || files.some((f) => f.size > 25 * 1024 * 1024)) {
    files = [];
    importError("Choose 1–12 CSV or XLSX files of at most 25 MB each.");
  }
  $("selected-files").innerHTML = files
    .map(
      (f) =>
        `<div class="selected-file"><span>${icon("file")} ${esc(f.name)}</span><span class="small">${Math.max(1, Math.round(f.size / 1024))} KB</span></div>`,
    )
    .join("");
  $("upload-files").disabled = !files.length;
}
async function loadSample() {
  const b = $("sample-files");
  const input = $("file-input");
  if (b) b.disabled = true;
  try {
    const response = await fetch("/demo/meridian/manifest.json");
    if (!response.ok) throw new Error("Sample files are unavailable.");
    const manifest = await response.json();
    const loaded = await Promise.all(
      manifest.files.map(async (name) => {
        const r = await fetch(`/demo/meridian/${encodeURIComponent(name)}`);
        if (!r.ok) throw new Error("A sample file could not be loaded.");
        return new File([await r.blob()], name, { type: "text/csv" });
      }),
    );
    if (!$("import-dialog").open || $("file-input") !== input) return;
    selectFiles(loaded);
  } catch (e) {
    if ($("file-input") === input) importError(e.message);
  } finally {
    if (b) b.disabled = false;
  }
}
function encoded(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        name: file.name,
        content: String(reader.result).split(",")[1],
        mime_type: file.type || "",
      });
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}
function setBusy(value) {
  busy = value;
  $("close-import").disabled = value;
  $("company").disabled = value;
}
function replaceJob(job) {
  jobs = jobs.some((j) => j.id === job.id) ? jobs.map((j) => (j.id === job.id ? job : j)) : [...jobs, job];
  if (wizard) wizard.jobs = wizard.jobs.map((j) => (j.id === job.id ? job : j));
}
async function uploadFiles() {
  if (busy) return;
  setBusy(true);
  importError();
  const button = $("upload-files");
  button.disabled = true;
  button.textContent = "Reading and saving files…";
  try {
    for (const file of files) {
      const body = await encoded(file);
      const job = await api(`/deals/${company().id}/imports`, { method: "POST", body: JSON.stringify(body) });
      wizard.jobs = [...wizard.jobs.filter((j) => j.id !== job.id), job];
      replaceJob(job);
    }
    renderMapping();
  } catch (e) {
    importError(e.message);
    button.disabled = false;
    button.innerHTML = `Detect and map fields ${icon("arrow")}`;
  } finally {
    setBusy(false);
  }
}
const fieldsOf = (dataset) => Object.entries(datasets[dataset]?.fields ?? {});
function renderMapping() {
  setStep(1);
  importError();
  $("import-body").innerHTML =
    `<h3>Confirm how these records fit.</h3><p class="small">${wizard.jobs.length} file${wizard.jobs.length === 1 ? "" : "s"}. Vista detected each file's record type from its headers and proposed a Vista field per column; anything under 90% confidence is marked <strong>Review</strong>. Required fields must be mapped.</p>${wizard.jobs
      .map(
        (j) =>
          `<section class="mapping-card"><div class="mapping-head"><div><strong>${esc(j.filename)}</strong><small>${j.sheet ? `${esc(j.sheet)} · ` : ""}${number(j.recordsDetected ?? 0)} rows · detected ${esc(datasets[j.detection?.dataset ?? j.dataset]?.label ?? j.dataset)} (${Math.round((j.detection?.confidence ?? 0) * 100)}%)</small></div><label class="small">Record type<select data-type="${esc(j.id)}" aria-label="Record type for ${esc(j.filename)}">${Object.entries(
            datasets,
          )
            .map(([k, d]) => `<option value="${k}" ${j.dataset === k ? "selected" : ""}>${esc(d.label)}</option>`)
            .join("")}</select></label></div><div class="table-wrap">${mappingTable(j)}</div><details><summary>Preview original columns and records</summary><div class="table-wrap">${sampleMarkup(j)}</div></details></section>`,
      )
      .join(
        "",
      )}<div class="dialog-actions"><span class="small">Confirming saves the mappings, normalises every row and lists the records Vista is unsure about. Originals remain unchanged.</span><button id="confirm-mapping" class="primary">Confirm mappings ${icon("arrow")}</button></div>`;
  document.querySelectorAll("[data-type]").forEach(
    (el) =>
      (el.onchange = action(async () => {
        replaceJob(
          await api(`/deals/${company().id}/imports/${el.dataset.type}/dataset`, {
            method: "POST",
            body: JSON.stringify({ dataset: el.value }),
          }),
        );
        renderMapping();
      })),
  );
  document.querySelectorAll("[data-map]").forEach(
    (el) =>
      (el.onchange = () => {
        const m = findMapping(el.dataset.map);
        m.target = el.value || null;
        m.status = el.value ? "Confirmed" : "Ready";
        m.confidence = el.value ? 1 : 0;
        m.decided = true;
        renderMapping();
      }),
  );
  document.querySelectorAll("[data-confirm]").forEach(
    (el) =>
      (el.onclick = () => {
        const m = findMapping(el.dataset.confirm);
        m.status = "Confirmed";
        m.confidence = 1;
        m.decided = true;
        renderMapping();
      }),
  );
  $("confirm-mapping").onclick = action(confirmMappings);
}
function findMapping(key) {
  const [jobId, source] = key.split(/:(.*)/s);
  return wizard.jobs.find((j) => j.id === jobId).mappings.find((m) => m.source === source);
}
function mappingTable(j) {
  if (!fieldsOf(j.dataset).length)
    return '<p class="small muted">This record type has no canonical fields; the file is kept as a source document only.</p>';
  return `<table><thead><tr><th>Source column</th><th>Example</th><th>Vista field</th><th class="num">Confidence</th><th>Status</th></tr></thead><tbody>${j.mappings
    .map(
      (m) =>
        `<tr><td><strong>${esc(m.source)}</strong></td><td><code class="small">${esc(m.example ?? "")}</code></td><td><select data-map="${esc(j.id)}:${esc(m.source)}" aria-label="${esc(j.filename)}: ${esc(m.source)}"><option value="">— ignore —</option>${fieldsOf(j.dataset)
          .map(([k, d]) => `<option value="${k}" ${k === m.target ? "selected" : ""}>${esc(d.label)}${d.required ? " *" : ""}</option>`)
          .join("")}</select></td><td class="num">${m.confidence ? `${Math.round(m.confidence * 100)}%` : "—"}</td><td>${
          m.status === "Confirmed"
            ? '<span class="tag success">Confirmed</span>'
            : m.status === "Review"
              ? `<span class="tag warning">Review</span> <button data-confirm="${esc(j.id)}:${esc(m.source)}" class="text-button">Confirm</button>`
              : '<span class="tag">Ready</span>'
        }</td></tr>`,
    )
    .join("")}</tbody></table>`;
}
function sampleMarkup(j) {
  const columns = j.columns ?? [];
  const rows = j.sample ?? [];
  return `<table><thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${columns.map((c) => `<td>${esc(r[c] ?? "")}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}
async function confirmMappings() {
  if (busy) return;
  for (const j of wizard.jobs) {
    for (const [key, d] of fieldsOf(j.dataset)) {
      if (d.required && !j.mappings.some((m) => m.target === key)) {
        importError(`${j.filename}: required field “${d.label}” is not mapped.`);
        return;
      }
    }
    if (j.mappings.some((m) => m.status === "Review")) {
      importError(`${j.filename}: confirm or change the mappings marked Review.`);
      return;
    }
  }
  setBusy(true);
  importError();
  try {
    for (const j of wizard.jobs.filter((j) => fieldsOf(j.dataset).length)) {
      replaceJob(
        await api(`/deals/${company().id}/imports/${j.id}/mappings/approve`, {
          method: "POST",
          body: JSON.stringify({
            mappings: j.mappings.map((m) => ({ source: m.source, target: m.target, confirmed: Boolean(m.decided) || m.status === "Confirmed" })),
          }),
        }),
      );
    }
    renderApprove();
  } catch (e) {
    importError(e.message);
  } finally {
    setBusy(false);
  }
}
function renderApprove() {
  setStep(2);
  importError();
  const importable = wizard.jobs.filter((j) => fieldsOf(j.dataset).length);
  const xs = importable.flatMap((j) => (j.exceptions ?? []).map((x) => ({ ...x, importJobId: j.id })));
  const pending = xs.filter((x) => x.open).length;
  const total = importable.reduce((n, j) => n + (j.recordsDetected ?? 0), 0);
  const reviewed = importable.reduce((n, j) => n + (j.recordsNeedingReview ?? 0), 0);
  $("import-body").innerHTML =
    `<h3>Approve the import.</h3><div class="metrics">${[
      ["Records to import", number(total), `${importable.length} file${importable.length === 1 ? "" : "s"}`],
      ["Auto-accepted", number(total - reviewed), "Mapped at 90% or above"],
      ["Under review", number(reviewed), "Mapped below 90%"],
      ["Exceptions", `${number(xs.length - pending)} / ${number(xs.length)}`, "Decided"],
    ]
      .map(([label, value, note]) => `<div class="metric"><span class="eyebrow">${label}</span><b>${value}</b><small>${esc(note)}</small></div>`)
      .join("")}</div><p class="small">${pending ? `<strong>${pending}</strong> record${pending === 1 ? "" : "s"} still need a decision. You can approve now and decide them later from Findings.` : "Every ambiguous record has a decision."}</p>${exceptionRows(xs)}<div class="dialog-actions"><span class="small">Approving writes canonical records with the source file, row and original values on each one.</span><button id="approve-import" class="primary">Approve import ${icon("arrow")}</button></div>`;
  document.querySelectorAll("[data-exception]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const [jobId, ref] = b.dataset.exception.split(":");
        replaceJob(
          await api(`/deals/${company().id}/imports/${jobId}/exceptions/${ref}`, {
            method: "POST",
            body: JSON.stringify({ decision: b.dataset.decide }),
          }),
        );
        renderApprove();
      })),
  );
  $("approve-import").onclick = action(approveImport);
}
async function approveImport() {
  if (busy) return;
  setBusy(true);
  importError();
  $("import-body").innerHTML =
    `<div class="busy">${icon("scan")}<h2>Writing the records.</h2><p>Each row keeps its source file, row number and original values.</p></div>`;
  try {
    for (const j of wizard.jobs.filter((j) => fieldsOf(j.dataset).length))
      replaceJob(await api(`/deals/${company().id}/imports/${j.id}/approve`, { method: "POST", body: "{}" }));
    await refreshImports();
    $("import-dialog").close();
    view = "overview";
    render();
    message(
      `Import complete. ${number(recordCount())} canonical records on file. ${canEdit() ? "Run the File Reviewer from the overview to see what deserves attention." : ""}`,
    );
  } catch (e) {
    renderApprove();
    importError(e.message);
  } finally {
    setBusy(false);
  }
}
const PAGE = 25;
function showSource(key, page = 0) {
  const set = recordSets().find((r) => r.key === key);
  if (!set) return;
  const rows = set.rows;
  const hidden = new Set(["id", "companyId", "provenance", "dataSourceType", "syntheticDemo", "customerId", "vendorId", "purchaseOrderId"]);
  const columns = Object.keys(rows[0] ?? {}).filter((c) => !hidden.has(c)).slice(0, 8);
  const slice = rows.slice(page * PAGE, page * PAGE + PAGE);
  $("source-title").textContent = set.label;
  $("source-body").innerHTML =
    `<p class="small">${number(rows.length)} canonical records. Each row shows its source file and row; open a row for the original values as exported.</p><div class="table-wrap"><table><thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}<th>Source</th></tr></thead><tbody>${slice
      .map(
        (r, i) =>
          `<tr>${columns.map((c) => `<td>${esc(r[c] ?? "")}</td>`).join("")}<td>${r.provenance ? `<button data-row="${page * PAGE + i}" class="text-button">${esc(r.provenance.file)} · row ${esc(r.provenance.row)}</button>` : "—"}</td></tr>`,
      )
      .join("")}</tbody></table></div><div class="pager"><button id="source-prev" ${page === 0 ? "disabled" : ""}>Previous</button><span>${page * PAGE + 1}–${Math.min(rows.length, (page + 1) * PAGE)} of ${number(rows.length)}</span><button id="source-next" ${(page + 1) * PAGE >= rows.length ? "disabled" : ""}>Next</button></div><div id="source-row"></div>`;
  $("source-prev").onclick = () => showSource(key, page - 1);
  $("source-next").onclick = () => showSource(key, page + 1);
  document.querySelectorAll("#source-body [data-row]").forEach(
    (b) =>
      (b.onclick = () => {
        const r = rows[Number(b.dataset.row)];
        const p = r.provenance ?? {};
        $("source-row").innerHTML =
          `<section class="evidence-source"><header><strong>${icon("file")} ${esc(p.file ?? "")}${p.sheet ? ` · ${esc(p.sheet)}` : ""}</strong><span class="tag">Row ${esc(p.row ?? "")}</span></header><p class="small">${esc(p.review ?? "")} · confidence ${Math.round((p.confidence ?? 0) * 100)}%</p><dl>${Object.entries(
            p.original ?? {},
          )
            .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v) || "—"}</dd>`)
            .join("")}</dl></section>`;
      }),
  );
  openDialog($("source-dialog"));
}
async function refreshImports() {
  const result = await api(`/deals/${company().id}/imports`);
  jobs = result.imports;
  openExceptions = result.openExceptions ?? [];
  role = result.role;
  records = jobs.some((j) => j.status === "completed") ? await api(`/deals/${company().id}/records`) : null;
}
async function enterCompany() {
  const current = ++generation;
  jobs = [];
  openExceptions = [];
  records = null;
  agents = null;
  role = "viewer";
  render();
  message();
  try {
    await refreshImports();
    if (current !== generation) return;
    if (!Object.keys(datasets).length)
      datasets = await api(`/deals/${company().id}/import-datasets`);
    if (current !== generation) return;
    render();
    await Promise.all([loadAgents(current), loadReports(current), loadWorkflows(current)]);
    if (current === generation) render();
  } catch (e) {
    if (current === generation) {
      render();
      message(e.message);
    }
  }
}
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      view = b.dataset.view;
      render();
    }),
);
$("import-top").onclick = openImport;
$("close-import").onclick = () => {
  if (!busy) {
    $("import-dialog").close();
    render();
  }
};
$("import-dialog").addEventListener("cancel", (e) => {
  if (busy) e.preventDefault();
  else render();
});
$("close-evidence").onclick = () => $("evidence-dialog").close();
$("close-source").onclick = () => $("source-dialog").close();
$("close-run").onclick = () => {
  clearInterval(runPoll);
  $("run-dialog").close();
};
$("close-report").onclick = () => $("report-dialog").close();
$("company").onchange = action(enterCompany);
$("signout").onclick = action(async () => {
  await api("/auth/session", { method: "DELETE" });
  navigate("/signin/");
});
try {
  const me = await api("/auth/me");
  $("identity").textContent = me.email;
  companies = await api("/deals");
  $("company").replaceChildren(
    ...companies.map((c) => new Option(c.name, c.id)),
  );
  if (companies.length) await enterCompany();
  else render();
} catch (e) {
  if (e.message !== "Sign in to continue") message(e.message);
}
