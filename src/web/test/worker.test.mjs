import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker.mjs";

test("serves assets with a restrictive CSP and proxies only the report surface", async () => {
  const result = await worker.fetch(new Request("https://bumpsolutions.org/"), {
    ASSETS: { fetch: async () => new Response("Vista") },
  });
  assert.equal(await result.text(), "Vista");
  assert.match(
    result.headers.get("content-security-policy"),
    /frame-ancestors 'none'/,
  );
  assert.equal(
    (
      await worker.fetch(
        new Request("https://bumpsolutions.org/api/tenants"),
        {},
      )
    ).status,
    404,
  );
  assert.equal(
    (
      await worker.fetch(
        new Request("https://bumpsolutions.org/api/deals", { method: "POST" }),
        {},
      )
    ).status,
    405,
  );
  const status = async (path, method) =>
    (
      await worker.fetch(
        new Request(`https://bumpsolutions.org${path}`, { method }),
        {},
      )
    ).status;
  const rec = "/api/recordings/00000000-0000-0000-0000-000000000002/review";
  assert.equal(await status("/api/auth/me", "GET"), 503);
  assert.equal(await status(rec, "GET"), 503);
  assert.equal(await status(`${rec}/sections`, "PUT"), 503);
  assert.equal(await status(`${rec}/S1`, "POST"), 503);
  assert.equal(await status(`${rec}/S1`, "PUT"), 405);
  assert.equal(await status(`${rec}/sections`, "POST"), 405);
  assert.equal(await status(rec, "PUT"), 405);
  assert.equal(await status("/api/deals", "PUT"), 405);
  assert.equal(await status(`${rec}/S1/x`, "GET"), 404);
});
test("proxy forwards sessions, preserves cookies, never caches data, rejects cross-origin writes", async () => {
  const original = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (url, options) => {
    forwarded = { url, options };
    return new Response("{}", {
      headers: {
        "Set-Cookie":
          "vista_session=opaque; Secure; HttpOnly; SameSite=Lax; Path=/",
      },
    });
  };
  try {
    const env = { API_ORIGIN: "https://api.example.com" };
    const request = new Request("https://bumpsolutions.org/api/auth/session", {
      method: "POST",
      headers: {
        origin: "https://bumpsolutions.org",
        "X-Vista-Request": "1",
        "Content-Type": "application/json",
        cookie: "vista_session=old",
        "x-untrusted": "no",
      },
      body: "{}",
    });
    const response = await worker.fetch(request, env);
    assert.equal(
      forwarded.url.href,
      "https://api.example.com/api/auth/session",
    );
    assert.equal(forwarded.options.headers.get("cookie"), "vista_session=old");
    assert.equal(forwarded.options.headers.get("x-untrusted"), null);
    assert.equal(forwarded.options.redirect, "manual");
    assert.match(response.headers.get("set-cookie"), /HttpOnly/);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(
      (
        await worker.fetch(
          new Request("https://bumpsolutions.org/api/auth/session", {
            method: "POST",
            headers: { origin: "https://evil.example" },
          }),
          env,
        )
      ).status,
      403,
    );
    globalThis.fetch = async () =>
      new Response(null, {
        status: 302,
        headers: { location: "https://other.example" },
      });
    assert.equal(
      (
        await worker.fetch(
          new Request("https://bumpsolutions.org/api/auth/me"),
          env,
        )
      ).status,
      502,
    );
  } finally {
    globalThis.fetch = original;
  }
});

test("ingestion proxy allows only scoped import operations", async () => {
  const status = async (path, method) =>
    (
      await worker.fetch(
        new Request(`https://vista.test/api${path}`, { method }),
        {},
      )
    ).status;
  const id = "00000000-0000-0000-0000-000000000001";
  assert.equal(await status(`/deals/${id}/imports`, "GET"), 503);
  assert.equal(await status(`/deals/${id}/imports`, "POST"), 503);
  assert.equal(await status(`/imports/${id}`, "GET"), 503);
  assert.equal(await status(`/imports/${id}/commit`, "POST"), 503);
  assert.equal(
    await status(`/imports/${id}/findings/123456789abcdef0`, "POST"),
    503,
  );
  assert.equal(await status(`/imports/${id}/export`, "GET"), 503);
  assert.equal(await status(`/imports/${id}/export`, "POST"), 405);
  assert.equal(await status(`/imports/${id}`, "DELETE"), 405);
  assert.equal(await status(`/imports/${id}/commit`, "PUT"), 405);
});
