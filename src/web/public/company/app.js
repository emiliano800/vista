import { mountShell, $, qs, esc, badge, metricStrip, table, enableRowLinks, section, message, mountSourceDialog, statusSelect } from "/lib/components.js";
import { compactMoney, money, integer, monthYear, date, PERIOD, DEMO_NOTE, age, daysBetween } from "/lib/format.js";
import { company, companyMetrics, integrationSteps, companySummary, attentionQueue, activity, tasks, opportunities, findings, agents, runs, setFindingStatus, createTask, resolveException, companyName } from "/lib/store.js";

const TABS = [
  ["overview", "Overview"],
  ["data", "Data"],
  ["customers", "Customers"],
  ["finance", "Finance"],
  ["vendors", "Vendors"],
  ["software", "Software"],
  ["findings", "Findings"],
  ["tasks", "Tasks"],
  ["agents", "Agents"],
];
const analyst = mountShell();
const showSource = mountSourceDialog();
const id = qs().get("id");
const tab = qs().get("tab") ?? "overview";
const c = company(id);
const viewSource = (list) => (r) => `<button class="link sm" data-source="${esc(r.id)}">View source</button>`;
if (analyst) {
  if (!c) $("view").innerHTML = `<div class="page-head"><div><p class="eyebrow">Company</p><h1>Unknown company</h1><p class="muted">No company with id “${esc(id)}” is in this portfolio.</p></div></div><p class="block"><a href="/portfolio/">← Back to portfolio</a></p>`;
  else render();
}

function bindSources(list) {
  $("view").querySelectorAll("[data-source]").forEach((b) => {
    b.onclick = () => {
      const r = list.find((x) => x.id === b.dataset.source);
      showSource(r, r?.name ?? r?.number ?? r?.sku ?? r?.product ?? r?.id);
    };
  });
}

function render() {
  const m = companyMetrics(c);
  const integ = integrationSteps(c);
  document.title = `Vista · ${c.name}`;
  $("view").innerHTML = `
    <p><a class="back" href="/portfolio/">← Portfolio</a></p>
    <div class="page-head">
      <div>
        <p class="eyebrow">${esc(c.industry)}</p>
        <h1>${esc(c.name)}</h1>
        <p class="muted">${esc(c.location)} · Acquired ${esc(monthYear(c.acquired))} · Integration: ${esc(integ.label)} (${esc(integ.complete)} / ${esc(integ.total)} steps)</p>
      </div>
      <div class="page-actions">
        <a class="button quiet" href="/acquisitions/?id=${esc(c.id)}">Import history</a>
        <button id="new-task" class="solid">Create task</button>
      </div>
    </div>
    ${metricStrip([
      { label: "Customers", value: integer(m.customers), href: `/company/?id=${c.id}&tab=customers` },
      { label: "Invoices", value: integer(m.invoiceCount), note: PERIOD.label, href: `/company/?id=${c.id}&tab=finance` },
      { label: "Outstanding AR", value: compactMoney(m.outstandingAr), note: "point in time", href: `/company/?id=${c.id}&tab=finance&filter=outstanding` },
      { label: "Vendor spend", value: compactMoney(m.vendorSpend), note: PERIOD.label, href: `/company/?id=${c.id}&tab=vendors` },
      { label: "Open tasks", value: integer(m.openTasks), href: `/company/?id=${c.id}&tab=tasks` },
      { label: "Automation candidates", value: integer(m.automationCandidates), href: `/company/?id=${c.id}&tab=findings` },
    ])}
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    <nav class="tabs" aria-label="Company sections">${TABS.map(([key, label]) => `<a href="/company/?id=${esc(c.id)}&tab=${key}" ${key === tab ? 'aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
    <div id="tab"></div>`;
  $("new-task").onclick = () => openTaskDialog({ companyId: c.id });
  ({ overview, data, customers, finance, vendors, software, findings: findingsTab, tasks: tasksTab, agents: agentsTab })[tab]?.(m, integ) ?? overview(m, integ);
}

function overview(m, integ) {
  const issues = attentionQueue(c.id);
  $("tab").innerHTML = `
    <div class="two-col block">
      <section>
        <h2>Integration progress</h2>
        <ul class="checklist">${integ.steps.map((s) => `<li><span>${esc(s.label)}</span>${badge(s.status)}</li>`).join("")}</ul>
      </section>
      <section>
        <h2>Company summary</h2>
        <p class="summary-text">${esc(companySummary(c))}</p>
        <small>Generated from imported records, findings and open tasks — figures are computed, not written.</small>
      </section>
    </div>
    ${section(
      "Open issues",
      issues.length
        ? `<ul class="queue">${issues.map((q) => `<li><span class="who">${esc(q.type)}</span><span class="what">${esc(q.text)}</span><span class="age">${esc(q.age)}</span>${badge(q.severity)}<a class="button quiet sm" href="${esc(q.href)}">${esc(q.cta)}</a></li>`).join("")}</ul>`
        : `<p class="empty">No open issues for ${esc(c.name)}.</p>`,
    )}
    ${section(
      "Recent activity",
      `<ul class="feed">${activity(c.id, 10)
        .map((a) => `<li><time>${esc(age(a.at))} ago</time><span class="who">${esc(a.kind)}</span><span class="kind-${esc(a.kind)}">${esc(a.text)}</span></li>`)
        .join("")}</ul>`,
    )}`;
}

function data() {
  const job = c.importJobs?.at(-1);
  const openX = (c.importExceptions ?? []).filter((x) => x.open !== false);
  $("tab").innerHTML = `
    ${section(
      "What Vista knows about this company",
      table(
        [
          { label: "Entity", key: "entity" },
          { label: "Records", num: true, render: (r) => esc(integer(r.count)) },
          { label: "Source files", render: (r) => `<span class="mono">${esc(r.files.join(", ") || "—")}</span>` },
          { label: "Auto-accepted", num: true, render: (r) => esc(integer(r.auto)) },
          { label: "Reviewed", num: true, render: (r) => esc(integer(r.count - r.auto)) },
          { label: "", render: (r) => `<a href="/data/?company=${esc(c.id)}&entity=${esc(r.key)}">Explore →</a>` },
        ],
        [
          ["customers", "Customers", c.customers],
          ["invoices", "Invoices", c.invoices],
          ["vendors", "Vendors", c.vendors],
          ["purchases", "Vendor purchases", c.purchases],
          ["subscriptions", "Subscriptions", c.subscriptions],
        ].map(([key, entity, list]) => ({ key, entity, count: list.length, files: [...new Set(list.map((r) => r.provenance?.file).filter(Boolean))], auto: list.filter((r) => r.provenance?.review === "auto-accepted").length }))
      ),
      { eyebrow: "Canonical records with provenance" },
    )}
    ${section(
      "Import jobs",
      table(
        [
          { label: "Job", render: (j) => `<span class="mono">${esc(j.id)}</span>` },
          { label: "Created", render: (j) => esc(date(j.createdAt)) },
          { label: "Files", render: (j) => `<span class="mono">${esc(j.files.join(", "))}</span>` },
          { label: "Accepted", num: true, render: (j) => esc(integer(j.accepted)) },
          { label: "Reviewed", num: true, render: (j) => esc(integer(j.reviewed)) },
          { label: "Rejected", num: true, render: (j) => esc(integer(j.rejected)) },
        ],
        c.importJobs ?? [],
        { empty: "No imports yet." },
      ),
    )}
    ${section(
      `Open import exceptions (${openX.length})`,
      openX.length
        ? openX
            .map(
              (x) => `<div class="exception" data-id="${esc(x.id)}">
          <div class="pair"><small>${esc(x.type)} · ${esc(Math.round(x.confidence * 100))}%</small><b>${esc(x.left)}</b><small>${x.leftRow ? `row ${esc(x.leftRow)}` : "this import"}</small></div>
          <div class="pair"><small>Potential match</small><b>${esc(x.right)}</b><small>${x.rightRow ? `row ${esc(x.rightRow)}` : "portfolio vendor master"}</small></div>
          <div class="actions">${x.actions.map((a) => `<button class="sm" data-decide="${esc(a)}">${esc(a)}</button>`).join("")}</div></div>`,
            )
            .join("")
        : `<p class="empty">No open exceptions. ${job ? `Last import ${esc(date(job.createdAt))}.` : ""}</p>`,
    )}`;
  $("tab").querySelectorAll("[data-decide]").forEach((b) => {
    b.onclick = () => {
      resolveException(c.id, b.closest(".exception").dataset.id, b.dataset.decide);
      render();
      message(`Exception resolved: ${b.dataset.decide}.`, "success");
    };
  });
}

function customers() {
  const rows = c.customers;
  $("tab").innerHTML = section(
    `Customers (${integer(rows.length)})`,
    table(
      [
        { label: "Customer", render: (r) => `<b>${esc(r.name)}</b>` },
        { label: "Contact", key: "contact" },
        { label: "Email", key: "email" },
        { label: "Phone", key: "phone", mono: true },
        { label: "City / State", render: (r) => esc(`${r.city}${r.state ? `, ${r.state}` : ""}`) },
        { label: "Service type", key: "serviceType" },
        { label: "Status", render: (r) => badge(r.status, r.status === "active" ? "success" : "") },
        { label: "", render: viewSource(rows) },
      ],
      rows,
    ),
    { eyebrow: "Standardized customer records" },
  );
  bindSources(rows);
}

function finance(m) {
  const filter = qs().get("filter") ?? "all";
  const sets = {
    all: [c.invoices, "All invoices"],
    outstanding: [m.outstandingInvoices, "Outstanding balances"],
    overdue: [m.overdueInvoices, "Past due"],
    overdue90: [m.overdue90, "More than 90 days overdue"],
  };
  const [rows, label] = sets[filter] ?? sets.all;
  const sorted = [...rows].sort((a, b) => (a.issueDate < b.issueDate ? 1 : -1));
  $("tab").innerHTML = `
    ${metricStrip([
      { label: "Invoiced", value: compactMoney(m.revenue), note: PERIOD.label, href: `/company/?id=${c.id}&tab=finance` },
      { label: "Outstanding AR", value: money(m.outstandingAr), href: `/company/?id=${c.id}&tab=finance&filter=outstanding` },
      { label: "Overdue AR", value: money(m.overdueAr), href: `/company/?id=${c.id}&tab=finance&filter=overdue` },
      { label: ">90 days", value: money(m.overdue90.reduce((n, i) => n + i.outstanding, 0)), note: `${m.overdue90.length} invoices`, href: `/company/?id=${c.id}&tab=finance&filter=overdue90` },
    ])}
    <div class="filters">${Object.entries(sets)
      .map(([k, [list, l]]) => `<a class="button quiet sm" href="/company/?id=${esc(c.id)}&tab=finance&filter=${k}" ${k === filter ? 'aria-current="page"' : ""}>${esc(l)} (${esc(list.length)})</a>`)
      .join("")}</div>
    ${section(
      `${label} (${integer(sorted.length)})`,
      table(
        [
          { label: "Invoice", render: (r) => `<span class="mono">${esc(r.number)}</span>` },
          { label: "Customer", key: "customerName" },
          { label: "Issued", render: (r) => esc(date(r.issueDate)) },
          { label: "Due", render: (r) => esc(date(r.dueDate)) },
          { label: "Days past due", num: true, render: (r) => (r.outstanding > 0 && daysBetween(r.dueDate) > 0 ? esc(daysBetween(r.dueDate)) : "—") },
          { label: "Amount", num: true, render: (r) => esc(money(r.amount)) },
          { label: "Outstanding", num: true, render: (r) => esc(money(r.outstanding)) },
          { label: "Status", render: (r) => badge(r.status) },
          { label: "", render: viewSource(sorted) },
        ],
        sorted.slice(0, 250),
      ),
      { aside: sorted.length > 250 ? `Showing 250 of ${integer(sorted.length)}` : "" },
    )}`;
  bindSources(sorted);
}

function vendors(m) {
  const byVendor = c.vendors.map((v) => {
    const ps = c.purchases.filter((p) => p.vendorId === v.id);
    return { ...v, lines: ps.length, spend: ps.reduce((n, p) => n + p.total, 0), skus: new Set(ps.map((p) => p.sku)).size };
  });
  const purchases = [...c.purchases].sort((a, b) => (a.date < b.date ? 1 : -1));
  $("tab").innerHTML = `
    ${section(
      `Vendors (${c.vendors.length})`,
      table(
        [
          { label: "Vendor", render: (v) => `<b>${esc(v.name)}</b>${v.sourceName !== v.name ? `<small>source: ${esc(v.sourceName)}</small>` : ""}` },
          { label: "Purchase lines", num: true, render: (v) => esc(integer(v.lines)) },
          { label: "Distinct SKUs", num: true, render: (v) => esc(integer(v.skus)) },
          { label: "Spend", num: true, render: (v) => esc(money(v.spend)) },
          { label: "Match", render: (v) => badge(v.provenance?.confidence >= 0.9 ? "Verified" : "Needs review") },
          { label: "", render: viewSource(byVendor) },
        ],
        byVendor,
      ),
      { eyebrow: `Vendor spend ${money(m.vendorSpend)} · ${PERIOD.label}` },
    )}
    ${section(
      `Vendor purchases (${integer(purchases.length)})`,
      table(
        [
          { label: "Date", render: (p) => esc(date(p.date)) },
          { label: "Vendor", key: "vendorName" },
          { label: "SKU", render: (p) => `<span class="mono">${esc(p.sku)}</span>` },
          { label: "Description", key: "description" },
          { label: "Qty", num: true, render: (p) => `${esc(p.quantity)} ${esc(p.unit)}` },
          { label: "Unit price", num: true, render: (p) => esc(money(p.unitPrice)) },
          { label: "Total", num: true, render: (p) => esc(money(p.total)) },
          { label: "", render: viewSource(purchases) },
        ],
        purchases.slice(0, 200),
      ),
    )}`;
  bindSources([...byVendor, ...purchases]);
}

function software() {
  const rows = c.subscriptions;
  const annual = rows.reduce((n, s) => n + s.monthlyCost * 12, 0);
  $("tab").innerHTML = section(
    `Software subscriptions (${rows.length})`,
    table(
      [
        { label: "Product", render: (s) => `<b>${esc(s.product)}</b>` },
        { label: "Category", key: "category" },
        { label: "Monthly cost", num: true, render: (s) => esc(money(s.monthlyCost)) },
        { label: "Seats", num: true, key: "seats" },
        { label: "Renewal", render: (s) => `${esc(date(s.renewalDate))}${-daysBetween(s.renewalDate) <= 30 && -daysBetween(s.renewalDate) >= 0 ? ` ${badge(`${-daysBetween(s.renewalDate)} days`, "danger")}` : ""}` },
        { label: "Contract notes", render: (s) => esc(s.notes || "—") },
        { label: "", render: viewSource(rows) },
      ],
      rows,
    ),
    { eyebrow: `${money(annual)} annualized from monthly cost × 12` },
  );
  bindSources(rows);
}

function findingsTab() {
  const rows = findings(c.id);
  const agentName = (id) => agents(c.id).find((a) => a.id === id)?.name ?? id;
  $("tab").innerHTML = section(
    `Findings (${rows.length})`,
    table(
      [
        { label: "Finding", render: (f) => `<b>${esc(f.title)}</b><small>${esc(f.detail)}</small>` },
        { label: "Agent · run", render: (f) => `${esc(agentName(f.agentId))}<br><a class="mono" href="/agents/?run=${esc(f.runId)}">${esc(f.runId)}</a>` },
        { label: "Found", render: (f) => esc(date(f.foundAt)) },
        { label: "Severity", render: (f) => badge(f.severity) },
        { label: "Status", render: (f) => statusSelect(f.id, ["Open", "Reviewed", "Dismissed", "Actioned"], f.status) },
        { label: "", render: (f) => `<button class="sm" data-task="${esc(f.id)}">Convert to task</button>` },
      ],
      rows,
      { empty: "No findings yet. Agents post findings here after each run." },
    ),
    { eyebrow: "A finding is something an agent discovered inside this company; opportunities live at portfolio level." },
  );
  $("tab").querySelectorAll("[data-status]").forEach((s) => {
    s.onchange = () => {
      setFindingStatus(s.dataset.status, s.value);
      message(`Finding ${s.dataset.status} marked ${s.value.toLowerCase()}.`, "success");
    };
  });
  $("tab").querySelectorAll("[data-task]").forEach((b) => {
    b.onclick = () => {
      const f = rows.find((x) => x.id === b.dataset.task);
      openTaskDialog({ companyId: c.id, title: f.title, description: f.detail, category: "Integration", sourceType: "finding", sourceId: f.id, priority: f.severity });
    };
  });
}

function tasksTab() {
  const rows = tasks().filter((t) => t.companyId === c.id);
  $("tab").innerHTML = section(
    `Tasks (${rows.length})`,
    table(
      [
        { label: "Task", render: (t) => `<a href="/tasks/?id=${esc(t.id)}"><b>${esc(t.title)}</b></a><small class="mono">${esc(t.id)} · ${esc(t.category)}</small>` },
        { label: "Source", render: (t) => (t.sourceId ? `<span class="mono">${esc(t.sourceId)}</span>` : "—") },
        { label: "Assignee", key: "assignee" },
        { label: "Priority", render: (t) => badge(t.priority) },
        { label: "Due", render: (t) => esc(date(t.dueDate)) },
        { label: "Status", render: (t) => badge(t.status) },
      ],
      rows,
      { rowHref: (t) => `/tasks/?id=${t.id}`, empty: "No tasks for this company." },
    ),
    { aside: `<a href="/tasks/?company=${esc(c.id)}">Open in tracker →</a>` },
  );
}

function agentsTab() {
  const list = agents(c.id);
  const recent = runs(c.id).sort((a, b) => (a.startedAt < b.startedAt ? 1 : -1));
  $("tab").innerHTML = `
    ${section(
      `Agents (${list.length})`,
      list.length
        ? table(
            [
              { label: "Agent", render: (a) => `<b>${esc(a.name)}</b><small>${esc(a.represents)}</small>` },
              { label: "Status", render: (a) => badge(a.status) },
              { label: "Last run", render: (a) => `${esc(age(a.lastRunAt))} ago` },
              { label: "Cases", num: true, render: (a) => esc(integer(a.cases)) },
              { label: "Need review", num: true, render: (a) => (a.review ? `<span class="attn">${esc(a.review)}</span>` : "0") },
              { label: "Findings", num: true, key: "findings" },
              { label: "Run cost", num: true, render: (a) => esc(money(a.cost)) },
            ],
            list,
            { rowHref: (a) => `/agents/?company=${c.id}` },
          )
        : `<p class="empty">No agents are deployed at ${esc(c.name)} yet. Run the initial analysis from the Agents page.</p>`,
    )}
    ${section(
      "Recent runs",
      table(
        [
          { label: "Run", render: (r) => `<a class="mono" href="/agents/?run=${esc(r.id)}">${esc(r.id)}</a>` },
          { label: "Goal", key: "goal" },
          { label: "Started", render: (r) => esc(date(r.startedAt)) },
          { label: "Status", render: (r) => badge(r.status) },
          { label: "Cost", num: true, render: (r) => esc(money(r.modelCost)) },
        ],
        recent,
        { rowHref: (r) => `/agents/?run=${r.id}`, empty: "No runs yet." },
      ),
    )}`;
}

// ---- Create task dialog ----------------------------------------------------------
function openTaskDialog(prefill = {}) {
  let dialog = $("task-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "task-dialog";
    dialog.className = "form-dialog";
    document.body.append(dialog);
  }
  const opts = (list, cur) => list.map((o) => `<option${o === cur ? " selected" : ""}>${esc(o)}</option>`).join("");
  dialog.innerHTML = `<form class="dialog-body" method="dialog">
    <div class="dialog-head"><div><p class="eyebrow">New task · ${esc(companyName(prefill.companyId))}</p><h2>Create integration task</h2></div><button type="button" class="ghost sm" data-close>Close</button></div>
    <div class="form-grid">
      <label class="full">Title<input name="title" required value="${esc(prefill.title ?? "")}" /></label>
      <label class="full">Description<textarea name="description">${esc(prefill.description ?? "")}</textarea></label>
      <label>Category<select name="category">${opts(["Integration", "Opportunity follow-up", "Agent exception", "Data quality"], prefill.category)}</select></label>
      <label>Priority<select name="priority">${opts(["High", "Medium", "Low"], prefill.priority ?? "Medium")}</select></label>
      <label>Assignee<select name="assignee">${opts([analyst.name, "Miguel Torres", "Priya Raman", "Company controller"], analyst.name)}</select></label>
      <label>Due date<input name="dueDate" type="date" value="2026-10-03" /></label>
    </div>
    ${prefill.sourceId ? `<small>Source: <span class="mono">${esc(prefill.sourceType)} ${esc(prefill.sourceId)}</span> — the task keeps this link.</small>` : ""}
    <div class="form-actions"><button class="primary" value="save">Create task</button></div>
  </form>`;
  dialog.querySelector("[data-close]").onclick = () => dialog.close("cancel");
  dialog.onclose = () => {
    if (dialog.returnValue !== "save") return;
    const f = new FormData(dialog.querySelector("form"));
    const task = createTask({ ...prefill, title: f.get("title"), description: f.get("description"), category: f.get("category"), priority: f.get("priority"), assignee: f.get("assignee"), dueDate: f.get("dueDate") }, analyst.name);
    render();
    message(`Task ${task.id} created.`, "success", { href: `/tasks/?id=${task.id}`, label: "Open in tracker →" });
  };
  dialog.showModal();
}
enableRowLinks();
