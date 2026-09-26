// Dependency-free inline SVG/HTML charts for the agent analytics blocks. Colors
// come from the --chart-N tokens so they follow the design system.
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const integer = (v) => Number(v).toLocaleString("en-US");
export const spend = (v) =>
  `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

// Inline SVG bars: runs per day over the window, failures drawn in rust on top.
export function runsChart(days, { width = 600, height = 120 } = {}) {
  const max = Math.max(1, ...days.map((d) => d.runs));
  const gap = 2;
  const w = (width - gap * (days.length - 1)) / days.length;
  const bars = days
    .map((d, i) => {
      const x = i * (w + gap);
      const h = (d.runs / max) * (height - 20);
      const fh = (d.failed / max) * (height - 20);
      return `<g><title>${esc(d.day)}: ${d.runs} run${d.runs === 1 ? "" : "s"}${d.failed ? `, ${d.failed} failed` : ""} · ${esc(spend(d.cost_usd))}</title><rect x="${x.toFixed(1)}" y="${(height - 20 - h).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="var(--chart-1)"/>${fh ? `<rect x="${x.toFixed(1)}" y="${(height - 20 - fh).toFixed(1)}" width="${w.toFixed(1)}" height="${fh.toFixed(1)}" fill="var(--chart-2)"/>` : ""}</g>`;
    })
    .join("");
  const first = days[0]?.day ?? "",
    last = days.at(-1)?.day ?? "";
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Agent runs per day, ${esc(first)} to ${esc(last)}; peak ${max}"><line x1="0" y1="${height - 20}" x2="${width}" y2="${height - 20}" stroke="var(--line)"/>${bars}<text x="0" y="${height - 4}" class="axis">${esc(first)}</text><text x="${width}" y="${height - 4}" text-anchor="end" class="axis">${esc(last)}</text><text x="0" y="10" class="axis">peak ${max}/day</text></svg>`;
}

// Horizontal proportion bars for spend by company or model. Each bar is a tiny
// SVG whose width is a presentation attribute, not an inline style: the site's
// CSP (`style-src 'self'`, no 'unsafe-inline') drops inline styles, which left
// every bar at zero width.
export function spendBars(rows, label = (r) => r.key ?? "Portfolio-wide") {
  if (!rows.length) return `<p class="empty">No model spend recorded yet.</p>`;
  const max = Math.max(...rows.map((r) => Number(r.cost_usd)), 1e-9);
  return `<ul class="spend-bars">${rows
    .slice(0, 8)
    .map((r, i) => {
      const pct = ((Number(r.cost_usd) / max) * 100).toFixed(1);
      return `<li><span class="label">${esc(label(r))}</span><svg class="bar" viewBox="0 0 100 1" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="0" width="${pct}" height="1" fill="var(--chart-${(i % 8) + 1})"/></svg><span class="num">${esc(spend(r.cost_usd))}<small>${esc(integer(r.runs))} run${r.runs === 1 ? "" : "s"} · ${esc(integer(r.tokens))} tok</small></span></li>`;
    })
    .join("")}</ul>`;
}
