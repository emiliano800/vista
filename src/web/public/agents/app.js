import { mountShell, $, qs, esc, badge, table, enableRowLinks, section, message, definitionList } from "/lib/components.js";
import { money, integer, date, age, DEMO_NOTE } from "/lib/format.js";
import { agents, runs, run, companies, companyName, findings, setAgentStatus, runAgentNow, createTask } from "/lib/store.js";

const analyst = await mountShell();
const runId = qs().get("run");
if (analyst) (runId ? runDetail : list)();

function list() {
  const companyFilter = qs().get("company") ?? "";
  const list = agents(companyFilter || null);
  const all = agents();
  const totalCost = all.reduce((n, a) => n + a.cost, 0);
  const recent = runs(companyFilter || null).sort((a, b) => (a.startedAt < b.startedAt ? 1 : -1)).slice(0, 12);
  $("view").innerHTML = `
    <div class="page-head">
      <div><p class="eyebrow">${esc(analyst.firm)}</p><h1>Agents</h1><p class="muted">Each agent stands in for an employee process. It reads canonical records, does the work it can, and escalates what it cannot.</p></div>
    </div>
    <div class="kpi-strip">
      <div class="kpi"><span>Agents</span><b>${esc(integer(all.length))}</b><small>${esc(all.filter((a) => a.status === "Active").length)} active · ${esc(all.filter((a) => a.status === "Paused").length)} paused</small></div>
      <div class="kpi"><span>Cases processed</span><b>${esc(integer(all.reduce((n, a) => n + a.cases, 0)))}</b></div>
      <div class="kpi"><span>Need review</span><b>${esc(integer(all.reduce((n, a) => n + a.review, 0)))}</b><small>escalated to a human</small></div>
      <div class="kpi"><span>Findings</span><b>${esc(integer(findings().length))}</b></div>
      <div class="kpi"><span>Model cost</span><b>${esc(money(totalCost))}</b><small>all runs to date</small></div>
    </div>
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    <div class="filters"><a class="button quiet sm" href="/agents/" ${!companyFilter ? 'aria-current="page"' : ""}>All companies</a>${companies().map((c) => `<a class="button quiet sm" href="/agents/?company=${esc(c.id)}" ${c.id === companyFilter ? 'aria-current="page"' : ""}>${esc(c.name)}</a>`).join("")}</div>
    <div class="agent-cards">${list.length ? list.map(card).join("") : `<p class="empty">No agents deployed for this company yet. Agents are deployed from a company's Agents tab after its first import.</p>`}</div>
    ${section(
      "Recent runs",
      table(
        [
          { label: "Run", render: (r) => `<span class="mono">${esc(r.id)}</span>` },
          { label: "Company", render: (r) => esc(companyName(r.companyId)) },
          { label: "Goal", key: "goal" },
          { label: "Started", render: (r) => esc(date(r.startedAt)) },
          { label: "Needs review", num: true, render: (r) => (r.needsReview ? `<span class="attn">${esc(r.needsReview)}</span>` : "0") },
          { label: "Cost", num: true, render: (r) => esc(money(r.modelCost)) },
          { label: "Status", render: (r) => badge(r.status) },
        ],
        recent,
        { rowHref: (r) => `/agents/?run=${r.id}`, empty: "No runs yet." },
      ),
    )}`;
  $("view").querySelectorAll("[data-action]").forEach((b) => {
    b.onclick = async () => {
      const [action, id] = b.dataset.action.split(":");
      const a = agents().find((x) => x.id === id);
      if (action === "pause") await setAgentStatus(id, "Paused");
      if (action === "resume") await setAgentStatus(id, "Active");
      if (action === "run") {
        const r = await runAgentNow(id);
        list();
        return message(`${a.name} ran on ${companyName(a.companyId)}.`, "success", { href: `/agents/?run=${r.id}`, label: `Open ${r.id} →` });
      }
      list();
      message(`${a.name} ${action}d.`, "success");
    };
  });
}

function card(a) {
  const latest = runs(a.companyId).filter((r) => r.agentId === a.id).sort((x, y) => (x.startedAt < y.startedAt ? 1 : -1))[0];
  return `<article class="agent-card paper">
    <div class="block-head"><h3>${esc(a.name)}</h3>${badge(a.status)}</div>
    <p class="muted">${esc(companyName(a.companyId))} · represents ${esc(a.represents)}</p>
    <div class="figures">
      <div>Last run<b>${esc(age(a.lastRunAt))} ago</b></div>
      <div>Cases processed<b>${esc(integer(a.cases))}</b></div>
      <div>Need review<b class="${a.review ? "attn" : ""}">${esc(a.review)}</b></div>
      <div>Findings<b>${esc(a.findings)}</b></div>
      <div>Run cost<b>${esc(money(a.cost))}</b></div>
    </div>
    ${a.lastFailure ? `<p class="fail">Last failure: ${esc(a.lastFailure)}</p>` : ""}
    <div class="actions">
      ${latest ? `<a class="button quiet sm" href="/agents/?run=${esc(latest.id)}">View activity</a>` : ""}
      <button class="sm" data-action="run:${esc(a.id)}">Run now</button>
      ${a.status === "Paused" ? `<button class="sm" data-action="resume:${esc(a.id)}">Resume</button>` : `<button class="sm" data-action="pause:${esc(a.id)}">Pause</button>`}
    </div>
  </article>`;
}

function runDetail() {
  const r = run(runId);
  if (!r) {
    $("view").innerHTML = `<div class="page-head"><div><h1>Unknown run</h1></div></div><p class="block"><a href="/agents/">← Agents</a></p>`;
    return;
  }
  const a = agents().find((x) => x.id === r.agentId);
  const related = findings(r.companyId).filter((f) => f.runId === r.id);
  document.title = `Vista · ${r.id}`;
  $("view").innerHTML = `
    <p><a class="back" href="/agents/?company=${esc(r.companyId)}">← Agents</a></p>
    <div class="page-head">
      <div><p class="eyebrow">${esc(a?.name ?? r.agentId)} · ${esc(companyName(r.companyId))} · <span class="mono">${esc(r.id)}</span></p><h1>${esc(r.goal)}</h1><p class="muted">Started ${esc(date(r.startedAt))} · ${badge(r.status)} · model cost ${esc(money(r.modelCost))}${r.needsReview ? ` · <span class="attn">${esc(r.needsReview)} case${r.needsReview === 1 ? "" : "s"} need review</span>` : ""}</p></div>
      <div class="page-actions">${r.needsReview ? `<button id="task" class="accent">Assign review task</button>` : ""}</div>
    </div>
    <div class="two-col block">
      <section>
        <h2>Sources read</h2>
        <ul class="checklist">${r.sources.map((s) => `<li><span>${esc(s)}</span></li>`).join("")}</ul>
        <h2 class="block">Output</h2>
        <p class="fact">${esc(r.output)}</p>
        ${definitionList([["Final status", badge(r.status)], ["Model cost", esc(money(r.modelCost))], ["Human corrections", esc(r.corrections.length)]])}
      </section>
      <section>
        <h2>Steps & actions</h2>
        <ol class="event-log">${r.events.map(([t, text]) => `<li><time>${esc(t)}</time><span>${esc(text)}</span></li>`).join("")}</ol>
      </section>
    </div>
    ${section("Evidence attached", r.evidence.length ? `<ul class="checklist">${r.evidence.map((e) => `<li><span class="mono">${esc(e)}</span></li>`).join("")}</ul>` : `<p class="empty">No evidence rows attached to this run.</p>`)}
    ${section("Human corrections", r.corrections.length ? `<ul class="checklist">${r.corrections.map((e) => `<li><span>${esc(e)}</span></li>`).join("")}</ul>` : `<p class="muted">None recorded. Corrections made here feed the next version of the agent.</p>`)}
    ${section(
      `Findings from this run (${related.length})`,
      table(
        [
          { label: "Finding", render: (f) => `<b>${esc(f.title)}</b><small>${esc(f.detail)}</small>` },
          { label: "Severity", render: (f) => badge(f.severity) },
          { label: "Status", render: (f) => badge(f.status) },
          { label: "", render: (f) => `<a href="/company/?id=${esc(f.companyId)}&tab=findings">Open →</a>` },
        ],
        related,
        { empty: "This run produced no findings." },
      ),
    )}`;
  const btn = $("task");
  if (btn)
    btn.onclick = async () => {
      const t = await createTask({ title: `Review ${a?.name ?? "agent"} run ${r.id}`, companyId: r.companyId, description: `${r.output}\n\nRun goal: ${r.goal}`, category: "Agent exception", sourceType: "run", sourceId: r.id, priority: "Medium", assignee: analyst.name, dueDate: "2026-09-26" }, analyst.name);
      message(`Task ${t.id} created for this run.`, "success", { href: `/tasks/?id=${t.id}`, label: "Open task →" });
      btn.disabled = true;
    };
}
enableRowLinks();
