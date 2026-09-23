import {
  mountShell,
  $,
  qs,
  esc,
  badge,
  metricStrip,
  table,
  enableRowLinks,
  section,
  message,
  mountSourceDialog,
  statusSelect,
} from "/lib/components.js";
import {
  compactMoney,
  money,
  integer,
  monthYear,
  date,
  PERIOD,
  DEMO_NOTE,
  age,
  daysBetween,
} from "/lib/format.js";
import {
  company,
  companyMetrics,
  integrationSteps,
  companySummary,
  attentionQueue,
  activity,
  tasks,
  opportunities,
  findings,
  agents,
  runs,
  setFindingStatus,
  createTask,
  resolveException,
  companyName,
} from "/lib/store.js";
import { api } from "/lib/auth.js";

const TABS = [
  ["overview", "Overview"],
  ["data", "Data"],
  ["customers", "Customers"],
  ["finance", "Finance"],
  ["vendors", "Vendors"],
  ["software", "Software"],
  ["policies", "Policies"],
  ["purchasing", "Purchase orders"],
  ["inventory", "Inventory"],
  ["findings", "Findings"],
  ["recordings", "Recordings"],
  ["tasks", "Tasks"],
  ["agents", "Agents"],
];
// Sector-specific tabs only appear once the company has rows of that kind.
const HIDE_EMPTY = {
  policies: "policies",
  purchasing: "purchaseOrders",
  inventory: "inventory",
};
const analyst = await mountShell();
const showSource = mountSourceDialog();
const id = qs().get("id");
const tab = qs().get("tab") ?? "overview";
let c = null;
const viewSource = (list) => (r) =>
  `<button class="link sm" data-source="${esc(r.id)}">View source</button>`;
if (analyst) {
  c = company(id);
  if (!c)
    $("view").innerHTML =
      `<div class="page-head"><div><p class="eyebrow">Company</p><h1>Unknown company</h1><p class="muted">No company with id “${esc(id)}” is in this portfolio.</p></div></div><p class="block"><a href="/portfolio/">← Back to portfolio</a></p>`;
  else render();
}

function bindSources(list) {
  $("view")
    .querySelectorAll("[data-source]")
    .forEach((b) => {
      b.onclick = () => {
        const r = list.find((x) => x.id === b.dataset.source);
        showSource(
          r,
          r?.name ??
            r?.number ??
            r?.sku ??
            r?.product ??
            r?.policyNumber ??
            r?.poNumber ??
            r?.itemId ??
            r?.id,
        );
      };
    });
}

function render() {
  c = company(id);
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
      {
        label: "Customers",
        value: integer(m.customers),
        href: `/company/?id=${c.id}&tab=customers`,
      },
      {
        label: "Invoices",
        value: integer(m.invoiceCount),
        note: PERIOD.label,
        href: `/company/?id=${c.id}&tab=finance`,
      },
      {
        label: "Outstanding AR",
        value: compactMoney(m.outstandingAr),
        note: "point in time",
        href: `/company/?id=${c.id}&tab=finance&filter=outstanding`,
      },
      {
        label: "Vendor spend",
        value: compactMoney(m.vendorSpend),
        note: PERIOD.label,
        href: `/company/?id=${c.id}&tab=vendors`,
      },
      {
        label: "Open tasks",
        value: integer(m.openTasks),
        href: `/company/?id=${c.id}&tab=tasks`,
      },
      {
        label: "Automation candidates",
        value: integer(m.automationCandidates),
        href: `/company/?id=${c.id}&tab=findings`,
      },
    ])}
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    <nav class="tabs" aria-label="Company sections">${TABS.filter(
      ([key]) => !HIDE_EMPTY[key] || (c[HIDE_EMPTY[key]] ?? []).length,
    )
      .map(
        ([key, label]) =>
          `<a href="/company/?id=${esc(c.id)}&tab=${key}" ${key === tab ? 'aria-current="page"' : ""}>${label}</a>`,
      )
      .join("")}</nav>
    <div id="tab"></div>`;
  $("new-task").onclick = () => openTaskDialog({ companyId: c.id });
  const renderTab =
    {
      overview,
      data,
      customers,
      finance,
      vendors,
      software,
      policies,
      purchasing,
      inventory,
      findings: findingsTab,
      recordings: recordingsTab,
      tasks: tasksTab,
      agents: agentsTab,
    }[tab] ?? overview;
  renderTab(m, integ);
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
        .map(
          (a) =>
            `<li><time>${esc(age(a.at))} ago</time><span class="who">${esc(a.kind)}</span><span class="kind-${esc(a.kind)}">${esc(a.text)}</span></li>`,
        )
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
          {
            label: "Source files",
            render: (r) =>
              `<span class="mono">${esc(r.files.join(", ") || "—")}</span>`,
          },
          {
            label: "Auto-accepted",
            num: true,
            render: (r) => esc(integer(r.auto)),
          },
          {
            label: "Reviewed",
            num: true,
            render: (r) => esc(integer(r.count - r.auto)),
          },
          {
            label: "",
            render: (r) =>
              `<a href="/data/?company=${esc(c.id)}&entity=${esc(r.key)}">Explore →</a>`,
          },
        ],
        [
          ["customers", "Customers", c.customers],
          ["invoices", "Invoices", c.invoices],
          ["vendors", "Vendors", c.vendors],
          ["purchases", "Vendor purchases", c.purchases],
          ["subscriptions", "Subscriptions", c.subscriptions],
          ["policies", "Policies", c.policies ?? []],
          ["purchaseOrders", "Purchase orders", c.purchaseOrders ?? []],
          [
            "purchaseOrderLines",
            "Purchase order lines",
            c.purchaseOrderLines ?? [],
          ],
          ["inventory", "Inventory balances", c.inventory ?? []],
        ]
          .filter(([, , list]) => list.length)
          .map(([key, entity, list]) => ({
            key,
            entity,
            count: list.length,
            files: [
              ...new Set(list.map((r) => r.provenance?.file).filter(Boolean)),
            ],
            auto: list.filter((r) => r.provenance?.review === "auto-accepted")
              .length,
          })),
      ),
      { eyebrow: "Canonical records with provenance" },
    )}
    ${section(
      "Import jobs",
      table(
        [
          {
            label: "Job",
            render: (j) => `<span class="mono">${esc(j.id)}</span>`,
          },
          { label: "Created", render: (j) => esc(date(j.createdAt)) },
          {
            label: "Files",
            render: (j) =>
              `<span class="mono">${esc(j.files.join(", "))}</span>`,
          },
          {
            label: "Accepted",
            num: true,
            render: (j) => esc(integer(j.accepted)),
          },
          {
            label: "Reviewed",
            num: true,
            render: (j) => esc(integer(j.reviewed)),
          },
          {
            label: "Rejected",
            num: true,
            render: (j) => esc(integer(j.rejected)),
          },
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
  $("tab")
    .querySelectorAll("[data-decide]")
    .forEach((b) => {
      b.onclick = async () => {
        try {
          await resolveException(
            c.id,
            b.closest(".exception").dataset.id,
            b.dataset.decide,
          );
        } catch (error) {
          return message(error.message, "danger");
        }
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
        {
          label: "City / State",
          render: (r) => esc(`${r.city}${r.state ? `, ${r.state}` : ""}`),
        },
        { label: "Service type", key: "serviceType" },
        {
          label: "Status",
          render: (r) =>
            badge(r.status, r.status === "active" ? "success" : ""),
        },
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
      {
        label: "Invoiced",
        value: compactMoney(m.revenue),
        note: PERIOD.label,
        href: `/company/?id=${c.id}&tab=finance`,
      },
      {
        label: "Outstanding AR",
        value: money(m.outstandingAr),
        href: `/company/?id=${c.id}&tab=finance&filter=outstanding`,
      },
      {
        label: "Overdue AR",
        value: money(m.overdueAr),
        href: `/company/?id=${c.id}&tab=finance&filter=overdue`,
      },
      {
        label: ">90 days",
        value: money(m.overdue90.reduce((n, i) => n + i.outstanding, 0)),
        note: `${m.overdue90.length} invoices`,
        href: `/company/?id=${c.id}&tab=finance&filter=overdue90`,
      },
    ])}
    <div class="filters">${Object.entries(sets)
      .map(
        ([k, [list, l]]) =>
          `<a class="button quiet sm" href="/company/?id=${esc(c.id)}&tab=finance&filter=${k}" ${k === filter ? 'aria-current="page"' : ""}>${esc(l)} (${esc(list.length)})</a>`,
      )
      .join("")}</div>
    ${section(
      `${label} (${integer(sorted.length)})`,
      table(
        [
          {
            label: "Invoice",
            render: (r) => `<span class="mono">${esc(r.number)}</span>`,
          },
          { label: "Customer", key: "customerName" },
          { label: "Issued", render: (r) => esc(date(r.issueDate)) },
          { label: "Due", render: (r) => esc(date(r.dueDate)) },
          {
            label: "Days past due",
            num: true,
            render: (r) =>
              r.outstanding > 0 && daysBetween(r.dueDate) > 0
                ? esc(daysBetween(r.dueDate))
                : "—",
          },
          { label: "Amount", num: true, render: (r) => esc(money(r.amount)) },
          {
            label: "Outstanding",
            num: true,
            render: (r) => esc(money(r.outstanding)),
          },
          { label: "Status", render: (r) => badge(r.status) },
          { label: "", render: viewSource(sorted) },
        ],
        sorted.slice(0, 250),
      ),
      {
        aside:
          sorted.length > 250 ? `Showing 250 of ${integer(sorted.length)}` : "",
      },
    )}`;
  bindSources(sorted);
}

function vendors(m) {
  const byVendor = c.vendors.map((v) => {
    const ps = c.purchases.filter((p) => p.vendorId === v.id);
    return {
      ...v,
      lines: ps.length,
      spend: ps.reduce((n, p) => n + p.total, 0),
      skus: new Set(ps.map((p) => p.sku)).size,
    };
  });
  const purchases = [...c.purchases].sort((a, b) => (a.date < b.date ? 1 : -1));
  $("tab").innerHTML = `
    ${section(
      `Vendors (${c.vendors.length})`,
      table(
        [
          {
            label: "Vendor",
            render: (v) =>
              `<b>${esc(v.name)}</b>${v.sourceName !== v.name ? `<small>source: ${esc(v.sourceName)}</small>` : ""}`,
          },
          {
            label: "Purchase lines",
            num: true,
            render: (v) => esc(integer(v.lines)),
          },
          {
            label: "Distinct SKUs",
            num: true,
            render: (v) => esc(integer(v.skus)),
          },
          { label: "Spend", num: true, render: (v) => esc(money(v.spend)) },
          {
            label: "Match",
            render: (v) =>
              badge(
                v.provenance?.confidence >= 0.9 ? "Verified" : "Needs review",
              ),
          },
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
          {
            label: "SKU",
            render: (p) => `<span class="mono">${esc(p.sku)}</span>`,
          },
          { label: "Description", key: "description" },
          {
            label: "Qty",
            num: true,
            render: (p) => `${esc(p.quantity)} ${esc(p.unit)}`,
          },
          {
            label: "Unit price",
            num: true,
            render: (p) => esc(money(p.unitPrice)),
          },
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
        {
          label: "Monthly cost",
          num: true,
          render: (s) => esc(money(s.monthlyCost)),
        },
        { label: "Seats", num: true, key: "seats" },
        {
          label: "Renewal",
          render: (s) =>
            `${esc(date(s.renewalDate))}${-daysBetween(s.renewalDate) <= 30 && -daysBetween(s.renewalDate) >= 0 ? ` ${badge(`${-daysBetween(s.renewalDate)} days`, "danger")}` : ""}`,
        },
        { label: "Contract notes", render: (s) => esc(s.notes || "—") },
        { label: "", render: viewSource(rows) },
      ],
      rows,
    ),
    { eyebrow: `${money(annual)} annualized from monthly cost × 12` },
  );
  bindSources(rows);
}

function policies(m) {
  const rows = [...(c.policies ?? [])].sort((a, b) =>
    (a.expirationDate ?? "") < (b.expirationDate ?? "") ? -1 : 1,
  );
  const expiring = rows.filter((p) => {
    const d = -daysBetween(p.expirationDate);
    return d >= 0 && d <= 90;
  });
  $("tab").innerHTML = `
    ${metricStrip([
      { label: "Policies", value: integer(m.policyCount) },
      { label: "Annual premium", value: compactMoney(m.policyPremium) },
      {
        label: "Expiring ≤ 90 days",
        value: integer(m.policiesExpiring90),
        note: `as of ${date(PERIOD.end)}`,
      },
    ])}
    ${section(
      `Policies (${integer(rows.length)})`,
      table(
        [
          {
            label: "Policy",
            render: (p) => `<span class="mono">${esc(p.policyNumber)}</span>`,
          },
          { label: "Client", key: "customerName" },
          { label: "Line", key: "lineOfBusiness" },
          { label: "Carrier", key: "carrier" },
          { label: "Effective", render: (p) => esc(date(p.effectiveDate)) },
          {
            label: "Expires",
            render: (p) =>
              `${esc(date(p.expirationDate))}${expiring.includes(p) ? ` ${badge(`${-daysBetween(p.expirationDate)} days`, "warning")}` : ""}`,
          },
          {
            label: "Premium",
            num: true,
            render: (p) => esc(money(p.annualPremium)),
          },
          {
            label: "Commission",
            num: true,
            render: (p) => esc(money(p.expectedCommission)),
          },
          { label: "Status", render: (p) => badge(p.status) },
          { label: "", render: viewSource(rows) },
        ],
        rows.slice(0, 250),
      ),
      {
        eyebrow: "Book of business from the agency management system",
        aside:
          rows.length > 250 ? `Showing 250 of ${integer(rows.length)}` : "",
      },
    )}`;
  bindSources(rows);
}

function purchasing(m) {
  const orders = [...(c.purchaseOrders ?? [])].sort((a, b) =>
    (a.date ?? "") < (b.date ?? "") ? 1 : -1,
  );
  const lines = c.purchaseOrderLines ?? [];
  const openLines = lines.filter((l) => l.receivedQty < l.orderedQty);
  const late = openLines.filter(
    (l) => l.promisedDate && daysBetween(l.promisedDate) > 0,
  );
  $("tab").innerHTML = `
    ${metricStrip([
      {
        label: "Purchase orders",
        value: integer(m.purchaseOrderCount),
        note: PERIOD.label,
      },
      {
        label: "PO spend",
        value: compactMoney(m.purchaseOrderSpend),
        note: PERIOD.label,
      },
      { label: "Open lines", value: integer(m.openPoLines) },
      { label: "Past promised date", value: integer(late.length) },
    ])}
    ${section(
      `Purchase orders (${integer(orders.length)})`,
      table(
        [
          {
            label: "PO",
            render: (p) => `<span class="mono">${esc(p.poNumber)}</span>`,
          },
          { label: "Supplier", key: "supplierName" },
          { label: "Date", render: (p) => esc(date(p.date)) },
          { label: "Buyer", key: "buyer" },
          { label: "Terms", key: "paymentTerms" },
          {
            label: "Lines",
            num: true,
            render: (p) => esc(integer(p.lineCount)),
          },
          { label: "Total", num: true, render: (p) => esc(money(p.total)) },
          { label: "Status", render: (p) => badge(p.status) },
          { label: "", render: viewSource(orders) },
        ],
        orders.slice(0, 250),
      ),
      {
        aside:
          orders.length > 250 ? `Showing 250 of ${integer(orders.length)}` : "",
      },
    )}
    ${section(
      `Open purchase order lines (${integer(openLines.length)})`,
      table(
        [
          {
            label: "PO / line",
            render: (l) =>
              `<span class="mono">${esc(l.poNumber)}-${esc(l.lineNumber)}</span>`,
          },
          {
            label: "Item",
            render: (l) => `<span class="mono">${esc(l.itemId)}</span>`,
          },
          { label: "Description", key: "description" },
          {
            label: "Ordered / received",
            num: true,
            render: (l) =>
              `${esc(l.orderedQty)} / ${esc(l.receivedQty)} ${esc(l.uom)}`,
          },
          {
            label: "Unit cost",
            num: true,
            render: (l) => esc(money(l.unitCost)),
          },
          {
            label: "Promised",
            render: (l) =>
              `${esc(date(l.promisedDate))}${late.includes(l) ? ` ${badge(`${daysBetween(l.promisedDate)} days late`, "danger")}` : ""}`,
          },
          { label: "Status", render: (l) => badge(l.status) },
          { label: "", render: viewSource(openLines) },
        ],
        openLines.slice(0, 250),
      ),
      {
        aside:
          openLines.length > 250
            ? `Showing 250 of ${integer(openLines.length)}`
            : "",
      },
    )}`;
  bindSources([...orders, ...openLines]);
}

function inventory(m) {
  const rows = [...(c.inventory ?? [])].sort(
    (a, b) => (b.extendedValue ?? 0) - (a.extendedValue ?? 0),
  );
  const negative = rows.filter((b) => b.onHandQty < 0);
  const mismatch = rows.filter(
    (b) => Math.abs(b.onHandQty - b.allocatedQty - b.availableQty) > 0.001,
  );
  $("tab").innerHTML = `
    ${metricStrip([
      { label: "Stocked items", value: integer(m.inventoryItems) },
      { label: "Inventory value", value: compactMoney(m.inventoryValue) },
      { label: "Negative on hand", value: integer(negative.length) },
      { label: "Balance mismatches", value: integer(mismatch.length) },
    ])}
    ${section(
      `Inventory balances (${integer(rows.length)})`,
      table(
        [
          {
            label: "Item",
            render: (b) => `<span class="mono">${esc(b.itemId)}</span>`,
          },
          { label: "Warehouse", key: "warehouse" },
          { label: "Bin", key: "bin", mono: true },
          {
            label: "On hand",
            num: true,
            render: (b) =>
              `${esc(b.onHandQty)} ${esc(b.uom)}${b.onHandQty < 0 ? ` ${badge("negative", "danger")}` : ""}`,
          },
          { label: "Allocated", num: true, key: "allocatedQty" },
          {
            label: "Available",
            num: true,
            render: (b) =>
              `${esc(b.availableQty)}${mismatch.includes(b) ? ` ${badge("mismatch", "warning")}` : ""}`,
          },
          { label: "On order", num: true, key: "onOrderQty" },
          {
            label: "Unit cost",
            num: true,
            render: (b) => esc(money(b.unitCost)),
          },
          {
            label: "Value",
            num: true,
            render: (b) => esc(money(b.extendedValue)),
          },
          { label: "Last count", render: (b) => esc(date(b.lastCountDate)) },
          { label: "", render: viewSource(rows) },
        ],
        rows.slice(0, 250),
      ),
      {
        eyebrow: rows[0]?.asOfDate ? `As of ${date(rows[0].asOfDate)}` : "",
        aside:
          rows.length > 250 ? `Showing 250 of ${integer(rows.length)}` : "",
      },
    )}`;
  bindSources(rows);
}

function findingsTab() {
  const rows = findings(c.id);
  const agentName = (id) => agents(c.id).find((a) => a.id === id)?.name ?? id;
  $("tab").innerHTML = section(
    `Findings (${rows.length})`,
    table(
      [
        {
          label: "Finding",
          render: (f) =>
            `<b>${esc(f.title)}</b><small>${esc(f.detail)}</small>`,
        },
        {
          label: "Agent · run",
          render: (f) =>
            `${esc(agentName(f.agentId))}<br><a class="mono" href="/agents/?run=${esc(f.runId)}">${esc(f.runId)}</a>`,
        },
        { label: "Found", render: (f) => esc(date(f.foundAt)) },
        { label: "Severity", render: (f) => badge(f.severity) },
        {
          label: "Status",
          render: (f) =>
            statusSelect(
              f.id,
              ["Open", "Reviewed", "Dismissed", "Actioned"],
              f.status,
            ),
        },
        {
          label: "",
          render: (f) =>
            `<button class="sm" data-task="${esc(f.id)}">Convert to task</button>`,
        },
      ],
      rows,
      { empty: "No findings yet. Agents post findings here after each run." },
    ),
    {
      eyebrow:
        "A finding is something an agent discovered inside this company; opportunities live at portfolio level.",
    },
  );
  $("tab")
    .querySelectorAll("[data-status]")
    .forEach((s) => {
      s.onchange = async () => {
        try {
          await setFindingStatus(s.dataset.status, s.value);
        } catch (error) {
          return message(error.message, "danger");
        }
        message(
          `Finding ${s.dataset.status} marked ${s.value.toLowerCase()}.`,
          "success",
        );
      };
    });
  $("tab")
    .querySelectorAll("[data-task]")
    .forEach((b) => {
      b.onclick = () => {
        const f = rows.find((x) => x.id === b.dataset.task);
        openTaskDialog({
          companyId: c.id,
          title: f.title,
          description: f.detail,
          category: "Integration",
          sourceType: "finding",
          sourceId: f.id,
          priority: f.severity,
        });
      };
    });
}

// ---- Recordings: what employees chose to publish from the desktop recorder ----------
// Read-only here. Drafts stay private to the employee; only published reports exist
// from the analyst's side, and they are the same rows the company workspace shows.
const sessionSpan = (s) =>
  s?.started_at
    ? `${date(s.started_at)} ${new Date(s.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}–${s.ended_at ? new Date(s.ended_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "…"}`
    : "—";

async function recordingsTab() {
  $("tab").innerHTML = `<p class="muted">Loading published recordings…</p>`;
  let rows;
  try {
    rows = await api(`/companies/${c.id}/reports`);
  } catch (error) {
    $("tab").innerHTML = `<p class="empty">${esc(error.message)}</p>`;
    return;
  }
  $("tab").innerHTML = section(
    `Published recordings (${rows.length})`,
    table(
      [
        {
          label: "Session",
          render: (r) =>
            `<b>${esc(sessionSpan(r.session))}</b><small>${esc(r.summary ? r.summary.slice(0, 140) : "No model interpretation")}</small>`,
        },
        { label: "Apps", render: (r) => esc((r.apps ?? []).join(" · ")) },
        { label: "Switches", num: true, render: (r) => esc(integer(r.switches ?? 0)) },
        { label: "Workflows", num: true, render: (r) => esc(integer(r.workflows ?? 0)) },
        {
          label: "Automation candidates",
          num: true,
          render: (r) => esc(integer(r.automation_candidates ?? 0)),
        },
        {
          label: "Questions answered",
          num: true,
          render: (r) =>
            `${esc(integer((r.questions_total ?? 0) - (r.questions_open ?? 0)))} / ${esc(integer(r.questions_total ?? 0))}`,
        },
        { label: "Published", render: (r) => esc(date(r.published_at)) },
        {
          label: "",
          render: (r) => `<button class="sm" data-report="${esc(r.id)}">Open</button>`,
        },
      ],
      rows,
      {
        empty:
          "No published recordings yet. Reports appear once an employee uploads a session from the Vista recorder and publishes the reviewed draft.",
      },
    ),
    {
      eyebrow:
        "Employee evidence · observed facts computed from activity metadata; the agent's reading is a hypothesis until verified",
    },
  );
  $("tab")
    .querySelectorAll("[data-report]")
    .forEach((b) => {
      b.onclick = () => openReportDialog(b.dataset.report);
    });
}

function reportHtml(r) {
  const o = r.observed ?? {};
  const i = r.interpretation ?? {};
  const list = (items, f) =>
    items.length
      ? `<ul class="checklist">${items.map((x) => `<li><span>${f(x)}</span></li>`).join("")}</ul>`
      : `<p class="muted">None.</p>`;
  return `
    <p class="muted">${esc(sessionSpan(o.session))} · ${esc(integer(o.events ?? 0))} interactions · ${esc(integer(o.switches ?? 0))} app switches · published ${esc(date(r.published_at))}</p>
    ${r.coverage?.note ? `<p class="muted">${esc(r.coverage.note)}${(r.coverage.excluded ?? []).length ? ` Not observed: ${esc(r.coverage.excluded.join(", "))}.` : ""}</p>` : ""}
    <h3>Observed</h3>
    ${table(
      [
        { label: "Application", render: (a) => esc(a.app) },
        { label: "Share of active time", num: true, render: (a) => `${Math.round((a.share ?? 0) * 100)}%` },
        { label: "Events", num: true, render: (a) => esc(integer(a.events ?? 0)) },
        { label: "Copies", num: true, render: (a) => esc(integer(a.copies ?? 0)) },
        { label: "Pastes", num: true, render: (a) => esc(integer(a.pastes ?? 0)) },
      ],
      (o.apps ?? []).slice(0, 10),
      { empty: "No application activity was shared." },
    )}
    ${list(o.transfers ?? [], (t) => `Copied from <b>${esc(t.from)}</b> into <b>${esc(t.to)}</b> ${esc(integer(t.count))}× (about ${esc(integer(t.mean_latency_s))}s apart)`)}
    <h3>Agent's reading ${badge(i.source === "stub" ? "no model" : "hypothesis")}</h3>
    ${i.summary ? `<p>${esc(i.summary)}</p>` : `<p class="muted">No model interpretation was produced.</p>`}
    ${list(i.workflows ?? [], (w) => `<b>${esc(w.name)}</b> — ${esc((w.apps ?? []).join(", "))}${w.evidence ? ` · ${esc(w.evidence)}` : ""} · ${Math.round((w.confidence ?? 0) * 100)}%`)}
    ${(i.automation_candidates ?? []).length ? `<h4>Automation candidates</h4>${list(i.automation_candidates, (a) => `<b>${esc(a.title)}</b>${a.rationale ? ` — ${esc(a.rationale)}` : ""}`)}` : ""}
    ${(i.documents ?? []).length ? `<h4>Shared documents</h4>${list(i.documents, (d) => `${esc(d.filename)} <span class="muted">${esc(d.summary?.kind ?? "")}${d.summary?.rows != null ? ` · ${esc(integer(d.summary.rows))} rows` : ""}</span>`)}` : ""}
    <h3>Employee's answers</h3>
    ${list(r.questions ?? [], (q) => `<i>${esc(q.question)}</i><br>${q.answer ? esc(q.answer) : `<span class="muted">Not answered</span>`}`)}`;
}

async function openReportDialog(id) {
  let dialog = $("report-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "report-dialog";
    dialog.className = "form-dialog";
    document.body.append(dialog);
  }
  dialog.innerHTML = `<div class="dialog-body"><div class="dialog-head"><div><p class="eyebrow">Recording report · ${esc(c.name)}</p><h2>Loading report…</h2></div><button type="button" class="ghost sm" data-close>Close</button></div><div id="report-body"></div></div>`;
  dialog.querySelector("[data-close]").onclick = () => dialog.close();
  dialog.showModal();
  try {
    const r = await api(`/companies/${c.id}/reports/${id}`);
    dialog.querySelector("h2").textContent = sessionSpan(r.session);
    $("report-body").innerHTML = reportHtml(r);
  } catch (error) {
    $("report-body").innerHTML = `<p class="empty">${esc(error.message)}</p>`;
  }
}

function tasksTab() {
  const rows = tasks().filter((t) => t.companyId === c.id);
  $("tab").innerHTML = section(
    `Tasks (${rows.length})`,
    table(
      [
        {
          label: "Task",
          render: (t) =>
            `<a href="/tasks/?id=${esc(t.id)}"><b>${esc(t.title)}</b></a><small class="mono">${esc(t.id)} · ${esc(t.category)}</small>`,
        },
        {
          label: "Source",
          render: (t) =>
            t.sourceId ? `<span class="mono">${esc(t.sourceId)}</span>` : "—",
        },
        { label: "Assignee", key: "assignee" },
        { label: "Priority", render: (t) => badge(t.priority) },
        { label: "Due", render: (t) => esc(date(t.dueDate)) },
        { label: "Status", render: (t) => badge(t.status) },
      ],
      rows,
      {
        rowHref: (t) => `/tasks/?id=${t.id}`,
        empty: "No tasks for this company.",
      },
    ),
    { aside: `<a href="/tasks/?company=${esc(c.id)}">Open in tracker →</a>` },
  );
}

function agentsTab() {
  const list = agents(c.id);
  const recent = runs(c.id).sort((a, b) =>
    a.startedAt < b.startedAt ? 1 : -1,
  );
  $("tab").innerHTML = `
    ${section(
      `Agents (${list.length})`,
      list.length
        ? table(
            [
              {
                label: "Agent",
                render: (a) =>
                  `<b>${esc(a.name)}</b><small>${esc(a.represents)}</small>`,
              },
              { label: "Status", render: (a) => badge(a.status) },
              {
                label: "Last run",
                render: (a) => `${esc(age(a.lastRunAt))} ago`,
              },
              {
                label: "Runs",
                num: true,
                render: (a) => esc(integer(a.cases)),
              },
              {
                label: "Need review",
                num: true,
                render: (a) =>
                  a.review ? `<span class="attn">${esc(a.review)}</span>` : "0",
              },
              { label: "Findings", num: true, key: "findings" },
              {
                label: "Run cost",
                num: true,
                render: (a) => esc(money(a.cost)),
              },
            ],
            list,
            { rowHref: (a) => `/agents/?company=${c.id}` },
          )
        : `<p class="empty">No agent has run for ${esc(c.name)} yet. Run the portfolio analysis from the overview after the first import.</p>`,
    )}
    ${section(
      "Recent runs",
      table(
        [
          {
            label: "Run",
            render: (r) =>
              `<a class="mono" href="/agents/?run=${esc(r.id)}">${esc(r.id)}</a>`,
          },
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
  const opts = (list, cur) =>
    list
      .map((o) => `<option${o === cur ? " selected" : ""}>${esc(o)}</option>`)
      .join("");
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
  dialog.onclose = async () => {
    if (dialog.returnValue !== "save") return;
    const f = new FormData(dialog.querySelector("form"));
    let task;
    try {
      task = await createTask({
        ...prefill,
        title: f.get("title"),
        description: f.get("description"),
        category: f.get("category"),
        priority: f.get("priority"),
        assignee: f.get("assignee"),
        dueDate: f.get("dueDate") || null,
      });
    } catch (error) {
      return message(error.message, "danger");
    }
    render();
    message(`Task ${task.id} created.`, "success", {
      href: `/tasks/?id=${task.id}`,
      label: "Open in tracker →",
    });
  };
  dialog.showModal();
}
enableRowLinks();
