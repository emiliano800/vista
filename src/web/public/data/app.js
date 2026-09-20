import { mountShell, $, qs, esc, badge, table, section, mountSourceDialog } from "/lib/components.js";
import { money, integer, date, DEMO_NOTE, daysBetween } from "/lib/format.js";
import { companies, companyName } from "/lib/store.js";

const ENTITIES = {
  customers: {
    label: "Customers",
    columns: [
      { label: "Customer", render: (r) => `<b>${esc(r.name)}</b>` },
      { label: "Company", render: (r) => esc(companyName(r.companyId)) },
      { label: "Contact", key: "contact" },
      { label: "Phone", key: "phone", mono: true },
      { label: "City / State", render: (r) => esc(`${r.city}${r.state ? `, ${r.state}` : ""}`) },
      { label: "Service type", key: "serviceType" },
      { label: "Status", render: (r) => badge(r.status, r.status === "active" ? "success" : "") },
    ],
    filters: { all: ["All", () => true], inactive: ["Inactive", (r) => r.status !== "active"] },
  },
  invoices: {
    label: "Invoices",
    columns: [
      { label: "Invoice", render: (r) => `<span class="mono">${esc(r.number)}</span>` },
      { label: "Company", render: (r) => esc(companyName(r.companyId)) },
      { label: "Customer", key: "customerName" },
      { label: "Issued", render: (r) => esc(date(r.issueDate)) },
      { label: "Due", render: (r) => esc(date(r.dueDate)) },
      { label: "Amount", num: true, render: (r) => esc(money(r.amount)) },
      { label: "Outstanding", num: true, render: (r) => esc(money(r.outstanding)) },
      { label: "Status", render: (r) => badge(r.status) },
    ],
    filters: {
      all: ["All", () => true],
      outstanding: ["Outstanding", (r) => r.outstanding > 0],
      overdue: ["Overdue", (r) => r.outstanding > 0 && daysBetween(r.dueDate) > 0],
      overdue90: [">90 days", (r) => r.outstanding > 0 && daysBetween(r.dueDate) > 90],
    },
  },
  vendors: {
    label: "Vendors",
    columns: [
      { label: "Vendor", render: (r) => `<b>${esc(r.name)}</b>${r.sourceName && r.sourceName !== r.name ? `<small>source: ${esc(r.sourceName)}</small>` : ""}` },
      { label: "Company", render: (r) => esc(companyName(r.companyId)) },
      { label: "Contact", render: (r) => esc(r.contact || "—") },
      { label: "Match", render: (r) => badge((r.provenance?.confidence ?? 1) >= 0.9 ? "Verified" : "Needs review") },
    ],
    filters: { all: ["All", () => true], review: ["Needs review", (r) => (r.provenance?.confidence ?? 1) < 0.9] },
  },
  purchases: {
    label: "Vendor purchases",
    columns: [
      { label: "Date", render: (r) => esc(date(r.date)) },
      { label: "Company", render: (r) => esc(companyName(r.companyId)) },
      { label: "Vendor", key: "vendorName" },
      { label: "SKU", render: (r) => `<span class="mono">${esc(r.sku)}</span>` },
      { label: "Description", key: "description" },
      { label: "Qty", num: true, render: (r) => `${esc(r.quantity)} ${esc(r.unit)}` },
      { label: "Unit price", num: true, render: (r) => esc(money(r.unitPrice)) },
      { label: "Total", num: true, render: (r) => esc(money(r.total)) },
    ],
    filters: { all: ["All", () => true] },
  },
  subscriptions: {
    label: "Subscriptions",
    columns: [
      { label: "Product", render: (r) => `<b>${esc(r.product)}</b>` },
      { label: "Company", render: (r) => esc(companyName(r.companyId)) },
      { label: "Category", key: "category" },
      { label: "Monthly", num: true, render: (r) => esc(money(r.monthlyCost)) },
      { label: "Seats", num: true, key: "seats" },
      { label: "Renewal", render: (r) => esc(date(r.renewalDate)) },
      { label: "Notes", render: (r) => esc(r.notes || "—") },
    ],
    filters: { all: ["All", () => true], renewing: ["Renews ≤ 90 days", (r) => -daysBetween(r.renewalDate) <= 90 && -daysBetween(r.renewalDate) >= 0] },
  },
};
const PAGE = 100;
const analyst = await mountShell();
const showSource = mountSourceDialog();
if (analyst) render();

function render() {
  const q = qs();
  const entity = ENTITIES[q.get("entity")] ? q.get("entity") : "customers";
  const companyId = q.get("company") ?? "";
  const filter = q.get("filter") ?? "all";
  const search = (q.get("q") ?? "").trim().toLowerCase();
  const page = Math.max(1, Number(q.get("page") ?? 1));
  const def = ENTITIES[entity];
  const src = companies().filter((c) => !companyId || c.id === companyId);
  let rows = src.flatMap((c) => c[entity] ?? []);
  const total = rows.length;
  rows = rows.filter(def.filters[filter]?.[1] ?? (() => true));
  if (search) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(search));
  const reviewed = rows.filter((r) => r.provenance?.review === "reviewed").length;
  const link = (k, v) => {
    const p = new URLSearchParams({ entity, company: companyId, filter, q: search });
    p.set(k, v);
    if (k !== "page") p.delete("page");
    return `/data/?${p}`;
  };
  const shown = rows.slice((page - 1) * PAGE, page * PAGE);
  const pages = Math.ceil(rows.length / PAGE);
  $("view").innerHTML = `
    <div class="page-head">
      <div><p class="eyebrow">${esc(analyst.firm)}</p><h1>Data explorer</h1><p class="muted">Canonical records across the portfolio. Every row keeps its source file, sheet, row and original values.</p></div>
      <form id="search" class="page-actions"><input name="q" type="search" placeholder="Search records…" value="${esc(search)}" /><button class="quiet">Search</button></form>
    </div>
    <nav class="tabs" aria-label="Entities">${Object.entries(ENTITIES).map(([k, d]) => `<a href="${link("entity", k)}" ${k === entity ? 'aria-current="page"' : ""}>${esc(d.label)}</a>`).join("")}</nav>
    <div class="filters"><a class="button quiet sm" href="${link("company", "")}" ${!companyId ? 'aria-current="page"' : ""}>All companies</a>${companies().map((c) => `<a class="button quiet sm" href="${link("company", c.id)}" ${c.id === companyId ? 'aria-current="page"' : ""}>${esc(c.name)}</a>`).join("")}</div>
    <div class="filters">${Object.entries(def.filters).map(([k, [l]]) => `<a class="button quiet sm" href="${link("filter", k)}" ${k === filter ? 'aria-current="page"' : ""}>${esc(l)}</a>`).join("")}</div>
    ${section(
      `${def.label} (${integer(rows.length)}${rows.length !== total ? ` of ${integer(total)}` : ""})`,
      table([...def.columns, { label: "Provenance", render: (r) => `${badge(r.provenance?.review === "reviewed" ? "Reviewed" : "Auto", r.provenance?.review === "reviewed" ? "warning" : "")} <button class="link sm" data-source="${esc(r.id)}">View source</button>` }], shown, { empty: "No records match." }),
      { eyebrow: DEMO_NOTE, aside: `${integer(reviewed)} reviewed by a human · ${integer(rows.length - reviewed)} auto-accepted${pages > 1 ? ` · page ${page} of ${pages}` : ""}` },
    )}
    ${pages > 1 ? `<div class="form-actions">${page > 1 ? `<a class="button quiet sm" href="${link("page", page - 1)}">← Previous</a>` : ""}${page < pages ? `<a class="button quiet sm" href="${link("page", page + 1)}">Next →</a>` : ""}</div>` : ""}`;
  $("search").onsubmit = (e) => {
    e.preventDefault();
    location.assign(link("q", new FormData(e.target).get("q")));
  };
  $("view").querySelectorAll("[data-source]").forEach((b) => {
    b.onclick = () => {
      const r = shown.find((x) => x.id === b.dataset.source);
      showSource(r, r?.name ?? r?.number ?? r?.sku ?? r?.product ?? r?.id);
    };
  });
}
