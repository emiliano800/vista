const apiRoutes =
  /^\/api\/(?:auth\/(?:session|me)|deals(?:\/[0-9a-f-]+\/recordings)?|recordings\/[0-9a-f-]+(?:\/(?:evidence|download|review(?:\/[a-z0-9_-]{1,64})?))?|health)$/i;
const reviewSections = /^\/api\/recordings\/[0-9a-f-]+\/review\/sections$/i;
const reviewDecision =
  /^\/api\/recordings\/[0-9a-f-]+\/review\/(?!sections$)[a-z0-9_-]{1,64}$/i;
const importRead =
  /^\/api\/(?:deals\/[0-9a-f-]+\/imports|imports\/[0-9a-f-]+(?:\/export)?)$/i;
const importWrite =
  /^\/api\/(?:deals\/[0-9a-f-]+\/imports|imports\/[0-9a-f-]+\/(?:commit|findings\/[a-f0-9]{16}))$/i;
const securityHeaders = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};
async function handle(request, env) {
  const url = new URL(request.url);
  let response;
  if (url.pathname.startsWith("/api/")) {
    if (
      !apiRoutes.test(url.pathname) &&
      !importRead.test(url.pathname) &&
      !importWrite.test(url.pathname)
    )
      return new Response("Not found", { status: 404 });
    // Only explicitly allowed workspace operations reach the backend.
    if (
      !["GET", "HEAD", "POST", "PUT", "DELETE"].includes(request.method) ||
      (request.method === "POST" &&
        !url.pathname.endsWith("/recordings") &&
        !reviewDecision.test(url.pathname) &&
        !importWrite.test(url.pathname) &&
        url.pathname !== "/api/auth/session") ||
      (request.method === "PUT" && !reviewSections.test(url.pathname)) ||
      (request.method === "DELETE" && url.pathname !== "/api/auth/session")
    )
      return new Response("Method not allowed", { status: 405 });
    if (!env.API_ORIGIN)
      return Response.json(
        { detail: "The workspace backend has not been configured." },
        { status: 503 },
      );
    const upstream = new URL(env.API_ORIGIN);
    if (
      upstream.host === url.host ||
      upstream.protocol !== "https:" ||
      upstream.username ||
      upstream.password ||
      upstream.pathname !== "/" ||
      upstream.search ||
      upstream.hash
    )
      return Response.json(
        { detail: "The workspace backend configuration is invalid." },
        { status: 503 },
      );
    if (!["GET", "HEAD"].includes(request.method)) {
      if (
        request.headers.get("origin") &&
        request.headers.get("origin") !== url.origin
      )
        return new Response("Origin rejected", { status: 403 });
      if (Number(request.headers.get("content-length")) > 8 * 1024 * 1024)
        return new Response("Upload too large", { status: 413 });
    }
    const headers = new Headers();
    for (const name of [
      "content-type",
      "authorization",
      "cookie",
      "x-vista-request",
      "origin",
    ]) {
      if (request.headers.has(name))
        headers.set(name, request.headers.get(name));
    }
    upstream.pathname = url.pathname;
    upstream.search = url.search;
    try {
      response = await fetch(upstream, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method)
          ? undefined
          : request.body,
        redirect: "manual",
        signal: AbortSignal.timeout(30000),
      });
      if (response.status >= 300 && response.status < 400)
        return Response.json(
          { detail: "The workspace backend returned an unexpected redirect." },
          { status: 502 },
        );
    } catch {
      return Response.json(
        {
          detail: "The workspace is temporarily unavailable. Please try again.",
        },
        { status: 502 },
      );
    }
  } else {
    response = await env.ASSETS.fetch(request);
  }
  return response;
}
export default {
  async fetch(request, env) {
    const response = await handle(request, env);
    const result = new Response(response.body, response);
    for (const [name, value] of Object.entries(securityHeaders))
      result.headers.set(name, value);
    if (new URL(request.url).pathname.startsWith("/api/"))
      result.headers.set("Cache-Control", "no-store");
    return result;
  },
};
