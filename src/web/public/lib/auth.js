// PE analyst sign-in. The firm-scoped access key is exchanged for an httpOnly
// browser-session cookie by POST /api/auth/session (the same session API the
// company workspace uses); the analyst's firm and role come from
// GET /api/portfolio/me, so the browser never decides who is allowed in.
const SESSION_KEY = "vista.analyst.session";

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

export async function api(path, options = {}, fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Vista-Request": "1",
      ...options.headers,
    },
  });
  if (response.status === 204) return null;
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* non-JSON proxy response */
  }
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : response.status === 401
          ? "Sign in to continue"
          : response.status === 403
            ? "This account is not a member of a portfolio firm."
            : response.status === 422
              ? "The request could not be accepted. Check the input and try again."
              : "The workspace could not complete this request. Please try again.";
    throw new ApiError(response.status, detail);
  }
  return body;
}

export function normalizeKey(token) {
  const key = String(token ?? "")
    .trim()
    .toLowerCase();
  return /^[0-9a-f]{64}$/.test(key) ? key : null;
}

export async function signIn(
  token,
  storage = globalThis.localStorage,
  fetchImpl,
) {
  const key = normalizeKey(token);
  if (!key) throw new Error("Invalid access key");
  try {
    await api(
      "/auth/session",
      { method: "POST", body: JSON.stringify({ token: key }) },
      fetchImpl,
    );
  } catch (error) {
    throw new Error(
      error.status === 401 ? "Invalid access key" : error.message,
    );
  }
  const me = await api("/portfolio/me", {}, fetchImpl);
  const analyst = {
    name: me.name,
    email: me.email,
    firm: me.firm.name,
    firmId: me.firm.id,
    role: me.role,
    signedInAt: new Date().toISOString(),
  };
  storage?.setItem(SESSION_KEY, JSON.stringify(analyst));
  return analyst;
}
// Cached copy of the last /portfolio/me answer; only used to avoid a flash of
// the sign-in page. The server session is authoritative on every request.
export function session(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
export async function signOut(storage = globalThis.localStorage, fetchImpl) {
  storage?.removeItem(SESSION_KEY);
  try {
    await api("/auth/session", { method: "DELETE" }, fetchImpl);
  } catch {
    /* already signed out */
  }
}
export function toSignIn() {
  const next = encodeURIComponent(location.pathname + location.search);
  (window.VISTA_NAVIGATE ?? ((u) => location.replace(u)))(
    `/signin/analyst/?next=${next}`,
  );
}
// Call at the top of every analyst page. Resolves to the analyst (from the
// server) or redirects to sign-in and resolves to null.
export async function requireAnalyst(
  storage = globalThis.localStorage,
  fetchImpl,
) {
  try {
    const me = await api("/portfolio/me", {}, fetchImpl);
    const analyst = {
      ...(session(storage) ?? {}),
      name: me.name,
      email: me.email,
      firm: me.firm.name,
      firmId: me.firm.id,
      role: me.role,
    };
    storage?.setItem(SESSION_KEY, JSON.stringify(analyst));
    return analyst;
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      storage?.removeItem(SESSION_KEY);
      toSignIn();
      return null;
    }
    throw error;
  }
}
