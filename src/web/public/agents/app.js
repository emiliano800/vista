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
} from "/lib/components.js";
import { money, integer, date, age, DEMO_NOTE } from "/lib/format.js";
import { runsChart, spendBars, spend } from "/lib/charts.js";
import { agents, runs, run, companies, companyName, findings, setAgentStatus, runAgentNow, createTask, fleetAnalytics } from "/lib/store.js";

const analyst = await mountShell();
const runId = qs().get("run");
if (analyst) (runId ? runDetail : list)();

async function list() {
  const fleet = await fleetAnalytics().catch(() => null);
  const companyFilter = qs().get("company") ?? "";
  const shown = agents(companyFilter || null);
  const all = agents();
  const totalCost = all.reduce((n, a) => n + a.cost, 0);
  const recent = runs(companyFilter || null)
    .sort((a, b) => (a.startedAt < b.startedAt ? 1 : -1))
    .slice(0, 12);
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
    ${fleetHtml(fleet)}
    <div class="filters"><a class="button quiet sm" href="/agents/" ${!companyFilter ? 'aria-current="page"' : ""}>All companies</a>${companies()
      .map(
        (c) =>
          `<a class="button quiet sm" href="/agents/?company=${esc(c.id)}" ${c.id === companyFilter ? 'aria-current="page"' : ""}>${esc(c.name)}</a>`,
      )
      .join("")}</div>
    <div class="agent-cards">${shown.length ? shown.map(card).join("") : `<p class="empty">No agents deployed for this company yet. Agents are deployed from a company's Agents tab after its first import.</p>`}</div>
    ${section(
      "Recent runs",
      table(
        [
          {
            label: "Run",
            render: (r) => `<span class="mono">${esc(r.id)}</span>`,
          },
          { label: "Company", render: (r) => esc(companyName(r.companyId)) },
          { label: "Goal", key: "goal" },
          { label: "Started", render: (r) => esc(date(r.startedAt)) },
          {
            label: "Needs review",
            num: true,
            render: (r) =>
              r.needsReview
                ? `<span class="attn">${esc(r.needsReview)}</span>`
                : "0",
          },
          { label: "Cost", num: true, render: (r) => esc(money(r.modelCost)) },
          { label: "Status", render: (r) => badge(r.status) },
        ],
        recent,
        { rowHref: (r) => `/agents/?run=${r.id}`, empty: "No runs yet." },
      ),
    )}`;
  $("view")
    .querySelectorAll("[data-action]")
    .forEach((b) => {
      b.onclick = async () => {
        const [action, id] = b.dataset.action.split(":");
        const a = agents().find((x) => x.id === id);
        try {
          if (action === "pause") await setAgentStatus(id, "Paused");
          if (action === "resume") await setAgentStatus(id, "Active");
          if (action === "run") {
            const r = await runAgentNow(id);
            list();
            return message(
              `${a.name} ran on ${companyName(a.companyId)}.`,
              "success",
              { href: `/agents/?run=${r.id}`, label: `Open ${r.id} →` },
            );
          }
        } catch (error) {
          return message(error.message, "danger");
        }
        list();
        message(`${a.name} ${action}d.`, "success");
      };
    });
}

// ---- Suite fleet: five named agents, their throughput, spend and quality --------
const SUITE_TRIGGER = {
  recording_reviewer: "Runs when an employee submits a recording",
  file_reviewer: "Reads a division's exports on request",
  report_generator: "Summarizes findings into a company report",
  sector_merger: "Compares sister companies in a sector",
  computer_use: "Runs an approved sandbox workflow through an employee's recorder, one bounded step at a time",
};
const RUN_LABEL = { queued: "Queued", running: "Running", succeeded: "Complete", failed: "Failed", waiting: "Waiting", stopped: "Stopped" };
const KIND_LABEL = { observed_fact: "Observed facts", inefficiency: "Inefficiencies", proposed_automation: "Proposed automations" };
const PHASE_LABEL = { discover: "Discover", execute: "Execute", analyze: "Analyze" };
const pct = (v) => `${Math.round(Number(v) * 100)}%`;
const seconds = (s) => (s == null ? "—" : s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);

function fleetHtml(fleet) {
  if (!fleet) return `<p class="empty">Suite analytics are unavailable right now.</p>`;
  const cards = fleet.agents
    .map((a) => {
      const rate = a.runs ? Math.round((a.succeeded / a.runs) * 100) : null;
      return `<article class="agent-card paper suite-card" data-agent-key="${esc(a.agent_key)}">
    <div class="block-head"><h3>${esc(a.name)}</h3>${a.last_status ? badge(RUN_LABEL[a.last_status] ?? a.last_status) : badge("Never run", "")}</div>
    <p class="muted">${esc(SUITE_TRIGGER[a.agent_key] ?? "")}</p>
    <div class="figures">
      <div>Last run<b>${a.last_run_at ? esc(age(a.last_run_at, new Date())) + " ago" : "—"}</b></div>
      <div>Runs<b>${esc(integer(a.runs))}</b>${a.active ? `<small>${esc(a.active)} in progress</small>` : ""}</div>
      <div>Success rate<b class="${a.failed ? "attn" : ""}">${rate == null ? "—" : `${rate}%`}</b>${a.failed ? `<small>${esc(a.failed)} failed</small>` : ""}</div>
      <div>Avg duration<b>${esc(seconds(a.avg_seconds))}</b></div>
      <div>Open findings<b>${esc(integer(a.findings_open))}</b><small>of ${esc(integer(a.findings_total))}</small></div>
      <div>Spend this month<b>${esc(spend(a.cost_month_usd))}</b><small>${esc(spend(a.cost_usd))} to date · ${esc(integer(a.tokens))} tokens</small></div>
    </div>
    <div class="actions"><a class="button quiet sm" href="/account/?view=runs">Runs &amp; traces</a></div>
  </article>`;
    })
    .join("");
  const kinds = Object.entries(KIND_LABEL)
    .map(([k, l]) => `<div class="kpi"><span>${esc(l)}</span><b>${esc(integer(fleet.findings_by_kind[k] ?? 0))}</b></div>`)
    .join("");
  const quality = fleet.quality ?? [];
  const scope = (q) => (q.sector ? q.sector.replace("_", " ") : [q.company, q.division].filter(Boolean).join(" · "));
  const qualityHtml = quality.length
    ? table(
        [
          { label: "Phase", render: (q) => esc(PHASE_LABEL[q.phase] ?? q.phase) },
          { label: "Scope", render: (q) => esc(scope(q)) },
          { label: "Precision", num: true, render: (q) => `<b>${esc(pct(q.precision))}</b>` },
          { label: "Recall", num: true, render: (q) => `<b>${esc(pct(q.recall))}</b>` },
          { label: "Traps", render: (q) => (q.trap_hits ? badge(`${q.trap_hits} trap${q.trap_hits === 1 ? "" : "s"}`, "danger") : badge("0 traps", "success")) },
          { label: "Matched", num: true, render: (q) => esc(`${q.tp} / ${q.tp + q.fn}`) },
          { label: "Model", render: (q) => esc(q.model ?? "—") },
          { label: "Scored", render: (q) => esc(date(q.created_at)) },
        ],
        quality,
      )
    : `<p class="empty">No answer-key evaluations recorded yet. Run <code>make eval PHASE=analyze SECTOR=industrial_goods TENANT=&lt;schema&gt;</code>.</p>`;
  return `
    ${section(
      "Agent suite",
      `<div class="kpi-strip">
        <div class="kpi"><span>Runs this month</span><b>${esc(integer(fleet.runs_month))}</b><small>${esc(integer(fleet.runs_total))} to date</small></div>
        <div class="kpi"><span>Model spend this month</span><b>${esc(spend(fleet.total_cost_month_usd))}</b><small>${esc(spend(fleet.total_cost_usd))} to date</small></div>
        ${kinds}
      </div>
      <div class="agent-cards">${cards}</div>`,
      { eyebrow: "Recording Reviewer · File Reviewer · Report Generator · Sector Merger" },
    )}
    <div class="two-col block">
      <section><h2>Throughput · last ${esc(fleet.window_days)} days</h2>${runsChart(fleet.by_day)}<p class="muted small">Runs per day; failed runs in rust. Hover a bar for spend.</p></section>
      <section><h2>Spend by company</h2>${spendBars(fleet.by_company)}<h2 class="block">Spend by model</h2>${spendBars(fleet.by_model, (r) => r.key ?? "unknown")}</section>
    </div>
    ${section("Quality · latest answer-key evaluation per scope", qualityHtml, { eyebrow: "Measures the agent against synthetic_data/answer_key.json — not business outcomes" })}
    <p class="muted small">Findings, tokens and spend are separate measures: a finding is an evidence-backed observation or proposal, not a realized saving.</p>`;
}

function card(a) {
  const latest = runs(a.companyId)
    .filter((r) => r.agentId === a.id)
    .sort((x, y) => (x.startedAt < y.startedAt ? 1 : -1))[0];
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
    $("view").innerHTML =
      `<div class="page-head"><div><h1>Unknown run</h1></div></div><p class="block"><a href="/agents/">← Agents</a></p>`;
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
        ${definitionList([
          ["Final status", badge(r.status)],
          ["Model cost", esc(money(r.modelCost))],
          ["Human corrections", esc(r.corrections.length)],
        ])}
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
          {
            label: "Finding",
            render: (f) =>
              `<b>${esc(f.title)}</b><small>${esc(f.detail)}</small>`,
          },
          { label: "Severity", render: (f) => badge(f.severity) },
          { label: "Status", render: (f) => badge(f.status) },
          {
            label: "",
            render: (f) =>
              `<a href="/company/?id=${esc(f.companyId)}&tab=findings">Open →</a>`,
          },
        ],
        related,
        { empty: "This run produced no findings." },
      ),
    )}`;
  const btn = $("task");
  if (btn)
    btn.onclick = async () => {
      let t;
      try {
        t = await createTask({
          title: `Review ${a?.name ?? "agent"} run ${r.id}`,
          companyId: r.companyId,
          description: `${r.output}\n\nRun goal: ${r.goal}`,
          category: "Agent exception",
          sourceType: "run",
          sourceId: r.id,
          priority: "Medium",
          assignee: analyst.name,
          dueDate: "2026-09-26",
        });
      } catch (error) {
        return message(error.message, "danger");
      }
      message(`Task ${t.id} created for this run.`, "success", {
        href: `/tasks/?id=${t.id}`,
        label: "Open task →",
      });
      btn.disabled = true;
    };
}
enableRowLinks();
