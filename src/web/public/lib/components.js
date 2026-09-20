// Shared rendering helpers for the PE analyst workspace (vanilla JS, no
// inline scripts or styles so the same-origin CSP keeps holding).
import { requireAnalyst, signOut } from "./auth.js";
import { DEMO_NOTE } from "./format.js";
import { companies, reset } from "./store.js";

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const $ = (id) => document.getElementById(id);
export const qs = () => new URLSearchParams(location.search);
export const navigate = (url) => (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);

const NAV = [
  ["/portfolio/", "Portfolio"],
  ["/acquisitions/", "Acquisitions"],
  ["/opportunities/", "Opportunities"],
  ["/tasks/", "Tasks"],
  ["/agents/", "Agents"],
  ["/data/", "Data"],
];

// Mounts the sidebar + header controls and returns the analyst session.
export function mountShell() {
  const analyst = requireAnalyst();
  if (!analyst) return null;
  const here = location.pathname.replace(/index\.html$/, "");
  const nav = $("sidebar");
  if (nav) {
    const list = companies();
    nav.innerHTML = `
      <p class="eyebrow">${esc(analyst.firm)}</p>
      <ul class="side-nav">
        ${NAV.map(([href, label]) => `<li><a href="${href}" ${here.startsWith(href) ? 'aria-current="page"' : ""}>${label}</a></li>`).join("")}
      </ul>
      <p class="eyebrow side-eyebrow">Companies</p>
      <ul class="side-nav side-companies">
        ${list.map((c) => `<li><a href="/company/?id=${esc(c.id)}" ${here.startsWith("/company/") && qs().get("id") === c.id ? 'aria-current="page"' : ""}>${esc(c.name)}</a></li>`).join("")}
        <li><a class="side-add" href="/acquisitions/new/">+ Add acquisition</a></li>
      </ul>
      <div class="side-foot">
        <span class="side-user">${esc(analyst.name)}<small>${esc(analyst.role)} · ${esc(analyst.email)}</small></span>
        <button id="reset-demo" class="link sm">Reset demo data</button>
      </div>`;
    nav.querySelector("#reset-demo").onclick = () => {
      if (confirm("Reset the demo portfolio to its seeded state? Imports, tasks and decisions made in this browser will be cleared.")) {
        reset();
        navigate("/portfolio/");
      }
    };
  }
  const out = $("signout");
  if (out) {
    out.hidden = false;
    out.onclick = () => {
      signOut();
      navigate("/signin/analyst/");
    };
  }
  const note = $("demo-note");
  if (note) note.textContent = DEMO_NOTE;
  document.body.classList.add("analyst");
  return analyst;
}

export function message(text = "", tone = "", link = null) {
  const el = $("message");
  if (!el) return;
  el.textContent = text;
  el.dataset.tone = tone;
  if (link) {
    const a = document.createElement("a");
    a.href = link.href;
    a.textContent = link.label;
    el.append(" ", a);
  }
  if (text) el.scrollIntoView({ block: "nearest" });
}

const TONES = {
  Complete: "success",
  Realized: "success",
  Validated: "success",
  Active: "success",
  Ready: "success",
  Verified: "success",
  paid: "success",
  Implemented: "success",
  High: "danger",
  overdue: "danger",
  Blocked: "danger",
  Overdue: "danger",
  Failed: "danger",
  Dismissed: "",
  "Needs review": "warning",
  Review: "warning",
  Medium: "warning",
  "In progress": "brand",
  "Under review": "brand",
  "Task created": "brand",
  Open: "brand",
  New: "human",
  open: "brand",
  Low: "",
  Paused: "",
  "Not started": "",
};
export function badge(label, tone = TONES[label] ?? "") {
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}

// Ruled KPI strip. Each figure may link to the records behind it.
export function metricStrip(items) {
  return `<div class="kpi-strip">${items
    .map(
      (m) =>
        `<${m.href ? `a href="${esc(m.href)}"` : "div"} class="kpi"><span>${esc(m.label)}</span><b>${esc(m.value)}</b>${m.note ? `<small>${esc(m.note)}</small>` : ""}</${m.href ? "a" : "div"}>`,
    )
    .join("")}</div>`;
}

// Ruled table. columns: [{key,label,num,render}]; rows: objects.
export function table(columns, rows, { rowHref, empty = "Nothing to show.", rowClass } = {}) {
  if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
  return `<div class="table-wrap"><table class="ruled">
    <thead><tr>${columns.map((c) => `<th${c.num ? ' class="num"' : ""}>${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows
      .map((row) => {
        const href = rowHref?.(row);
        return `<tr${href ? ` class="clickable${rowClass ? ` ${rowClass(row)}` : ""}" data-href="${esc(href)}" tabindex="0"` : rowClass ? ` class="${rowClass(row)}"` : ""}>${columns
          .map((c) => `<td${c.num ? ' class="num"' : ""}${c.mono ? ' class="mono"' : ""}>${c.render ? c.render(row) : esc(row[c.key])}</td>`)
          .join("")}</tr>`;
      })
      .join("")}</tbody></table></div>`;
}
// Row navigation for tables rendered with rowHref (delegated once).
export function enableRowLinks(root = document) {
  root.addEventListener("click", (event) => {
    if (event.target.closest("a, button, select, input, label")) return;
    const row = event.target.closest("tr[data-href]");
    if (row) navigate(row.dataset.href);
  });
  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const row = event.target.closest?.("tr[data-href]");
    if (row) navigate(row.dataset.href);
  });
}

export function section(title, body, { eyebrow = "", aside = "" } = {}) {
  return `<section class="block">
    <div class="block-head">${eyebrow ? `<p class="eyebrow">${esc(eyebrow)}</p>` : ""}<h2>${esc(title)}</h2>${aside ? `<div class="block-aside">${aside}</div>` : ""}</div>
    ${body}
  </section>`;
}

export function definitionList(pairs) {
  return `<dl class="defs">${pairs.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
}

// Provenance popover content for "View source".
export function provenanceHtml(p) {
  if (!p) return `<p class="muted">No provenance recorded.</p>`;
  const kv = (obj) =>
    `<table class="kv">${Object.entries(obj ?? {})
      .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`)
      .join("")}</table>`;
  return `
    <dl class="defs compact">
      <div><dt>Source file</dt><dd class="mono">${esc(p.file || "—")}</dd></div>
      <div><dt>Sheet · row</dt><dd class="mono">${esc(p.sheet || "—")} · ${esc(p.row ?? "—")}</dd></div>
      <div><dt>Import job</dt><dd class="mono">${esc(p.importJob || "—")}</dd></div>
      <div><dt>Confidence · review</dt><dd>${esc(Math.round((p.confidence ?? 1) * 100))}% · ${esc(p.review || "—")}</dd></div>
    </dl>
    <div class="two-col">
      <div><p class="eyebrow">Original values</p>${kv(p.original)}</div>
      <div><p class="eyebrow">Normalized values</p>${kv(p.normalized)}</div>
    </div>`;
}
export function mountSourceDialog() {
  let dialog = $("source-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "source-dialog";
    dialog.className = "source-dialog";
    document.body.append(dialog);
  }
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-close]")) dialog.close();
  });
  return (record, title = "Where did this come from?") => {
    dialog.innerHTML = `<div class="dialog-body"><div class="dialog-head"><p class="eyebrow">View source</p><h2>${esc(title)}</h2><button class="ghost sm" data-close>Close</button></div>${provenanceHtml(record?.provenance)}</div>`;
    dialog.showModal();
  };
}

export function statusSelect(id, options, current, cls = "sm") {
  return `<select class="${cls}" data-status="${esc(id)}">${options.map((o) => `<option${o === current ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
}

export function companyLink(c) {
  return `<a href="/company/?id=${esc(c.id)}">${esc(c.name)}</a>`;
}
