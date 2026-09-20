const $ = (id) => document.getElementById(id);
const navigate = (url) =>
  (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const date = (value) =>
  new Date(value).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
const time = (value) =>
  new Date(value).toLocaleTimeString([], { timeStyle: "short" });
const duration = (seconds) =>
  seconds < 60
    ? `${Math.round(seconds)} sec`
    : `${(seconds / 60).toFixed(1)} min`;
const sourceTone = (source) =>
  source === "human" ? "human" : source === "ai" ? "agent" : "";
const badge = (source, fallback) =>
  source
    ? `<span class="badge ${sourceTone(source)}">${esc(source)}</span>`
    : `<span class="badge">${esc(fallback)}</span>`;
let reportOffset = 0,
  evidenceOffset = 0,
  current = null,
  requestGeneration = 0;
function message(text = "") {
  $("message").textContent = text;
}
function view(name) {
  for (const id of ["workspace", "detail"]) $(id).hidden = id !== name;
}
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Vista-Request": "1",
      ...options.headers,
    },
  });
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      /* upstream unavailable */
    }
    if (response.status === 401) {
      ++requestGeneration;
      navigate("/signin/");
    }
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : response.status === 422
          ? "The request could not be accepted. Check your input."
          : "The workspace is unavailable. Please try again.",
    );
  }
  return response.status === 204 ? null : response.json();
}
function handle(fn) {
  return async (event) => {
    try {
      message();
      await fn(event);
    } catch (error) {
      message(error.message);
    }
  };
}
async function enter() {
  const me = await api("/auth/me");
  $("identity").textContent = me.email;
  $("signout").hidden = false;
  const companies = await api("/deals");
  $("company").replaceChildren(
    ...companies.map((c) => new Option(c.name, c.id)),
  );
  view("workspace");
  reportOffset = 0;
  await reports();
}
async function reports() {
  const generation = ++requestGeneration;
  const company = $("company").value;
  $("company-id").textContent = company || "No company assigned";
  $("copy-company").disabled = !company;
  $("prev-reports").disabled = true;
  $("next-reports").disabled = true;
  $("reports").innerHTML = '<p class="empty">Loading reports…</p>';
  const rows = company
    ? await api(`/deals/${company}/recordings?offset=${reportOffset}&limit=20`)
    : [];
  if (generation !== requestGeneration) return;
  $("reports").innerHTML = rows.length
    ? rows
        .map(
          (r) =>
            `<article class="report"><div><h2>${esc(date(r.started_at))}</h2><small>${esc(duration(r.active_seconds))} active</small></div><div class="figures"><span class="mono-figure">${esc(r.summary.n_steps)} steps</span><span class="mono-figure">${esc(r.summary.n_cases)} cases</span></div><button class="sm" data-report="${esc(r.id)}">View report</button></article>`,
        )
        .join("")
    : `<div class="empty">${company ? "No uploaded reports yet. Connect the desktop recorder above to share your first session." : "No companies are assigned to your account. Ask your administrator for access."}</div>`;
  $("report-page").textContent = rows.length
    ? `Reports ${reportOffset + 1}–${reportOffset + rows.length}`
    : "";
  $("prev-reports").disabled = reportOffset === 0;
  $("next-reports").disabled = rows.length < 20;
  $("reports")
    .querySelectorAll("[data-report]")
    .forEach(
      (button) =>
        (button.onclick = handle(() => detail(button.dataset.report))),
    );
}
async function detail(id) {
  const generation = ++requestGeneration;
  const r = await api(`/recordings/${id}`);
  if (generation !== requestGeneration) return;
  current = r;
  $("detail-title").textContent = date(r.started_at);
  $("detail-sub").textContent =
    `Analyzed on device · Uploaded ${date(r.uploaded_at)} · ${r.source_id}`;
  const s = r.summary;
  $("metrics").innerHTML = [
    ["Active time", duration(r.active_seconds)],
    ["Source steps", s.n_steps],
    ["Cases", s.n_cases],
    ["Uncorrelated steps", s.n_uncorrelated_steps],
  ]
    .map(
      ([label, value]) =>
        `<div class="metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`,
    )
    .join("");
  $("download-json").href = `/api/recordings/${id}/download`;
  $("download-csv").href = `/api/recordings/${id}/download?format=csv`;
  $("activity").replaceChildren(
    new Option("All activities", ""),
    ...s.activities.map((a) => new Option(a.activity, a.activity)),
  );
  $("candidates").innerHTML = s.automation_potential.length
    ? s.automation_potential
        .map(
          (a, i) =>
            `<div class="candidate"><div><strong>${esc(a.activity)}</strong><small><span class="mono-figure">${Math.round(a.score * 100)} / 100</span> candidate score · <span class="mono-figure">${a.hours_total.toFixed(2)} h</span> observed</small></div><button class="sm" data-candidate="${i}">View evidence</button></div>`,
        )
        .join("")
    : '<p class="muted">No automation candidates in this session.</p>';
  $("candidates")
    .querySelectorAll("[data-candidate]")
    .forEach(
      (button) =>
        (button.onclick = handle(async () => {
          const activity =
            s.automation_potential[Number(button.dataset.candidate)].activity;
          if (![...$("activity").options].some((o) => o.value === activity))
            $("activity").add(new Option(activity, activity));
          $("activity").value = activity;
          evidenceOffset = 0;
          await evidence();
          $("activity").scrollIntoView({ behavior: "smooth" });
        })),
    );
  view("detail");
  evidenceOffset = 0;
  await Promise.all([
    review(id).catch(() => {
      $("review-summary").textContent =
        "Employee review is unavailable for this report.";
      $("review").innerHTML =
        '<tr><td colspan="5">The report and activity evidence remain available below.</td></tr>';
    }),
    evidence(),
  ]);
}
const REVIEW_BADGE = {
  pending: ["", "Explaining…"],
  proposed: ["agent", "Awaiting approval"],
  unsure: ["warning", "Unclear · needs employee"],
  failed: ["danger", "Failed · needs employee"],
  approved: ["success", "Approved"],
  fixed: ["human", "Fixed by employee"],
  explained: ["human", "Explained by employee"],
};
function reviewSummaryText(rv) {
  const s = rv.summary;
  if (!s.total && !rv.session)
    return "No review yet — the employee has not submitted this session from the recorder.";
  const parts = [
    `${s.total} stretch${s.total === 1 ? "" : "es"} explained at a ${Math.round(s.threshold * 100)}% confidence threshold.`,
  ];
  if (rv.generating) parts.push("Explanations are still being generated.");
  if (s.awaiting) parts.push(`${s.awaiting} awaiting approval.`);
  if (s.unclear + s.failed)
    parts.push(
      `${s.unclear + s.failed} unclear and need${s.unclear + s.failed === 1 ? "s" : ""} the employee's explanation.`,
    );
  if (s.resolved) parts.push(`${s.resolved} confirmed.`);
  if (s.total && !s.open) parts.push("Nothing left to review.");
  parts.push(agentRunText(rv.run));
  return parts.join(" ");
}
const RUN_TEXT = {
  queued: "Recording Reviewer queued.",
  running: "Recording Reviewer is explaining the sections.",
  succeeded: "Reviewed by the Recording Reviewer agent",
  failed: "Recording Reviewer run failed",
};
function agentRunText(run) {
  if (!run)
    return "Explained on the employee's computer; no workspace agent run.";
  const text = RUN_TEXT[run.status] ?? run.status;
  return run.finished_at
    ? `${text} · ${new Date(run.finished_at).toLocaleString()}.`
    : text;
}
async function review(id) {
  $("review").innerHTML = '<tr><td colspan="5">Loading review…</td></tr>';
  const rv = await api(`/recordings/${id}/review`);
  if (current?.id !== id) return;
  $("review-summary").textContent = reviewSummaryText(rv);
  const rows = Object.values(rv.items).sort(
    (a, b) =>
      (a.section.whole ? 1 : 0) - (b.section.whole ? 1 : 0) ||
      String(a.section.start ?? "").localeCompare(
        String(b.section.start ?? ""),
      ),
  );
  $("review").innerHTML = rows.length
    ? rows
        .map((it) => {
          const [cls, text] = REVIEW_BADGE[it.status] ?? ["", it.status];
          const sec = it.section;
          const name = sec.whole
            ? "Whole session"
            : sec.name || sec.app || it.id;
          const when = sec.whole
            ? duration(sec.seconds)
            : `${sec.start ? time(sec.start) : ""} · ${duration(sec.seconds)}`;
          const ai = it.label
            ? `<strong>${esc(it.label)}</strong><br /><small>${esc(it.explanation)}</small>`
            : `<small class="muted">${esc(it.error ?? "—")}</small>`;
          const final = it.final_label
            ? `<strong>${esc(it.final_label)}</strong>${it.final_note && it.final_note !== it.explanation ? `<br /><small>${esc(it.final_note)}</small>` : ""}`
            : `<small class="muted">${it.questions?.length ? esc(it.questions.join(" ")) : "—"}</small>`;
          return `<tr><td><strong>${esc(name)}</strong><br /><small>${esc(when)}</small></td><td>${ai}</td><td class="num"><span class="mono-figure">${it.label ? Math.round(it.confidence * 100) + "%" : "—"}</span></td><td><span class="badge ${cls}">${esc(text)}</span></td><td>${final}</td></tr>`;
        })
        .join("")
    : '<tr><td colspan="5">No stretches submitted for review.</td></tr>';
}
async function evidence() {
  const generation = ++requestGeneration;
  $("evidence").innerHTML = '<tr><td colspan="7">Loading evidence…</td></tr>';
  $("prev-evidence").disabled = true;
  $("next-evidence").disabled = true;
  const query = new URLSearchParams({ offset: evidenceOffset, limit: 50 });
  if ($("activity").value) query.set("activity", $("activity").value);
  const result = await api(`/recordings/${current.id}/evidence?${query}`);
  if (generation !== requestGeneration) return;
  $("evidence").innerHTML = result.rows.length
    ? result.rows
        .map(
          (r) =>
            `<tr><td class="num">${r.row}</td><td title="${esc(r.activity)}">${esc(r.activity)}${r.note ? `<small>${esc(r.note)}</small>` : ""}</td><td>${esc(date(r.start))}</td><td class="num">${esc(duration(Number(r.duration_s)))}</td><td>${esc(r.app)}</td><td class="mono">${esc(r.case_id || "Unassigned")}</td><td>${badge(r.activity_source, "unknown")} ${badge(r.case_source, "unassigned")}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="7">No evidence rows for this selection.</td></tr>';
  $("evidence-page").textContent =
    `${result.rows.length ? evidenceOffset + 1 : 0}–${evidenceOffset + result.rows.length} of ${result.total} steps`;
  $("prev-evidence").disabled = evidenceOffset === 0;
  $("next-evidence").disabled = evidenceOffset + 50 >= result.total;
}
$("signout").onclick = handle(async () => {
  await api("/auth/session", { method: "DELETE" });
  ++requestGeneration;
  current = null;
  $("reports").replaceChildren();
  $("evidence").replaceChildren();
  $("signout").hidden = true;
  navigate("/signin/");
});
$("company").onchange = handle(async () => {
  reportOffset = 0;
  await reports();
});
$("copy-company").onclick = handle(async () => {
  await navigator.clipboard.writeText($("company").value);
  message("Company ID copied.");
});
$("back").onclick = handle(async () => {
  view("workspace");
  await reports();
});
$("prev-reports").onclick = handle(async () => {
  reportOffset = Math.max(0, reportOffset - 20);
  await reports();
});
$("next-reports").onclick = handle(async () => {
  reportOffset += 20;
  await reports();
});
$("activity").onchange = handle(async () => {
  evidenceOffset = 0;
  await evidence();
});
$("prev-evidence").onclick = handle(async () => {
  evidenceOffset = Math.max(0, evidenceOffset - 50);
  await evidence();
});
$("next-evidence").onclick = handle(async () => {
  evidenceOffset += 50;
  await evidence();
});
try {
  await enter();
} catch (error) {
  if (error.message !== "Sign in to continue") message(error.message);
}
