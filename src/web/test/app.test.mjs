import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const read = (path) =>
  fs.readFileSync(new URL(`../public/${path}`, import.meta.url), "utf8");
const signinHtml = read("signin/index.html");
const signinSource = read("signin/signin.js");
const navSource = read("nav.js");
const companyId = "00000000-0000-0000-0000-000000000001";
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("UI did not reach the expected state");
}
function mockApi(state) {
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
      return Response.json([{ id: companyId, name: "Harbor Heating" }]);
    throw new Error(`Unexpected request ${path}`);
  };
}
function page(html, source, url, state) {
  const dom = new JSDOM(html, { url, runScripts: "outside-only" });
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  dom.window.VISTA_NAVIGATE = (to) => state.navigations.push(to);
  dom.window.fetch = mockApi(state);
  dom.window.eval(`(async () => {${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}
const freshState = (signedIn) => ({
  signedIn,
  requests: [],
  navigations: [],
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
test("hamburger drawer opens, marks the current page and closes on Escape", async () => {
  const state = freshState(false);
  const dom = new JSDOM(signinHtml, {
    url: "https://bumpsolutions.org/signin/",
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
    assert.equal(new URL(current.href).pathname, "/signin/");
    dom.window.document.dispatchEvent(
      new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    assert.equal($("drawer").hidden, true);
    // Every page links the same drawer destinations.
    for (const html of [signinHtml, read("index.html")]) {
      for (const path of ["/", "/account/", "/signin/"])
        assert.ok(html.includes(`href="${path}"`), `${path} linked`);
    }
    void state;
  } finally {
    dom.window.close();
  }
});
