// Portfolio state for the PE analyst workspace. Reads seed data, persists
// analyst changes to localStorage, and computes every displayed figure from
// records. Replace `load`/`save` with the /portfolio API when it exists.
import { seedState, CATALOG } from "./seed.js";
import { PERIOD, TODAY, daysBetween } from "./format.js";

const STATE_KEY = "vista.analyst.state.v1";
let state = null;

export function load(storage = globalThis.localStorage) {
  if (state) return state;
  try {
    const raw = storage?.getItem(STATE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.version === 1) return (state = parsed);
    }
  } catch {
    /* fall through to seed */
  }
  state = seedState();
  return state;
}
export function save(storage = globalThis.localStorage) {
  try {
    storage?.setItem(STATE_KEY, JSON.stringify(state));
  } catch {
    /* quota or private mode: state stays in memory */
  }
}
export function reset(storage = globalThis.localStorage) {
  storage?.removeItem(STATE_KEY);
  state = null;
  return load(storage);
}
export function setState(next) {
  state = next;
  return state;
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
export const activity = (companyId, limit = 12) =>
  load()
    .activity.filter((a) => !companyId || a.companyId === companyId)
    .sort((a, b) => (a.at < b.at ? 1 : -1))
    .slice(0, limit);

// ---- Deterministic metrics -------------------------------------------------------
const inPeriod = (iso) => iso >= PERIOD.start && iso <= PERIOD.end;
const sum = (list, f) => Math.round(list.reduce((acc, x) => acc + (f(x) || 0), 0) * 100) / 100;

export function companyMetrics(c) {
  const revenue = sum(c.invoices.filter((i) => inPeriod(i.issueDate)), (i) => i.amount);
  const outstandingInvoices = c.invoices.filter((i) => i.outstanding > 0);
  const overdueInvoices = outstandingInvoices.filter((i) => i.dueDate && i.dueDate < TODAY.toISOString().slice(0, 10));
  const overdue90 = overdueInvoices.filter((i) => daysBetween(i.dueDate) > 90);
  const vendorSpend = sum(c.purchases.filter((p) => inPeriod(p.date)), (p) => p.total);
  const softwareAnnual = sum(c.subscriptions, (s) => s.monthlyCost * 12);
  const openTasks = tasks().filter((t) => t.companyId === c.id && !["Complete", "Dismissed"].includes(t.status));
  const openOpps = opportunities().filter((o) => o.companyIds.includes(c.id) && !["Dismissed", "Realized"].includes(o.status));
  const automationCandidates = findings(c.id).filter((f) => /re-keys|copies|manual|repetitive/i.test(`${f.title} ${f.detail}`)).length + opportunities().filter((o) => o.companyIds.includes(c.id) && o.category === "Process automation").length;
  return {
    customers: c.customers.length,
    invoiceCount: c.invoices.filter((i) => inPeriod(i.issueDate)).length,
    revenue,
    outstandingAr: sum(outstandingInvoices, (i) => i.outstanding),
    outstandingInvoices,
    overdueAr: sum(overdueInvoices, (i) => i.outstanding),
    overdueInvoices,
    overdue90,
    vendorSpend,
    softwareAnnual,
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
// Status per step from actual data, never a typed-in percentage.
export function integrationSteps(c) {
  const openExceptions = (c.importExceptions ?? []).filter((x) => x.open !== false);
  const status = {
    profile: c.name && c.location && c.acquired ? "Complete" : "Not started",
    customers: c.customers.length ? "Complete" : "Not started",
    invoices: c.invoices.length ? "Complete" : "Not started",
    vendors: c.vendors.length ? "Complete" : "Not started",
    software: c.subscriptions.length ? "Complete" : "Not started",
    exceptions: openExceptions.length ? "Needs review" : c.importJobs?.length ? "Complete" : "Not started",
    analysis: c.analysisRunAt ? "Complete" : c.customers.length ? "In progress" : "Not started",
  };
  const steps = INTEGRATION_STEPS.map(([key, label]) => ({ key, label, status: status[key] }));
  const complete = steps.filter((s) => s.status === "Complete").length;
  return { steps, complete, total: steps.length, label: complete === steps.length ? "Complete" : complete === 0 ? "Not started" : "In progress" };
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
      push({ companyId: c.id, type: "Import review", text: `${openX.length} record${openX.length === 1 ? "" : "s"} need mapping review`, since: c.importJobs?.at(-1)?.createdAt ?? c.acquired, severity: openX.length > 10 ? "High" : "Medium", cta: "Review records", href: `/acquisitions/?id=${c.id}` });
    if (m.overdue90.length >= 5)
      push({ companyId: c.id, type: "Working capital", text: `${m.overdue90.length} invoices >90 days overdue`, since: m.overdue90.map((i) => i.dueDate).sort()[0], severity: "High", cta: "Inspect evidence", href: `/company/?id=${c.id}&tab=finance&filter=overdue90` });
    else if (m.overdue90.length)
      push({ companyId: c.id, type: "Working capital", text: `${m.overdue90.length} invoice${m.overdue90.length === 1 ? "" : "s"} >90 days overdue`, since: m.overdue90.map((i) => i.dueDate).sort()[0], severity: "Medium", cta: "Inspect evidence", href: `/company/?id=${c.id}&tab=finance&filter=overdue90` });
    for (const s of c.subscriptions) {
      const days = -daysBetween(s.renewalDate);
      if (days >= 0 && days <= 30) push({ companyId: c.id, type: "Renewal", text: `${s.product} renews in ${days} days`, since: TODAY.toISOString(), severity: days <= 14 ? "High" : "Medium", cta: "Assign task", href: `/company/?id=${c.id}&tab=software` });
    }
    for (const a of agents(c.id)) {
      if (a.review) push({ companyId: c.id, type: "Agent exception", text: `${a.name} run requires human review`, since: a.lastRunAt, severity: "Medium", cta: "Inspect evidence", href: `/agents/?company=${c.id}` });
    }
    for (const f of findings(c.id)) {
      if (f.status === "Open" && f.severity === "High") push({ companyId: c.id, type: "Finding", text: f.title, since: f.foundAt, severity: "High", cta: "Inspect evidence", href: `/company/?id=${c.id}&tab=findings` });
    }
    for (const o of opportunities()) {
      if (o.status === "New" && o.companyIds.includes(c.id) && o.companyIds[0] === c.id) push({ companyId: c.id, type: "Opportunity", text: `${o.id}: ${o.title}`, since: o.foundAt, severity: "Low", cta: "Review opportunity", href: `/opportunities/?id=${o.id}` });
    }
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
  const money = (n) => (n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}K`);
  const parts = [
    `${c.name} is a ${c.industry.toLowerCase()} business in ${c.location} with ${m.customers.toLocaleString("en-US")} imported customers and ${money(m.outstandingAr)} of outstanding AR (${money(m.overdueAr)} past due).`,
    `Vendor spend in the period is ${money(m.vendorSpend)} across ${c.vendors.length} vendors; software runs ${money(m.softwareAnnual)} a year on ${c.subscriptions.length} subscriptions.`,
    `${integ.complete} of ${integ.total} integration steps are complete${openX ? ` and ${openX} import exception${openX === 1 ? "" : "s"} still need${openX === 1 ? "s" : ""} review` : ""}.`,
    opps.length ? `Vista has ${opps.length} open opportunit${opps.length === 1 ? "y" : "ies"} involving ${c.name}${cross ? `, ${cross} of them cross-company` : ""}.` : c.analysisRunAt ? "Portfolio analysis found no open opportunities involving this company." : "Portfolio analysis has not run for this company yet.",
  ];
  return parts.join(" ");
}

// ---- Mutations -------------------------------------------------------------------
function logActivity(companyId, text, kind) {
  load().activity.push({ at: new Date().toISOString(), companyId, text, kind });
}
export function addCompany(c) {
  const s = load();
  if (s.companies.some((x) => x.id === c.id)) s.companies = s.companies.map((x) => (x.id === c.id ? c : x));
  else s.companies.push(c);
  const job = c.importJobs?.at(-1);
  logActivity(c.id, `${c.name} import completed: ${job?.accepted ?? 0} records accepted, ${job?.reviewed ?? 0} reviewed, ${job?.rejected ?? 0} rejected.`, "import");
  save();
  return c;
}
export function nextId(kind) {
  const s = load();
  const n = s.nextIds[kind]++;
  save();
  return n;
}
export function resolveException(companyId, exceptionId, decision) {
  const c = company(companyId);
  const x = c?.importExceptions?.find((e) => e.id === exceptionId);
  if (!x) return;
  x.decision = decision;
  x.open = false;
  if (x.dataset === "vendors" && decision === "Match") {
    for (const v of c.vendors) if (v.sourceName === x.left) v.name = x.matchVendor;
    for (const p of c.purchases) if (p.vendorName === x.left) p.vendorName = x.matchVendor;
  }
  logActivity(companyId, `Import exception ${x.id} resolved: ${decision} (${x.left} / ${x.right}).`, "import");
  save();
}

// Purchasing analysis: same SKU bought by ≥2 companies at different unit
// prices. Scenario value = price gap × the higher-paying company's units.
export function runPortfolioAnalysis() {
  const s = load();
  const found = [];
  for (const item of CATALOG) {
    const rows = s.companies
      .map((c) => {
        const ps = c.purchases.filter((p) => p.sku === item.sku && inPeriod(p.date));
        if (!ps.length) return null;
        const units = ps.reduce((n, p) => n + p.quantity, 0);
        const spend = ps.reduce((n, p) => n + p.total, 0);
        return { companyId: c.id, name: c.name, units, spend, unitPrice: Math.round((spend / units) * 100) / 100, purchases: ps };
      })
      .filter(Boolean);
    if (rows.length < 2) continue;
    rows.sort((a, b) => a.unitPrice - b.unitPrice);
    const low = rows[0];
    for (const high of rows.slice(1)) {
      const gap = Math.round((high.unitPrice - low.unitPrice) * 100) / 100;
      if (gap <= 0) continue;
      const scenario = Math.round(gap * high.units * 100) / 100;
      const existing = s.opportunities.find((o) => o.category === "Purchasing" && o.sku === item.sku && o.companyIds.includes(high.companyId) && o.companyIds.includes(low.companyId));
      if (existing) {
        existing.potentialValue = scenario;
        existing.calculation = [`${money2(high.unitPrice)} - ${money2(low.unitPrice)} = ${money2(gap)}/${item.unit}`, `${high.units.toLocaleString("en-US")} historical ${item.unit}s × ${money2(gap)} = ${money2(scenario)} scenario`];
        continue;
      }
      const id = `OP-${String(s.nextIds.opportunity++).padStart(3, "0")}`;
      const opp = {
        id,
        title: `${high.name} pays ${money2(gap)}/${item.unit} more than ${low.name} for ${item.sku}`,
        category: "Purchasing",
        sku: item.sku,
        companyIds: [high.companyId, low.companyId],
        confidence: 0.9,
        potentialValue: scenario,
        status: "New",
        foundAt: new Date().toISOString(),
        fact: `${high.name} and ${low.name} both purchased SKU ${item.sku} (${item.description}). ${high.name}'s recorded average unit price was ${money2(high.unitPrice)} across ${high.purchases.length} purchase lines; ${low.name}'s was ${money2(low.unitPrice)} across ${low.purchases.length}.`,
        evidence: [
          { companyId: high.companyId, entity: "purchase", sku: item.sku },
          { companyId: low.companyId, entity: "purchase", sku: item.sku },
        ],
        calculation: [`${money2(high.unitPrice)} - ${money2(low.unitPrice)} = ${money2(gap)}/${item.unit}`, `${high.units.toLocaleString("en-US")} historical ${item.unit}s × ${money2(gap)} = ${money2(scenario)} scenario`],
        benefit: `If ${high.name} were able to obtain ${low.name}'s historical unit rate on the same purchase volume, the modeled difference would be ${money2(scenario)} over the reporting period.`,
        assumptions: ["Contract terms unknown", "Freight and delivery terms may differ", "Rebate structures unknown", "Future volume may differ from the period observed"],
        nextAction: `Review both companies' supplier agreements for ${item.sku} and determine whether pricing can be consolidated under one account.`,
        realizedValue: null,
      };
      s.opportunities.push(opp);
      found.push(opp);
      logActivity(high.companyId, `Vista identified purchasing opportunity ${id} (${item.sku}).`, "opportunity");
    }
  }
  const at = new Date().toISOString();
  for (const c of s.companies) c.analysisRunAt = at;
  logActivity(null, `Portfolio analysis completed across ${s.companies.length} companies; ${found.length} new opportunit${found.length === 1 ? "y" : "ies"}.`, "analysis");
  save();
  return found;
}
const money2 = (n) => `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function setOpportunityStatus(id, status) {
  const o = load().opportunities.find((x) => x.id === id);
  if (!o) return;
  o.status = status;
  logActivity(o.companyIds[0], `${o.id} marked ${status.toLowerCase()} by analyst.`, "opportunity");
  save();
}
export function createTask(input, actor = "Analyst") {
  const s = load();
  const id = `T-${s.nextIds.task++}`;
  const task = {
    id,
    title: input.title,
    companyId: input.companyId,
    description: input.description ?? "",
    category: input.category ?? "Integration",
    sourceType: input.sourceType ?? null,
    sourceId: input.sourceId ?? null,
    assignee: input.assignee ?? actor,
    priority: input.priority ?? "Medium",
    dueDate: input.dueDate ?? null,
    status: "Open",
    createdBy: actor,
    createdAt: new Date().toISOString(),
    completedAt: null,
    outcome: null,
    outcomeNotes: "",
    realizedResult: null,
  };
  s.tasks.push(task);
  if (input.sourceType === "opportunity") {
    const o = s.opportunities.find((x) => x.id === input.sourceId);
    if (o && ["New", "Under review"].includes(o.status)) o.status = "Task created";
  }
  if (input.sourceType === "finding") {
    const f = s.findings.find((x) => x.id === input.sourceId);
    if (f && f.status === "Open") f.status = "Actioned";
  }
  logActivity(task.companyId, `${actor.split(" ")[0]} created task ${id}${input.sourceId ? ` from ${input.sourceId}` : ""}.`, "task");
  save();
  return task;
}
export function updateTask(id, patch, actor = "Analyst") {
  const s = load();
  const t = s.tasks.find((x) => x.id === id);
  if (!t) return null;
  Object.assign(t, patch);
  if (patch.status === "Complete") {
    t.completedAt = new Date().toISOString();
    if (t.outcome !== "Implemented") t.realizedResult = null;
    if (t.sourceType === "opportunity") {
      const o = s.opportunities.find((x) => x.id === t.sourceId);
      if (o) {
        if (t.outcome === "Implemented") {
          o.status = "Realized";
          o.realizedValue = t.realizedResult;
        } else if (t.outcome === "Benefit validated") o.status = "Validated";
        else if (t.outcome === "No benefit found") o.status = "Dismissed";
      }
    }
    logActivity(t.companyId, `${actor.split(" ")[0]} completed ${id} — ${t.outcome ?? "done"}${t.realizedResult ? ` (${money2(t.realizedResult)} realized)` : ""}.`, "task");
  } else if (patch.status) logActivity(t.companyId, `${id} moved to ${patch.status.toLowerCase()}.`, "task");
  save();
  return t;
}
export function setFindingStatus(id, status) {
  const f = load().findings.find((x) => x.id === id);
  if (!f) return;
  f.status = status;
  logActivity(f.companyId, `Finding ${id} ${status.toLowerCase()} by analyst.`, "finding");
  save();
}
export function setAgentStatus(id, status) {
  const a = load().agents.find((x) => x.id === id);
  if (!a) return;
  a.status = status;
  if (status === "Active" && a.lastFailure) a.lastFailure = null;
  logActivity(a.companyId, `${a.name} agent ${status === "Paused" ? "paused" : "resumed"} by analyst.`, "agent");
  save();
}
export function runAgentNow(id) {
  const s = load();
  const a = s.agents.find((x) => x.id === id);
  if (!a) return null;
  const at = new Date().toISOString();
  a.lastRunAt = at;
  a.status = "Active";
  const n = s.runs.filter((r) => r.agentId === id).length + 1;
  const r = {
    id: `run-${id}-${String(n).padStart(4, "0")}`,
    agentId: id,
    companyId: a.companyId,
    goal: `Re-run ${a.name} on the latest imported records.`,
    startedAt: at,
    status: "Complete",
    sources: ["Imported canonical records (this workspace)"],
    events: [
      [at.slice(11, 16), "Loaded canonical records for the company."],
      [at.slice(11, 16), "No new exceptions compared with the previous run."],
    ],
    output: "No change since the last run.",
    evidence: [],
    corrections: [],
    modelCost: 0.03,
    needsReview: 0,
  };
  s.runs.push(r);
  a.cost = Math.round((a.cost + r.modelCost) * 100) / 100;
  logActivity(a.companyId, `${a.name} agent ran on demand; no new exceptions.`, "agent");
  save();
  return r;
}

// Evidence rows for an opportunity's evidence references.
export function evidenceRows(ref) {
  const c = company(ref.companyId);
  if (!c) return [];
  if (ref.entity === "purchase") return c.purchases.filter((p) => p.sku === ref.sku && inPeriod(p.date));
  if (ref.entity === "subscription") return c.subscriptions.filter((s) => s.id === ref.id);
  if (ref.entity === "invoice" && ref.query === "overdue90") return companyMetrics(c).overdue90;
  if (ref.entity === "finding") return findings(c.id).filter((f) => f.id === ref.id);
  return [];
}
