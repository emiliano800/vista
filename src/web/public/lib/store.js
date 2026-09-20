// Portfolio state for the PE analyst workspace, read from the Vista backend.
//
// The firm is the tenant and each portfolio company is a deal, so everything
// here is a projection of stored records: import batches, findings, agents and
// runs. `hydrate` loads one snapshot per page load; the accessors below stay
// synchronous so rendering never has to await. Mutations write through to the
// API and patch the snapshot, so a render after an await shows stored state.
import { apiFetch } from "./auth.js";
import { PERIOD, TODAY, daysBetween } from "./format.js";

let state = null;

class ApiError extends Error {}

async function get(path) {
  const response = await apiFetch(path);
  if (!response.ok) throw new ApiError(`GET ${path} failed (${response.status})`);
  return response.json();
}
async function send(method, path, body) {
  const response = await apiFetch(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) throw new ApiError(`${method} ${path} failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}

// ---- Shape mapping -----------------------------------------------------------
// The API speaks the ingestion vocabulary (snake_case, source field names); the
// workspace speaks camelCase. Mapping lives here and nowhere else.
const num = (value) => {
  const n = Number(String(value ?? "").replace(/[$,]/g, "").trim());
  return Number.isFinite(n) ? n : 0;
};
const orNull = (value) => (value === null || value === undefined || value === "" ? null : value);

const mapInvoice = (r) => ({
  id: r.invoice_id ?? `${r._table}:${r._row}`,
  clientId: r.client_id ?? null,
  issueDate: orNull(r.issue_date),
  dueDate: orNull(r.due_date),
  amount: num(r.amount ?? r.balance),
  outstanding: num(r.balance),
  status: r.status ?? "",
  paymentPlan: r.payment_plan ?? "",
  source: { table: r._table, row: r._row },
});
const mapPurchase = (r) => ({
  id: r.purchase_id ?? `${r._table}:${r._row}`,
  sku: r.sku ?? "",
  description: r.description ?? "",
  vendorName: r.vendor_name ?? "",
  date: orNull(r.purchase_date),
  quantity: num(r.quantity),
  unitPrice: num(r.unit_price),
  total: num(r.total) || num(r.quantity) * num(r.unit_price),
  unit: r.unit || "unit",
  source: { table: r._table, row: r._row },
});
const mapSubscription = (r) => ({
  id: r.subscription_id ?? `${r._table}:${r._row}`,
  product: r.product ?? "",
  vendorName: r.vendor_name ?? "",
  monthlyCost: num(r.monthly_cost),
  seats: num(r.seats),
  seatsActive: num(r.seats_active),
  renewalDate: orNull(r.renewal_date),
  source: { table: r._table, row: r._row },
});
const mapVendor = (r) => ({
  id: r.vendor_id ?? r.carrier_code ?? `${r._table}:${r._row}`,
  name: r.vendor_name ?? r.carrier_name ?? "",
  sourceName: r.vendor_name ?? r.carrier_name ?? "",
  category: r.category ?? "",
  source: { table: r._table, row: r._row },
});
const mapCustomer = (r) => ({
  id: r.client_id ?? `${r._table}:${r._row}`,
  name: r.client_name ?? "",
  status: r.status ?? "",
  source: { table: r._table, row: r._row },
});

// Findings are the ingestion analysis of the company's own import, so they are
// company-scoped and carry their calculation with them.
const mapFinding = (f, companyId, batchId) => ({
  id: f.id,
  companyId,
  batchId,
  title: f.title,
  detail: f.detail,
  category: f.category,
  severity: f.amount && Number(f.amount) > 0 ? "High" : "Medium",
  amount: f.amount === null || f.amount === undefined ? null : Number(f.amount),
  recommendation: f.recommendation,
  calculation: f.calculation,
  evidence: f.evidence ?? [],
  status: { open: "Open", reviewed: "Actioned", dismissed: "Dismissed" }[f.status] ?? "Open",
  foundAt: null,
});

const mapTask = (t) => ({
  id: t.id,
  companyId: t.deal_id,
  title: t.title,
  description: t.description,
  category: t.category,
  sourceType: t.source_type,
  sourceId: t.source_id,
  assignee: t.assignee,
  priority: t.priority,
  dueDate: t.due_date,
  status: t.status,
  createdBy: t.created_by,
  createdAt: t.created_at,
  completedAt: t.completed_at,
  outcome: t.outcome,
  outcomeNotes: t.outcome_notes,
  realizedResult: t.realized_result === null ? null : Number(t.realized_result),
});
const mapOpportunity = (o) => ({
  id: o.id,
  title: o.title,
  category: o.category,
  companyIds: o.deal_ids,
  confidence: o.confidence,
  potentialValue: Number(o.potential_value),
  status: o.status,
  foundAt: o.found_at,
  fact: o.fact,
  evidence: o.evidence ?? [],
  calculation: o.calculation ?? [],
  benefit: o.benefit,
  assumptions: o.assumptions ?? [],
  nextAction: o.next_action,
  realizedValue: o.realized_value === null ? null : Number(o.realized_value),
});
const mapAgent = (a, runsForAgent) => ({
  id: a.id,
  companyId: a.deal_id,
  name: a.employee_name,
  roleTitle: a.role_title,
  status: a.status === "paused" ? "Paused" : "Active",
  scopes: a.scopes ?? [],
  schedule: a.schedule,
  lastRunAt: a.last_run_at,
  review: runsForAgent.filter((r) => r.status === "failed").length,
  cost: 0,
  lastFailure: runsForAgent.find((r) => r.status === "failed")?.created_at ?? null,
});
const mapRun = (r) => ({
  id: r.id,
  agentId: r.employee_agent_id,
  companyId: r.deal_id,
  goal: r.run_type,
  startedAt: r.created_at,
  finishedAt: r.finished_at,
  status: { queued: "Queued", running: "Running", succeeded: "Complete", failed: "Failed" }[r.status] ?? r.status,
  sources: ["Imported canonical records (this workspace)"],
  events: (r.events ?? []).map((e) => [String(e.created_at ?? "").slice(11, 16), e.event_type]),
  output: r.status === "failed" ? "Run failed; see the event log." : "",
  evidence: [],
  corrections: [],
  needsReview: r.status === "failed" ? 1 : 0,
});
const mapActivity = (a) => ({ at: a.at, companyId: a.deal_id, text: a.summary, kind: a.kind, actor: a.actor });

function mergeDetail(company, detail) {
  return {
    ...company,
    industry: detail.profile?.industry ?? company.industry ?? "",
    location: detail.profile?.location ?? company.location ?? "",
    acquired: detail.profile?.acquired ?? company.acquired ?? null,
    asOf: detail.as_of,
    batchId: detail.batch_id,
    customers: (detail.customers ?? []).map(mapCustomer),
    invoices: (detail.invoices ?? []).map(mapInvoice),
    vendors: (detail.vendors ?? []).map(mapVendor),
    purchases: (detail.purchases ?? []).map(mapPurchase),
    subscriptions: (detail.subscriptions ?? []).map(mapSubscription),
    // A bucket with no import behind it is unsourced, which the UI must show
    // as "no source" rather than as a measured zero.
    unsourced: detail.unsourced ?? [],
    importExceptions: detail.exceptions ?? [],
    analysis: detail.analysis ?? {},
  };
}

// ---- Hydration ---------------------------------------------------------------
export async function hydrate() {
  const [companyRows, taskRows, opportunityRows, activityRows, agentRows, runRows] = await Promise.all([
    get("/portfolio/companies"),
    get("/portfolio/tasks"),
    get("/portfolio/opportunities"),
    get("/portfolio/activity?limit=200"),
    get("/agents"),
    get("/runs?limit=200"),
  ]);
  const details = await Promise.all(companyRows.map((c) => get(`/portfolio/companies/${c.id}`)));
  const runs = runRows.map(mapRun);
  const companies = companyRows.map((c, i) =>
    mergeDetail(
      {
        id: c.id,
        name: c.name,
        industry: c.profile?.industry ?? "",
        location: c.profile?.location ?? "",
        acquired: c.profile?.acquired ?? null,
        importJobs: c.imports ? [{ createdAt: c.created_at, accepted: c.records }] : [],
        analysisRunAt: c.last_run_at,
      },
      details[i],
    ),
  );
  const findings = companies.flatMap((c) => (c.analysis?.findings ?? []).map((f) => mapFinding(f, c.id, c.batchId)));
  state = {
    companies,
    tasks: taskRows.map(mapTask),
    opportunities: opportunityRows.map(mapOpportunity),
    activity: activityRows.map(mapActivity),
    agents: agentRows.map((a) => mapAgent(a, runs.filter((r) => r.agentId === a.id))),
    runs,
    findings,
  };
  return state;
}

const empty = { companies: [], tasks: [], opportunities: [], activity: [], agents: [], runs: [], findings: [] };
export function load() {
  return state ?? empty;
}
export function setState(next) {
  state = next;
  return state;
}
// Re-reads the workspace from the backend. There is no local demo state to
// clear any more: what the analyst sees is what is stored.
export async function reset() {
  state = null;
  return hydrate();
}

// ---- Lookups -----------------------------------------------------------------
export const companies = () => load().companies;
export const company = (id) => load().companies.find((c) => c.id === id) ?? null;
export const companyName = (id) => company(id)?.name ?? "Portfolio";
export const tasks = () => load().tasks;
export const opportunities = () => load().opportunities;
export const findings = (companyId) => load().findings.filter((f) => !companyId || f.companyId === companyId);
export const agents = (companyId) => load().agents.filter((a) => !companyId || a.companyId === companyId);
export const runs = (companyId) => load().runs.filter((r) => !companyId || r.companyId === companyId);
export const run = (id) => load().runs.find((r) => r.id === id) ?? null;
// Fleet analytics for the four suite agents; fetched on demand, not part of the snapshot.
export const fleetAnalytics = () => get("/agents/analytics");
export const activity = (companyId, limit = 12) =>
  load()
    .activity.filter((a) => !companyId || a.companyId === companyId)
    .sort((a, b) => (a.at < b.at ? 1 : -1))
    .slice(0, limit);

// ---- Derived figures ---------------------------------------------------------
const inPeriod = (iso) => !!iso && iso >= PERIOD.start && iso <= PERIOD.end;
const sum = (list, f) => Math.round(list.reduce((acc, x) => acc + (f(x) || 0), 0) * 100) / 100;
// A figure whose source was never imported is null, not zero.
const sourced = (c, bucket, value) => (c.unsourced?.includes(bucket) ? null : value);

export function companyMetrics(c) {
  const periodInvoices = c.invoices.filter((i) => inPeriod(i.issueDate));
  const outstandingInvoices = c.invoices.filter((i) => i.outstanding > 0);
  const today = TODAY.toISOString().slice(0, 10);
  const overdueInvoices = outstandingInvoices.filter((i) => i.dueDate && i.dueDate < today);
  const overdue90 = overdueInvoices.filter((i) => daysBetween(i.dueDate) > 90);
  const openTasks = tasks().filter((t) => t.companyId === c.id && !["Complete", "Dismissed"].includes(t.status));
  const openOpps = opportunities().filter((o) => o.companyIds.includes(c.id) && !["Dismissed", "Realized"].includes(o.status));
  const automationCandidates =
    findings(c.id).filter((f) => /re-keys|copies|manual|repetitive/i.test(`${f.title} ${f.detail}`)).length +
    opportunities().filter((o) => o.companyIds.includes(c.id) && o.category === "Process automation").length;
  return {
    customers: c.customers.length,
    invoiceCount: periodInvoices.length,
    // Revenue needs an issue date and an amount; without them it is unsourced.
    revenue: c.invoices.some((i) => i.issueDate) ? sum(periodInvoices, (i) => i.amount) : null,
    outstandingAr: sourced(c, "invoices", sum(outstandingInvoices, (i) => i.outstanding)),
    outstandingInvoices,
    overdueAr: sourced(c, "invoices", sum(overdueInvoices, (i) => i.outstanding)),
    overdueInvoices,
    overdue90,
    vendorSpend: sourced(c, "purchases", sum(c.purchases.filter((p) => inPeriod(p.date) || !p.date), (p) => p.total)),
    softwareAnnual: sourced(c, "subscriptions", sum(c.subscriptions, (s) => s.monthlyCost * 12)),
    openTasks: openTasks.length,
    openOpportunities: openOpps.length,
    automationCandidates,
  };
}

export const INTEGRATION_STEPS = [
  ["profile", "Company profile"],
  ["customers", "Customers"],
  ["invoices", "Invoices"],
  ["vendors", "Vendors"],
  ["software", "Software"],
  ["exceptions", "Import exceptions resolved"],
  ["analysis", "Initial agent analysis"],
];
// Status per step from what is actually stored, never a typed-in percentage.
export function integrationSteps(c) {
  const openExceptions = (c.importExceptions ?? []).filter((x) => x.open !== false);
  const status = {
    profile: c.name && c.location && c.acquired ? "Complete" : "Not started",
    customers: c.customers.length ? "Complete" : "Not started",
    invoices: c.invoices.length ? "Complete" : "Not started",
    vendors: c.vendors.length ? "Complete" : "Not started",
    software: c.subscriptions.length ? "Complete" : "Not started",
    exceptions: openExceptions.length ? "Needs review" : c.batchId ? "Complete" : "Not started",
    analysis: c.analysisRunAt ? "Complete" : c.customers.length ? "In progress" : "Not started",
  };
  const steps = INTEGRATION_STEPS.map(([key, label]) => ({ key, label, status: status[key] }));
  const complete = steps.filter((s) => s.status === "Complete").length;
  return {
    steps,
    complete,
    total: steps.length,
    label: complete === steps.length ? "Complete" : complete === 0 ? "Not started" : "In progress",
  };
}

export function agentStatus(companyId) {
  const list = agents(companyId);
  if (!list.length) return { label: "No agents", tone: "" };
  const review = list.reduce((n, a) => n + a.review, 0);
  if (review) return { label: `${review} need${review === 1 ? "s" : ""} review`, tone: "warning" };
  if (list.some((a) => a.status === "Paused")) return { label: "Paused", tone: "" };
  return { label: "Active", tone: "success" };
}

export function portfolioMetrics() {
  const rows = companies().map((c) => ({ c, m: companyMetrics(c) }));
  return {
    companies: rows.length,
    revenue: sum(rows, (r) => r.m.revenue),
    outstandingAr: sum(rows, (r) => r.m.outstandingAr),
    overdueAr: sum(rows, (r) => r.m.overdueAr),
    vendorSpend: sum(rows, (r) => r.m.vendorSpend),
    openOpportunities: opportunities().filter((o) => !["Dismissed", "Realized"].includes(o.status)).length,
    openTasks: tasks().filter((t) => !["Complete", "Dismissed"].includes(t.status)).length,
    rows,
  };
}

// ---- Attention queue (application rules, not model judgement) ------------------
export function attentionQueue(companyId = null) {
  const items = [];
  const push = (item) => items.push({ age: daysBetween(item.since) <= 0 ? "today" : `${daysBetween(item.since)}d`, ...item });
  for (const c of companies()) {
    if (companyId && c.id !== companyId) continue;
    const m = companyMetrics(c);
    const openX = (c.importExceptions ?? []).filter((x) => x.open !== false);
    if (openX.length)
      push({
        companyId: c.id,
        type: "Import review",
        text: `${openX.length} record${openX.length === 1 ? "" : "s"} need mapping review`,
        since: c.importJobs?.at(-1)?.createdAt ?? c.acquired,
        severity: openX.length > 10 ? "High" : "Medium",
        cta: "Review records",
        href: `/acquisitions/?id=${c.id}`,
      });
    if (m.overdue90.length)
      push({
        companyId: c.id,
        type: "Working capital",
        text: `${m.overdue90.length} invoice${m.overdue90.length === 1 ? "" : "s"} >90 days overdue`,
        since: m.overdue90.map((i) => i.dueDate).sort()[0],
        severity: m.overdue90.length >= 5 ? "High" : "Medium",
        cta: "Inspect evidence",
        href: `/company/?id=${c.id}&tab=finance&filter=overdue90`,
      });
    for (const s of c.subscriptions) {
      if (!s.renewalDate) continue;
      const days = -daysBetween(s.renewalDate);
      if (days >= 0 && days <= 30)
        push({
          companyId: c.id,
          type: "Renewal",
          text: `${s.product} renews in ${days} days`,
          since: TODAY.toISOString(),
          severity: days <= 14 ? "High" : "Medium",
          cta: "Assign task",
          href: `/company/?id=${c.id}&tab=software`,
        });
    }
    for (const a of agents(c.id))
      if (a.review)
        push({
          companyId: c.id,
          type: "Agent exception",
          text: `${a.name} run requires human review`,
          since: a.lastRunAt,
          severity: "Medium",
          cta: "Inspect evidence",
          href: `/agents/?company=${c.id}`,
        });
    for (const f of findings(c.id))
      if (f.status === "Open" && f.severity === "High")
        push({
          companyId: c.id,
          type: "Finding",
          text: f.title,
          since: f.foundAt ?? c.asOf,
          severity: "High",
          cta: "Inspect evidence",
          href: `/company/?id=${c.id}&tab=findings`,
        });
    for (const o of opportunities())
      if (o.status === "New" && o.companyIds.includes(c.id) && o.companyIds[0] === c.id)
        push({
          companyId: c.id,
          type: "Opportunity",
          text: `${o.title}`,
          since: o.foundAt,
          severity: "Low",
          cta: "Review opportunity",
          href: `/opportunities/?id=${o.id}`,
        });
  }
  const rank = { High: 0, Medium: 1, Low: 2 };
  return items.sort((a, b) => rank[a.severity] - rank[b.severity] || (a.since < b.since ? -1 : 1));
}

export function companySummary(c) {
  const m = companyMetrics(c);
  const integ = integrationSteps(c);
  const openX = (c.importExceptions ?? []).filter((x) => x.open !== false).length;
  const opps = opportunities().filter((o) => o.companyIds.includes(c.id) && !["Dismissed", "Realized"].includes(o.status));
  const cross = opps.filter((o) => o.companyIds.length > 1).length;
  const money = (n) => (n === null ? "no imported source" : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}K`);
  const where = [c.industry?.toLowerCase(), c.location].filter(Boolean).join(" business in ");
  const parts = [
    `${c.name}${where ? ` is a ${where}` : ""} with ${m.customers.toLocaleString("en-US")} imported customers and ${money(m.outstandingAr)} of outstanding AR (${money(m.overdueAr)} past due).`,
    `Vendor spend in the period is ${money(m.vendorSpend)} across ${c.vendors.length} vendors; software runs ${money(m.softwareAnnual)} a year on ${c.subscriptions.length} subscriptions.`,
    `${integ.complete} of ${integ.total} integration steps are complete${openX ? ` and ${openX} import exception${openX === 1 ? "" : "s"} still need${openX === 1 ? "s" : ""} review` : ""}.`,
    opps.length
      ? `Vista has ${opps.length} open opportunit${opps.length === 1 ? "y" : "ies"} involving ${c.name}${cross ? `, ${cross} of them cross-company` : ""}.`
      : c.analysisRunAt
        ? "Portfolio analysis found no open opportunities involving this company."
        : "Portfolio analysis has not run for this company yet.",
  ];
  return parts.join(" ");
}

// ---- Mutations ----------------------------------------------------------------
// Each writes to the backend first, then patches the snapshot, so a failed
// request leaves the workspace showing stored state rather than a local guess.
export async function createTask(input, actor = "Analyst") {
  const created = await send("POST", "/portfolio/tasks", {
    deal_id: input.companyId,
    title: input.title,
    description: input.description ?? "",
    category: input.category ?? "Integration",
    source_type: input.sourceType ?? null,
    source_id: input.sourceId ?? null,
    assignee: input.assignee ?? actor,
    priority: input.priority ?? "Medium",
    due_date: input.dueDate ?? null,
  });
  const task = mapTask(created);
  load().tasks.unshift(task);
  if (input.sourceType === "opportunity") {
    const o = opportunities().find((x) => x.id === input.sourceId);
    if (o && ["New", "Under review"].includes(o.status)) await setOpportunityStatus(o.id, "Task created");
  }
  if (input.sourceType === "finding") await setFindingStatus(input.sourceId, "Actioned");
  return task;
}

export async function updateTask(id, patch, actor = "Analyst") {
  const body = {};
  if (patch.status !== undefined) body.status = patch.status;
  if (patch.title !== undefined) body.title = patch.title;
  if (patch.description !== undefined) body.description = patch.description;
  if (patch.assignee !== undefined) body.assignee = patch.assignee;
  if (patch.priority !== undefined) body.priority = patch.priority;
  if (patch.dueDate !== undefined) body.due_date = patch.dueDate;
  if (patch.outcome !== undefined) body.outcome = patch.outcome;
  if (patch.outcomeNotes !== undefined) body.outcome_notes = patch.outcomeNotes;
  if (patch.realizedResult !== undefined && patch.outcome === "Implemented") body.realized_result = patch.realizedResult;
  const updated = mapTask(await send("PATCH", `/portfolio/tasks/${id}`, body));
  const list = load().tasks;
  const i = list.findIndex((t) => t.id === id);
  if (i >= 0) list[i] = updated;
  if (patch.status === "Complete" && updated.sourceType === "opportunity" && updated.sourceId) {
    const next =
      updated.outcome === "Implemented"
        ? "Realized"
        : updated.outcome === "Benefit validated"
          ? "Validated"
          : updated.outcome === "No benefit found"
            ? "Dismissed"
            : null;
    if (next) await setOpportunityStatus(updated.sourceId, next, updated.realizedResult);
  }
  return updated;
}

export async function setOpportunityStatus(id, status, realizedValue = null) {
  const body = { status };
  if (realizedValue !== null && realizedValue !== undefined) body.realized_value = realizedValue;
  const updated = mapOpportunity(await send("PATCH", `/portfolio/opportunities/${id}`, body));
  const list = load().opportunities;
  const i = list.findIndex((o) => o.id === id);
  if (i >= 0) list[i] = updated;
  return updated;
}

// Ingestion findings are reviewed on their import batch, which is where the
// evidence and the decision log live.
const FINDING_DECISION = { Open: "open", Actioned: "reviewed", Dismissed: "dismissed" };
export async function setFindingStatus(id, status) {
  const f = load().findings.find((x) => x.id === id);
  if (!f?.batchId) return null;
  await send("POST", `/imports/${f.batchId}/findings/${id}`, { status: FINDING_DECISION[status] ?? "open" });
  f.status = status;
  return f;
}

export async function setAgentStatus(id, status) {
  const updated = await send("PATCH", `/agents/${id}`, { status: status === "Paused" ? "paused" : "active" });
  const a = load().agents.find((x) => x.id === id);
  if (a) {
    a.status = updated.status === "paused" ? "Paused" : "Active";
    if (a.status === "Active") a.lastFailure = null;
  }
  return a;
}

export async function runAgentNow(id) {
  const created = mapRun(await send("POST", `/agents/${id}/runs`));
  load().runs.unshift(created);
  const a = load().agents.find((x) => x.id === id);
  if (a) a.lastRunAt = created.startedAt;
  return created;
}

// Cross-company purchasing arbitrage is the one analysis a single company
// cannot run on its own data, so the backend does it over every deal.
export async function runPortfolioAnalysis() {
  const result = await send("POST", "/portfolio/analysis");
  const created = result.created.map(mapOpportunity);
  const updated = result.updated.map(mapOpportunity);
  const list = load().opportunities;
  for (const o of updated) {
    const i = list.findIndex((x) => x.id === o.id);
    if (i >= 0) list[i] = o;
  }
  list.push(...created);
  const at = new Date().toISOString();
  for (const c of companies()) c.analysisRunAt = at;
  return created;
}

// Import exceptions are resolved on the batch that raised them.
export async function resolveException(companyId, exceptionId, decision) {
  const c = company(companyId);
  const x = c?.importExceptions?.find((e) => e.id === exceptionId);
  if (!x || !c.batchId) return null;
  await send("POST", `/imports/${c.batchId}/findings/${exceptionId}`, {
    status: decision === "Match" ? "reviewed" : "dismissed",
  });
  x.decision = decision;
  x.open = false;
  return x;
}

// Companies are deals, created through the deals API.
export async function addCompany(c) {
  const created = await send("POST", "/deals", { name: c.name });
  const row = mergeDetail({ id: created.id, name: created.name, importJobs: [], analysisRunAt: null }, {
    profile: {},
    unsourced: ["customers", "invoices", "vendors", "purchases", "subscriptions"],
  });
  load().companies.push(row);
  return row;
}

// Identifiers come from the database now; kept so callers that still ask for a
// local counter get something stable within the page.
let counter = 0;
export function nextId() {
  return ++counter;
}

// ---- Evidence ------------------------------------------------------------------
export function evidenceRows(ref) {
  const c = company(ref.companyId ?? ref.deal_id);
  if (!c) return [];
  if (ref.entity === "purchase" || ref.sku) return c.purchases.filter((p) => p.sku === ref.sku);
  if (ref.entity === "subscription") return c.subscriptions.filter((s) => s.id === ref.id);
  if (ref.entity === "invoice" && ref.query === "overdue90") return companyMetrics(c).overdue90;
  if (ref.entity === "finding") return findings(c.id).filter((f) => f.id === ref.id);
  return [];
}
