// PE analyst sign-in against the Vista backend. The access key is a real
// tenant API token: POST /api/auth/session exchanges it for an httpOnly
// session cookie, exactly like the company workspace. Nothing about the key
// is kept in the browser.
const IDENTITY_KEY = "vista.analyst.identity";

export async function apiFetch(path, options = {}) {
  return fetch(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Vista-Request": "1",
      ...options.headers,
    },
  });
}

export async function signIn(token, storage = globalThis.localStorage) {
  const key = String(token ?? "").trim();
  if (key.length < 32) throw new Error("Invalid access key");
  const response = await apiFetch("/auth/session", {
    method: "POST",
    body: JSON.stringify({ token: key }),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "Invalid access key" : "Sign-in failed, try again");
  const identity = await response.json();
  try {
    storage?.setItem(IDENTITY_KEY, JSON.stringify(identity));
  } catch {
    /* private mode: the cookie is still what authenticates */
  }
  return identity;
}

// Cached display identity. The session itself is the httpOnly cookie, so this
// is only what the header shows; `requireAnalyst` confirms it against /auth/me.
export function session(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(IDENTITY_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function signOut(storage = globalThis.localStorage) {
  try {
    await apiFetch("/auth/session", { method: "DELETE" });
  } catch {
    /* clear the local identity regardless */
  }
  storage?.removeItem(IDENTITY_KEY);
}

export function redirectToSignIn() {
  const next = encodeURIComponent(location.pathname + location.search);
  (window.VISTA_NAVIGATE ?? ((u) => location.replace(u)))(`/signin/analyst/?next=${next}`);
}

// Call at the top of every analyst page: confirms the cookie with the backend
// and redirects to sign-in when it is missing or expired.
export async function requireAnalyst(storage = globalThis.localStorage) {
  const response = await apiFetch("/auth/me");
  if (!response.ok) {
    storage?.removeItem(IDENTITY_KEY);
    redirectToSignIn();
    return null;
  }
  const identity = await response.json();
  try {
    storage?.setItem(IDENTITY_KEY, JSON.stringify(identity));
  } catch {
    /* display-only cache */
  }
  return identity;
}
