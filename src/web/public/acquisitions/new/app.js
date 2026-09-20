import {
  mountShell,
  $,
  esc,
  badge,
  table,
  section,
  message,
  navigate,
} from "/lib/components.js";
import { integer, DEMO_NOTE } from "/lib/format.js";
import { addCompany, refresh } from "/lib/store.js";
import { api } from "/lib/auth.js";

const STEPS = [
  "Create acquisition",
  "Upload files",
  "Detect datasets",
  "Map fields",
  "Preview transforms",
  "Review records",
  "Approve import",
];
const analyst = await mountShell();

// Every step is backed by the import-job API: the browser only keeps the
// wizard position, the company it created and the import jobs the backend
// returned. Files are parsed, mapped, normalised and staged server-side and
// nothing becomes a canonical record until step 7 approves each job.
const wizard = {
  step: 0,
  profile: {
    name: "",
    location: "",
    industry: "",
    acquired: "",
    description: "",
  },
  company: null, // {id, slug, name, ...} from POST /portfolio/companies
  jobs: [], // import-job views from the API, in upload order
  previews: {}, // jobId -> preview rows
  busy: false,
};
let DATASETS = {};
if (analyst) {
  DATASETS = await api("/import-datasets");
  render();
}

const datasetLabel = (key) => DATASETS[key]?.label ?? key;
const hasFields = (job) =>
  Object.keys(DATASETS[job.dataset]?.fields ?? {}).length > 0;
const exceptions = () => wizard.jobs.flatMap((j) => j.exceptions ?? []);

async function run(work, failure) {
  if (wizard.busy) return false;
  wizard.busy = true;
  render();
  try {
    await work();
    return true;
  } catch (error) {
    message(`${failure}${error.message ? `: ${error.message}` : ""}`, "danger");
    return false;
  } finally {
    wizard.busy = false;
    render();
  }
}

function render() {
  const view = $("view");
  const last = wizard.step === STEPS.length - 1;
  view.innerHTML = `
    <p><a class="back" href="/acquisitions/">← Acquisitions</a></p>
    <div class="page-head">
      <div><p class="eyebrow">New acquisition</p><h1>${esc(wizard.profile.name || "Add an acquisition")}</h1><p class="muted">Vista proposes; you decide. Nothing enters the portfolio until step 7.</p></div>
    </div>
    <ol class="wizard-steps">${STEPS.map((s, i) => `<li ${i === wizard.step ? 'aria-current="step"' : ""} class="${i < wizard.step ? "done" : ""}">${esc(s)}</li>`).join("")}</ol>
    <div id="wizard" class="wizard-body"></div>
    <div class="wizard-nav">
      <button id="back" class="ghost" ${wizard.step === 0 || wizard.busy ? "disabled" : ""}>← Back</button>
      <button id="next" class="solid" ${wizard.busy ? "disabled" : ""}>${wizard.busy ? "Working…" : last ? "Approve import & open workspace" : "Continue →"}</button>
    </div>`;
  [
    stepProfile,
    stepUpload,
    stepDetect,
    stepMap,
    stepPreview,
    stepReview,
    stepApprove,
  ][wizard.step]();
  $("back").onclick = () => {
    wizard.step--;
    render();
  };
  $("next").onclick = async () => {
    const error = validate[wizard.step]?.();
    if (error) return message(error, "");
    message();
    const advance = ADVANCE[wizard.step];
    if (advance && !(await run(advance, "Could not continue"))) return;
    if (last) return;
    wizard.step++;
    render();
  };
}

// ---- Step 1 ---------------------------------------------------------------
function stepProfile() {
  const p = wizard.profile;
  const locked = Boolean(wizard.company);
  $("wizard").innerHTML = `
    <div class="two-col">
      <form id="profile" class="form-grid">
        <label class="full">Company name<input name="name" required value="${esc(p.name)}" placeholder="Cedar Climate" ${locked ? "readonly" : ""} /></label>
        <label>Location<input name="location" value="${esc(p.location)}" placeholder="Boston, MA" ${locked ? "readonly" : ""} /></label>
        <label>Acquisition date<input name="acquired" type="date" value="${esc(p.acquired)}" ${locked ? "readonly" : ""} /></label>
        <label class="full">Industry<input name="industry" value="${esc(p.industry)}" placeholder="Residential HVAC service" ${locked ? "readonly" : ""} /></label>
        <label class="full">Description<textarea name="description" placeholder="One paragraph from the CIM or deal memo." ${locked ? "readonly" : ""}>${esc(p.description)}</textarea></label>
      </form>
      <aside class="paper">
        <h2>Demo shortcut</h2>
        <p class="muted">Fill the profile with the third synthetic company and load its four messy export files at step 2.</p>
        <button id="use-cedar" class="quiet" ${locked ? "disabled" : ""}>Use Cedar Climate sample</button>
        <p class="demo-line">${esc(DEMO_NOTE)}</p>
        ${locked ? `<p class="muted">Profile saved to the portfolio as <span class="mono">${esc(wizard.company.slug)}</span>.</p>` : ""}
      </aside>
    </div>`;
  $("profile").oninput = () =>
    Object.assign(
      wizard.profile,
      Object.fromEntries(new FormData($("profile"))),
    );
  $("use-cedar").onclick = async () => {
    const manifest = await fetch("/demo/cedar/manifest.json", {
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    Object.assign(wizard.profile, manifest?.profile ?? CEDAR_FALLBACK);
    wizard.useSample = true;
    render();
  };
}
const CEDAR_FALLBACK = {
  name: "Cedar Climate",
  location: "Boston, MA",
  industry: "Residential HVAC service",
  acquired: "2026-09-08",
  description: "",
};

// ---- Step 2 ---------------------------------------------------------------
function stepUpload() {
  $("wizard").innerHTML = `
    <div class="dropzone" id="drop">
      Drop CSV or XLSX exports here — customers, open AR, vendor purchases, software.<br />
      <label for="file-input">or choose files</label><input id="file-input" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple />
      <div class="upload-groups"><span>Customers</span><span>Invoices / AR</span><span>Vendors / purchases</span><span>Software subscriptions</span><span>Profile PDF / Markdown (parsed later)</span></div>
    </div>
    <div class="block-head staged-head"><h2>Files staged (${wizard.jobs.length})</h2><div class="block-aside"><button id="load-sample" class="quiet sm" ${wizard.busy ? "disabled" : ""}>Load Cedar sample files</button></div></div>
    ${table(
      [
        {
          label: "File",
          render: (j) => `<span class="mono">${esc(j.filename)}</span>`,
        },
        {
          label: "Rows",
          num: true,
          render: (j) => esc(integer(j.recordsDetected)),
        },
        { label: "Columns", render: (j) => esc((j.columns ?? []).join(", ")) },
        { label: "Status", render: (j) => badge(j.status.replace(/_/g, " ")) },
        {
          label: "",
          render: (j) =>
            `<button class="link sm" data-remove="${esc(j.id)}">Remove</button>`,
        },
      ],
      wizard.jobs,
      { empty: "No files yet. Drop exports above or load the sample set." },
    )}`;
  const drop = $("drop");
  drop.ondragover = (e) => {
    e.preventDefault();
    drop.classList.add("over");
  };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    addFiles([...e.dataTransfer.files]);
  };
  $("file-input").onchange = (e) => addFiles([...e.target.files]);
  $("load-sample").onclick = () =>
    run(async () => {
      const manifest = await fetch("/demo/cedar/manifest.json", {
        cache: "no-store",
      }).then((r) => r.json());
      for (const f of manifest.files) {
        const blob = await fetch(`/demo/cedar/${f.name}`, {
          cache: "no-store",
        }).then((r) => r.blob());
        await upload(new File([blob], f.name, { type: f.type }));
      }
    }, "Could not load the sample files");
  $("wizard")
    .querySelectorAll("[data-remove]")
    .forEach((b) => {
      b.onclick = () => {
        wizard.jobs = wizard.jobs.filter((j) => j.id !== b.dataset.remove);
        render();
      };
    });
}
function addFiles(list) {
  return run(async () => {
    for (const file of list) {
      if (!/\.(csv|xlsx)$/i.test(file.name)) {
        message(
          `${file.name}: only CSV and XLSX exports are accepted. Profile documents are parsed later by the backend importer.`,
        );
        continue;
      }
      await upload(file);
    }
  }, "Upload failed");
}
async function toBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
async function upload(file) {
  const job = await api(`/companies/${wizard.company.id}/imports`, {
    method: "POST",
    body: JSON.stringify({
      name: file.name,
      content: await toBase64(file),
      mime_type: file.type || "",
    }),
  });
  wizard.jobs = wizard.jobs.filter((j) => j.filename !== job.filename);
  wizard.jobs.push(job);
}
function replaceJob(job) {
  wizard.jobs = wizard.jobs.map((j) => (j.id === job.id ? job : j));
}

// ---- Step 3 ---------------------------------------------------------------
function stepDetect() {
  $("wizard").innerHTML = `
    <p class="muted">Vista scores each file's headers against the canonical datasets. Change a type if the guess is wrong; mappings are re-proposed.</p>
    <div class="detect">${wizard.jobs
      .map(
        (j) => `<div class="paper">
        <h3 class="mono">${esc(j.filename)}</h3>
        <p class="muted">${esc(integer(j.recordsDetected))} rows · ${esc((j.columns ?? []).length)} columns · detected <b>${esc(datasetLabel(j.detection?.dataset ?? j.dataset))}</b> ${badge(`${Math.round((j.detection?.confidence ?? 0) * 100)}% confidence`, (j.detection?.confidence ?? 0) >= 0.8 ? "success" : "warning")}</p>
        <label>Dataset type<select data-job="${esc(j.id)}" ${wizard.busy ? "disabled" : ""}>${Object.entries(
          DATASETS,
        )
          .map(
            ([k, d]) =>
              `<option value="${k}" ${k === j.dataset ? "selected" : ""}>${esc(d.label)}</option>`,
          )
          .join("")}</select></label>
        <p class="cols"><b>Columns:</b> ${esc((j.columns ?? []).join(" · "))}</p>
      </div>`,
      )
      .join("")}</div>`;
  $("wizard")
    .querySelectorAll("select[data-job]")
    .forEach((s) => {
      s.onchange = () =>
        run(async () => {
          replaceJob(
            await api(`/import-jobs/${s.dataset.job}/dataset`, {
              method: "POST",
              body: JSON.stringify({ dataset: s.value }),
            }),
          );
        }, "Could not change the dataset type");
    });
}

// ---- Step 4 ---------------------------------------------------------------
function stepMap() {
  $("wizard").innerHTML = `
    <p class="muted">One Vista field per source column. Anything under 90% confidence is marked <b>Review</b> and needs your confirmation. Required fields must be mapped.</p>
    ${wizard.jobs
      .filter(hasFields)
      .map(
        (j) =>
          `<section class="block"><div class="block-head"><h2 class="mono">${esc(j.filename)}</h2><div class="block-aside">${esc(datasetLabel(j.dataset))} · ${j.mappings.filter((m) => m.status === "Review").length} to review</div></div>
        ${table(
          [
            {
              label: "Source column",
              render: (m) => `<b>${esc(m.source)}</b>`,
            },
            {
              label: "Example value",
              render: (m) => `<span class="mono">${esc(m.example)}</span>`,
            },
            {
              label: "Vista field",
              render: (m) =>
                `<select class="sm" data-map="${esc(j.id)}:${esc(m.source)}"><option value="">— ignore —</option>${Object.entries(
                  DATASETS[j.dataset].fields,
                )
                  .map(
                    ([k, d]) =>
                      `<option value="${k}" ${k === m.target ? "selected" : ""}>${esc(d.label)}${d.required ? " *" : ""}</option>`,
                  )
                  .join("")}</select>`,
            },
            {
              label: "Confidence",
              num: true,
              render: (m) =>
                m.confidence ? `${Math.round(m.confidence * 100)}%` : "—",
            },
            {
              label: "Status",
              render: (m) =>
                m.status === "Confirmed"
                  ? badge("Confirmed", "success")
                  : m.status === "Review"
                    ? `${badge("Review")} <button class="sm" data-confirm="${esc(j.id)}:${esc(m.source)}">Confirm</button>`
                    : badge("Ready"),
            },
          ],
          j.mappings,
        )}</section>`,
      )
      .join("")}`;
  const find = (key) => {
    const [jobId, source] = key.split(/:(.*)/s);
    return wizard.jobs
      .find((j) => j.id === jobId)
      .mappings.find((m) => m.source === source);
  };
  $("wizard")
    .querySelectorAll("select[data-map]")
    .forEach((s) => {
      s.onchange = () => {
        const m = find(s.dataset.map);
        m.target = s.value || null;
        m.confidence = s.value ? 1 : 0;
        m.status = s.value ? "Confirmed" : "Ready";
        m.decided = true;
        render();
      };
    });
  $("wizard")
    .querySelectorAll("[data-confirm]")
    .forEach((b) => {
      b.onclick = () => {
        const m = find(b.dataset.confirm);
        m.status = "Confirmed";
        m.confidence = 1;
        m.decided = true;
        render();
      };
    });
}

// ---- Step 5 ---------------------------------------------------------------
function stepPreview() {
  $("wizard").innerHTML = `
    <p class="muted">Deterministic clean-up rules applied to your rows — dates to ISO, currency to numbers, states to two letters, phone formats, status codes. Distinct examples from the first 60 rows of each file.</p>
    ${wizard.jobs
      .filter(hasFields)
      .map((j) =>
        section(
          j.filename,
          table(
            [
              { label: "Field", render: (r) => esc(r.label ?? r.field) },
              {
                label: "Source value",
                render: (r) => `<span class="mono">${esc(r.source)}</span>`,
              },
              { label: "→", render: () => "→" },
              {
                label: "Normalized",
                render: (r) => `<span class="mono">${esc(r.normalized)}</span>`,
              },
            ],
            wizard.previews[j.id] ?? [],
            {
              empty: wizard.busy
                ? "Loading…"
                : "No values change for this file.",
            },
          ),
        ),
      )
      .join("")}`;
}

// ---- Step 6 ---------------------------------------------------------------
function stepReview() {
  const xs = exceptions();
  const pending = xs.filter((x) => !x.decision).length;
  $("wizard").innerHTML = `
    <p class="muted">Records Vista is not confident about. ${pending ? `<b>${pending}</b> still need a decision — you can also leave them for later; they stay visible in the company's Data tab.` : "All records decided."}</p>
    ${
      xs.length
        ? xs
            .map(
              (
                x,
              ) => `<div class="exception" data-id="${esc(x.uuid)}" ${x.decision ? `data-decided="${esc(x.decision)}"` : ""}>
        <div class="pair"><small>${esc(x.type)} · ${esc(Math.round((x.confidence ?? 0) * 100))}% similar</small><b>${esc(x.left)}</b><small>${x.leftRow ? `source row ${esc(x.leftRow)}` : "this import"}</small></div>
        <div class="pair"><small>Compared with</small><b>${esc(x.right)}</b><small>${x.rightRow ? `source row ${esc(x.rightRow)}` : "existing portfolio vendor"}</small></div>
        <div class="actions">${x.actions.map((a) => `<button class="sm ${x.decision === a ? "solid" : ""}" data-decide="${esc(a)}" ${wizard.busy || x.decision ? "disabled" : ""}>${esc(a)}</button>`).join("")}</div>
      </div>`,
            )
            .join("")
        : `<p class="empty">No ambiguous records in this import.</p>`
    }`;
  $("wizard")
    .querySelectorAll("[data-decide]")
    .forEach((b) => {
      b.onclick = () =>
        run(async () => {
          const x = xs.find(
            (e) => e.uuid === b.closest(".exception").dataset.id,
          );
          const updated = await api(
            `/import-jobs/${x.importJobId}/exceptions/${x.id}`,
            {
              method: "POST",
              body: JSON.stringify({ decision: b.dataset.decide }),
            },
          );
          const job = wizard.jobs.find((j) => j.id === x.importJobId);
          job.exceptions = job.exceptions.map((e) =>
            e.uuid === updated.uuid ? updated : e,
          );
        }, "Could not record the decision");
    });
}

// ---- Step 7 ---------------------------------------------------------------
function stepApprove() {
  const jobs = wizard.jobs.filter(hasFields);
  const total = jobs.reduce((n, j) => n + (j.recordsDetected ?? 0), 0);
  const reviewed = jobs.reduce((n, j) => n + (j.recordsNeedingReview ?? 0), 0);
  const xs = exceptions();
  const decided = xs.filter((x) => x.decision).length;
  $("wizard").innerHTML = `
    <div class="approve-figures">
      <div><b>${esc(integer(total))}</b><span>Records to import</span></div>
      <div><b>${esc(integer(total - reviewed))}</b><span>Auto-accepted (≥90%)</span></div>
      <div><b>${esc(integer(reviewed))}</b><span>Mapped under review</span></div>
      <div><b>${esc(decided)} / ${esc(xs.length)}</b><span>Exceptions decided</span></div>
    </div>
    <div class="two-col">
      <section>
        <h2>${esc(wizard.profile.name)}</h2>
        <dl class="defs">
          <div><dt>Location</dt><dd>${esc(wizard.profile.location || "—")}</dd></div>
          <div><dt>Acquired</dt><dd>${esc(wizard.profile.acquired || "—")}</dd></div>
          <div><dt>Industry</dt><dd>${esc(wizard.profile.industry || "—")}</dd></div>
          <div><dt>Import jobs</dt><dd class="mono">${jobs.map((j) => esc(j.id.slice(0, 8))).join(", ")}</dd></div>
        </dl>
      </section>
      <section>
        <h2>Datasets</h2>
        <ul class="checklist">${jobs.map((j) => `<li><span>${esc(datasetLabel(j.dataset))} <small class="mono">${esc(j.filename)}</small></span><b>${esc(integer(j.recordsDetected))}</b></li>`).join("")}</ul>
      </section>
    </div>
    <p class="demo-line">Every record keeps its source file, row, original values and the mapping confidence. Undecided exceptions stay open on the company's Data tab.</p>`;
}

const validate = {
  0: () =>
    wizard.profile.name.trim()
      ? ""
      : "Give the acquisition a company name before continuing.",
  1: () =>
    wizard.jobs.length
      ? ""
      : "Stage at least one export (or load the Cedar sample files).",
  3: () => {
    for (const j of wizard.jobs.filter(hasFields)) {
      for (const [key, d] of Object.entries(DATASETS[j.dataset].fields)) {
        if (d.required && !j.mappings.some((m) => m.target === key))
          return `${j.filename}: required field “${d.label}” is not mapped.`;
      }
      if (j.mappings.some((m) => m.status === "Review"))
        return `${j.filename}: confirm or change the mappings marked Review.`;
    }
    return "";
  },
};

// Server calls made when leaving a step.
const ADVANCE = {
  0: async () => {
    if (wizard.company) return;
    const p = wizard.profile;
    wizard.company = await addCompany({
      name: p.name.trim(),
      location: p.location,
      industry: p.industry,
      description: p.description,
      acquired: p.acquired || null,
    });
  },
  3: async () => {
    for (const j of wizard.jobs.filter(hasFields)) {
      const body = {
        mappings: j.mappings.map((m) => ({
          source: m.source,
          target: m.target,
          confirmed: Boolean(m.decided) || m.status === "Confirmed",
        })),
      };
      replaceJob(
        await api(`/import-jobs/${j.id}/mappings/approve`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
    }
    for (const j of wizard.jobs.filter(hasFields))
      wizard.previews[j.id] = await api(`/import-jobs/${j.id}/preview`);
  },
  6: async () => {
    for (const j of wizard.jobs.filter(hasFields))
      replaceJob(await api(`/import-jobs/${j.id}/approve`, { method: "POST" }));
    await refresh();
    navigate(`/company/?id=${wizard.company.id}`);
  },
};
