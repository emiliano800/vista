import { session, signIn } from "/lib/auth.js";
const $ = (id) => document.getElementById(id);
const navigate = (url) =>
  (window.VISTA_NAVIGATE ?? ((u) => location.assign(u)))(url);
const params = new URLSearchParams(location.search);
const next = params.get("next");
const destination =
  next && next.startsWith("/") && !next.startsWith("//") ? next : "/portfolio/";
function message(text = "") {
  $("message").textContent = text;
}
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  message();
  const button = event.submitter;
  button.disabled = true;
  const token = $("key").value.trim();
  $("key").value = "";
  try {
    await signIn(token);
    navigate(destination);
  } catch (error) {
    message(error.message);
  } finally {
    button.disabled = false;
  }
};
if (session()) navigate(destination);
