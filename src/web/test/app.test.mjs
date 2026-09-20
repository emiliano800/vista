import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const read = (path) =>
  fs.readFileSync(new URL(`../public/${path}`, import.meta.url), "utf8");
const accountHtml = read("account/recordings/index.html");
const accountSource = read("account/recordings/app.js");
const signinHtml = read("signin/index.html");
const signinSource = read("signin/signin.js");
const navSource = read("nav.js");
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
function mockApi(state, { empty = false } = {}) {
  return async (path, options = {}) => {
    state.requests.push({ path, options });
    if (path === "/api/auth/session") {
      if (options.method === "DELETE") {
        state.signedIn = false;
        return new Response(null, { status: 204 });
      }
      if (JSON.parse(options.body).token !== "k".repeat(64))
        return Response.json({ detail: "Invalid access key" }, { status: 401 });
      state.signedIn = true;
      return Response.json({ email: "test@example.com" });
    }
    if (!state.signedIn)
      return Response.json({ detail: "Sign in to continue" }, { status: 401 });
    if (path === "/api/auth/me")
      return Response.json({ email: "test@example.com" });
    if (path === "/api/deals")
      return Response.json(
        empty ? [] : [{ id: companyId, name: "Harbor Heating" }],
      );
    if (path.startsWith(`/api/deals/${companyId}/recordings`))
      return Response.json([record]);
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
}
function page(html, source, url, state, options) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only" });
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  dom.window.VISTA_NAVIGATE = (to) => state.navigations.push(to);
  dom.window.fetch = mockApi(state, options);
  dom.window.eval(`(async () => {${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}
const freshState = (signedIn) => ({
  signedIn,
  requests: [],
  navigations: [],
});
test("signed-in workspace at /account: reports, review, evidence, downloads, sign out", async () => {
  const state = freshState(true);
  const ui = page(
    accountHtml,
    accountSource,
    "https://bumpsolutions.org/account/",
    state,
  );
  try {
    await settle(() => !!ui.$("reports").querySelector("[data-report]"));
    assert.equal(ui.dom.window.localStorage.length, 0);
    assert.equal(ui.$("company-id").textContent, companyId);
    ui.$("reports").querySelector("button").click();
    await settle(() => ui.$("evidence").textContent.includes("INV-1"));
    assert.equal(ui.dom.window.document.querySelectorAll("main img").length, 0);
    assert.ok(ui.$("evidence").textContent.includes("<script>bad()</script>"));
    await settle(() => ui.$("review").textContent.includes("Unclear"));
    assert.ok(ui.$("review").textContent.includes("<b>Enter</b> bills"));
    assert.equal(ui.$("review").querySelectorAll("b").length, 0);
    assert.ok(ui.$("review-summary").textContent.includes("1 unclear"));
    ui.$("candidates").querySelector("button").click();
    await settle(() => state.requests.some((r) => r.path.includes("activity=")));
    assert.equal(
      new URL(ui.$("download-json").href).pathname,
      `/api/recordings/${recordId}/download`,
    );
    assert.equal(ui.$("activity").value, activity);
    ui.$("signout").click();
    await settle(() => state.navigations.includes("/signin/"));
    assert.equal(state.signedIn, false);
    assert.ok(
      state.requests
        .filter((r) => r.options.method === "POST")
        .every((r) => r.options.headers["X-Vista-Request"] === "1"),
    );
  } finally {
    ui.dom.window.close();
  }
});
test("signed-out /account redirects to /signin", async () => {
  const state = freshState(false);
  const ui = page(
    accountHtml,
    accountSource,
    "https://bumpsolutions.org/account/",
    state,
  );
  try {
    await settle(() => state.navigations.includes("/signin/"));
    assert.equal(ui.$("message").textContent, "");
  } finally {
    ui.dom.window.close();
  }
});
test("sign-in page: invalid key shows an error, valid key navigates to /account", async () => {
  const state = freshState(false);
  const ui = page(
    signinHtml,
    signinSource,
    "https://bumpsolutions.org/signin/",
    state,
  );
  try {
    await settle(() => state.requests.some((r) => r.path === "/api/auth/me"));
    ui.$("key").value = "x".repeat(64);
    ui.$("login-form").requestSubmit(
      ui.$("login-form").querySelector("button"),
    );
    await settle(() =>
      ui.$("message").textContent.includes("Invalid access key"),
    );
    assert.equal(ui.$("key").value, "");
    assert.equal(state.navigations.length, 0);
    ui.$("key").value = "k".repeat(64);
    ui.$("login-form").requestSubmit(
      ui.$("login-form").querySelector("button"),
    );
    await settle(() => state.navigations.includes("/account/"));
    assert.equal(ui.dom.window.localStorage.length, 0);
  } finally {
    ui.dom.window.close();
  }
});
test("already-signed-in visitor to /signin is sent to /account", async () => {
  const state = freshState(true);
  const ui = page(
    signinHtml,
    signinSource,
    "https://bumpsolutions.org/signin/",
    state,
  );
  try {
    await settle(() => state.navigations.includes("/account/"));
  } finally {
    ui.dom.window.close();
  }
});
test("account with no companies shows guidance", async () => {
  const state = freshState(true);
  const ui = page(
    accountHtml,
    accountSource,
    "https://bumpsolutions.org/account/",
    state,
    { empty: true },
  );
  try {
    await settle(() =>
      ui.$("reports").textContent.includes("No companies are assigned"),
    );
    assert.equal(ui.$("workspace").hidden, false);
    assert.equal(ui.$("next-reports").disabled, true);
  } finally {
    ui.dom.window.close();
  }
});
test("hamburger drawer opens, marks the current page and closes on Escape", async () => {
  const state = freshState(false);
  const dom = new JSDOM(read("recorder/index.html"), {
    url: "https://bumpsolutions.org/recorder/",
    runScripts: "outside-only",
  });
  try {
    dom.window.eval(navSource);
    const $ = (id) => dom.window.document.getElementById(id);
    assert.equal($("drawer").hidden, true);
    $("menu").click();
    assert.equal($("drawer").hidden, false);
    assert.equal($("menu").getAttribute("aria-expanded"), "true");
    const current = $("drawer").querySelector('a[aria-current="page"]');
    assert.equal(new URL(current.href).pathname, "/recorder/");
    dom.window.document.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    assert.equal($("drawer").hidden, true);
    // Every page links the same drawer destinations.
    for (const html of [accountHtml, signinHtml, read("index.html")]) {
      for (const path of ["/", "/recorder/", "/account/", "/signin/"])
        assert.ok(html.includes(`href="${path}"`), `${path} linked`);
    }
    void state;
  } finally {
    dom.window.close();
  }
});
