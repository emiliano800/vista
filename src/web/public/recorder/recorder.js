// Platform-aware download buttons. Artifacts are published by the
// release-recorder GitHub workflow with stable names, so "latest" always works.
const RELEASES = "https://github.com/emiliano800/vista/releases";
const ASSET = (name) => `${RELEASES}/latest/download/${name}`;
export const DOWNLOADS = {
  "mac-arm64": { label: "Download for Mac (Apple silicon)", file: "Vista-Recorder-mac-arm64.dmg" },
  "mac-x64": { label: "Download for Mac (Intel)", file: "Vista-Recorder-mac-x64.dmg" },
  windows: { label: "Download for Windows", file: "Vista-Recorder-windows-setup.exe" },
};
export function detectPlatform(ua, platform) {
  const s = `${ua} ${platform}`.toLowerCase();
  if (s.includes("win")) return "windows";
  if (s.includes("mac")) {
    // Apple silicon Safari/Chrome still report Intel in the UA; arm64 is the
    // safe default for modern Macs, and the alternate link covers the rest.
    return "mac-arm64";
  }
  return null;
}
const primary = document.getElementById("download-primary");
const alt = document.getElementById("download-alt");
const key = detectPlatform(navigator.userAgent, navigator.platform);
if (key) {
  primary.textContent = DOWNLOADS[key].label;
  primary.href = ASSET(DOWNLOADS[key].file);
  const other = key === "windows" ? "mac-arm64" : "windows";
  alt.textContent = DOWNLOADS[other].label.replace("Download for", "Or get it for");
  alt.href = ASSET(DOWNLOADS[other].file);
} else {
  primary.textContent = "All downloads";
  primary.href = RELEASES;
  alt.hidden = true;
}
