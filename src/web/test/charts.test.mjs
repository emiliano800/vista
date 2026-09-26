import test from "node:test";
import assert from "node:assert/strict";
import { runsChart, spendBars, spend } from "../public/lib/charts.js";

test("runsChart draws one bar per day, scales to the peak and escapes titles", () => {
  const days = [
    { day: "2026-09-01", runs: 2, failed: 1, cost_usd: "0.0012" },
    { day: "2026-09-02", runs: 0, failed: 0, cost_usd: "0" },
    { day: "<b>", runs: 4, failed: 0, cost_usd: "0.5" },
  ];
  const svg = runsChart(days, { width: 300, height: 100 });
  assert.equal((svg.match(/<g>/g) ?? []).length, 3);
  assert.match(svg, /peak 4\/day/);
  assert.match(
    svg,
    /fill="var\(--chart-2\)"/,
    "failures drawn in the second chart color",
  );
  assert.doesNotMatch(svg, /<b>/);
  assert.match(svg, /&lt;b&gt;/);
  assert.match(svg, /height="80\.0"/, "peak bar fills the plot height");
  assert.match(runsChart([]), /peak 1\/day/, "empty window still renders");
});

test("spendBars ranks rows, labels the portfolio bucket and formats spend", () => {
  const html = spendBars([
    { key: "Ridgeway", runs: 3, cost_usd: "0.0034", tokens: 1200 },
    { key: null, runs: 1, cost_usd: "0.0017", tokens: 600 },
  ]);
  assert.match(html, /Ridgeway/);
  assert.match(html, /Portfolio-wide/);
  assert.match(html, /<rect [^>]*width="100\.0"/);
  assert.match(html, /<rect [^>]*width="50\.0"/);
  assert.match(html, /fill="var\(--chart-1\)"/);
  // The site's CSP has no 'unsafe-inline' for styles: bars must not rely on style="".
  assert.doesNotMatch(html, /style=/);
  assert.equal(spend("0.0034"), "$0.0034");
  assert.equal(spend(12), "$12.00");
  assert.match(spendBars([]), /No model spend/);
});
