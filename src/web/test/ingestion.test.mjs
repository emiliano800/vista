import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(
  new URL("../public/account/index.html", import.meta.url),
  "utf8",
);
const source = fs.readFileSync(
  new URL("../public/account/app.js", import.meta.url),
  "utf8",
);
const company = "00000000-0000-0000-0000-000000000001";
const batchId = "00000000-0000-0000-0000-000000000002";
const malicious = "<img src=x onerror=alert(1)>";
function batch(status = "preview") {
  const t = {
    id: "0",
    filename: "policies.csv",
    sheet: "",
    kind: "policies",
    columns: ["policy_number", "commission_pct"],
    mapping: {
      policy_number: "policy_number",
      commission_pct: "commission_pct",
    },
    records: [
      { row: 2, values: { policy_number: malicious, commission_pct: "12" } },
    ],
  };
  return {
    id: batchId,
    deal_id: company,
    status,
    as_of: "2026-03-31",
    created_at: "2026-09-19T12:00:00Z",
    files: ["policies.csv"],
    record_count: 1,
    tables: [t],
    schemas: {
      policies: {
        label: "Policies",
        required: ["policy_number", "commission_pct"],
        optional: [],
      },
    },
    events: [
      {
        action: "confirmed_and_analyzed",
        by: "demo-user",
        at: "2026-09-19T12:00:00Z",
      },
    ],
    analysis:
      status === "completed"
        ? {
            summary: {
              records: 1,
              clients: 0,
              policies: 1,
              findings: 1,
              commission_variance: "1584.48",
            },
            checks: [
              {
                name: "Commission reconciliation",
                status: "completed",
                detail: "Matched one statement.",
              },
            ],
            findings: [
              {
                id: "123456789abcdef0",
                category: "commission",
                kind: "observed_fact",
                title: "Commission below the policy rate",
                detail: malicious,
                recommendation: "Ask the carrier to investigate.",
                amount: "1584.48",
                status: "open",
                calculation: {
                  premium: "79224",
                  rate: "12",
                  expected: "9506.88",
                  paid: "7922.4",
                  difference: "1584.48",
                },
                evidence: [
                  {
                    table_id: "0",
                    filename: "policies.csv",
                    sheet: "",
                    row: 2,
                    values: t.records[0].values,
                  },
                ],
              },
            ],
          }
        : {},
  };
}
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
function mount({
  saved = null,
  signedIn = true,
  failCommit = false,
  role = "owner",
  unavailable = false,
  empty = false,
} = {}) {
  const state = { saved, requests: [], navigations: [], failCommit };
  const dom = new JSDOM(html, {
    url: "https://vista.test/account/",
    runScripts: "outside-only",
  });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  dom.window.VISTA_NAVIGATE = (url) => state.navigations.push(url);
  dom.window.fetch = async (path, options = {}) => {
    state.requests.push({ path, options });
    if (!signedIn)
      return Response.json({ detail: "Sign in to continue" }, { status: 401 });
    if (path === "/api/auth/me")
      return Response.json({ email: "demo@meridianrisk.com" });
    if (path === "/api/deals")
      return Response.json(
        empty ? [] : [{ id: company, name: "Meridian Risk Partners, LLC" }],
      );
    if (path === `/api/deals/${company}/imports`) {
      if (unavailable)
        return Response.json({ detail: "Not Found" }, { status: 404 });
      if (options.method === "POST") {
        state.saved = batch();
        return Response.json(state.saved, { status: 201 });
      }
      return Response.json({ role, imports: state.saved ? [state.saved] : [] });
    }
    if (path === `/api/imports/${batchId}/commit`) {
      if (state.failCommit)
        return Response.json(
          { detail: "policies.csv, row 2: invalid commission_pct." },
          { status: 422 },
        );
      state.saved = batch("completed");
      return Response.json(state.saved);
    }
    if (path === `/api/imports/${batchId}/findings/123456789abcdef0`) {
      state.saved.analysis.findings[0].status = JSON.parse(options.body).status;
      return Response.json(state.saved);
    }
    if (path === `/api/imports/${batchId}`) return Response.json(state.saved);
    throw new Error("Unexpected URL " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}
async function upload(ui) {
  await settle(() => ui.$("import-top").disabled === false);
  ui.$("import-top").click();
  const input = ui.$("file-input");
  Object.defineProperty(input, "files", {
    value: [
      new ui.dom.window.File(
        ["policy_number,commission_pct\nP-1,12\n"],
        "policies.csv",
        { type: "text/csv" },
      ),
    ],
  });
  input.dispatchEvent(new ui.dom.window.Event("change"));
  ui.$("snapshot-date").value = "2026-03-31";
  ui.$("upload-files").click();
  await settle(() => !!ui.$("confirm-import"));
}
test("ingestion flow: upload, map, calculate, inspect escaped evidence, review, reload", async () => {
  const ui = mount();
  try {
    await upload(ui);
    const sent = JSON.parse(
      ui.state.requests.find((r) => r.options.method === "POST").options.body,
    );
    assert.equal(sent.as_of, "2026-03-31");
    assert.equal(
      Buffer.from(sent.files[0].content, "base64").toString(),
      "policy_number,commission_pct\nP-1,12\n",
    );
    const select = ui.dom.window.document.querySelector(
      '[data-field="commission_pct"]',
    );
    select.value = "";
    select.dispatchEvent(new ui.dom.window.Event("change"));
    ui.$("confirm-import").click();
    assert.match(ui.$("import-error").textContent, /Map the required fields/);
    select.value = "commission_pct";
    select.dispatchEvent(new ui.dom.window.Event("change"));
    ui.$("confirm-import").click();
    await settle(() => !ui.$("import-dialog").open);
    assert.match(ui.$("content").textContent, /\$1,584\.48/);
    assert.equal(ui.dom.window.document.querySelectorAll("img").length, 0);
    ui.dom.window.document.querySelector("[data-finding]").click();
    assert.equal(ui.$("evidence-dialog").open, true);
    assert.match(ui.$("evidence-body").textContent, /\$9,506\.88/);
    assert.match(ui.$("evidence-body").textContent, /Row 2/);
    assert.ok(ui.$("evidence-body").textContent.includes(malicious));
    ui.dom.window.document.querySelector('[data-decision="reviewed"]').click();
    await settle(() => ui.$("finding-count").textContent === "0");
    assert.equal(ui.state.saved.analysis.findings[0].status, "reviewed");
    assert.equal(ui.dom.window.localStorage.length, 0);
    const reload = mount({ saved: ui.state.saved });
    try {
      await settle(() =>
        reload.$("content").textContent.includes("Your business"),
      );
      assert.equal(reload.$("finding-count").textContent, "0");
    } finally {
      reload.dom.window.close();
    }
    ui.$("close-evidence").click();
    ui.dom.window.document.querySelector('[data-view="sources"]').click();
    ui.dom.window.document.querySelector("[data-source]").click();
    assert.ok(ui.$("source-body").textContent.includes(malicious));
    assert.equal(ui.$("source-body").querySelectorAll("img").length, 0);
  } finally {
    ui.dom.window.close();
  }
});
test("failed analysis returns to mapping and preserves a retryable import", async () => {
  const ui = mount({ failCommit: true });
  try {
    await upload(ui);
    ui.$("confirm-import").click();
    await settle(() =>
      ui.$("import-error").textContent.includes("invalid commission_pct"),
    );
    assert.equal(ui.$("import-dialog").open, true);
    assert.equal(ui.state.saved.status, "preview");
    assert.equal(
      ui.dom.window.document.querySelector('[data-field="commission_pct"]')
        .value,
      "commission_pct",
    );
    ui.state.failCommit = false;
    ui.$("confirm-import").click();
    await settle(() => !ui.$("import-dialog").open);
  } finally {
    ui.dom.window.close();
  }
});
test("viewer can inspect sources but cannot import or review", async () => {
  const ui = mount({ saved: batch("completed"), role: "viewer" });
  try {
    await settle(
      () => !!ui.dom.window.document.querySelector("[data-finding]"),
    );
    assert.equal(ui.$("import-top").disabled, true);
    ui.dom.window.document.querySelector("[data-finding]").click();
    assert.equal(
      ui.dom.window.document.querySelectorAll("[data-decision]").length,
      0,
    );
  } finally {
    ui.dom.window.close();
  }
});
test("signed out, unassigned and unavailable backend have explicit states", async () => {
  for (const config of [
    { signedIn: false },
    { empty: true },
    { unavailable: true },
  ]) {
    const ui = mount(config);
    try {
      if (config.signedIn === false)
        await settle(() => ui.state.navigations.includes("/signin/"));
      else if (config.empty)
        await settle(() =>
          ui.$("content").textContent.includes("No company assigned"),
        );
      else {
        await settle(() => !ui.$("message").hidden);
        assert.equal(ui.$("import-top").disabled, true);
      }
    } finally {
      ui.dom.window.close();
    }
  }
});
