// The task-graph review panel, shared by the company workspace (tenant API, workspace owner) and
// the firm's deployment view (firm API, FDE). One `GraphReviewOut` in, HTML out; the only writes
// are POST …/graph/draft with the promotions and accepted shadow disagreements the person chose.
// Who may click what is the server's finding: `review.can_draft` (the version is current and has
// something to fold) and `review.fde` (the requester holds the FDE scope). The client never claims
// either; it only greys out what the server would refuse.
import { esc } from "/lib/components.js";

const number = (value) => Number(value).toLocaleString("en-US");
const stamp = (value) => (value ? new Date(value).toLocaleString() : "—");
export const nodeLabel = (n) => `${n.app_role} · ${n.activity}${n.signature?.length ? ` · ${n.signature.join(" ")}` : ""}`;
export const edgeLabel = (e) => `${e.action_class}${e.control ? ` ${e.control}` : ""}${e.slot ? ` ← ${e.slot}` : ""}`;
export const policyTag = (p) => `<span class="tag ${p === "auto" ? "success" : p === "always_ask" ? "danger" : ""}">${esc(p)}</span>`;
export const tierTag = (t) => (t ? `<span class="tag ${t === "unattended" ? "success" : t === "shadow" ? "warning" : ""}">${esc(t)}</span>` : "");
const irreversibilityTag = (c) => (c ? `<span class="tag ${c === "committing" ? "danger" : c === "mutating" ? "warning" : ""}">${esc(c)}</span>` : "");
export const statsText = (s) =>
  `${number(s.support)} seen · ${number(s.executed)} run · ${number(s.verified_ok)} verified · ${number(s.approved)} approved · ${number(s.denied)} denied · ${number(s.effect_missing)} effect missing${s.verified_fail ? ` · ${number(s.verified_fail)} failed verification` : ""}${s.leakage_failed ? ` · ${number(s.leakage_failed)} leakage` : ""}${s.recovery_used ? ` · ${number(s.recovery_used)} recovered` : ""}`;
export const provenanceText = (p) => p.map((x) => `${x.source} ${x.id}${x.event_ids?.length ? ` (${number(x.event_ids.length)} events)` : ""}`).join(", ");
const runTag = (status) =>
  `<span class="tag ${status === "succeeded" ? "success" : status === "failed" || status === "stopped" ? "danger" : "warning"}">${esc(status)}</span>`;

// What the person in front of this panel may do. `who` labels the gate in the copy.
export function gates(review, { canDraft }) {
  const draft = !!review.can_draft && !!canDraft;
  return { draft, fde: draft && !!review.fde };
}

function proposalRow(p, label, g) {
  const past = !!p.needs_fde;
  const cooling = past && !p.cooled;
  const enabled = g.draft && (!past || (g.fde && !cooling));
  const tiers = p.from_tier ? `${tierTag(p.from_tier)} → ${tierTag(p.to_tier)} ` : "";
  const gate = cooling
    ? `<span class="tag warning">cooling</span>`
    : past
      ? g.fde
        ? `<span class="tag">FDE click</span>`
        : `<span class="tag">FDE decision</span>`
      : "";
  const ceiling = p.ceiling && p.ceiling !== "unattended" ? ` <span class="muted">ceiling ${esc(p.ceiling)}</span>` : "";
  return `<li><label><input type="checkbox" data-promote="${esc(p.edge_id)}" ${enabled ? "" : "disabled"}> ${esc(label)}: ${tiers}${policyTag(p.from_policy)} → ${policyTag(p.to_policy)} ${gate} <span class="muted">${esc(p.reason)}</span>${ceiling}</label></li>`;
}

export function compileReportHtml(c) {
  if (!c) return "";
  const slots = c.slots.length
    ? `<div class="table-wrap"><table><thead><tr><th>Slot</th><th>Aligned by</th><th>Controls</th><th></th></tr></thead><tbody>${c.slots
        .map((s) => `<tr><td class="mono">${esc(s.slot)}</td><td>${esc(s.method)}</td><td class="small">${esc(s.controls.join(", "))}</td><td>${s.single_recording ? '<span class="tag warning">one recording</span>' : ""}</td></tr>`)
        .join("")}</tbody></table></div>`
    : '<p class="small muted">No slot table: this graph predates slot alignment.</p>';
  const criteria = c.criteria.length
    ? `<ul class="small">${c.criteria
        .map((k) => `<li><span class="tag">${esc(k.type)}</span> <span class="mono">${esc(k.slot)}</span>${k.read_back_via ? ` <span class="muted">read back via ${esc(k.read_back_via)}</span>` : ""}${k.covers.length ? ` <span class="muted">covers ${number(k.covers.length)} write${k.covers.length === 1 ? "" : "s"}</span>` : ""} <span class="muted">${esc(k.source)}</span></li>`)
        .join("")}</ul>`
    : '<p class="small muted">No goal criteria yet; verification falls back to Jev over the final state.</p>';
  const aliases = Object.entries(c.aliases);
  const irreversibility = Object.entries(c.irreversibility)
    .map(([k, v]) => `${irreversibilityTag(k)} ${number(v)}`)
    .join(" ");
  return `<details class="compile-report"><summary class="small">Compile report · ${number(c.states)} states · ${number(c.moves)} moves · ${c.leakage_ok ? '<span class="tag success">no leakage</span>' : '<span class="tag danger">leakage</span>'}${c.uncovered_writes.length ? ` · <span class="tag warning">${number(c.uncovered_writes.length)} write${c.uncovered_writes.length === 1 ? "" : "s"} without read-back</span>` : ""}</summary>
<p class="small">Compiled by <span class="mono">${esc(c.compiled_by)}</span> from ${number(c.recordings.length)} recording${c.recordings.length === 1 ? "" : "s"}${c.runs ? ` and statistics of ${number(c.runs)} run${c.runs === 1 ? "" : "s"}` : ""}. ${irreversibility ? `Moves by class: ${irreversibility}. ` : ""}Vocabulary ${number(c.vocabulary_size)} words${c.under_segmented ? ` · <b>${number(c.under_segmented)} under-segmented state${c.under_segmented === 1 ? "" : "s"}</b>` : ""}.</p>
<p class="small">Cloud privacy check: ${c.leakage_ok ? "passed" : "<b>failed</b>"} over ${number(c.leakage_strings_checked)} strings (the device checked the same graph against the recording's raw values before uploading; the recording itself never left the device).</p>
<h5>Recordings</h5><p class="small mono">${c.recordings.map(esc).join(", ") || "—"}</p>
<h5>Slots</h5>${slots}
<h5>Goal criteria</h5>${criteria}${c.uncovered_writes.length ? `<p class="small">Writes without a covering read-back stay at <b>confirm</b> at most: <span class="mono">${c.uncovered_writes.map(esc).join(", ")}</span></p>` : ""}
<h5>Control aliases</h5>${aliases.length ? `<ul class="small">${aliases.map(([id, a]) => `<li><span class="mono">${esc(id.slice(0, 8))}</span> ${a.map((x) => `<span class="tag">${esc(x)}</span>`).join(" ")}</li>`).join("")}</ul>` : '<p class="small muted">No aliases kept.</p>'}
<h5>Held-out locate / coverage</h5><p class="small muted">${c.held_out ? esc(JSON.stringify(c.held_out)) : "Not measured yet — numbers come from the evaluation harness over frozen recording sets, never from this view."}</p>
</details>`;
}

export function shadowReportHtml(s, edges, name, g) {
  if (!s || !s.runs) return '<p class="small muted">No shadow run yet: in the <i>shadow</i> tier the recorder proposes a move, the employee acts, and code scores the agreement.</p>';
  const rows = s.edges
    .map(
      (e) =>
        `<tr><td>${esc(edges[e.edge_id] ? edgeLabel(edges[e.edge_id]) : e.edge_id)}</td><td class="num">${number(e.proposed)}</td><td class="num">${number(e.agreed)}</td><td class="num">${Math.round(e.agreement * 100)}%</td><td>${
          e.disagreements.length
            ? `<ul class="small">${e.disagreements
                .map(
                  (d) =>
                    `<li>${d.acted ? `<label><input type="checkbox" data-accept-edge="${esc(d.edge_id)}" data-accept-acted="${esc(d.acted)}" ${g.fde ? "" : "disabled"}> ` : ""}from ${name(d.frm)} the employee went to ${d.acted ? name(d.acted) : "<i>an unknown state</i>"} <span class="muted">run ${esc(String(d.run_id).slice(0, 8))}</span>${d.acted ? "</label>" : ""}</li>`,
                )
                .join("")}</ul>`
            : '<span class="muted">none</span>'
        }</td></tr>`,
    )
    .join("");
  return `<p class="small">${number(s.runs)} shadow run${s.runs === 1 ? "" : "s"} · ${number(s.proposed)} proposals · ${number(s.agreed)} agreed${s.agreement == null ? "" : ` · ${Math.round(s.agreement * 100)}% agreement`}</p><div class="table-wrap"><table><thead><tr><th>Move proposed</th><th class="num">Proposed</th><th class="num">Agreed</th><th class="num">Agreement</th><th>Disagreements${g.fde ? " · tick to accept as a new shadow move" : g.draft ? " · accepting one is an FDE decision" : ""}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function graphReviewHtml(review, { canDraft = false, workflowId = "", who = "the workspace owner" } = {}) {
  const g = review.graph;
  if (!g) return '<p class="small muted">This version has no task graph; it was written by hand rather than compiled from recordings.</p>';
  const gate = gates(review, { canDraft });
  const nodes = Object.fromEntries(g.nodes.map((n) => [n.key, n]));
  const edges = Object.fromEntries(g.edges.map((e) => [e.id, e]));
  const name = (key) => esc(nodeLabel(nodes[key] ?? { app_role: "?", activity: key }));
  const label = (id) => (edges[id] ? edgeLabel(edges[id]) : id);
  const start = new Set(g.start);
  const nodeRows = g.nodes
    .map((n) => `<tr><td>${esc(nodeLabel(n))}</td><td>${start.has(n.key) ? '<span class="tag">start</span>' : ""}${n.terminal ? '<span class="tag success">goal</span>' : ""}</td><td class="num">${number(g.edges.filter((e) => e.frm === n.key).length)}</td><td class="mono small">${esc(n.key)}</td></tr>`)
    .join("");
  const edgeRows = g.edges
    .map(
      (e) =>
        `<tr><td>${name(e.frm)}<br><span class="muted">→ ${name(e.to)}</span></td><td><b>${esc(edgeLabel(e))}</b>${e.produces?.length ? `<br><span class="small muted">produces ${esc(e.produces.join(", "))}</span>` : ""}${e.irreversibility ? `<br>${irreversibilityTag(e.irreversibility)}` : ""}</td><td>${policyTag(e.policy)}${e.tier ? `<br>${tierTag(e.tier)}${e.tier_since ? `<br><span class="small muted">since ${esc(e.tier_since)}</span>` : ""}` : ""}</td><td class="small">${esc(statsText(e.stats))}</td><td class="small muted">${esc(provenanceText(e.provenance))}</td></tr>`,
    )
    .join("");
  const runs = review.runs.length
    ? `<h4>Runs of this version (${number(review.runs.length)})</h4><ul class="run-list small">${review.runs
        .map(
          (r) =>
            `<li>${runTag(r.status)} ${r.verified === true ? '<span class="tag success">verified</span>' : r.verified === false ? '<span class="tag danger">not verified</span>' : ""} ${esc(r.mode)}${r.finished_at ? ` · ${esc(stamp(r.finished_at))}` : ""} <button class="text-button" data-workflow-run="${esc(r.run_id)}">Open →</button><ol class="step-list">${r.steps
              .map((s) => `<li>${esc(label(s.edge))} <span class="muted">from ${edges[s.edge] ? name(edges[s.edge].frm) : "?"}</span> · ${s.executed ? "executed" : "proposed"}${s.effect_seen === false ? " · <b>effect not seen</b>" : ""}${s.decision ? ` · ${esc(s.decision)}d by a person` : ""}</li>`)
              .join("")}</ol></li>`,
        )
        .join("")}</ul>`
    : '<p class="small muted">No finished run of this version yet.</p>';
  const changeRows = review.changes
    .map((c) =>
      c.kind === "tier"
        ? `<li>${esc(label(c.edge_id))}: ${tierTag(c.before)} → ${tierTag(c.after)} <span class="muted">automatic — ${esc(c.reason)}</span></li>`
        : c.kind === "policy"
          ? `<li>${esc(label(c.edge_id))}: ${c.before ? policyTag(c.before) : '<span class="tag">new</span>'} → ${policyTag(c.after)} <span class="muted">automatic — ${esc(c.reason)}</span></li>`
          : `<li>${esc(label(c.edge_id))}: <span class="muted">${esc(statsText(c.before))}</span><br>→ ${esc(statsText(c.after))}</li>`,
    )
    .join("");
  const proposals = review.proposals.map((p) => proposalRow(p, label(p.edge_id), gate)).join("");
  const previous = review.against_previous.length
    ? `<h4>What approving v${review.version_number} changes against v${review.previous_version_number}</h4><ul class="small">${review.against_previous
        .map((c) => `<li>${esc(label(c.edge_id))}: ${c.kind === "tier" ? `${tierTag(c.before)} → ${tierTag(c.after)}` : `${c.before ? policyTag(c.before) : '<span class="tag">new</span>'} → ${policyTag(c.after)}`}${c.reason ? ` <span class="muted">${esc(c.reason)}</span>` : ""}</li>`)
        .join("")}</ul>`
    : review.status === "draft" && review.previous_version_number
      ? `<p class="small muted">No policy differs from v${review.previous_version_number}; this draft carries statistics only.</p>`
      : "";
  const shadow = `<h4>Shadow report</h4>${shadowReportHtml(review.shadow, edges, name, gate)}`;
  const draft =
    review.changes.length || review.proposals.length || review.shadow?.runs
      ? `<h4>Since approval</h4>${changeRows ? `<ul class="small">${changeRows}</ul>` : ""}${proposals ? `<p class="small">Code proposes these promotions from the statistics — one tier at a time, past <i>ask</i> only by an FDE after the cooling period; nothing changes until a person drafts and approves them.</p><ul class="small">${proposals}</ul>` : ""}${
          gate.draft
            ? `<div class="actions"><button class="primary" data-draft-runs="${esc(workflowId)}" data-version="${esc(review.version_id)}" data-expected="${esc(review.version_number)}">Create draft v${review.version_number + 1} from these runs</button><span class="small muted">then approve it under Workflows</span></div>`
            : review.can_draft
              ? `<p class="small muted">Only ${esc(who)} can draft the next version.</p>`
              : ""
        }`
      : "";
  return `<p class="small">${number(g.nodes.length)} states · ${number(g.edges.length)} moves · compiled from ${number(g.trajectories)} recorded pass${g.trajectories === 1 ? "" : "es"}${g.truncated ? " · truncated" : ""} · <span class="mono">${esc(g.compiled_by)}</span></p>${compileReportHtml(review.compile)}${previous}<h4>States</h4><div class="table-wrap"><table><thead><tr><th>State</th><th></th><th class="num">Moves out</th><th>Key</th></tr></thead><tbody>${nodeRows}</tbody></table></div><h4>Moves</h4><div class="table-wrap"><table><thead><tr><th>From → to</th><th>Move</th><th>Policy · tier</th><th>Statistics</th><th>Provenance</th></tr></thead><tbody>${edgeRows}</tbody></table></div>${runs}${shadow}${draft}<p class="small muted">Names only: a state says which fields hold a value and which facts are known, never the values. Statistics accumulate on every run; the structure, the policies and the tiers change only through an approved version.</p>`;
}

// The request body for POST …/graph/draft from the ticked boxes. Never carries who is clicking.
export function draftBody(root, expected) {
  return {
    expected_version: Number(expected),
    promote: [...root.querySelectorAll("[data-promote]:checked")].map((c) => c.dataset.promote),
    accept: [...root.querySelectorAll("[data-accept-edge]:checked")].map((c) => ({ edge_id: c.dataset.acceptEdge, acted: c.dataset.acceptActed })),
  };
}

export function bindGraphReview(root, { onRun, onDraft }) {
  root.querySelectorAll("[data-workflow-run]").forEach((b) => (b.onclick = () => onRun?.(b.dataset.workflowRun)));
  root.querySelectorAll("[data-draft-runs]").forEach((b) => (b.onclick = () => onDraft?.(draftBody(root, b.dataset.expected), b)));
}
