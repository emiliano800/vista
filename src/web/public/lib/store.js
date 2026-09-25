// Portfolio state for the PE analyst workspace. The browser holds a cached
// snapshot of GET /api/portfolio?records=false (firm-scoped, computed by the
// backend from canonical records); every figure shown is read from that
// snapshot and every change goes through the API, then the snapshot is
// refreshed. The canonical rows themselves (invoices, purchases, …) are not in
// the snapshot: a page that shows them asks `loadRecords` for one company and
// collection at a time, and the rows are attached to that company object.
//
// Every analyst page is its own document, so the snapshot is also kept in
// sessionStorage with the API's ETag: the next page renders from that copy at
// once and revalidates in the background (a 304 means nothing changed; a 200
// replaces the snapshot and calls `onRefresh` listeners). Nothing in browser
// storage is authoritative — the server session decides what is returned.
import { api } from "./auth.js";
import { PERIOD, TODAY, daysBetween, setClock } from "./format.js";

export const SNAPSHOT_KEY = "vista.analyst.snapshot";
const SNAPSHOT_PATH = "/portfolio?records=false";

let state = null;
let pending = null; // the in-flight snapshot fetch, shared by concurrent callers
let etag = null; // validator of the snapshot in `state`
const records = new Map(); // companyId -> { invoices: [...], ... } fetched so far
const recordFetches = new Map(); // `${companyId}:${route}` -> in-flight promise
const listeners = new Set();

const storage = () => {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
};
function readCache() {
  try {
    const raw = storage()?.getItem(SNAPSHOT_KEY);
    const cached = raw ? JSON.parse(raw) : null;
    return cached?.snapshot?.companies ? cached : null;
  } catch {
    return null;
  }
}
function writeCache(snapshot, tag) {
  try {
    storage()?.setItem(SNAPSHOT_KEY, JSON.stringify({ etag: tag, snapshot }));
  } catch {
    /* quota or private mode: the page still works from memory */
  }
}
export function clearCache() {
  try {
    storage()?.removeItem(SNAPSHOT_KEY);
  } catch {
    /* nothing to clear */
  }
}

// Called when a background revalidation replaced the snapshot; pages re-render.
export function onRefresh(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function fetchSnapshot() {
  const res = await api(SNAPSHOT_PATH, {
    meta: true,
    headers: etag ? { "If-None-Match": etag } : {},
  });
  if (res.status === 304) return false;
  etag = res.etag ?? null;
  writeCache(res.body, etag); // before records are attached to the companies
  setState(res.body);
  return true;
}
function fetchLatest(notify) {
  if (!pending)
    pending = fetchSnapshot()
      .then((changed) => {
        if (changed && notify) for (const fn of listeners) fn(state);
        return state;
      })
      .finally(() => (pending = null));
  return pending;
}

// Resolves once a snapshot is in memory. With a cached copy it resolves at
// once and revalidates behind the page; `force` always fetches (after a
// mutation), even when a revalidation is already in flight.
export async function load(force = false) {
  if (state && !force) return state;
  if (!state && !force) {
    const cached = readCache();
    if (cached) {
      etag = cached.etag ?? null;
      setState(cached.snapshot);
      fetchLatest(true).catch(() => state);
      return state;
    }
  }
  if (force && pending)
    return pending.then(
      () => fetchLatest(false),
      () => fetchLatest(false),
    );
  return fetchLatest(false);
}
export const refresh = () => load(true);
export function setState(next) {
  state = next;
  if (!state) {
    etag = null;
    records.clear();
    recordFetches.clear();
    return state;
  }
  setClock(state.today, state.period);
  for (const c of state.companies ?? []) {
    const rows = records.get(c.id);
    if (rows) Object.assign(c, rows);
  }
  return state;
}
function current() {
  if (!state)
    throw new Error("Portfolio state has not been loaded; await load() first.");
  return state;
}

// ---- Canonical rows, fetched per company and collection ----------------------
// Snapshot collection -> API route. Purchase orders and their lines share one route.
export const RECORD_ROUTES = {
  customers: "customers",
  invoices: "invoices",
  vendors: "vendors",
  purchases: "purchases",
  subscriptions: "subscriptions",
  policies: "policies",
  purchaseOrders: "purchase-orders",
  purchaseOrderLines: "purchase-orders",
  inventory: "inventory",
};
export const recordsLoaded = (companyId, kinds) => {
  const c = company(companyId);
  return !!c && kinds.every((k) => Array.isArray(c[k]));
};
function attach(companyId, route, body) {
  const rows =
    route === "purchase-orders"
      ? { purchaseOrders: body.purchaseOrders, purchaseOrderLines: body.lines }
      : {
          [Object.keys(RECORD_ROUTES).find((k) => RECORD_ROUTES[k] === route)]:
            body,
        };
  records.set(companyId, { ...(records.get(companyId) ?? {}), ...rows });
  const c = company(companyId);
  if (c) Object.assign(c, rows);
}
// Fetches the collections of one company that are not loaded yet; resolves to
// the company with the rows attached. Concurrent callers share one request.
export async function loadRecords(companyId, kinds) {
  const c = company(companyId);
  if (!c) return null;
  const routes = [
    ...new Set(
      kinds.filter((k) => !Array.isArray(c[k])).map((k) => RECORD_ROUTES[k]),
    ),
  ];
  await Promise.all(
    routes.map((route) => {
      const key = `${c.id}:${route}`;
      if (!recordFetches.has(key))
        recordFetches.set(
          key,
          api(`/companies/${c.id}/${route}`)
            .then((body) => attach(c.id, route, body))
            .catch((error) => {
              recordFetches.delete(key);
              throw error;
            }),
        );
      return recordFetches.get(key);
    }),
  );
  return company(companyId);
}
export const loadPortfolioRecords = (kinds, companyIds = null) =>
  Promise.all(
    current()
      .companies.filter((c) => !companyIds || companyIds.includes(c.id))
      .map((c) => loadRecords(c.id, kinds)),
  );
// Drops one company's rows (an import decision changed them); the next page
// that needs them fetches again.
export function forgetRecords(companyId) {
  records.delete(companyId);
  for (const key of [...recordFetches.keys()])
    if (key.startsWith(`${companyId}:`)) recordFetches.delete(key);
  const c = company(companyId);
  if (c) for (const k of Object.keys(RECORD_ROUTES)) delete c[k];
}
// One row with its full provenance (original and normalized values).
export const recordDetail = (companyId, kind, recordId) =>
  api(`/companies/${companyId}/records/${kind}/${recordId}`);

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
// Agents are derived server-side from the ledger: one per (agent key, company) that
// has run; firm-level runs (the Sector Merger's) carry companyId null.
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
export async function resolveException(companyId, exceptionId, decision) {
  const x = (company(companyId)?.importExceptions ?? []).find(
    (e) => e.id === exceptionId || e.uuid === exceptionId,
  );
  const result = await mutate(
    `/companies/${companyId}/exceptions/${x?.uuid ?? exceptionId}/resolve`,
    { decision },
  );
  forgetRecords(companyId); // a merge decision rewrites the canonical rows
  return result;
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
// Finding triage lands on the same ledger row the company workspace and the agents
// read; `id` is the display ref (F-012) or the finding uuid.
export const setFindingStatus = (id, status) =>
  mutate(`/findings/${id}/status`, { status });

// Evidence rows for an opportunity's evidence references.
export function evidenceRows(ref) {
  const c = company(ref.companyId);
  if (!c) return [];
  // Rows are present only once `loadRecords` fetched that collection.
  if (ref.entity === "purchase")
    return (c.purchases ?? []).filter(
      (p) => p.sku === ref.sku && inPeriod(p.date),
    );
  if (ref.entity === "subscription")
    return (c.subscriptions ?? []).filter((s) => s.id === ref.id);
  if (ref.entity === "invoice" && ref.query === "overdue90")
    return companyMetrics(c).overdue90;
  if (ref.entity === "finding")
    return findings(c.id).filter((f) => f.id === ref.id);
  return [];
}
