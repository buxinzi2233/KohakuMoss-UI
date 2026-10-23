/* Formatting and tiny text charts. No chart library, no build step. */

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

export function num(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  if (n !== 0 && Math.abs(n) < 0.001) return n.toExponential(2);
  return n.toFixed(digits);
}

export function metric(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits);
}

export function rate(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(digits)}k`;
  return n.toFixed(digits);
}

export function bytes(size) {
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(size);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[index]}`;
}

export function when(seconds) {
  if (!seconds) return "—";
  const date = new Date(Number(seconds) * 1000);
  return date.toLocaleString(undefined, { hour12: false });
}

export function clock(seconds) {
  if (!seconds) return "—";
  const date = new Date(Number(seconds) * 1000);
  return date.toLocaleTimeString(undefined, { hour12: false });
}

export function duration(seconds) {
  if (!seconds || seconds < 0) return "—";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

export function pct(value) {
  if (!Number.isFinite(Number(value))) return "0%";
  return `${Math.round(Number(value) * 100)}%`;
}

export function bar(ratio, width = 10, filled = "#", empty = ".") {
  const count = Math.max(0, Math.min(width, Math.round((Number(ratio) || 0) * width)));
  return `[${filled.repeat(count)}${empty.repeat(width - count)}]`;
}

export function bars(values, rows = 5, glyph = "#", width = 54) {
  const numbers = values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
  if (!numbers.length) return "";
  const sample = numbers.length > width
    ? Array.from({ length: width }, (_, index) => numbers[Math.floor(index * numbers.length / width)])
    : numbers;
  const max = Math.max(...sample);
  const min = Math.min(...sample) * 0.96;
  const span = max - min || 1;
  const lines = [];
  for (let row = rows - 1; row >= 0; row -= 1) {
    lines.push(sample.map((value) => ((value - min) / span * rows > row ? glyph : " ")).join(""));
  }
  return lines.join("\n");
}

export function spark(values, width = 54) {
  const numbers = values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
  if (!numbers.length) return "";
  const sample = numbers.length > width
    ? Array.from({ length: width }, (_, index) => numbers[Math.floor(index * numbers.length / width)])
    : numbers;
  const chars = "▁▂▃▄▅▆▇█";
  const max = Math.max(...sample);
  const min = Math.min(...sample);
  const span = max - min || 1;
  return sample.map((value) => chars[Math.min(7, Math.floor((value - min) / span * 8))]).join("");
}

export function levelClass(line) {
  if (/\b(ERROR|ERR|FATAL|Traceback)\b/.test(line)) return "err";
  if (/\b(WARN|WARNING)\b/.test(line)) return "warn";
  return "";
}

export function logLine(line) {
  const match = String(line).match(/^(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(INFO|WARN|WARNING|ERROR|ERR|DEBUG)\s+(.*)$/);
  if (!match) return `<div class="ln"><span class="m">${esc(line)}</span></div>`;
  const level = match[2];
  const cls = level.startsWith("WARN") || level.startsWith("ERR") ? "w" : "";
  return `<div class="ln ${cls ? "warn" : ""}"><span class="t">${esc(match[1])}</span><span class="lv ${cls}">${esc(level.slice(0, 4))}</span><span class="m">${esc(match[3])}</span></div>`;
}
