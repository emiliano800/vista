import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(
  new URL("../public/index.html", import.meta.url),
  "utf8",
);
const source = fs.readFileSync(
  new URL("../public/app.js", import.meta.url),
  "utf8",
);
const companyId = "00000000-0000-0000-0000-000000000001";
const recordId = "00000000-0000-0000-0000-000000000002";
const activity = "<img src=x onerror=alert(1)> Review invoice";
const record = {
  id: recordId,
  source_id: "session",
  started_at: "2026-09-19T09:00:00Z",
  uploaded_at: "2026-09-19T10:00:00Z",
  active_seconds: 60,
  summary: {
    n_steps: 1,
    n_cases: 1,
    n_uncorrelated_steps: 0,
    activities: [{ activity }],
    automation_potential: [{ activity, score: 0.8, hours_total: 0.01 }],
  },
};
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("UI did not reach the expected state");
}
function setup({ empty = false } = {}) {
  const dom = new JSDOM(html, {
    url: "https://bumpsolutions.org",
    runScripts: "outside-only",
  });
  let signedIn = false;
  const requests = [];
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  dom.window.fetch = async (path, options) => {
    requests.push({ path, options });
    if (path === "/api/auth/session") {
      if (options.method === "DELETE") {
        signedIn = false;
        return new Response(null, { status: 204 });
      }
      if (JSON.parse(options.body).token !== "k".repeat(64))
        return Response.json({ detail: "Invalid access key" }, { status: 401 });
      signedIn = true;
      return Response.json({ email: "test@example.com" });
    }
    if (!signedIn)
      return Response.json({ detail: "Sign in to continue" }, { status: 401 });
    if (path === "/api/auth/me")
      return Response.json({ email: "test@example.com" });
    if (path === "/api/deals")
      return Response.json([{ id: companyId, name: "Harbor Heating" }]);
    if (path.startsWith(`/api/deals/${companyId}/recordings`))
      return Response.json(empty ? [] : [record]);
    if (path === `/api/recordings/${recordId}`) return Response.json(record);
    if (path.startsWith(`/api/recordings/${recordId}/evidence`))
      return Response.json({
        total: 1,
        rows: [
          {
            row: 1,
            activity,
            note: "<script>bad()</script>",
            start: record.started_at,
            duration_s: "60",
            app: "ERP",
            case_id: "INV-1",
            activity_source: "rule",
            case_source: "observed",
          },
        ],
      });
    if (path === `/api/recordings/${recordId}/review`)
      return Response.json({
        threshold: 0.88,
        generating: false,
        summary: { total: 1, threshold: 0.88, awaiting: 0, unclear: 1, failed: 0, resolved: 0, open: 1 },
        session: null,
        items: {
          S1: {
            id: "S1",
            section: { name: "ERP", app: "ERP", start: record.started_at, seconds: 300, whole: false },
            status: "unsure",
            label: "<b>Enter</b> bills",
            explanation: "Typing bills.",
            confidence: 0.41,
            questions: ["What for?"],
            final_label: "",
            final_note: "",
          },
        },
      });
    throw new Error(`Unexpected request ${path}`);
  };
  dom.window.eval(`(async () => {${source}\n})()`);
  return { dom, requests, $: (id) => dom.window.document.getElementById(id) };
}
async function login(ui, key = "k".repeat(64)) {
  await settle(() => !ui.$("login").hidden);
  ui.$("key").value = key;
  ui.$("login-form").requestSubmit(ui.$("login-form").querySelector("button"));
}
test("sign in, view real report data, filter supporting evidence and sign out without persisting the key", async () => {
  const ui = setup();
  try {
    await login(ui);
    await settle(() => !!ui.$("reports").querySelector("[data-report]"));
    assert.equal(ui.$("key").value, "");
    assert.equal(ui.dom.window.localStorage.length, 0);
    assert.equal(ui.$("company-id").textContent, companyId);
    ui.$("reports").querySelector("button").click();
    await settle(() => ui.$("evidence").textContent.includes("INV-1"));
    assert.equal(ui.dom.window.document.querySelectorAll("img").length, 0);
    assert.ok(ui.$("evidence").textContent.includes("<script>bad()</script>"));
    await settle(() => ui.$("review").textContent.includes("Unclear"));
    assert.ok(ui.$("review").textContent.includes("<b>Enter</b> bills"));
    assert.equal(ui.$("review").querySelectorAll("b").length, 0);
    assert.ok(ui.$("review-summary").textContent.includes("1 unclear"));
    ui.$("candidates").querySelector("button").click();
    await settle(() => ui.requests.some((r) => r.path.includes("activity=")));
    assert.equal(
      new URL(ui.$("download-json").href).pathname,
      `/api/recordings/${recordId}/download`,
    );
    assert.equal(ui.$("activity").value, activity);
    ui.$("signout").click();
    await settle(() => !ui.$("login").hidden);
    assert.equal(ui.$("evidence").textContent, "");
    assert.ok(
      ui.requests
        .filter((r) => r.options.method === "POST")
        .every((r) => r.options.headers["X-Vista-Request"] === "1"),
    );
  } finally {
    ui.dom.window.close();
  }
});
test("invalid keys show an actionable error and empty companies show the upload instructions", async () => {
  const ui = setup({ empty: true });
  try {
    await login(ui, "x".repeat(64));
    await settle(() =>
      ui.$("message").textContent.includes("Invalid access key"),
    );
    assert.equal(ui.$("key").value, "");
    await login(ui);
    await settle(() =>
      ui.$("reports").textContent.includes("No uploaded reports yet"),
    );
    assert.equal(ui.$("workspace").hidden, false);
    assert.equal(ui.$("next-reports").disabled, true);
  } finally {
    ui.dom.window.close();
  }
});
