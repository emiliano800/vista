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
  const media = "/api/recordings/00000000-0000-0000-0000-000000000002/media";
  assert.equal(await status(media, "POST"), 503);
  assert.equal(await status(`${media}/complete`, "POST"), 503);
  assert.equal(await status(media, "GET"), 503);
  assert.equal(await status(media, "PUT"), 405);
  assert.equal(await status(`${media}/screen.webm`, "GET"), 404);
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

test("agent proxy allows run traces, usage and starting agent runs only", async () => {
  const status = async (path, method) =>
    (
      await worker.fetch(
        new Request(`https://vista.test/api${path}`, { method }),
        {},
      )
    ).status;
  const id = "00000000-0000-0000-0000-000000000001";
  assert.equal(await status(`/runs/${id}`, "GET"), 503);
  assert.equal(
    await status(`/runs?agent_key=file_reviewer&company=Ridgeway`, "GET"),
    503,
  );
  assert.equal(await status(`/findings?kind=inefficiency`, "GET"), 503);
  assert.equal(await status(`/findings/${id}`, "PATCH"), 503);
  assert.equal(await status(`/findings/${id}`, "GET"), 405);
  assert.equal(await status(`/findings/${id}`, "POST"), 405);
  assert.equal(await status(`/findings/${id}`, "DELETE"), 405);
  assert.equal(await status(`/findings`, "PATCH"), 405);
  assert.equal(await status(`/runs/${id}`, "PATCH"), 405);
  assert.equal(await status(`/findings/not-a-uuid`, "PATCH"), 404);
  assert.equal(
    await status(`/usage?group_by=company&group_by=model`, "GET"),
    503,
  );
  assert.equal(await status(`/summaries/latest`, "GET"), 503);
  assert.equal(await status(`/synthetic/companies`, "GET"), 503);
  assert.equal(await status(`/agents/analytics`, "GET"), 503);
  assert.equal(await status(`/agents/analytics`, "POST"), 405);
  assert.equal(await status(`/evals?latest=true`, "GET"), 503);
  assert.equal(await status(`/evals`, "POST"), 405);
  assert.equal(await status(`/synthetic/discovery`, "POST"), 503);
  assert.equal(await status(`/synthetic/analyze`, "POST"), 503);
  assert.equal(await status(`/summaries`, "POST"), 503);
  assert.equal(await status(`/agents/${id}/runs`, "POST"), 503);
  assert.equal(await status(`/runs/${id}`, "POST"), 405);
  assert.equal(await status(`/runs/${id}`, "DELETE"), 405);
  assert.equal(await status(`/usage`, "POST"), 405);
  assert.equal(await status(`/synthetic/companies`, "POST"), 405);
  assert.equal(await status(`/synthetic/discovery`, "GET"), 405);
  assert.equal(await status(`/tenants`, "POST"), 404);
  assert.equal(await status(`/synthetic/nope`, "GET"), 404);
});

test("finding triage from the company workspace reaches the backend as PATCH", async () => {
  // The same request account/app.js sends from the evidence dialog, run through
  // the Worker rather than a stubbed fetch, so a method gate cannot hide it.
  const original = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (url, options) => {
    forwarded = { url, options };
    return Response.json({ status: "reviewed" });
  };
  try {
    const id = "00000000-0000-0000-0000-000000000001";
    const response = await worker.fetch(
      new Request(`https://bumpsolutions.org/api/findings/${id}`, {
        method: "PATCH",
        headers: {
          origin: "https://bumpsolutions.org",
          "X-Vista-Request": "1",
          "Content-Type": "application/json",
          cookie: "vista_session=opaque",
        },
        body: JSON.stringify({ status: "reviewed" }),
      }),
      { API_ORIGIN: "https://api.example.com" },
    );
    assert.equal(response.status, 200);
    assert.equal(
      forwarded.url.href,
      `https://api.example.com/api/findings/${id}`,
    );
    assert.equal(forwarded.options.method, "PATCH");
    assert.equal(forwarded.options.headers.get("x-vista-request"), "1");
    assert.equal(
      await new Response(forwarded.options.body).text(),
      '{"status":"reviewed"}',
    );
  } finally {
    globalThis.fetch = original;
  }
});

test("synthetic agent proxy allows discovery, analysis, and run polling only", async () => {
  const status = async (path, method) =>
    (
      await worker.fetch(
        new Request(`https://vista.test/api${path}`, { method }),
        {},
      )
    ).status;
  const run = "/runs/00000000-0000-0000-0000-000000000001";
  for (const path of ["/synthetic/companies", run]) {
    assert.equal(await status(path, "GET"), 503);
    assert.equal(await status(path, "HEAD"), 503);
    for (const method of ["POST", "PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  }
  for (const path of ["/synthetic/discovery", "/synthetic/analyze"]) {
    assert.equal(await status(path, "POST"), 503);
    for (const method of ["GET", "HEAD", "PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  }
  for (const path of [
    "/synthetic/answer_key",
    "/synthetic/discovery/extra",
    `${run}/delete`,
  ])
    assert.equal(await status(path, "GET"), 404);
});

test("portfolio proxy exposes policies, purchasing and inventory reads and the interpretation trigger", async () => {
  const status = async (path, method) =>
    (
      await worker.fetch(
        new Request(`https://vista.test/api${path}`, { method }),
        {},
      )
    ).status;
  const company = "/companies/c-northfield";
  for (const path of [
    `${company}/policies`,
    `${company}/purchase-orders`,
    `${company}/inventory`,
  ]) {
    assert.equal(await status(path, "GET"), 503);
    for (const method of ["POST", "PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  }
  assert.equal(await status("/portfolio/interpretation", "POST"), 503);
  for (const method of ["PUT", "PATCH", "DELETE"])
    assert.equal(await status("/portfolio/interpretation", method), 405);
  const request = "/portfolio/interpretation/8f1c2a3e-0b4d-4c5e-9f6a-7b8c9d0e1f2a";
  assert.equal(await status(request, "GET"), 503);
  assert.equal(await status(request, "POST"), 405);
  assert.equal(await status("/portfolio/interpretation/not-a-uuid", "GET"), 404);
  assert.equal(await status(`${company}/purchase-order-lines`, "GET"), 404);
});

test("workflow proxy permits registry reads and version decisions, not execution or mutation", async () => {
  const status = async (path, method) =>
    (await worker.fetch(new Request(`https://vista.test/api${path}`, { method }), {})).status;
  const id = "00000000-0000-0000-0000-000000000001";
  const base = `/companies/c-meridian/workflows`;
  const workflow = `${base}/${id}`;
  const version = `${workflow}/versions/${id}`;
  for (const path of [base, workflow, `${workflow}/versions`, version, `${version}/eligibility`]) {
    assert.equal(await status(path, "GET"), 503);
    assert.equal(await status(path, "HEAD"), 503);
    for (const method of ["PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  }
  for (const path of [base, `${workflow}/versions`, `${version}/decision`])
    assert.equal(await status(path, "POST"), 503);
  for (const path of [workflow, version, `${version}/eligibility`])
    assert.equal(await status(path, "POST"), 405);
  for (const method of ["GET", "HEAD", "PUT", "PATCH", "DELETE"])
    assert.equal(await status(`${version}/decision`, method), 405);
  for (const path of [`${workflow}/runs`, `${base}/not-a-uuid`, `${version}/decision/extra`])
    assert.equal(await status(path, "POST"), 404);
});

test("recorder intake proxy exposes enrollment and private upload operations only", async () => {
  const status = async (path, method) =>
    (await worker.fetch(new Request(`https://vista.test/api${path}`, { method }), {})).status;
  const base = "/recorder/submissions";
  const submission = `${base}/00000000-0000-0000-0000-000000000001`;
  for (const path of ["/recorder/workspaces", base, submission]) {
    assert.equal(await status(path, "GET"), 503);
    assert.equal(await status(path, "HEAD"), 503);
    for (const method of ["PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  }
  for (const path of [base, `${submission}/upload-urls`, `${submission}/complete`, `${submission}/analyze`, `${submission}/answers`, `${submission}/publish`])
    assert.equal(await status(path, "POST"), 503);
  for (const path of ["/recorder/workspaces", submission, "/recorder/reports", `/recorder/reports/${submission.slice(-36)}`])
    assert.equal(await status(path, "POST"), 405);
  for (const path of ["/recorder/reports", `/recorder/reports/${submission.slice(-36)}`])
    assert.equal(await status(path, "GET"), 503);
  for (const path of [`${submission}/upload-urls`, `${submission}/complete`, `${submission}/analyze`, `${submission}/answers`, `${submission}/publish`])
    for (const method of ["GET", "HEAD", "PUT", "PATCH", "DELETE"])
      assert.equal(await status(path, method), 405);
  for (const path of [`${submission}/download`, `${submission}/withdraw`, `${base}/not-a-uuid`, "/recorder/reports/not-a-uuid"])
    assert.equal(await status(path, "GET"), 404);
});
