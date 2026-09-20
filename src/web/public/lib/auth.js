// PE analyst sign-in. Front-end only for now: the demo access key is checked
// against its SHA-256 digest in the browser and the session lives in
// localStorage. When the portfolio backend lands this becomes a call to
// POST /api/auth/session with a firm-scoped key, like the company workspace.
const SESSION_KEY = "vista.analyst.session";
const ANALYST_KEY_DIGESTS = new Set([
  // Northstar HVAC Holdings demo analyst (see DEMO_ACCESS.md)
  "e840c3b9e484376c0b0e1a367e0f71c778b1b804cbc5024e7c6c21951f76901f",
]);
export const ANALYST = {
  name: "Sarah Okafor",
  email: "sarah@northstarhvac.com",
  firm: "Northstar HVAC Holdings",
  role: "analyst",
};

export async function sha256Hex(text, cryptoImpl = globalThis.crypto) {
  const bytes = new TextEncoder().encode(text);
  const digest = await cryptoImpl.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export async function verifyAnalystKey(token, cryptoImpl) {
  const key = String(token ?? "").trim();
  if (!/^[0-9a-f]{64}$/i.test(key)) return false;
  return ANALYST_KEY_DIGESTS.has(await sha256Hex(key.toLowerCase(), cryptoImpl));
}
export async function signIn(token, storage = localStorage) {
  if (!(await verifyAnalystKey(token))) throw new Error("Invalid access key");
  storage.setItem(
    SESSION_KEY,
    JSON.stringify({ ...ANALYST, signedInAt: new Date().toISOString() }),
  );
  return ANALYST;
}
export function session(storage = localStorage) {
  try {
    const raw = storage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
export function signOut(storage = localStorage) {
  storage.removeItem(SESSION_KEY);
}
// Call at the top of every analyst page. Returns the session or redirects.
export function requireAnalyst() {
  const current = session();
  if (!current) {
    const next = encodeURIComponent(location.pathname + location.search);
    (window.VISTA_NAVIGATE ?? ((u) => location.replace(u)))(
      `/signin/analyst/?next=${next}`,
    );
  }
  return current;
}
