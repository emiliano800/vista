// Portfolio state for the PE analyst workspace. The browser holds a cached
// snapshot of GET /api/portfolio (firm-scoped, computed by the backend from
// canonical records); every figure shown is read from that snapshot and every
// change goes through the API, then the snapshot is refreshed. Nothing in
// localStorage is authoritative.
import { api } from "./auth.js";
import { PERIOD, TODAY, daysBetween, setClock } from "./format.js";

let state = null;
let pending = null;

export async function load(force = false) {
  if (state && !force) return state;
  if (!pending) {
    pending = api("/portfolio")
      .then((s) => setState(s))
      .finally(() => (pending = null));
  }
  return pending;
}
export const refresh = () => load(true);
export function setState(next) {
  state = next;
  if (state) setClock(state.today, state.period);
  return state;
}
function current() {
  if (!state)
    throw new Error("Portfolio state has not been loaded; await load() first.");
  return state;
}

// ---- Lookups -----------------------------------------------------------------
export const snapshot = () => current();
export const companies = () => current().companies;
export const company = (id) =>
  current().companies.find((c) => c.id === id || c.slug === id) ?? null;
export const companyName = (id) => company(id)?.name ?? "Portfolio";
export const tasks = () => current().tasks;
export const opportunities = () => current().opportunities;
export const findings = (companyId) =>
  current().findings.filter((f) => !companyId || f.companyId === companyId);
export const agents = (companyId) =>
  current().agents.filter((a) => !companyId || a.companyId === companyId);
export const runs = (companyId) =>
  current().runs.filter((r) => !companyId || r.companyId === companyId);
export const run = (id) => current().runs.find((r) => r.id === id) ?? null;
// Fleet analytics for the four suite agents; fetched on demand, not part of the snapshot.
export const fleetAnalytics = () => api("/agents/analytics");
export const activity = (companyId, limit = 12) =>
  current()
    .activity.filter((a) => !companyId || a.companyId === companyId)
    .sort((a, b) => (a.at < b.at ? 1 : -1))
    .slice(0, limit);

// ---- Metrics (computed server-side; lists derived here only for drill-down tables)
const inPeriod = (iso) => iso >= PERIOD.start && iso <= PERIOD.end;
const todayIso = () => TODAY.toISOString().slice(0, 10);

export function companyMetrics(c) {
  const invoices = c.invoices ?? [];
  const outstandingInvoices = invoices.filter((i) => i.outstanding > 0);
  const overdueInvoices = outstandingInvoices.filter(
    (i) => i.dueDate && i.dueDate < todayIso(),
  );
  const overdue90 = overdueInvoices.filter((i) => daysBetween(i.dueDate) > 90);
  return { ...c.metrics, outstandingInvoices, overdueInvoices, overdue90 };
}

export const INTEGRATION_STEPS = [
  ["profile", "Company profile"],
  ["customers", "Customers"],
  ["invoices", "Invoices"],
  ["vendors", "Vendors"],
  ["software", "Software"],
  ["operations", "Policies / purchasing & inventory"],
  ["exceptions", "Import exceptions resolved"],
  ["analysis", "Initial agent analysis"],
];
export const integrationSteps = (c) => c.integration;

export function agentStatus(companyId) {
  const list = agents(companyId);
  if (!list.length) return { label: "No agents", tone: "" };
  const review = list.reduce((n, a) => n + a.review, 0);
  if (review)
    return {
      label: `${review} need${review === 1 ? "s" : ""} review`,
      tone: "warning",
    };
  if (list.some((a) => a.status === "Paused"))
    return { label: "Paused", tone: "" };
  return { label: "Active", tone: "success" };
}

export function portfolioMetrics() {
  const s = current();
  return {
    ...s.metrics,
    rows: s.companies.map((c) => ({ c, m: companyMetrics(c) })),
  };
}
export const attentionQueue = (companyId = null) =>
  current().attention.filter((i) => !companyId || i.companyId === companyId);
export const companySummary = (c) => c.summary;

// ---- Mutations: API call, then refresh the snapshot -----------------------------
async function mutate(path, body, method = "POST") {
  const result = await api(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await refresh();
  return result;
}
export const addCompany = (profile) => mutate("/portfolio/companies", profile);
export function resolveException(companyId, exceptionId, decision) {
  const x = (company(companyId)?.importExceptions ?? []).find(
    (e) => e.id === exceptionId || e.uuid === exceptionId,
  );
  return mutate(
    `/companies/${companyId}/exceptions/${x?.uuid ?? exceptionId}/resolve`,
    { decision },
  );
}
export async function runPortfolioAnalysis() {
  const result = await mutate("/portfolio/analysis");
  return result.found;
}
// Interpretation layer: queue one File Reviewer run per company and one Sector
// Merger run per sector, then poll the request until the worker has finished
// every hop. Resolves with the run status plus the opportunities that are new
// relative to the snapshot the analyst was looking at.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export async function runPortfolioInterpretation({
  pollMs = 1500,
  timeoutMs = 180000,
} = {}) {
  const before = new Set(current().opportunities.map((o) => o.id));
  const queued = await api("/portfolio/interpretation", { method: "POST" });
  const deadline = Date.now() + timeoutMs;
  let status = await api(`/portfolio/interpretation/${queued.request_id}`);
  while (!status.done && Date.now() < deadline) {
    await sleep(pollMs);
    status = await api(`/portfolio/interpretation/${queued.request_id}`);
  }
  await refresh();
  return {
    ...status,
    timedOut: !status.done,
    found: current().opportunities.filter((o) => !before.has(o.id)),
  };
}
export const setOpportunityStatus = (id, status) =>
  mutate(`/opportunities/${id}/status`, { status });
export const createTask = (input) => mutate("/tasks", input);
export const updateTask = (id, patch) => mutate(`/tasks/${id}`, patch);
export const setFindingStatus = (id, status) =>
  mutate(`/workspace-findings/${id}/status`, { status });
export const setAgentStatus = (id, status) =>
  mutate(`/workspace-agents/${id}/status`, { status });
export const runAgentNow = (id) => mutate(`/workspace-agents/${id}/run`);

// Evidence rows for an opportunity's evidence references.
export function evidenceRows(ref) {
  const c = company(ref.companyId);
  if (!c) return [];
  if (ref.entity === "purchase")
    return c.purchases.filter((p) => p.sku === ref.sku && inPeriod(p.date));
  if (ref.entity === "subscription")
    return c.subscriptions.filter((s) => s.id === ref.id);
  if (ref.entity === "invoice" && ref.query === "overdue90")
    return companyMetrics(c).overdue90;
  if (ref.entity === "finding")
    return findings(c.id).filter((f) => f.id === ref.id);
  return [];
}
