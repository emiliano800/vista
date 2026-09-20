import {
  mountShell,
  $,
  qs,
  esc,
  badge,
  table,
  enableRowLinks,
  section,
  message,
  definitionList,
  companyLink,
} from "/lib/components.js";
import { money, integer, date, daysBetween, DEMO_NOTE } from "/lib/format.js";
import {
  tasks,
  companies,
  company,
  companyName,
  opportunities,
  updateTask,
  createTask,
} from "/lib/store.js";

const STATUSES = ["Open", "In progress", "Blocked", "Complete", "Dismissed"];
const OUTCOMES = [
  "No benefit found",
  "Benefit validated",
  "Implemented",
  "Needs further work",
];
const analyst = await mountShell();
const id = qs().get("id");

const isOpen = (t) => !["Complete", "Dismissed"].includes(t.status);
const overdue = (t) => isOpen(t) && t.dueDate && daysBetween(t.dueDate) > 0;
const sourceHref = (t) =>
  t.sourceType === "opportunity"
    ? `/opportunities/?id=${t.sourceId}`
    : t.sourceType === "finding"
      ? `/company/?id=${t.companyId}&tab=findings`
      : t.sourceType === "subscription"
        ? `/company/?id=${t.companyId}&tab=software`
        : t.sourceType === "import"
          ? `/acquisitions/?id=${t.companyId}`
          : null;
if (analyst) (id ? detail : list)();

function list() {
  const view = qs().get("view") ?? "all";
  const companyFilter = qs().get("company") ?? "";
  const VIEWS = {
    all: ["All", () => true],
    mine: ["My tasks", (t) => t.assignee === analyst.name],
    overdue: ["Overdue", overdue],
    integration: ["Integration", (t) => t.category === "Integration"],
    opportunity: [
      "Opportunity follow-up",
      (t) => t.category === "Opportunity follow-up",
    ],
    exception: ["Agent exception", (t) => t.category === "Agent exception"],
    done: ["Completed", (t) => !isOpen(t)],
  };
  let rows = tasks().filter(VIEWS[view]?.[1] ?? VIEWS.all[1]);
  if (view !== "done" && view !== "all") rows = rows.filter(isOpen);
  if (view === "all")
    rows = rows.filter(isOpen).concat(rows.filter((t) => !isOpen(t)));
  if (companyFilter) rows = rows.filter((t) => t.companyId === companyFilter);
  const all = tasks();
  const link = (k, v) => {
    const p = new URLSearchParams({ view, company: companyFilter });
    p.set(k, v);
    return `/tasks/?${p}`;
  };
  $("view").innerHTML = `
    <div class="page-head">
      <div><p class="eyebrow">${esc(analyst.firm)}</p><h1>Tasks</h1><p class="muted">Integration work, opportunity follow-ups and agent exceptions, each linked back to what created it.</p></div>
      <div class="page-actions"><button id="new-task" class="solid">New task</button></div>
    </div>
    <div class="kpi-strip">
      <a class="kpi" href="${link("view", "all")}"><span>Open</span><b>${esc(integer(all.filter(isOpen).length))}</b></a>
      <a class="kpi" href="${link("view", "overdue")}"><span>Overdue</span><b>${esc(integer(all.filter(overdue).length))}</b></a>
      <a class="kpi" href="${link("view", "mine")}"><span>Mine</span><b>${esc(integer(all.filter((t) => isOpen(t) && t.assignee === analyst.name).length))}</b></a>
      <a class="kpi" href="${link("view", "done")}"><span>Completed</span><b>${esc(integer(all.filter((t) => t.status === "Complete").length))}</b><small>${esc(money(all.reduce((n, t) => n + (t.realizedResult ?? 0), 0)))} realized</small></a>
    </div>
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    <div class="filters">${Object.entries(VIEWS)
      .map(
        ([k, [l]]) =>
          `<a class="button quiet sm" href="${link("view", k)}" ${k === view ? 'aria-current="page"' : ""}>${esc(l)}</a>`,
      )
      .join("")}</div>
    <div class="filters"><a class="button quiet sm" href="${link("company", "")}" ${!companyFilter ? 'aria-current="page"' : ""}>All companies</a>${companies()
      .map(
        (c) =>
          `<a class="button quiet sm" href="${link("company", c.id)}" ${c.id === companyFilter ? 'aria-current="page"' : ""}>${esc(c.name)}</a>`,
      )
      .join("")}</div>
    ${table(
      [
        {
          label: "Task",
          render: (t) =>
            `<b>${esc(t.title)}</b><small class="mono">${esc(t.id)} · ${esc(t.category)}${t.sourceId ? ` · from ${esc(t.sourceId)}` : ""}</small>`,
        },
        { label: "Company", render: (t) => esc(companyName(t.companyId)) },
        { label: "Assignee", key: "assignee" },
        { label: "Priority", render: (t) => badge(t.priority) },
        {
          label: "Due",
          render: (t) =>
            `${esc(date(t.dueDate))}${overdue(t) ? ` ${badge(`${daysBetween(t.dueDate)}d late`, "danger")}` : ""}`,
        },
        { label: "Status", render: (t) => badge(t.status) },
        {
          label: "Outcome",
          render: (t) => (t.outcome ? badge(t.outcome) : "—"),
        },
      ],
      rows,
      { rowHref: (t) => `/tasks/?id=${t.id}`, empty: "No tasks in this view." },
    )}`;
  $("new-task").onclick = () => openNewTask();
}

function detail() {
  const t = tasks().find((x) => x.id === id);
  if (!t) {
    $("view").innerHTML =
      `<div class="page-head"><div><h1>Unknown task</h1></div></div><p class="block"><a href="/tasks/">← Tasks</a></p>`;
    return;
  }
  const c = company(t.companyId);
  const opp =
    t.sourceType === "opportunity"
      ? opportunities().find((o) => o.id === t.sourceId)
      : null;
  const done = !isOpen(t);
  document.title = `Vista · ${t.id}`;
  $("view").innerHTML = `
    <p><a class="back" href="/tasks/">← Tasks</a></p>
    <div class="page-head">
      <div><p class="eyebrow">${esc(t.category)} · <span class="mono">${esc(t.id)}</span></p><h1>${esc(t.title)}</h1><p class="muted">${c ? companyLink(c) : "Portfolio"} · ${badge(t.priority)} ${badge(t.status)}${overdue(t) ? ` ${badge(`${daysBetween(t.dueDate)} days late`, "danger")}` : ""}</p></div>
      <div class="page-actions">
        ${
          done
            ? ""
            : `<select id="status" class="sm">${STATUSES.filter(
                (s) => s !== "Complete",
              )
                .map(
                  (s) =>
                    `<option ${s === t.status ? "selected" : ""}>${esc(s)}</option>`,
                )
                .join(
                  "",
                )}</select><button id="complete" class="accent">Complete task…</button>`
        }
      </div>
    </div>
    <div class="two-col block">
      <section>
        <h2>Details</h2>
        <p class="fact">${esc(t.description || "No description.")}</p>
        ${definitionList([
          ["Assignee", esc(t.assignee)],
          ["Due", esc(date(t.dueDate))],
          ["Created by", `${esc(t.createdBy)} · ${esc(date(t.createdAt))}`],
          ["Completed", t.completedAt ? esc(date(t.completedAt)) : "—"],
          [
            "Source",
            t.sourceId
              ? `<a class="mono" href="${esc(sourceHref(t) ?? "#")}">${esc(t.sourceType)} ${esc(t.sourceId)}</a>`
              : "—",
          ],
        ])}
      </section>
      <section>
        <h2>Outcome</h2>
        ${
          done
            ? `${definitionList([
                ["Outcome", t.outcome ? badge(t.outcome) : esc(t.status)],
                [
                  "Realized result",
                  t.realizedResult != null
                    ? `<b>${esc(money(t.realizedResult))}</b> <small>verified on completion</small>`
                    : `— <small>${t.outcome === "Implemented" ? "no amount recorded" : "only recorded when implemented"}</small>`,
                ],
              ])}<p class="summary-text">${esc(t.outcomeNotes || "No notes.")}</p>`
            : `<p class="muted">Open. ${opp ? `Completing this task updates ${esc(opp.id)}: <i>Benefit validated</i> → Validated, <i>Implemented</i> → Realized (with the amount you record), <i>No benefit found</i> → Dismissed.` : "Record what you found when you complete it."}</p>${t.outcomeNotes ? `<p class="summary-text">${esc(t.outcomeNotes)}</p>` : ""}`
        }
      </section>
    </div>
    ${opp ? section("Source opportunity", `<p class="fact">${esc(opp.fact)}</p><p><a href="/opportunities/?id=${esc(opp.id)}">Open ${esc(opp.id)} →</a> · potential ${esc(opp.potentialValue != null ? money(opp.potentialValue) : "not quantified")} · ${badge(opp.status)}</p>`) : ""}`;
  if (!done) {
    $("status").onchange = async (e) => {
      try {
        await updateTask(t.id, { status: e.target.value });
      } catch (error) {
        return message(error.message, "danger");
      }
      detail();
      message(`${t.id} moved to ${e.target.value.toLowerCase()}.`, "success");
    };
    $("complete").onclick = () => openComplete(t, opp);
  }
}

function dialog(id) {
  let d = $(id);
  if (!d) {
    d = document.createElement("dialog");
    d.id = id;
    d.className = "form-dialog";
    document.body.append(d);
  }
  return d;
}

function openComplete(t, opp) {
  const d = dialog("complete-dialog");
  d.innerHTML = `<form class="dialog-body" method="dialog">
    <div class="dialog-head"><div><p class="eyebrow">${esc(t.id)}</p><h2>Complete task</h2></div><button type="button" class="ghost sm" data-close>Close</button></div>
    ${
      opp
        ? `<label>Outcome<select name="outcome">${OUTCOMES.map((o) => `<option>${esc(o)}</option>`).join("")}</select></label>
      <label id="realized-wrap" hidden>Realized result (USD, verified)<input name="realized" type="number" min="0" step="0.01" placeholder="${esc(opp.potentialValue ?? 0)}" /></label>`
        : ""
    }
    <label>Outcome notes<textarea name="notes" placeholder="What was found, what changed, who confirmed it.">${esc(t.outcomeNotes ?? "")}</textarea></label>
    <div class="form-actions"><button class="primary" value="save">Mark complete</button></div>
  </form>`;
  d.querySelector("[data-close]").onclick = () => d.close("cancel");
  const sel = d.querySelector("[name=outcome]");
  if (sel)
    sel.onchange = () =>
      (d.querySelector("#realized-wrap").hidden = sel.value !== "Implemented");
  d.onclose = async () => {
    if (d.returnValue !== "save") return;
    const f = new FormData(d.querySelector("form"));
    const outcome = f.get("outcome") ?? null;
    const realized =
      outcome === "Implemented" && f.get("realized") !== ""
        ? Number(f.get("realized"))
        : null;
    try {
      await updateTask(t.id, {
        status: outcome === "Needs further work" ? "In progress" : "Complete",
        outcome,
        outcomeNotes: f.get("notes"),
        realizedResult: realized,
      });
    } catch (error) {
      return message(error.message, "danger");
    }
    detail();
    message(
      outcome === "Needs further work"
        ? `${t.id} kept in progress — needs further work.`
        : `${t.id} completed${outcome ? ` — ${outcome.toLowerCase()}` : ""}.`,
      "success",
    );
  };
  d.showModal();
}

function openNewTask() {
  const d = dialog("task-dialog");
  const opts = (list, cur) =>
    list
      .map((o) => `<option${o === cur ? " selected" : ""}>${esc(o)}</option>`)
      .join("");
  d.innerHTML = `<form class="dialog-body" method="dialog">
    <div class="dialog-head"><div><p class="eyebrow">New task</p><h2>Create task</h2></div><button type="button" class="ghost sm" data-close>Close</button></div>
    <div class="form-grid">
      <label class="full">Title<input name="title" required /></label>
      <label class="full">Description<textarea name="description"></textarea></label>
      <label>Company<select name="companyId">${companies()
        .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`)
        .join("")}</select></label>
      <label>Category<select name="category">${opts(["Integration", "Opportunity follow-up", "Agent exception", "Data quality"])}</select></label>
      <label>Priority<select name="priority">${opts(["High", "Medium", "Low"], "Medium")}</select></label>
      <label>Assignee<select name="assignee">${opts([analyst.name, "Miguel Torres", "Priya Raman", "Company controller"])}</select></label>
      <label>Due date<input name="dueDate" type="date" value="2026-10-03" /></label>
    </div>
    <div class="form-actions"><button class="primary" value="save">Create task</button></div>
  </form>`;
  d.querySelector("[data-close]").onclick = () => d.close("cancel");
  d.onclose = async () => {
    if (d.returnValue !== "save") return;
    const f = Object.fromEntries(new FormData(d.querySelector("form")));
    if (!f.dueDate) f.dueDate = null;
    let t;
    try {
      t = await createTask(f);
    } catch (error) {
      return message(error.message, "danger");
    }
    list();
    message(`Task ${t.id} created.`, "success", {
      href: `/tasks/?id=${t.id}`,
      label: "Open →",
    });
  };
  d.showModal();
}
enableRowLinks();
