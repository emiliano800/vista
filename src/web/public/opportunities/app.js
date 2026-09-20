import { mountShell, $, qs, esc, badge, table, enableRowLinks, section, message, mountSourceDialog, companyLink } from "/lib/components.js";
import { money, compactMoney, integer, date, percent, DEMO_NOTE, PERIOD } from "/lib/format.js";
import { opportunities, company, companyName, tasks, evidenceRows, setOpportunityStatus, createTask } from "/lib/store.js";

const CATEGORIES = ["All", "Purchasing", "Software", "Cross-sell / revenue", "Working capital", "Process automation"];
const STATUSES = ["New", "Under review", "Task created", "Validated", "Dismissed", "Realized"];
const analyst = await mountShell();
const showSource = mountSourceDialog();
const id = qs().get("id");

const valueCell = (o) =>
  o.status === "Realized" && o.realizedValue != null
    ? `<span class="value-label">Realized</span>${esc(money(o.realizedValue))}`
    : o.potentialValue != null
      ? `<span class="value-label">Potential</span>${esc(money(o.potentialValue))}`
      : `<span class="muted">Not quantified</span>`;
if (analyst) (id ? detail : list)();

function list() {
  const cat = qs().get("category") ?? "All";
  const status = qs().get("status") ?? "open";
  let rows = opportunities();
  if (cat !== "All") rows = rows.filter((o) => o.category === cat);
  if (status === "open") rows = rows.filter((o) => !["Dismissed", "Realized"].includes(o.status));
  else if (status !== "all") rows = rows.filter((o) => o.status === status);
  rows = [...rows].sort((a, b) => (a.foundAt < b.foundAt ? 1 : -1));
  const potential = rows.filter((o) => o.status !== "Realized").reduce((n, o) => n + (o.potentialValue ?? 0), 0);
  const realized = opportunities().filter((o) => o.status === "Realized").reduce((n, o) => n + (o.realizedValue ?? 0), 0);
  const link = (k, v) => {
    const p = new URLSearchParams({ category: cat, status });
    p.set(k, v);
    return `/opportunities/?${p}`;
  };
  $("view").innerHTML = `
    <div class="page-head">
      <div><p class="eyebrow">${esc(analyst.firm)}</p><h1>Opportunities</h1><p class="muted">Cross-company facts Vista found in imported records. A potential benefit is a scenario until a task proves it.</p></div>
      <div class="page-actions"><a class="button quiet" href="/portfolio/">Run analysis from portfolio →</a></div>
    </div>
    <div class="kpi-strip">
      <div class="kpi"><span>Listed</span><b>${esc(integer(rows.length))}</b></div>
      <div class="kpi"><span>Potential (scenario)</span><b>${esc(compactMoney(potential))}</b><small>sum of unrealized potential in this view</small></div>
      <div class="kpi"><span>Realized (verified)</span><b>${esc(compactMoney(realized))}</b><small>only from tasks completed as Implemented</small></div>
    </div>
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    <div class="filters">${CATEGORIES.map((c) => `<a class="button quiet sm" href="${link("category", c)}" ${c === cat ? 'aria-current="page"' : ""}>${esc(c)}</a>`).join("")}</div>
    <div class="filters">${[["open", "Open"], ["all", "All"], ...STATUSES.map((s) => [s, s])].map(([k, l]) => `<a class="button quiet sm" href="${link("status", k)}" ${k === status ? 'aria-current="page"' : ""}>${esc(l)}</a>`).join("")}</div>
    ${table(
      [
        { label: "Opportunity", render: (o) => `<b>${esc(o.title)}</b><small class="mono">${esc(o.id)} · found ${esc(date(o.foundAt))}</small>` },
        { label: "Category", key: "category" },
        { label: "Companies", render: (o) => o.companyIds.map(companyName).map(esc).join(", ") },
        { label: "Confidence", num: true, render: (o) => esc(percent(o.confidence)) },
        { label: "Value", num: true, render: valueCell },
        { label: "Status", render: (o) => badge(o.status) },
      ],
      rows,
      { rowHref: (o) => `/opportunities/?id=${o.id}`, empty: "No opportunities match this view. Run portfolio analysis to look for new ones." },
    )}`;
}

function detail() {
  const o = opportunities().find((x) => x.id === id);
  if (!o) {
    $("view").innerHTML = `<div class="page-head"><div><h1>Unknown opportunity</h1></div></div><p class="block"><a href="/opportunities/">← Opportunities</a></p>`;
    return;
  }
  const linked = tasks().filter((t) => t.sourceType === "opportunity" && t.sourceId === o.id);
  const evidence = o.evidence.map((ref) => ({ ref, rows: evidenceRows(ref) }));
  document.title = `Vista · ${o.id}`;
  $("view").innerHTML = `
    <p><a class="back" href="/opportunities/">← Opportunities</a></p>
    <div class="page-head">
      <div><p class="eyebrow">${esc(o.category)} · <span class="mono">${esc(o.id)}</span> · found ${esc(date(o.foundAt))}</p><h1>${esc(o.title)}</h1><p class="muted">Involves ${o.companyIds.map((cid) => companyLink(company(cid) ?? { id: cid, name: cid })).join(" and ")} · confidence ${esc(percent(o.confidence))} · ${badge(o.status)}</p></div>
      <div class="page-actions">
        <select id="status" class="sm">${STATUSES.map((s) => `<option ${s === o.status ? "selected" : ""} ${["Realized", "Validated"].includes(s) && o.status !== s ? "disabled" : ""}>${esc(s)}</option>`).join("")}</select>
        <button id="create-task" class="accent" ${linked.some((t) => !["Complete", "Dismissed"].includes(t.status)) ? "disabled" : ""}>Create integration task</button>
      </div>
    </div>
    <div class="opp-sections">
      <section><p class="eyebrow">Observed fact</p><p class="fact">${esc(o.fact)}</p></section>
      <section><p class="eyebrow">Evidence · source records</p>
        ${evidence
          .map(({ ref, rows }) => {
            const c = company(ref.companyId);
            const label = `${c?.name ?? ref.companyId} · ${ref.entity}${ref.sku ? ` ${ref.sku}` : ""}${ref.query ? ` (${ref.query})` : ""}`;
            return `<h3 class="ev-title">${esc(label)} <small>${rows.length} record${rows.length === 1 ? "" : "s"}${ref.entity === "purchase" ? ` · ${PERIOD.label}` : ""}</small></h3>${evidenceTable(ref.entity, rows)}`;
          })
          .join("")}
      </section>
      <section><p class="eyebrow">Deterministic calculation</p><pre class="calc">${o.calculation.map(esc).join("\n")}</pre></section>
      <section><p class="eyebrow">Potential benefit</p><p class="fact">${esc(o.benefit)}</p>
        <p>${valueCell(o)}${o.status === "Realized" ? ` <small>Verified by ${esc(linked.find((t) => t.outcome === "Implemented")?.id ?? "completed task")}</small>` : o.potentialValue != null ? ` <small>Scenario over ${esc(PERIOD.label)}; not a realized saving.</small>` : ""}</p></section>
      <section><p class="eyebrow">Assumptions & missing information</p><ul class="assumptions">${o.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></section>
      <section><p class="eyebrow">Recommended next action</p><p class="fact">${esc(o.nextAction)}</p></section>
      ${section(
        `Linked tasks (${linked.length})`,
        table(
          [
            { label: "Task", render: (t) => `<b>${esc(t.title)}</b><small class="mono">${esc(t.id)}</small>` },
            { label: "Assignee", key: "assignee" },
            { label: "Due", render: (t) => esc(date(t.dueDate)) },
            { label: "Status", render: (t) => badge(t.status) },
            { label: "Outcome", render: (t) => (t.outcome ? badge(t.outcome) : "—") },
          ],
          linked,
          { rowHref: (t) => `/tasks/?id=${t.id}`, empty: "No task yet. Creating one moves this opportunity to Task created." },
        ),
      )}
    </div>`;
  $("status").onchange = (e) => {
    setOpportunityStatus(o.id, e.target.value);
    detail();
    message(`${o.id} marked ${e.target.value.toLowerCase()}.`, "success");
  };
  $("create-task").onclick = () => {
    const first = company(o.companyIds[0]);
    const t = createTask(
      {
        title: `Follow up: ${o.title}`,
        companyId: o.companyIds[0],
        description: `${o.nextAction}\n\nSource opportunity ${o.id}: ${o.fact}`,
        category: "Opportunity follow-up",
        sourceType: "opportunity",
        sourceId: o.id,
        assignee: analyst.name,
        priority: o.potentialValue >= 5000 ? "High" : "Medium",
        dueDate: "2026-10-03",
      },
      analyst.name,
    );
    detail();
    message(`Task ${t.id} created for ${first?.name ?? "portfolio"} and linked to ${o.id}.`, "success", { href: `/tasks/?id=${t.id}`, label: "Open task →" });
  };
  $("view").querySelectorAll("[data-source]").forEach((b) => {
    b.onclick = () => {
      const r = evidence.flatMap((e) => e.rows).find((x) => x.id === b.dataset.source);
      showSource(r, r?.number ?? r?.sku ?? r?.product ?? r?.title ?? r?.id);
    };
  });
}

function evidenceTable(entity, rows) {
  const src = { label: "", render: (r) => (r.provenance ? `<button class="link sm" data-source="${esc(r.id)}">View source</button>` : "") };
  if (entity === "purchase")
    return table(
      [
        { label: "Date", render: (r) => esc(date(r.date)) },
        { label: "Vendor", key: "vendorName" },
        { label: "SKU", render: (r) => `<span class="mono">${esc(r.sku)}</span>` },
        { label: "Qty", num: true, render: (r) => `${esc(r.quantity)} ${esc(r.unit)}` },
        { label: "Unit price", num: true, render: (r) => esc(money(r.unitPrice)) },
        { label: "Total", num: true, render: (r) => esc(money(r.total)) },
        src,
      ],
      rows,
    );
  if (entity === "subscription")
    return table(
      [
        { label: "Product", key: "product" },
        { label: "Seats", num: true, key: "seats" },
        { label: "Monthly", num: true, render: (r) => esc(money(r.monthlyCost)) },
        { label: "Renewal", render: (r) => esc(date(r.renewalDate)) },
        { label: "Notes", key: "notes" },
        src,
      ],
      rows,
    );
  if (entity === "invoice")
    return table(
      [
        { label: "Invoice", render: (r) => `<span class="mono">${esc(r.number)}</span>` },
        { label: "Customer", key: "customerName" },
        { label: "Due", render: (r) => esc(date(r.dueDate)) },
        { label: "Outstanding", num: true, render: (r) => esc(money(r.outstanding)) },
        { label: "Status", render: (r) => badge(r.status) },
        src,
      ],
      rows,
    );
  if (entity === "finding")
    return table(
      [
        { label: "Finding", render: (r) => `<b>${esc(r.title)}</b><small>${esc(r.detail)}</small>` },
        { label: "Run", render: (r) => `<a class="mono" href="/agents/?run=${esc(r.runId)}">${esc(r.runId)}</a>` },
        { label: "Severity", render: (r) => badge(r.severity) },
      ],
      rows,
    );
  return `<p class="empty">No evidence rows.</p>`;
}
enableRowLinks();
