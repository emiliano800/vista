import { mountShell, $, esc, badge, table, section, message, navigate } from "/lib/components.js";
import { integer, DEMO_NOTE } from "/lib/format.js";
import { companies, addCompany, nextId } from "/lib/store.js";
import { parseCsv, DATASETS, detectDataset, proposeMappings, transformRows, previewTransforms, detectExceptions, cedarSampleFiles, CEDAR_PROFILE, buildCompany } from "/lib/importer.js";

const STEPS = ["Create acquisition", "Upload files", "Detect datasets", "Map fields", "Preview transforms", "Review records", "Approve import"];
const analyst = mountShell();

// Wizard state lives in memory until approval; only the approved company is
// written to the store.
const wizard = {
  step: 0,
  profile: { name: "", location: "", industry: "", acquired: "", description: "" },
  files: [], // {name, text, columns, rows, detection, dataset, mappings}
  exceptions: [],
  jobId: null,
};
if (analyst) render();

function slug(name) {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").split("-")[0] || "company";
  let id = base;
  let n = 2;
  while (companies().some((c) => c.id === id)) id = `${base}-${n++}`;
  return id;
}

function render() {
  const view = $("view");
  view.innerHTML = `
    <p><a class="back" href="/acquisitions/">← Acquisitions</a></p>
    <div class="page-head">
      <div><p class="eyebrow">New acquisition</p><h1>${esc(wizard.profile.name || "Add an acquisition")}</h1><p class="muted">Vista proposes; you decide. Nothing enters the portfolio until step 7.</p></div>
    </div>
    <ol class="wizard-steps">${STEPS.map((s, i) => `<li ${i === wizard.step ? 'aria-current="step"' : ""} class="${i < wizard.step ? "done" : ""}">${esc(s)}</li>`).join("")}</ol>
    <div id="wizard" class="wizard-body"></div>
    <div class="wizard-nav">
      <button id="back" class="ghost" ${wizard.step === 0 ? "disabled" : ""}>← Back</button>
      <button id="next" class="solid">${wizard.step === STEPS.length - 1 ? "Approve import & open workspace" : "Continue →"}</button>
    </div>`;
  [stepProfile, stepUpload, stepDetect, stepMap, stepPreview, stepReview, stepApprove][wizard.step]();
  $("back").onclick = () => {
    wizard.step--;
    render();
  };
  $("next").onclick = () => {
    const error = validate[wizard.step]?.();
    if (error) return message(error, "");
    message();
    if (wizard.step === STEPS.length - 1) return approve();
    wizard.step++;
    render();
  };
}

// ---- Step 1 ---------------------------------------------------------------
function stepProfile() {
  const p = wizard.profile;
  $("wizard").innerHTML = `
    <div class="two-col">
      <form id="profile" class="form-grid">
        <label class="full">Company name<input name="name" required value="${esc(p.name)}" placeholder="Cedar Climate" /></label>
        <label>Location<input name="location" value="${esc(p.location)}" placeholder="Boston, MA" /></label>
        <label>Acquisition date<input name="acquired" type="date" value="${esc(p.acquired)}" /></label>
        <label class="full">Industry<input name="industry" value="${esc(p.industry)}" placeholder="Residential HVAC service" /></label>
        <label class="full">Description<textarea name="description" placeholder="One paragraph from the CIM or deal memo.">${esc(p.description)}</textarea></label>
      </form>
      <aside class="paper">
        <h2>Demo shortcut</h2>
        <p class="muted">Fill the profile with the third synthetic company and load its four messy export files at step 2.</p>
        <button id="use-cedar" class="quiet">Use Cedar Climate sample</button>
        <p class="demo-line">${esc(DEMO_NOTE)}</p>
      </aside>
    </div>`;
  $("profile").oninput = () => Object.assign(wizard.profile, Object.fromEntries(new FormData($("profile"))));
  $("use-cedar").onclick = () => {
    Object.assign(wizard.profile, CEDAR_PROFILE);
    wizard.useSample = true;
    render();
  };
}

// ---- Step 2 ---------------------------------------------------------------
function stepUpload() {
  $("wizard").innerHTML = `
    <div class="dropzone" id="drop">
      Drop CSV exports here — customers, open AR, vendor purchases, software.<br />
      <label for="file-input">or choose files</label><input id="file-input" type="file" accept=".csv,text/csv" multiple />
      <div class="upload-groups"><span>Customers</span><span>Invoices / AR</span><span>Vendors / purchases</span><span>Software subscriptions</span><span>Profile PDF / Markdown (parsed later)</span></div>
    </div>
    <div class="block-head staged-head"><h2>Files staged (${wizard.files.length})</h2><div class="block-aside"><button id="load-sample" class="quiet sm">Load Cedar sample files</button></div></div>
    ${table(
      [
        { label: "File", render: (f) => `<span class="mono">${esc(f.name)}</span>` },
        { label: "Rows", num: true, render: (f) => esc(integer(f.rows.length)) },
        { label: "Columns", render: (f) => esc(f.columns.join(", ")) },
        { label: "", render: (f) => `<button class="link sm" data-remove="${esc(f.name)}">Remove</button>` },
      ],
      wizard.files,
      { empty: "No files yet. Drop CSV exports above or load the sample set." },
    )}`;
  const drop = $("drop");
  drop.ondragover = (e) => {
    e.preventDefault();
    drop.classList.add("over");
  };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = async (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    await addFiles([...e.dataTransfer.files]);
  };
  $("file-input").onchange = async (e) => addFiles([...e.target.files]);
  $("load-sample").onclick = () => {
    for (const f of cedarSampleFiles()) stageFile(f.name, f.text);
    if (!wizard.profile.name) Object.assign(wizard.profile, CEDAR_PROFILE);
    render();
  };
  $("wizard").querySelectorAll("[data-remove]").forEach((b) => {
    b.onclick = () => {
      wizard.files = wizard.files.filter((f) => f.name !== b.dataset.remove);
      render();
    };
  });
}
async function addFiles(list) {
  for (const file of list) {
    if (!/\.csv$/i.test(file.name)) {
      message(`${file.name}: only CSV exports are parsed in this demo build (XLSX and PDF profile documents will be handled by the backend importer).`);
      continue;
    }
    stageFile(file.name, await file.text());
  }
  render();
}
function stageFile(name, text) {
  const { columns, rows } = parseCsv(text);
  if (!columns.length) return;
  const detection = detectDataset(columns, name);
  const dataset = detection.confidence >= 0.5 ? detection.dataset : "other";
  wizard.files = wizard.files.filter((f) => f.name !== name);
  wizard.files.push({ name, text, columns, rows, detection, dataset, mappings: proposeMappings(dataset, columns, rows) });
}

// ---- Step 3 ---------------------------------------------------------------
function stepDetect() {
  $("wizard").innerHTML = `
    <p class="muted">Vista scores each file's headers against the canonical datasets. Change a type if the guess is wrong; mappings are re-proposed.</p>
    <div class="detect">${wizard.files
      .map(
        (f, i) => `<div class="paper">
        <h3 class="mono">${esc(f.name)}</h3>
        <p class="muted">${esc(integer(f.rows.length))} rows · ${esc(f.columns.length)} columns · detected <b>${esc(f.detection.label)}</b> ${badge(`${Math.round(f.detection.confidence * 100)}% confidence`, f.detection.confidence >= 0.8 ? "success" : "warning")}</p>
        <label>Dataset type<select data-file="${i}">${Object.entries(DATASETS)
          .map(([k, d]) => `<option value="${k}" ${k === f.dataset ? "selected" : ""}>${esc(d.label)}</option>`)
          .join("")}</select></label>
        <p class="cols"><b>Columns:</b> ${esc(f.columns.join(" · "))}</p>
      </div>`,
      )
      .join("")}</div>`;
  $("wizard").querySelectorAll("select[data-file]").forEach((s) => {
    s.onchange = () => {
      const f = wizard.files[Number(s.dataset.file)];
      f.dataset = s.value;
      f.mappings = proposeMappings(f.dataset, f.columns, f.rows);
      render();
    };
  });
}

// ---- Step 4 ---------------------------------------------------------------
function stepMap() {
  $("wizard").innerHTML = `
    <p class="muted">One Vista field per source column. Anything under 90% confidence is marked <b>Review</b> and needs your confirmation. Required fields must be mapped.</p>
    ${wizard.files
      .filter((f) => DATASETS[f.dataset]?.fields && Object.keys(DATASETS[f.dataset].fields).length)
      .map(
        (f, fi) =>
          `<section class="block"><div class="block-head"><h2 class="mono">${esc(f.name)}</h2><div class="block-aside">${esc(DATASETS[f.dataset].label)} · ${f.mappings.filter((m) => m.status === "Review").length} to review</div></div>
        ${table(
          [
            { label: "Source column", render: (m) => `<b>${esc(m.source)}</b>` },
            { label: "Example value", render: (m) => `<span class="mono">${esc(m.example)}</span>` },
            {
              label: "Vista field",
              render: (m) => `<select class="sm" data-map="${wizard.files.indexOf(f)}:${esc(m.source)}"><option value="">— ignore —</option>${Object.entries(DATASETS[f.dataset].fields)
                .map(([k, d]) => `<option value="${k}" ${k === m.target ? "selected" : ""}>${esc(d.label)}${d.required ? " *" : ""}</option>`)
                .join("")}</select>`,
            },
            { label: "Confidence", num: true, render: (m) => (m.confidence ? `${Math.round(m.confidence * 100)}%` : "—") },
            { label: "Status", render: (m) => (m.status === "Confirmed" ? badge("Confirmed", "success") : m.status === "Review" ? `${badge("Review")} <button class="sm" data-confirm="${wizard.files.indexOf(f)}:${esc(m.source)}">Confirm</button>` : badge("Ready")) },
          ],
          f.mappings,
        )}</section>`,
      )
      .join("")}`;
  const find = (key) => {
    const [fi, source] = key.split(/:(.*)/s);
    return wizard.files[Number(fi)].mappings.find((m) => m.source === source);
  };
  $("wizard").querySelectorAll("select[data-map]").forEach((s) => {
    s.onchange = () => {
      const m = find(s.dataset.map);
      m.target = s.value || null;
      m.confidence = s.value ? 1 : 0;
      m.status = s.value ? "Confirmed" : "Ready";
      render();
    };
  });
  $("wizard").querySelectorAll("[data-confirm]").forEach((b) => {
    b.onclick = () => {
      const m = find(b.dataset.confirm);
      m.status = "Confirmed";
      m.confidence = 1;
      render();
    };
  });
}

// ---- Step 5 ---------------------------------------------------------------
function stepPreview() {
  $("wizard").innerHTML = `
    <p class="muted">Deterministic clean-up rules applied to your rows — dates to ISO, currency to numbers, states to two letters, phone formats, status codes. Distinct examples from the first 60 rows of each file.</p>
    ${wizard.files
      .filter((f) => f.dataset !== "other" && f.dataset !== "profile")
      .map((f) => {
        const rows = previewTransforms(f.dataset, f.rows, f.mappings);
        return section(
          f.name,
          table(
            [
              { label: "Field", render: (r) => esc(DATASETS[f.dataset].fields[r.field]?.label ?? r.field) },
              { label: "Source value", render: (r) => `<span class="mono">${esc(r.source)}</span>` },
              { label: "→", render: () => "→" },
              { label: "Normalized", render: (r) => `<span class="mono">${esc(r.normalized)}</span>` },
            ],
            rows,
            { empty: "No values change for this file." },
          ),
        );
      })
      .join("")}`;
}

// ---- Step 6 ---------------------------------------------------------------
function transformed() {
  const job = wizard.jobId ?? (wizard.jobId = `imp-${slug(wizard.profile.name || "company")}-${String(nextId("job")).padStart(3, "0")}`);
  const out = {};
  for (const f of wizard.files) {
    if (!DATASETS[f.dataset]?.fields || !Object.keys(DATASETS[f.dataset].fields).length) continue;
    out[f.dataset] = { file: f.name, records: transformRows(f.dataset, f.rows, f.mappings, { file: f.name, job }) };
  }
  return { job, datasets: out };
}
function stepReview() {
  const { datasets } = transformed();
  if (!wizard.exceptions.length || wizard.exceptionsStale) {
    const existing = companies().flatMap((c) => c.vendors);
    const prior = new Map(wizard.exceptions.map((x) => [`${x.left}|${x.right}`, x.decision]));
    wizard.exceptions = detectExceptions(datasets, existing).map((x) => ({ ...x, decision: prior.get(`${x.left}|${x.right}`) ?? null }));
    wizard.exceptionsStale = false;
  }
  const xs = wizard.exceptions;
  const pending = xs.filter((x) => !x.decision).length;
  $("wizard").innerHTML = `
    <p class="muted">Records Vista is not confident about. ${pending ? `<b>${pending}</b> still need a decision — you can also leave them for later; they stay visible in the company's Data tab.` : "All records decided."}</p>
    ${xs.length ? xs.map((x) => `<div class="exception" data-id="${esc(x.id)}" ${x.decision ? `data-decided="${esc(x.decision)}"` : ""}>
        <div class="pair"><small>${esc(x.type)} · ${esc(Math.round(x.confidence * 100))}% similar</small><b>${esc(x.left)}</b><small>${x.leftRow ? `source row ${esc(x.leftRow)}` : "this import"}</small></div>
        <div class="pair"><small>Compared with</small><b>${esc(x.right)}</b><small>${x.rightRow ? `source row ${esc(x.rightRow)}` : "existing portfolio vendor"}</small></div>
        <div class="actions">${x.actions.map((a) => `<button class="sm ${x.decision === a ? "solid" : ""}" data-decide="${esc(a)}">${esc(a)}</button>`).join("")}</div>
      </div>`).join("") : `<p class="empty">No ambiguous records in this import.</p>`}`;
  $("wizard").querySelectorAll("[data-decide]").forEach((b) => {
    b.onclick = () => {
      const x = xs.find((e) => e.id === b.closest(".exception").dataset.id);
      x.decision = x.decision === b.dataset.decide ? null : b.dataset.decide;
      render();
    };
  });
}

// ---- Step 7 ---------------------------------------------------------------
function stepApprove() {
  const { job, datasets } = transformed();
  const all = Object.values(datasets).flatMap((d) => d.records);
  const reviewed = all.filter((r) => r.provenance.review === "reviewed").length;
  const decided = wizard.exceptions.filter((x) => x.decision).length;
  $("wizard").innerHTML = `
    <div class="approve-figures">
      <div><b>${esc(integer(all.length))}</b><span>Records to import</span></div>
      <div><b>${esc(integer(all.length - reviewed))}</b><span>Auto-accepted (≥90%)</span></div>
      <div><b>${esc(integer(reviewed))}</b><span>Mapped under review</span></div>
      <div><b>${esc(decided)} / ${esc(wizard.exceptions.length)}</b><span>Exceptions decided</span></div>
    </div>
    <div class="two-col">
      <section>
        <h2>${esc(wizard.profile.name)}</h2>
        <dl class="defs">
          <div><dt>Location</dt><dd>${esc(wizard.profile.location || "—")}</dd></div>
          <div><dt>Acquired</dt><dd>${esc(wizard.profile.acquired || "—")}</dd></div>
          <div><dt>Industry</dt><dd>${esc(wizard.profile.industry || "—")}</dd></div>
          <div><dt>Import job</dt><dd class="mono">${esc(job)}</dd></div>
        </dl>
      </section>
      <section>
        <h2>Datasets</h2>
        <ul class="checklist">${Object.entries(datasets).map(([k, d]) => `<li><span>${esc(DATASETS[k].label)} <small class="mono">${esc(d.file)}</small></span><b>${esc(integer(d.records.length))}</b></li>`).join("")}</ul>
      </section>
    </div>
    <p class="demo-line">Every record keeps its source file, row, original values and the mapping confidence. Undecided exceptions stay open on the company's Data tab.</p>`;
}

const validate = {
  0: () => (wizard.profile.name.trim() ? "" : "Give the acquisition a company name before continuing."),
  1: () => (wizard.files.length ? "" : "Stage at least one CSV export (or load the Cedar sample files)."),
  3: () => {
    for (const f of wizard.files) {
      const fields = DATASETS[f.dataset]?.fields ?? {};
      for (const [key, d] of Object.entries(fields)) {
        if (d.required && !f.mappings.some((m) => m.target === key)) return `${f.name}: required field “${d.label}” is not mapped.`;
      }
      if (f.mappings.some((m) => m.status === "Review")) return `${f.name}: confirm or change the mappings marked Review.`;
    }
    wizard.exceptionsStale = true;
    return "";
  },
};

function approve() {
  const { job, datasets } = transformed();
  const id = slug(wizard.profile.name);
  const c = buildCompany({ id, profile: wizard.profile, datasets, exceptions: wizard.exceptions, job });
  addCompany(c);
  navigate(`/company/?id=${id}`);
}
