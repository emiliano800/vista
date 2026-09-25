import {
  mountShell,
  $,
  esc,
  badge,
  metricStrip,
  table,
  enableRowLinks,
  section,
  message,
} from "/lib/components.js";
import {
  compactMoney,
  integer,
  monthYear,
  date,
  PERIOD,
  TODAY,
  DEMO_NOTE,
  age,
} from "/lib/format.js";
import {
  portfolioMetrics,
  companyMetrics,
  integrationSteps,
  agentStatus,
  attentionQueue,
  activity,
  companyName,
  runPortfolioInterpretation,
} from "/lib/store.js";

const analyst = await mountShell({ onUpdate: () => render() });
if (analyst) render();

function render() {
  const pm = portfolioMetrics();
  const queue = attentionQueue();
  const feed = activity(null, 10);
  $("view").innerHTML = `
    <div class="page-head">
      <div>
        <p class="eyebrow">${esc(analyst.firm)}</p>
        <h1>Portfolio operating overview</h1>
        <p class="muted">${esc(pm.companies)} companies · ${esc(queue.length)} items need attention · ${esc(pm.openTasks)} open integration tasks</p>
      </div>
      <div class="page-actions">
        <label class="visually-hidden" for="period">Reporting period</label>
        <select id="period"><option>${esc(PERIOD.label)}</option></select>
        <button id="analyse" class="solid">Run portfolio analysis</button>
        <a class="button quiet" href="/acquisitions/new/">+ Add acquisition</a>
      </div>
    </div>
    ${metricStrip([
      {
        label: "Portfolio companies",
        value: integer(pm.companies),
        href: "#companies",
      },
      {
        label: "Invoiced revenue",
        value: compactMoney(pm.revenue),
        note: PERIOD.label,
        href: "/data/?entity=invoices",
      },
      {
        label: "Outstanding AR",
        value: compactMoney(pm.outstandingAr),
        note: "point in time",
        href: "/data/?entity=invoices&filter=outstanding",
      },
      {
        label: "Overdue AR",
        value: compactMoney(pm.overdueAr),
        note: "past due date",
        href: "/data/?entity=invoices&filter=overdue",
      },
      {
        label: "Vendor spend",
        value: compactMoney(pm.vendorSpend),
        note: PERIOD.label,
        href: "/data/?entity=purchases",
      },
      {
        label: "Open opportunities",
        value: integer(pm.openOpportunities),
        href: "/opportunities/",
      },
      { label: "Open tasks", value: integer(pm.openTasks), href: "/tasks/" },
    ])}
    <p class="demo-line">${esc(DEMO_NOTE)}. Revenue and vendor spend are period totals; AR figures are balances as of ${esc(date(TODAY.toISOString().slice(0, 10)))}. Click a figure to see the records behind it.</p>
    <section class="block" id="companies">
      <div class="block-head"><h2>Portfolio companies</h2><div class="block-aside">Integration status = completed steps of 8 (profile, customers, invoices, vendors, software, operations, exceptions, analysis)</div></div>
      ${table(
        [
          {
            label: "Company",
            render: (r) =>
              `<div class="company-cell"><b>${esc(r.c.name)}</b><small>${esc(r.c.industry)}</small></div>`,
          },
          { label: "Location", render: (r) => esc(r.c.location) },
          { label: "Acquired", render: (r) => esc(monthYear(r.c.acquired)) },
          {
            label: "Integration",
            render: (r) =>
              `${esc(r.i.complete)} / ${esc(r.i.total)} steps ${badge(r.i.label)}`,
          },
          {
            label: "Revenue",
            num: true,
            render: (r) => esc(compactMoney(r.m.revenue)),
          },
          {
            label: "Outstanding AR",
            num: true,
            render: (r) => esc(compactMoney(r.m.outstandingAr)),
          },
          {
            label: "Vendor spend",
            num: true,
            render: (r) => esc(compactMoney(r.m.vendorSpend)),
          },
          {
            label: "Opps",
            num: true,
            render: (r) => esc(integer(r.m.openOpportunities)),
          },
          {
            label: "Tasks",
            num: true,
            render: (r) => esc(integer(r.m.openTasks)),
          },
          { label: "Agents", render: (r) => badge(r.a.label, r.a.tone) },
          {
            label: "Needs attention",
            num: true,
            render: (r) =>
              r.n ? `<span class="attn">${esc(r.n)}</span>` : "—",
          },
        ],
        pm.rows.map((r) => ({
          ...r,
          i: integrationSteps(r.c),
          a: agentStatus(r.c.id),
          n: queue.filter((q) => q.companyId === r.c.id).length,
        })),
        {
          rowHref: (r) => `/company/?id=${r.c.id}`,
          empty: "No companies yet. Add an acquisition to begin.",
        },
      )}
    </section>
    ${section(
      "Needs attention",
      queue.length
        ? `<ul class="queue">${queue
            .map(
              (q) => `<li>
          <span class="who">${esc(companyName(q.companyId))}</span>
          <span class="what">${esc(q.text)}<small>${esc(q.type)}</small></span>
          <span class="age">${esc(q.age)}</span>
          ${badge(q.severity)}
          <a class="button quiet sm" href="${esc(q.href)}">${esc(q.cta)}</a></li>`,
            )
            .join("")}</ul>`
        : `<p class="empty">Nothing needs attention right now.</p>`,
      { eyebrow: "Cross-company queue · severity by application rules" },
    )}
    ${section(
      "Recent portfolio activity",
      `<ul class="feed">${feed
        .map(
          (a) =>
            `<li><time datetime="${esc(a.at)}">${esc(age(a.at))} ago</time><span class="who">${esc(companyName(a.companyId))}</span><span class="kind-${esc(a.kind)}">${esc(a.text)}</span></li>`,
        )
        .join("")}</ul>`,
    )}`;
  $("analyse").onclick = async () => {
    $("analyse").disabled = true;
    message(
      "Running interpretation agents — File Reviewer per company, then Sector Merger per sector…",
      "info",
    );
    let result;
    try {
      result = await runPortfolioInterpretation();
    } catch (error) {
      $("analyse").disabled = false;
      return message(error.message, "danger");
    }
    render();
    const { found, jobs, failed, timedOut } = result;
    if (timedOut)
      return message(
        `Interpretation still running (${jobs.filter((j) => j.status === "succeeded").length}/${jobs.length} agent runs done) — results will appear on the next refresh.`,
        "warn",
        { href: "/agents/", label: "View agent runs →" },
      );
    if (failed)
      return message(
        `${failed} of ${jobs.length} agent runs failed: ${jobs
          .filter((j) => j.status === "failed")
          .map((j) => `${j.kind} (${j.scope})`)
          .join(", ")}.`,
        "danger",
        { href: "/agents/", label: "View agent runs →" },
      );
    message(
      found.length
        ? `Interpretation complete — ${jobs.length} agent runs, ${found.length} new opportunit${found.length === 1 ? "y" : "ies"} (${found.map((o) => o.id).join(", ")}).`
        : `Interpretation complete — ${jobs.length} agent runs, no new opportunities beyond those already listed.`,
      "success",
      found.length
        ? {
            href: `/opportunities/?id=${found[0].id}`,
            label: `Open ${found[0].id} →`,
          }
        : { href: "/opportunities/", label: "View opportunities →" },
    );
  };
}
enableRowLinks();
