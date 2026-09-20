const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) =>
  Number(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
const number = (value) => Number(value).toLocaleString("en-US");
const day = (value) =>
  new Date(`${value}T12:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
const navigate = (url) =>
  (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);
const paths = {
  overview:
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
  database:
    '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2m10 0h2a2 2 0 0 1 2 2v2m0 10v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10M7 8h6M7 16h4"/>',
  archive:
    '<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v14h14V7M10 11h4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m18 6-12 12M6 6l12 12"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h5"/>',
  upload:
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  check: '<path d="m9 12 2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  shield:
    '<path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] ?? paths.file}</svg>`;
for (const el of document.querySelectorAll("[data-icon]"))
  el.innerHTML = icon(el.dataset.icon);
let companies = [],
  batches = [],
  active = null,
  preview = null,
  files = [],
  role = "viewer",
  view = "overview",
  filter = "open",
  busy = false,
  generation = 0;
const canEdit = () => role === "owner" || role === "member";
const company = () => companies.find((c) => c.id === $("company").value);
const isMeridian = () => /meridian risk partners/i.test(company()?.name ?? "");
function message(text = "") {
  $("message").textContent = text;
  $("message").hidden = !text;
}
function importError(text = "") {
  $("import-error").textContent = text;
  $("import-error").hidden = !text;
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
  if (response.status === 401) {
    navigate("/signin/");
    throw new Error("Sign in to continue");
  }
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      /* non-JSON proxy response */
    }
    throw new Error(
      response.status === 404 && (!body?.detail || body.detail === "Not Found")
        ? "Data imports are not available yet. Ask your workspace administrator to finish enabling imports."
        : typeof body?.detail === "string"
          ? body.detail
          : response.status === 422
            ? "The import could not be accepted. Check the file format and required fields."
            : "The workspace could not complete this request. Please try again.",
    );
  }
  return response.status === 204 ? null : response.json();
}
function action(fn) {
  return async (e) => {
    try {
      message();
      await fn(e);
    } catch (error) {
      if (error.message !== "Sign in to continue") message(error.message);
    }
  };
}
const statusTag = (status) =>
  `<span class="tag ${status === "reviewed" || status === "completed" ? "success" : status === "open" ? "warning" : ""}">${esc({ open: "Needs review", reviewed: "Reviewed", dismissed: "Dismissed", completed: "Imported", preview: "Mapping needed" }[status] ?? status)}</span>`;
const category = (value) =>
  ({
    commission: "Commission discrepancy",
    receivable: "Receivable exposure",
    data_quality: "Data quality",
  })[value] ?? value;
function batchPicker() {
  const completed = batches.filter((b) => b.status === "completed");
  if (completed.length < 2) return "";
  return `<label class="small">Import snapshot <select id="batch-select" class="batch-select">${completed.map((b) => `<option value="${esc(b.id)}" ${b.id === active?.id ? "selected" : ""}>${day(b.as_of)} · ${number(b.record_count)} records · ${new Date(b.created_at).toLocaleString()}</option>`).join("")}</select></label>`;
}
function render() {
  const count =
    active?.analysis.findings.filter((f) => f.status === "open").length ?? 0;
  $("source-count").textContent = active?.files.length ?? 0;
  $("finding-count").textContent = count;
  $("as-of").textContent = active ? `Data as of ${day(active.as_of)}` : "";
  $("view-name").textContent = {
    overview: "Overview",
    sources: "Data sources",
    findings: "Findings",
  }[view];
  document.title = `Vista · ${$("view-name").textContent}`;
  $("import-top").disabled = !company() || !canEdit();
  document.querySelectorAll("[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  });
  if (!company()) {
    $("content").innerHTML =
      '<div class="empty"><h2>No company assigned.</h2><p>Ask your workspace administrator to add your company.</p></div>';
    return;
  }
  $("content").innerHTML =
    view === "sources"
      ? sourcesView()
      : view === "findings"
        ? findingsView()
        : active
          ? overviewView()
          : welcomeView();
  bindContent();
}
function welcomeView() {
  const pending = batches.find((b) => b.status === "preview");
  return `<div class="intro"><div><span class="eyebrow">${esc(company().name)}</span><h1>A clearer picture.<br>From the data<br>you already <i>have.</i></h1><p class="lede">Turn scattered exports into a shared view of the business. Bring in your records, confirm how they fit, and review the opportunities inside.</p><div class="actions"><button class="primary" data-import ${!canEdit() ? "disabled" : ""}>${icon("upload")}Import company data</button>${isMeridian() && canEdit() ? '<button class="text-button" data-sample>Try Meridian sample data</button>' : ""}</div><p class="spacing-1 small">CSV, TSV and Excel workbooks · Up to 5 MiB per import</p></div><div class="source-stack"><header><span class="eyebrow">Your first import</span><span class="tag">${isMeridian() ? "Synthetic demo available" : "Files you already use"}</span></header>${[
    ["Policy book", "Policies, rates and client references"],
    ["Commission statements", "Carrier payments and premium basis"],
    ["Billing & receivables", "Invoices, due dates and balances"],
  ]
    .map(
      ([name, desc]) =>
        `<div class="sample-row"><span class="file-icon">${icon("file")}</span><div><strong>${name}</strong><small>${desc}</small></div><span class="tag">CSV / XLSX</span></div>`,
    )
    .join(
      "",
    )}<div class="stack-footer">${icon("shield")}Source records stay attached to every finding.</div></div></div>${pending ? `<div class="quiet-note">${icon("clock")}<div><strong>An import is waiting for mapping review.</strong><br>${pending.files.length} files · ${number(pending.record_count)} records</div><button data-resume="${esc(pending.id)}" ${!canEdit() ? "disabled" : ""}>Resume import</button></div>` : ""}<div class="explain-grid"><article><span class="number">01 / INGEST</span><h3>Start with your exports.</h3><p>Upload the records from your agency system and spreadsheets. Originals are retained with the import.</p></article><article><span class="number">02 / CONFIRM</span><h3>Make the connections clear.</h3><p>Review detected record types and field mappings before they enter your company snapshot.</p></article><article><span class="number">03 / REVIEW</span><h3>See what needs attention.</h3><p>Trace discrepancies to source rows, inspect the calculation, and record your review.</p></article></div><div class="quiet-note">${icon("scan")}<div><strong>Built for a defensible first look.</strong><br>Checks compare imported records. Findings are review candidates; recovered revenue and realized savings require confirmation.</div></div>`;
}
function metrics() {
  const s = active.analysis.summary;
  return `<div class="metrics">${[
    [
      "Records imported",
      number(s.records),
      `${active.files.length} source files`,
    ],
    [
      "Policies in this snapshot",
      number(s.policies),
      `${number(s.clients)} client records`,
    ],
    [
      "Findings to review",
      number(
        active.analysis.findings.filter((f) => f.status === "open").length,
      ),
      "Source-linked exceptions",
    ],
    [
      "Commission discrepancy",
      money(s.commission_variance),
      "Potential recovery · not realized",
    ],
  ]
    .map(
      ([label, value, note], i) =>
        `<div class="metric ${i === 3 ? "emphasis" : ""}"><span class="eyebrow">${label}</span><b>${value}</b><small>${note}</small></div>`,
    )
    .join("")}</div>`;
}
function findingRows(rows) {
  if (!rows.length)
    return '<div class="empty"><h3>No findings in this view.</h3><p>Review check coverage to see which records were assessed.</p></div>';
  return rows
    .map(
      (f) =>
        `<article class="finding-row"><div><span class="eyebrow">${esc(category(f.category))}</span><h3>${esc(f.title)}</h3><p>${esc(f.detail)}</p><p class="spacing-2">${f.evidence.length} source ${f.evidence.length === 1 ? "record" : "records"} · ${esc(f.kind === "observed_fact" ? "Observed fact" : f.kind)}</p></div><section>${statusTag(f.status)}${f.amount !== null ? `<span class="finding-value">${money(f.amount)}</span>` : ""}<button data-finding="${esc(f.id)}">Review evidence ${icon("arrow")}</button></section></article>`,
    )
    .join("");
}
function overviewView() {
  const s = active.analysis.summary;
  return `<div class="page-heading"><div><span class="eyebrow">${esc(company().name)}</span><h1>Your business, <i>in view.</i></h1><p>${number(s.records)} imported records. ${s.findings ? `${number(s.findings)} exceptions worth a closer look.` : "No exceptions found by the available checks."} Every conclusion starts with a source.</p></div><span class="tag success">${icon("check")}Import complete</span></div>${batchPicker()}${metrics()}<div class="overview-grid"><section class="panel"><div class="panel-heading"><h2>Where to focus</h2><button data-go="findings" class="text-button">All findings ${icon("arrow")}</button></div>${findingRows([...active.analysis.findings].sort((a, b) => (a.category === "commission" ? -1 : 0) - (b.category === "commission" ? -1 : 0)).slice(0, 3))}</section><section class="panel"><div class="panel-heading"><h2>What was checked</h2></div>${active.analysis.checks.map((c) => `<div class="check-row">${icon(c.status === "completed" ? "check" : "clock")}<div><strong>${esc(c.name)}</strong><p>${esc(c.detail)}</p>${c.status !== "completed" ? '<span class="tag warning">Needs more data</span>' : ""}</div></div>`).join("")}<p class="spacing-3 small">Rule-based checks calculate amounts and join records. No automatic changes are made to your source systems.</p></section></div><div class="import-note"><span>Snapshot as of ${day(active.as_of)} · ${active.files.length} files · Each import is analyzed independently.</span><a href="/api/imports/${active.id}/export">Download analysis & evidence</a></div>`;
}
function sourcesView() {
  return `<div class="page-heading"><div><span class="eyebrow">The evidence library</span><h1>Data with a <i>paper trail.</i></h1><p>Original columns, source rows and confirmed mappings. Everything behind your company snapshot.</p></div>${active ? `<a class="small" href="/api/imports/${active.id}/export">Download snapshot</a>` : ""}</div>${batchPicker()}${active ? `<section class="panel"><div class="panel-heading"><h2>Current snapshot</h2><span class="small">As of ${day(active.as_of)}</span></div><div class="table-wrap"><table><thead><tr><th>Source file</th><th>Record type</th><th class="num">Records</th><th>Status</th><th></th></tr></thead><tbody>${active.tables.map((t) => `<tr><td>${icon("file")} ${esc(t.filename)}${t.sheet ? `<small>${esc(t.sheet)}</small>` : ""}</td><td>${esc(active.schemas[t.kind].label)}</td><td class="num">${number(t.records.length)}</td><td>${statusTag("completed")}</td><td><button data-source="${esc(t.id)}">Browse records</button></td></tr>`).join("")}</tbody></table></div></section>` : '<div class="empty"><h2>Your source library starts here.</h2><p>Import your company exports to create a snapshot.</p><button data-import>Import data</button></div>'}<section class="panel section-gap"><div class="panel-heading"><h2>Import history</h2><span class="small">Latest 100 imports</span></div>${batches.length ? `<div class="table-wrap"><table><thead><tr><th>Imported</th><th>Files</th><th>Records</th><th>Status</th><th></th></tr></thead><tbody>${batches.map((b) => `<tr><td>${esc(new Date(b.created_at).toLocaleString())}<small>Data as of ${day(b.as_of)}</small></td><td>${b.files.length}</td><td>${number(b.record_count)}</td><td>${statusTag(b.status)}</td><td><button ${b.status === "preview" ? "data-resume" : "data-batch"}="${esc(b.id)}" ${b.status === "preview" && !canEdit() ? "disabled" : ""}>${b.status === "preview" ? "Review mapping" : "Open snapshot"}</button></td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No files imported yet.</p>'}</section>`;
}
function findingsView() {
  const findings = active?.analysis.findings ?? [];
  return `<div class="page-heading"><div><span class="eyebrow">From records to recommendations</span><h1>Attention, with <i>evidence.</i></h1><p>Inspect what was observed, decide what comes next, and keep a record of your review.</p></div>${batchPicker()}</div><div class="filterbar" role="group" aria-label="Finding status">${["open", "reviewed", "dismissed", "all"].map((f) => `<button data-filter="${f}" aria-pressed="${filter === f}" class="${filter === f ? "selected" : ""}">${{ open: "Needs review", reviewed: "Reviewed", dismissed: "Dismissed", all: "All findings" }[f]} · ${findings.filter((r) => f === "all" || r.status === f).length}</button>`).join("")}</div><section class="panel">${findingRows(findings.filter((f) => filter === "all" || f.status === filter))}</section><p class="spacing-4 small">Marking a finding reviewed records your assessment. It does not resolve the discrepancy or count it as realized savings.</p>`;
}
function bindContent() {
  document
    .querySelectorAll("[data-import]")
    .forEach((b) => (b.onclick = openImport));
  document.querySelectorAll("[data-sample]").forEach(
    (b) =>
      (b.onclick = () => {
        openImport();
        loadSample();
      }),
  );
  document.querySelectorAll("[data-go]").forEach(
    (b) =>
      (b.onclick = () => {
        view = b.dataset.go;
        render();
      }),
  );
  document
    .querySelectorAll("[data-finding]")
    .forEach((b) => (b.onclick = () => showFinding(b.dataset.finding)));
  document
    .querySelectorAll("[data-source]")
    .forEach((b) => (b.onclick = () => showSource(b.dataset.source)));
  document.querySelectorAll("[data-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        filter = b.dataset.filter;
        render();
      }),
  );
  document
    .querySelectorAll("[data-batch]")
    .forEach((b) => (b.onclick = action(() => selectBatch(b.dataset.batch))));
  document.querySelectorAll("[data-resume]").forEach(
    (b) =>
      (b.onclick = action(async () => {
        const current = generation;
        const loaded = await api(`/imports/${b.dataset.resume}`);
        if (current !== generation) return;
        preview = loaded;
        openDialog($("import-dialog"));
        renderMapping();
      })),
  );
  if ($("batch-select"))
    $("batch-select").onchange = action((e) => selectBatch(e.target.value));
}
function openDialog(dialog) {
  if (!dialog.open) dialog.showModal();
}
function setStep(stage) {
  ["upload", "map", "analyze"].forEach((name, i) => {
    $(`step-${name}`).className =
      i === stage ? "current" : i < stage ? "done" : "";
  });
}
function openImport() {
  if (!canEdit()) return;
  preview = null;
  files = [];
  importError();
  setStep(0);
  openDialog($("import-dialog"));
  $("import-body").innerHTML =
    `<div id="dropzone" class="dropzone">${icon("upload")}<h3>A few files. A useful first look.</h3><p>Drop CSV, TSV or XLSX exports here, or choose files below.<br>Import related files together so Vista can reconcile them.</p><input id="file-input" type="file" multiple accept=".csv,.tsv,.xlsx" aria-label="Choose company export files"/></div><div id="selected-files" class="selected-files"></div><div class="upload-meta"><label for="snapshot-date">Data as of<input id="snapshot-date" type="date" value="${new Date().toISOString().slice(0, 10)}" required/></label><p class="small">Choose the date of your export. Receivables are assessed against this date, so historical snapshots stay meaningful.</p></div>${isMeridian() ? '<div class="spacing-5 quiet-note"><div><strong>Presenting Meridian?</strong><br>Load five synthetic exports from the demo company.</div><button id="sample-files">Use sample files</button></div>' : ""}<div class="dialog-actions"><span class="small">Up to 12 files, 1,999 rows per table and 5 MiB total.<br>Files are saved to this company when you continue.</span><button class="primary" id="upload-files" disabled>Review field mapping ${icon("arrow")}</button></div>`;
  $("file-input").onchange = () => selectFiles([...$("file-input").files]);
  const drop = $("dropzone");
  drop.ondragover = (e) => {
    e.preventDefault();
    drop.classList.add("dragover");
  };
  drop.ondragleave = () => drop.classList.remove("dragover");
  drop.ondrop = (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");
    selectFiles([...e.dataTransfer.files]);
  };
  if ($("sample-files")) $("sample-files").onclick = loadSample;
  $("upload-files").onclick = uploadFiles;
}
function selectFiles(selected) {
  files = selected;
  importError();
  if (
    !files.length ||
    files.length > 12 ||
    files.reduce((n, f) => n + f.size, 0) > 5 * 1024 * 1024
  ) {
    files = [];
    importError("Choose 1–12 files totaling at most 5 MiB.");
  }
  $("selected-files").innerHTML = files
    .map(
      (f) =>
        `<div class="selected-file"><span>${icon("file")} ${esc(f.name)}</span><span class="small">${Math.max(1, Math.round(f.size / 1024))} KB</span></div>`,
    )
    .join("");
  $("upload-files").disabled = !files.length;
}
async function loadSample() {
  const b = $("sample-files");
  const input = $("file-input");
  if (b) b.disabled = true;
  try {
    const response = await fetch("/demo/meridian/manifest.json");
    if (!response.ok) throw new Error("Sample files are unavailable.");
    const manifest = await response.json();
    const loaded = await Promise.all(
      manifest.files.map(async (name) => {
        const r = await fetch(`/demo/meridian/${encodeURIComponent(name)}`);
        if (!r.ok) throw new Error("A sample file could not be loaded.");
        return new File([await r.blob()], name, { type: "text/csv" });
      }),
    );
    if (!$("import-dialog").open || $("file-input") !== input) return;
    selectFiles(loaded);
    $("snapshot-date").value = manifest.as_of;
  } catch (e) {
    if ($("file-input") === input) importError(e.message);
  } finally {
    if (b) b.disabled = false;
  }
}
function encoded(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        name: file.name,
        content: String(reader.result).split(",")[1],
      });
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}
function setBusy(value) {
  busy = value;
  $("close-import").disabled = value;
  $("company").disabled = value;
}
async function uploadFiles() {
  if (busy) return;
  if (!$("snapshot-date").value) {
    importError("Choose the date of this export.");
    return;
  }
  setBusy(true);
  importError();
  const button = $("upload-files");
  button.disabled = true;
  button.textContent = "Reading and saving files…";
  try {
    const data = await Promise.all(files.map(encoded));
    preview = await api(`/deals/${company().id}/imports`, {
      method: "POST",
      body: JSON.stringify({ files: data, as_of: $("snapshot-date").value }),
    });
    if (preview.status === "completed") {
      active = preview;
      await refreshBatches();
      $("import-dialog").close();
      view = "overview";
      render();
      message("These files were already imported. Opened the saved snapshot.");
    } else {
      await refreshBatches();
      renderMapping();
    }
  } catch (e) {
    importError(e.message);
    button.disabled = false;
    button.innerHTML = `Review field mapping ${icon("arrow")}`;
  } finally {
    setBusy(false);
  }
}
function renderMapping() {
  setStep(1);
  importError();
  $("import-body").innerHTML =
    `<h3>Confirm how these records fit.</h3><p class="small">${preview.files.length} files · ${number(preview.record_count)} records · As of ${day(preview.as_of)}. Types and fields are suggested from column headers. Review them before confirming.</p>${preview.tables
      .map(
        (t) =>
          `<section class="mapping-card"><div class="mapping-head"><div><strong>${esc(t.filename)}</strong><small>${t.sheet ? `${esc(t.sheet)} · ` : ""}${number(t.records.length)} records</small></div><label class="small">Record type<select data-type="${t.id}" aria-label="Record type for ${esc(t.filename)}"><option value="unclassified">Choose record type</option>${Object.entries(
            preview.schemas,
          )
            .map(
              ([k, s]) =>
                `<option value="${k}" ${t.kind === k ? "selected" : ""}>${esc(s.label)}</option>`,
            )
            .join(
              "",
            )}</select></label></div><div class="mapping-fields" id="fields-${t.id}">${mappingFields(t)}</div><details><summary>Preview original columns and records</summary><div class="table-wrap">${tableMarkup(t, t.records.slice(0, 3))}</div></details></section>`,
      )
      .join(
        "",
      )}<div class="dialog-actions"><span class="small">Confirming saves the mappings and runs checks on this import. Originals remain unchanged.</span><button id="confirm-import" class="primary">Confirm & analyze ${icon("arrow")}</button></div>`;
  document.querySelectorAll("[data-type]").forEach(
    (el) =>
      (el.onchange = () => {
        const t = preview.tables.find((t) => t.id === el.dataset.type);
        t.kind = el.value;
        t.mapping = {};
        if (preview.schemas[t.kind]) {
          for (const field of [
            ...preview.schemas[t.kind].required,
            ...preview.schemas[t.kind].optional,
          ]) {
            const matches = t.columns.filter(
              (c) =>
                c
                  .trim()
                  .toLowerCase()
                  .replace(/[\s-]+/g, "_") === field,
            );
            if (matches.length === 1) t.mapping[field] = matches[0];
          }
        }
        $(`fields-${t.id}`).innerHTML = mappingFields(t);
        bindMapping();
      }),
  );
  bindMapping();
  $("confirm-import").onclick = confirmImport;
}
function mappingFields(t) {
  const schema = preview.schemas[t.kind];
  if (!schema)
    return '<p class="small">Select a record type to map its fields.</p>';
  return [...schema.required, ...schema.optional]
    .map(
      (f) =>
        `<label>${esc(f.replaceAll("_", " "))}${schema.required.includes(f) ? " *" : " (optional)"}<select data-map-table="${t.id}" data-field="${f}" aria-label="${esc(t.filename)}: ${esc(f)}"><option value="">${schema.required.includes(f) ? "Choose source column" : "Not mapped"}</option>${t.columns.map((c) => `<option value="${esc(c)}" ${t.mapping[f] === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>`,
    )
    .join("");
}
function bindMapping() {
  document.querySelectorAll("[data-map-table]").forEach(
    (el) =>
      (el.onchange = () => {
        const t = preview.tables.find((t) => t.id === el.dataset.mapTable);
        if (el.value) t.mapping[el.dataset.field] = el.value;
        else delete t.mapping[el.dataset.field];
      }),
  );
}
async function confirmImport() {
  if (busy) return;
  for (const t of preview.tables) {
    const schema = preview.schemas[t.kind];
    if (!schema || schema.required.some((f) => !t.mapping[f])) {
      importError(`Map the required fields for ${t.filename}.`);
      return;
    }
  }
  setBusy(true);
  setStep(2);
  importError();
  $("import-body").innerHTML =
    `<div class="busy">${icon("scan")}<h2>Following the records.</h2><p>Validating mappings, matching policies and calculating discrepancies.</p><span class="small">Each result will include its source rows.</span></div>`;
  try {
    active = await api(`/imports/${preview.id}/commit`, {
      method: "POST",
      body: JSON.stringify({
        tables: preview.tables.map(({ id, kind, mapping }) => ({
          id,
          kind,
          mapping,
        })),
      }),
    });
    await refreshBatches();
    $("import-dialog").close();
    view = "overview";
    filter = "open";
    render();
    message(
      `Import complete. ${number(active.analysis.summary.records)} records checked; ${active.analysis.findings.length} findings ready for review.`,
    );
  } catch (e) {
    renderMapping();
    importError(e.message);
  } finally {
    setBusy(false);
  }
}
function tableMarkup(table, records) {
  return `<table><thead><tr><th>Source row</th>${table.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${records.map((r) => `<tr><td>${r.row}</td>${table.columns.map((c) => `<td>${esc(r.values[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function showSource(id, page = 0) {
  const t = active.tables.find((t) => t.id === id);
  $("source-title").textContent = t.filename + (t.sheet ? ` · ${t.sheet}` : "");
  $("source-body").innerHTML =
    `<p class="small">${number(t.records.length)} records · Original headers and values retained. Row numbers include the header row.</p><div class="table-wrap">${tableMarkup(t, t.records.slice(page * 25, page * 25 + 25))}</div><div class="pager"><button id="source-prev" ${page === 0 ? "disabled" : ""}>Previous</button><span>${page * 25 + 1}–${Math.min(t.records.length, (page + 1) * 25)} of ${number(t.records.length)}</span><button id="source-next" ${(page + 1) * 25 >= t.records.length ? "disabled" : ""}>Next</button></div><details class="section-gap"><summary>Confirmed field mapping</summary><dl>${Object.entries(
      t.mapping,
    )
      .map(([f, c]) => `<dt>${esc(c)}</dt><dd>${esc(f)}</dd>`)
      .join("")}</dl></details>`;
  $("source-prev").onclick = () => showSource(id, page - 1);
  $("source-next").onclick = () => showSource(id, page + 1);
  openDialog($("source-dialog"));
}
function showFinding(id) {
  const f = active.analysis.findings.find((f) => f.id === id),
    c = f.calculation;
  const keyFields = new Set([
    "policy_number",
    "statement_id",
    "insured_name",
    "client_name",
    "premium_basis",
    "commission_paid",
    "commission_pct",
    "expected_commission",
    "invoice_id",
    "due_date",
    "balance",
    "payment_plan",
    "carrier_code",
  ]);
  $("evidence-body").innerHTML =
    `<span class="tag">Observed fact</span> ${statusTag(f.status)}<h2 id="evidence-title">${esc(f.title)}</h2><p class="evidence-detail">${esc(f.detail)}</p>${c ? `<div class="calculation"><span class="eyebrow">Recalculated from source records</span><div class="calc-line"><span>Statement premium × policy rate</span><strong>${money(c.premium)} × ${esc(c.rate)}%</strong></div><div class="calc-line"><span>Expected commission</span><strong>${money(c.expected)}</strong></div><div class="calc-line"><span>Commission paid</span><strong>${money(c.paid)}</strong></div><div class="calc-line total"><span>Discrepancy to investigate</span><strong>${money(c.difference)}</strong></div><p class="spacing-6 small">Potential recovery only. Confirm policy terms and carrier adjustments.</p></div>` : f.amount !== null ? `<div class="calculation"><div class="calc-line"><span>Outstanding balance</span><strong>${money(f.amount)}</strong></div><span class="small">Receivable exposure as of ${day(active.as_of)}. Not realized savings.</span></div>` : ""}<div class="recommendation"><span class="eyebrow">Recommended next step</span><p>${esc(f.recommendation)}</p></div><h3>Follow the evidence</h3>${f.evidence
      .map((e) => {
        const fields = Object.entries(e.values);
        const shown = fields.filter(
          ([k]) =>
            keyFields.has(k) ||
            keyFields.has(k.toLowerCase().replace(/[\s-]+/g, "_")),
        );
        return `<section class="evidence-source"><header><strong>${icon("file")} ${esc(e.filename)}${e.sheet ? ` · ${esc(e.sheet)}` : ""}</strong><span class="tag">Row ${e.row}</span></header><dl>${(shown.length ? shown : fields.slice(0, 6)).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v) || "—"}</dd>`).join("")}</dl><details><summary>All original fields</summary><dl>${fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v) || "—"}</dd>`).join("")}</dl></details></section>`;
      })
      .join(
        "",
      )}<details class="section-gap"><summary class="small">Review history</summary><ul class="small">${active.events
      .filter(
        (e) => e.finding_id === id || e.action === "confirmed_and_analyzed",
      )
      .map(
        (e) =>
          `<li>${esc(new Date(e.at).toLocaleString())} · ${esc(e.status ?? "Calculated after mapping confirmation")} · User ${esc(e.by)}</li>`,
      )
      .join(
        "",
      )}</ul></details><div id="decision-error" class="notice" role="alert" hidden></div><div class="evidence-actions">${canEdit() ? `<button class="primary" data-decision="reviewed" ${f.status === "reviewed" ? "disabled" : ""}>${icon("check")}Mark reviewed</button><button data-decision="dismissed" ${f.status === "dismissed" ? "disabled" : ""}>Dismiss</button>${f.status !== "open" ? '<button data-decision="open">Reopen</button>' : ""}` : '<span class="small">You have read-only access to this company.</span>'}<a class="small" href="/api/imports/${active.id}/export">Download evidence</a></div>`;
  document.querySelectorAll("[data-decision]").forEach(
    (b) =>
      (b.onclick = async () => {
        document
          .querySelectorAll("[data-decision]")
          .forEach((x) => (x.disabled = true));
        try {
          active = await api(`/imports/${active.id}/findings/${id}`, {
            method: "POST",
            body: JSON.stringify({ status: b.dataset.decision }),
          });
          render();
          showFinding(id);
        } catch (e) {
          showFinding(id);
          $("decision-error").hidden = false;
          $("decision-error").textContent = e.message;
        }
      }),
  );
  openDialog($("evidence-dialog"));
}
async function refreshBatches() {
  const result = await api(`/deals/${company().id}/imports`);
  batches = result.imports;
  role = result.role;
}
async function selectBatch(id) {
  const current = generation;
  const loaded = await api(`/imports/${id}`);
  if (current !== generation) return;
  active = loaded;
  render();
}
async function enterCompany() {
  const current = ++generation;
  active = null;
  batches = [];
  role = "viewer";
  render();
  message();
  try {
    const result = await api(`/deals/${company().id}/imports`);
    if (current !== generation) return;
    batches = result.imports;
    role = result.role;
    const latest = batches.find((b) => b.status === "completed");
    if (latest) {
      const loaded = await api(`/imports/${latest.id}`);
      if (current !== generation) return;
      active = loaded;
    }
    render();
  } catch (e) {
    if (current === generation) {
      render();
      message(e.message);
    }
  }
}
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      view = b.dataset.view;
      render();
    }),
);
$("import-top").onclick = openImport;
$("close-import").onclick = () => {
  if (!busy) {
    $("import-dialog").close();
    render();
  }
};
$("import-dialog").addEventListener("cancel", (e) => {
  if (busy) e.preventDefault();
  else render();
});
$("close-evidence").onclick = () => $("evidence-dialog").close();
$("close-source").onclick = () => $("source-dialog").close();
$("company").onchange = action(enterCompany);
$("signout").onclick = action(async () => {
  await api("/auth/session", { method: "DELETE" });
  navigate("/signin/");
});
try {
  const me = await api("/auth/me");
  $("identity").textContent = me.email;
  companies = await api("/deals");
  $("company").replaceChildren(
    ...companies.map((c) => new Option(c.name, c.id)),
  );
  if (companies.length) await enterCompany();
  else render();
} catch (e) {
  if (e.message !== "Sign in to continue") message(e.message);
}
