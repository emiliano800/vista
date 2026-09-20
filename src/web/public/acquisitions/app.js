import { mountShell, $, qs, esc, badge, table, enableRowLinks, section, message } from "/lib/components.js";
import { integer, monthYear, date, DEMO_NOTE } from "/lib/format.js";
import { companies, company, integrationSteps, resolveException } from "/lib/store.js";

const analyst = await mountShell();
const id = qs().get("id");
if (analyst) (id ? detail : list)();

function list() {
  const rows = companies().map((c) => ({ c, i: integrationSteps(c), job: c.importJobs?.at(-1), openX: (c.importExceptions ?? []).filter((x) => x.open !== false).length }));
  $("view").innerHTML = `
    <div class="page-head">
      <div><p class="eyebrow">${esc(analyst.firm)}</p><h1>Acquisitions</h1><p class="muted">Every company enters the portfolio through an import. Each import keeps its files, mappings and decisions.</p></div>
      <div class="page-actions"><a class="button solid" href="/acquisitions/new/">+ Add acquisition</a></div>
    </div>
    <p class="demo-line">${esc(DEMO_NOTE)}</p>
    ${section(
      "Onboarded companies",
      table(
        [
          { label: "Company", render: (r) => `<div class="company-cell"><b>${esc(r.c.name)}</b><small>${esc(r.c.location)}</small></div>` },
          { label: "Acquired", render: (r) => esc(monthYear(r.c.acquired)) },
          { label: "Last import", render: (r) => (r.job ? `<span class="mono">${esc(r.job.id)}</span><small>${esc(date(r.job.createdAt))}</small>` : "—") },
          { label: "Files", render: (r) => esc(r.job?.files.length ?? 0) },
          { label: "Accepted", num: true, render: (r) => esc(integer(r.job?.accepted ?? 0)) },
          { label: "Reviewed", num: true, render: (r) => esc(integer(r.job?.reviewed ?? 0)) },
          { label: "Open exceptions", num: true, render: (r) => (r.openX ? `<span class="attn">${esc(r.openX)}</span>` : "0") },
          { label: "Integration", render: (r) => `${esc(r.i.complete)}/${esc(r.i.total)} ${badge(r.i.label)}` },
        ],
        rows,
        { rowHref: (r) => `/acquisitions/?id=${r.c.id}`, empty: "No acquisitions yet." },
      ),
    )}`;
}

function detail() {
  const c = company(id);
  if (!c) {
    $("view").innerHTML = `<div class="page-head"><div><h1>Unknown acquisition</h1></div></div><p class="block"><a href="/acquisitions/">← Acquisitions</a></p>`;
    return;
  }
  const integ = integrationSteps(c);
  const xs = c.importExceptions ?? [];
  const open = xs.filter((x) => x.open !== false);
  const decided = xs.filter((x) => x.open === false);
  $("view").innerHTML = `
    <p><a class="back" href="/acquisitions/">← Acquisitions</a></p>
    <div class="page-head">
      <div><p class="eyebrow">Acquisition · ${esc(c.location)}</p><h1>${esc(c.name)}</h1><p class="muted">Acquired ${esc(monthYear(c.acquired))} · ${esc(c.industry)} · Integration ${esc(integ.complete)}/${esc(integ.total)} ${badge(integ.label)}</p></div>
      <div class="page-actions"><a class="button quiet" href="/company/?id=${esc(c.id)}">Open workspace</a><a class="button quiet" href="/data/?company=${esc(c.id)}">Explore data</a></div>
    </div>
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
        { empty: "No import jobs." },
      ),
    )}
    ${section(
      `Records awaiting review (${open.length})`,
      open.length
        ? open.map(exceptionRow).join("")
        : `<p class="empty">Nothing is waiting on a human decision for ${esc(c.name)}.</p>`,
      { eyebrow: "Vista never merges or renames records without a decision here" },
    )}
    ${decided.length ? section(`Decided (${decided.length})`, decided.map(exceptionRow).join("")) : ""}
    ${section("Integration checklist", `<ul class="checklist">${integ.steps.map((s) => `<li><span>${esc(s.label)}</span>${badge(s.status)}</li>`).join("")}</ul>`)}`;
  $("view").querySelectorAll("[data-decide]").forEach((b) => {
    b.onclick = async () => {
      await resolveException(c.id, b.closest(".exception").dataset.id, b.dataset.decide);
      detail();
      message(`Decision recorded: ${b.dataset.decide}.`, "success");
    };
  });
}

function exceptionRow(x) {
  return `<div class="exception" data-id="${esc(x.id)}" ${x.decision ? `data-decided="${esc(x.decision)}"` : ""}>
    <div class="pair"><small>${esc(x.type)} · ${esc(Math.round(x.confidence * 100))}% similar</small><b>${esc(x.left)}</b><small>${x.leftRow ? `source row ${esc(x.leftRow)}` : "this import"}</small></div>
    <div class="pair"><small>Compared with</small><b>${esc(x.right)}</b><small>${x.rightRow ? `source row ${esc(x.rightRow)}` : "portfolio vendor master"}</small></div>
    <div class="actions">${x.decision ? badge(x.decision, "success") : x.actions.map((a) => `<button class="sm" data-decide="${esc(a)}">${esc(a)}</button>`).join("")}</div>
  </div>`;
}
enableRowLinks();
