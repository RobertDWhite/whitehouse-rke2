"use strict";

// Category -> accent color (mirrors the Activity ring palette).
const CATEGORY_COLOR = {
  "Move Goals": "var(--move)",
  "Exercise": "var(--exercise)",
  "Stand": "var(--stand)",
  "Perfect Week/Month": "var(--gold)",
  "Streaks": "var(--blue)",
  "Monthly Challenges": "var(--purple)",
  "Limited Edition": "var(--gold)",
  "Competitions": "var(--purple)",
  "Records": "var(--move)",
  "Workouts": "var(--exercise)",
  "Other": "var(--muted)",
};

function colorFor(cat) {
  return CATEGORY_COLOR[cat] || "var(--muted)";
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function fmtValue(a) {
  if (a.value == null) return "";
  const v = Number.isInteger(a.value) ? a.value : Math.round(a.value * 10) / 10;
  return a.unit ? `${v} ${a.unit}` : `${v}`;
}

// Two initials from the friendly name for the medallion glyph.
function initials(name) {
  const words = name.replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  // Prefer a meaningful number (e.g. "500") but skip 4-digit years.
  const num = name.match(/\b\d{1,3}\b/);
  if (num) return num[0];
  return (words[0][0] + (words[1] ? words[1][0] : "")).toUpperCase();
}

// Collapse repeated earns of the same template into one card with a count and
// the most-recent earned date.
function groupAwards(awards) {
  const map = new Map();
  for (const a of awards) {
    const g = map.get(a.template);
    if (!g) {
      map.set(a.template, { ...a, times: 1, latest: a.earned_date });
    } else {
      g.times += 1;
      if (a.earned_date && (!g.latest || a.earned_date > g.latest)) {
        g.latest = a.earned_date;
        g.value = a.value;
      }
    }
  }
  return [...map.values()].sort((x, y) =>
    (y.latest || "").localeCompare(x.latest || "")
  );
}

function renderStats(data, groups) {
  const cats = new Set(groups.map((g) => g.category));
  const mostEarned = groups.reduce(
    (best, g) => (g.times > (best?.times || 0) ? g : best),
    null
  );
  const stats = [
    ["num", data.total_earned, "Total earned"],
    ["num", data.unique_awards, "Unique badges"],
    ["num", cats.size, "Categories"],
    ["text", mostEarned ? `${mostEarned.name} (x${mostEarned.times})` : "-", "Most earned"],
  ];
  document.getElementById("stats").innerHTML = stats
    .map(
      ([kind, val, label]) => `
      <div class="stat">
        <div class="num" ${kind === "text" ? 'style="font-size:16px"' : ""}>${val}</div>
        <div class="label">${label}</div>
      </div>`
    )
    .join("");
}

let ALL_GROUPS = [];
let ACTIVE = "All";

function renderFilters(groups) {
  const counts = {};
  for (const g of groups) counts[g.category] = (counts[g.category] || 0) + 1;
  const cats = ["All", ...Object.keys(counts).sort()];
  const nav = document.getElementById("filters");
  nav.innerHTML = cats
    .map((c) => {
      const n = c === "All" ? groups.length : counts[c];
      return `<button class="chip ${c === ACTIVE ? "active" : ""}" data-cat="${c}">
        ${c}<span class="count">${n}</span></button>`;
    })
    .join("");
  nav.querySelectorAll(".chip").forEach((btn) =>
    btn.addEventListener("click", () => {
      ACTIVE = btn.dataset.cat;
      nav.querySelectorAll(".chip").forEach((b) =>
        b.classList.toggle("active", b.dataset.cat === ACTIVE)
      );
      renderGrid();
    })
  );
}

function renderGrid() {
  const shown =
    ACTIVE === "All" ? ALL_GROUPS : ALL_GROUPS.filter((g) => g.category === ACTIVE);
  document.getElementById("grid").innerHTML = shown
    .map((g) => {
      const color = colorFor(g.category);
      const value = fmtValue(g);
      return `
      <article class="badge">
        ${g.times > 1 ? `<span class="times">x${g.times}</span>` : ""}
        <div class="medallion" style="background:
          radial-gradient(circle at 35% 30%, rgba(255,255,255,.55), transparent 60%), ${color}">
          ${initials(g.name)}
        </div>
        <div class="name">${g.name}</div>
        <div class="cat">${g.category}</div>
        <div class="date">${fmtDate(g.latest)}</div>
        ${value ? `<div class="value">${value}</div>` : ""}
      </article>`;
    })
    .join("");
}

function init() {
  const data = window.AWARDS;
  if (!data || !data.awards || !data.awards.length) {
    document.getElementById("empty").hidden = false;
    return;
  }
  ALL_GROUPS = groupAwards(data.awards);
  document.getElementById("meta").innerHTML = `
    ${data.first_earned ? fmtDate(data.first_earned) : ""} &ndash;
    ${data.last_earned ? fmtDate(data.last_earned) : ""}<br />
    <span style="opacity:.7">source: ${data.source || "unknown"}</span>`;
  renderStats(data, ALL_GROUPS);
  renderFilters(ALL_GROUPS);
  renderGrid();
}

document.addEventListener("DOMContentLoaded", init);
