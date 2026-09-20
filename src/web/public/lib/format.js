// Deterministic display formatting for the PE analyst workspace.
export const TODAY = new Date("2026-09-19T12:00:00Z");
export const PERIOD = {
  label: "TTM ending Sep. 2026",
  start: "2025-10-01",
  end: "2026-09-30",
};
export const DEMO_NOTE = `Synthetic demo data · reporting period ${PERIOD.label}`;

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const usd0 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
export function money(value, { cents = true } = {}) {
  return (cents ? usd : usd0).format(Number(value) || 0);
}
export function compactMoney(value) {
  const n = Number(value) || 0;
  const abs = Math.abs(n);
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return usd0.format(n);
}
export function integer(value) {
  return new Intl.NumberFormat("en-US").format(Number(value) || 0);
}
export function percent(value, digits = 0) {
  return `${(Number(value) * 100).toFixed(digits)}%`;
}
export function date(value) {
  if (!value) return "—";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}
export function monthYear(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
export function daysBetween(from, to = TODAY) {
  const a = new Date(from);
  const b = to instanceof Date ? to : new Date(to);
  return Math.round((b - a) / 86_400_000);
}
export function age(since, now = TODAY) {
  const ms = (now instanceof Date ? now : new Date(now)) - new Date(since);
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${Math.max(minutes, 1)}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}
export function plural(n, word, pluralWord = `${word}s`) {
  return `${integer(n)} ${n === 1 ? word : pluralWord}`;
}
export function titleCase(text) {
  return String(text)
    .toLowerCase()
    .replace(/(^|[\s-])\S/g, (c) => c.toUpperCase());
}
