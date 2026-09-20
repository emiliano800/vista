const menu = document.getElementById("menu");
const drawer = document.getElementById("drawer");
const scrim = document.getElementById("scrim");
function toggle(show) {
  drawer.hidden = !show;
  scrim.hidden = !show;
  menu.setAttribute("aria-expanded", String(show));
}
menu.onclick = () => toggle(drawer.hidden);
scrim.onclick = () => toggle(false);
drawer.addEventListener("click", (event) => {
  if (event.target.closest("a")) toggle(false);
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !drawer.hidden) toggle(false);
});
const here = location.pathname.replace(/index\.html$/, "");
for (const link of drawer.querySelectorAll("a")) {
  const path = new URL(link.href).pathname;
  if (path === here || (path !== "/" && here.startsWith(path)))
    link.setAttribute("aria-current", "page");
}
