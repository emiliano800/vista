const $ = (id) => document.getElementById(id);
const navigate = (url) =>
  (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);
function message(text = "") {
  $("message").textContent = text;
}
async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Vista-Request": "1",
      ...options.headers,
    },
  });
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      /* upstream unavailable */
    }
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : response.status === 422
          ? "The request could not be accepted. Check your input."
          : "The workspace is unavailable. Please try again.",
    );
  }
  return response.status === 204 ? null : response.json();
}
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  message();
  const button = event.submitter;
  button.disabled = true;
  const token = $("key").value.trim();
  $("key").value = "";
  try {
    await api("/auth/session", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    navigate("/account/");
  } catch (error) {
    message(error.message);
  } finally {
    button.disabled = false;
  }
};
// Already signed in? Go straight to the workspace.
try {
  await api("/auth/me");
  navigate("/account/");
} catch {
  /* stay on the sign-in page */
}
