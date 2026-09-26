// FDE deployment surface: each company's workflow versions with their task graph, compile
// report, shadow report and tier proposals, through the firm-scoped routes
// (/api/companies/{id}/workflows). Whether this person is the FDE comes from the server
// (`review.fde`); the client never claims it.
import { mountShell, $, esc, section, message } from "/lib/components.js";
import { api } from "/lib/auth.js";
import { companies, companyName } from "/lib/store.js";
import { graphReviewHtml, bindGraphReview } from "/lib/graph-review.js";

const analyst = await mountShell({ onUpdate: () => render() });
let generation = 0; // a reply for the previously selected company must not overwrite this one's list
if (analyst) render();

function selectedCompany() {
  const id = new URLSearchParams(location.search).get("company");
  return companies().find((c) => c.id === id) ?? companies()[0] ?? null;
}

function render() {
  const company = selectedCompany();
  const picker = `<label class="small">Company
    <select id="company-pick">${companies()
      .map((c) => `<option value="${esc(c.id)}" ${company?.id === c.id ? "selected" : ""}>${esc(c.name)}</option>`)
      .join("")}</select></label>`;
  $("view").innerHTML = `
    ${section("Workflows", `<div id="workflows"><p class="small muted">Loading…</p></div>`, { eyebrow: company ? companyName(company.id) : "Deployment", aside: picker })}`;
  const pick = $("company-pick");
  if (pick) {
    pick.onchange = () => {
      const url = new URL(location.href);
      url.searchParams.set("company", pick.value);
      history.replaceState(null, "", url);
      render();
    };
  }
  const current = ++generation;
  if (company) loadWorkflows(company.id, current).catch((e) => message(e.message, "danger"));
  else $("workflows").innerHTML = `<p class="small muted">No company in this firm's scope yet.</p>`;
}

async function loadWorkflows(companyId, current = generation) {
  const workflows = await api(`/companies/${companyId}/workflows?limit=100`);
  if (current !== generation) return; // the person has moved on to another company
  const root = $("workflows");
  if (!root) return;
  if (!workflows.length) {
    root.innerHTML = `<p class="small muted">No workflow has been drafted for this company.</p>`;
    return;
  }
  root.innerHTML = workflows
    .map((w) => {
      const v = w.latest_version;
      return `<details class="card" data-deploy-workflow="${esc(w.id)}" data-version="${esc(v.id)}">
        <summary><b>${esc(w.name)}</b> · v${esc(v.number)} · ${esc(v.status)}${v.definition?.graph ? ` · ${v.definition.graph.nodes.length} states · ${v.definition.graph.edges.length} moves` : " · no task graph"}</summary>
        <div data-review><p class="small muted">Open to load the review.</p></div>
      </details>`;
    })
    .join("");
  root.querySelectorAll("[data-deploy-workflow]").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open && !d.dataset.loaded) {
        d.dataset.loaded = "1";
        loadReview(companyId, d.dataset.deployWorkflow, d.dataset.version, d.querySelector("[data-review]"), current);
      }
    });
  });
}

async function loadReview(companyId, workflowId, versionId, body, current = generation) {
  body.innerHTML = `<p class="small muted">Loading task graph…</p>`;
  try {
    const review = await api(`/companies/${companyId}/workflows/${workflowId}/versions/${versionId}/graph`);
    // `review.may_draft` is the server's word on whether this person may draft (firm admin).
    body.innerHTML = graphReviewHtml(review, { workflowId, who: "a firm admin" });
    bindGraphReview(body, {
      onDraft: async (draft, button) => {
        button.disabled = true;
        try {
          message();
          const created = await api(`/companies/${companyId}/workflows/${workflowId}/versions/${versionId}/graph/draft`, {
            method: "POST",
            body: JSON.stringify(draft),
          });
          message(`Draft v${created.number} created — it awaits the ordinary approval.`, "success");
          await loadWorkflows(companyId, current);
        } catch (e) {
          message(e.message, "danger");
          button.disabled = false;
        }
      },
    });
  } catch (e) {
    body.innerHTML = `<p class="small muted">${esc(e.message)}</p>`;
  }
}
