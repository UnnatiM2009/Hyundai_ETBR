/* ==========================================================================
   Hyundai Dealership Dashboard — frontend logic
   Vanilla JS + Chart.js. No build step; everything below runs as-is in
   the browser once FastAPI serves this file from /assets/js/app.js.
   ========================================================================== */

const state = {
  meta: null,
  filterOptions: null,
  breakdownCache: {},   // per-prefix cached {by_model, by_consultant, by_source}
  charts: {},           // chart.js instances keyed by canvas id
  loadedSections: new Set(),
  gaugeLength: null,
};

/* Every section that has its own Month / Model / Consultant / Source filter
   bar, keyed by the id prefix used in its <select> elements, mapped to the
   section key used elsewhere (SECTION_LOADERS, /api/breakdown?section=...). */
const PREFIX_TO_SECTION = {
  ov: "overview", enq: "enquiry", td: "testdrive", book: "booking", sales: "sales",
  conv: "conversion",
};

/* Column layout for the Model/Consultant/Source breakdown table on each page —
   the metrics that matter differ per section, so each gets its own column set. */
const BREAKDOWN_COLUMNS = {
  overview: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "bookings", label: "Bookings", fmt: (v) => fmtInt(v) },
    { key: "retail", label: "Retail", fmt: (v) => fmtInt(v) },
    { key: "revenue", label: "Revenue", fmt: (v) => fmtMoney(v) },
  ],
  enquiry: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "test_drives", label: "Test Drives", fmt: (v) => fmtInt(v) },
    { key: "test_drive_rate", label: "TD Rate", fmt: (v) => fmtPct(v) },
    { key: "lost", label: "Lost", fmt: (v) => fmtInt(v) },
    { key: "lost_rate", label: "Lost Rate", fmt: (v) => fmtPct(v) },
  ],
  testdrive: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "test_drives", label: "Test Drives Done", fmt: (v) => fmtInt(v) },
    { key: "test_drive_rate", label: "TD Rate", fmt: (v) => fmtPct(v) },
    { key: "booked_from_td", label: "Booked (of TD)", fmt: (v) => fmtInt(v) },
  ],
  booking: [
    { key: "bookings", label: "Bookings", fmt: (v) => fmtInt(v) },
    { key: "amount_received", label: "Amount Received", fmt: (v) => fmtMoney(v) },
    { key: "avg_booking_age", label: "Avg. Booking Age", fmt: (v) => `${v} days` },
  ],
  sales: [
    { key: "units", label: "Units", fmt: (v) => fmtInt(v) },
    { key: "revenue", label: "Revenue", fmt: (v) => fmtMoney(v) },
    { key: "avg_delivery_days", label: "Avg. Delivery", fmt: (v) => `${v} days` },
  ],
  conversion: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "e2t", label: "E2T %", fmt: (v) => fmtPct(v) },
    { key: "e2b", label: "E2B %", fmt: (v) => fmtPct(v) },
    { key: "e2r", label: "E2R %", fmt: (v) => fmtPct(v) },
    { key: "b2r", label: "B2R %", fmt: (v) => fmtPct(v) },
  ],
};

const DIM_LABELS = { model: "Model", consultant: "Consultant", source: "Source" };

/* ---------------------------------------------------------------------- */
/* Formatting helpers                                                      */
/* ---------------------------------------------------------------------- */
const fmtInt = (n) => new Intl.NumberFormat("en-US").format(Math.round(n || 0));
const fmtPct = (n) => `${(n ?? 0).toFixed(1)}%`;
const fmtMoney = (n) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "INR", maximumFractionDigits: 0 })
    .format(n || 0);
const fmtShortDate = (isoDay) => {
  const d = new Date(isoDay + "T00:00:00");
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" }).format(d);
};
const fmtStamp = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(d);
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function deltaHtml(pct, direction) {
  if (pct === null || pct === undefined) {
    return `<span class="delta up">new</span> vs previous month`;
  }
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "•";
  const sign = pct > 0 ? "+" : "";
  return `<span class="delta ${direction}">${arrow} ${sign}${pct.toFixed(1)}%</span> vs previous month`;
}

/* ---------------------------------------------------------------------- */
/* Fetch helpers                                                           */
/* ---------------------------------------------------------------------- */
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

/* ---------------------------------------------------------------------- */
/* KPI card rendering                                                      */
/* ---------------------------------------------------------------------- */
function renderKpiGrid(containerId, cards) {
  const el = document.getElementById(containerId);
  el.innerHTML = cards.map(c => `
    <div class="kpi-card" style="--bar-color:${c.color || "var(--blue)"}">
      <div class="kpi-label">${c.label}</div>
      <div class="kpi-value">${c.value}</div>
      <div class="kpi-sub">${c.sub || ""}</div>
    </div>
  `).join("");
}

function cmpLookup(comparison) {
  const map = {};
  comparison.rows.forEach(r => { map[r.metric] = r; });
  return map;
}

/* ---------------------------------------------------------------------- */
/* Generic Chart.js builders                                               */
/* ---------------------------------------------------------------------- */
function destroyChart(id) {
  if (state.charts[id]) {
    state.charts[id].destroy();
    delete state.charts[id];
  }
}

function baseGridColor() { return cssVar("--border"); }
function baseInkColor() { return cssVar("--ink-muted"); }

function barChart(canvasId, labels, values, color, horizontal = true) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  state.charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: color, borderRadius: 5, maxBarThickness: 26 }],
    },
    options: {
      indexAxis: horizontal ? "y" : "x",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor() } },
        y: { grid: { display: false }, ticks: { color: baseInkColor() } },
      },
    },
  });
}

function lineChart(canvasId, labels, values, color) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  const gradient = ctx.createLinearGradient(0, 0, 0, 240);
  gradient.addColorStop(0, color + "55");
  gradient.addColorStop(1, color + "05");
  state.charts[canvasId] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: values, borderColor: color, backgroundColor: gradient,
        fill: true, tension: 0.35, pointRadius: 3, pointBackgroundColor: color,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: baseInkColor() } },
        y: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor() }, beginAtZero: true },
      },
    },
  });
}

function doughnutChart(canvasId, labels, values, colors) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  state.charts[canvasId] = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "68%",
      plugins: { legend: { position: "bottom", labels: { color: baseInkColor(), boxWidth: 12, padding: 14 } } },
    },
  });
}

/* ---------------------------------------------------------------------- */
/* Funnel (custom, not a chart.js chart)                                   */
/* ---------------------------------------------------------------------- */
function renderFunnel(containerId, stages) {
  const el = document.getElementById(containerId);
  const max = stages.length ? Math.max(...stages.map(s => s.value), 1) : 1;
  el.innerHTML = stages.map(s => `
    <div class="funnel-row">
      <div class="funnel-label">${s.stage}</div>
      <div class="funnel-bar-track"><div class="funnel-bar-fill" style="width:${(s.value / max) * 100}%"></div></div>
      <div class="funnel-value">${fmtInt(s.value)}</div>
    </div>
  `).join("");
}

/* ---------------------------------------------------------------------- */
/* Gauge (test drive completion — speedometer style)                       */
/* ---------------------------------------------------------------------- */
function renderGauge(pct, done, total) {
  const fill = document.getElementById("gaugeFill");
  const needle = document.getElementById("gaugeNeedle");
  if (state.gaugeLength === null) {
    state.gaugeLength = fill.getTotalLength();
    fill.style.strokeDasharray = state.gaugeLength;
  }
  const clamped = Math.max(0, Math.min(100, pct));
  fill.style.strokeDashoffset = state.gaugeLength * (1 - clamped / 100);
  const angle = -90 + (clamped / 100) * 180;
  needle.style.transform = `rotate(${angle}deg)`;
  document.getElementById("gaugeValue").textContent = fmtPct(pct);
  document.getElementById("gaugeCaption").textContent = `${fmtInt(done)} of ${fmtInt(total)} enquiries test-driven`;
}

/* ---------------------------------------------------------------------- */
/* Section loaders                                                         */
/* ---------------------------------------------------------------------- */
function apiParams(f) {
  return new URLSearchParams({ period: f.month, model: f.model, consultant: f.consultant, source: f.source }).toString();
}

async function loadOverview() {
  await populateFilterBar("ov");
  const f = readFilterBar("ov");
  const qs = apiParams(f);

  const [kpis, enquiry, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/enquiry?${qs}`),
    fetchComparisonFor("ov"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("kpiGrid", [
    { label: "Total enquiries", value: fmtInt(kpis.total_enquiries), color: "var(--blue)",
      sub: deltaHtml(cmp["Total Enquiries"]?.change_pct, cmp["Total Enquiries"]?.direction) },
    { label: "Test drives completed", value: fmtInt(kpis.test_drive_done), color: "var(--amber)",
      sub: deltaHtml(cmp["Test Drives Completed"]?.change_pct, cmp["Test Drives Completed"]?.direction) },
    { label: "Total bookings", value: fmtInt(kpis.total_bookings), color: "var(--green)",
      sub: deltaHtml(cmp["Total Bookings"]?.change_pct, cmp["Total Bookings"]?.direction) },
    { label: "Units retailed", value: fmtInt(kpis.total_retail), color: "var(--purple)",
      sub: deltaHtml(cmp["Total Retail (Units Sold)"]?.change_pct, cmp["Total Retail (Units Sold)"]?.direction) },
    { label: "Revenue", value: fmtMoney(kpis.total_revenue), color: "var(--green)",
      sub: deltaHtml(cmp["Total Revenue"]?.change_pct, cmp["Total Revenue"]?.direction) },
    { label: "Lost enquiries", value: fmtInt(kpis.lost_enquiries), color: "var(--red)",
      sub: deltaHtml(cmp["Lost Enquiries"]?.change_pct, cmp["Lost Enquiries"]?.direction) },
  ]);

  renderFunnel("overviewFunnel", [
    { stage: "Enquiries", value: kpis.total_enquiries },
    { stage: "Bookings", value: kpis.total_bookings },
    { stage: "Retailed", value: kpis.total_retail },
  ]);

  renderGauge(kpis.test_drive_rate, kpis.test_drive_done, kpis.total_enquiries);

  const models = enquiry.model_breakdown || [];
  barChart("overviewModelsChart", models.map(m => m.label), models.map(m => m.value), cssVar("--blue"));

  const sources = enquiry.source_breakdown || [];
  doughnutChart("overviewSourceChart", sources.map(s => s.label), sources.map(s => s.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), cssVar("--red"), "#999"]);

  await loadBreakdownFor("ov", "overview", f);
  await loadBreakdownFor("conv", "conversion", f);
}

async function loadTestDrive() {
  await populateFilterBar("td");
  const f = readFilterBar("td");
  const qs = apiParams(f);

  const [kpis, td, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/test-drive?${qs}`),
    fetchComparisonFor("td"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("tdKpiGrid", [
    { label: "Test drives done (Y)", value: fmtInt(kpis.test_drive_done), color: "var(--amber)",
      sub: deltaHtml(cmp["Test Drives Completed"]?.change_pct, cmp["Test Drives Completed"]?.direction) },
    { label: "Not done (N)", value: fmtInt(kpis.test_drive_not_done), color: "var(--ink-faint)",
      sub: deltaHtml(cmp["Test Drives Pending (Not Done)"]?.change_pct, cmp["Test Drives Pending (Not Done)"]?.direction) },
    { label: "Test drive rate", value: fmtPct(kpis.test_drive_rate), color: "var(--blue)",
      sub: deltaHtml(cmp["Test Drive Rate (%)"]?.change_pct, cmp["Test Drive Rate (%)"]?.direction) },
    { label: "Test drive → booking", value: fmtPct(kpis.test_drive_to_booking_rate), color: "var(--green)",
      sub: deltaHtml(cmp["Test Drive to Booking Conv. (%)"]?.change_pct, cmp["Test Drive to Booking Conv. (%)"]?.direction) },
  ]);

  doughnutChart("tdDoneChart", ["Done (Y)", "Not done (N)"],
    [td.done_vs_not.Y, td.done_vs_not.N], [cssVar("--amber"), cssVar("--border")]);

  const trend = td.daily_trend || [];
  lineChart("tdTrendChart", trend.map(t => fmtShortDate(t.date)), trend.map(t => t.value), cssVar("--amber"));

  const byModel = td.by_model || [];
  barChart("tdModelChart", byModel.map(m => m.label), byModel.map(m => m.value), cssVar("--blue"));

  const byConsultant = td.by_consultant || [];
  barChart("tdConsultantChart", byConsultant.map(m => m.label), byConsultant.map(m => m.value), cssVar("--purple"));

  const bySource = td.by_source || [];
  barChart("tdSourceChart", bySource.map(m => m.label), bySource.map(m => m.value), cssVar("--green"), false);

  renderFunnel("tdFunnel", (td.funnel || []).map(fr => ({ stage: fr.stage, value: fr.value })));

  await loadBreakdownFor("td", "testdrive", f);
}

async function loadEnquiry() {
  await populateFilterBar("enq");
  const f = readFilterBar("enq");
  const qs = apiParams(f);

  const [kpis, enquiry, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/enquiry?${qs}`),
    fetchComparisonFor("enq"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("enqKpiGrid", [
    { label: "Total enquiries", value: fmtInt(kpis.total_enquiries), color: "var(--blue)",
      sub: deltaHtml(cmp["Total Enquiries"]?.change_pct, cmp["Total Enquiries"]?.direction) },
    { label: "Enquiry → booking", value: fmtPct(kpis.enquiry_to_booking_rate), color: "var(--green)",
      sub: deltaHtml(cmp["Enquiry to Booking Conv. (%)"]?.change_pct, cmp["Enquiry to Booking Conv. (%)"]?.direction) },
    { label: "Lost enquiries", value: fmtInt(kpis.lost_enquiries), color: "var(--red)",
      sub: deltaHtml(cmp["Lost Enquiries"]?.change_pct, cmp["Lost Enquiries"]?.direction) },
  ]);

  const status = enquiry.status_breakdown || [];
  doughnutChart("enqStatusChart", status.map(s => s.label), status.map(s => s.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), cssVar("--red"), "#999", "#666", "#444"]);

  const aging = enquiry.aging_buckets || [];
  barChart("enqAgingChart", aging.map(a => a.label), aging.map(a => a.value), cssVar("--purple"), false);

  const cities = enquiry.city_breakdown || [];
  barChart("enqCityChart", cities.map(c => c.label), cities.map(c => c.value), cssVar("--blue"));

  const lost = enquiry.lost_reasons || [];
  barChart("enqLostChart", lost.map(l => l.label), lost.map(l => l.value), cssVar("--red"));

  await loadBreakdownFor("enq", "enquiry", f);
}

async function loadBooking() {
  await populateFilterBar("book");
  const f = readFilterBar("book");
  const qs = apiParams(f);

  const [kpis, booking, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/booking?${qs}`),
    fetchComparisonFor("book"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("bookKpiGrid", [
    { label: "Total bookings", value: fmtInt(kpis.total_bookings), color: "var(--green)",
      sub: deltaHtml(cmp["Total Bookings"]?.change_pct, cmp["Total Bookings"]?.direction) },
    { label: "Booking → retail", value: fmtPct(kpis.booking_to_retail_rate), color: "var(--purple)",
      sub: deltaHtml(cmp["Booking to Retail Conv. (%)"]?.change_pct, cmp["Booking to Retail Conv. (%)"]?.direction) },
    { label: "Avg. booking age", value: `${kpis.avg_booking_age_days} days`, color: "var(--blue)",
      sub: "days since enquiry, this selection" },
    { label: "Amount received", value: fmtMoney(booking.total_amount_received), color: "var(--green)",
      sub: "advance collected, this selection" },
  ]);

  const mode = booking.mode_of_purchase || [];
  doughnutChart("bookModeChart", mode.map(m => m.label), mode.map(m => m.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), "#999"]);

  const byModel = booking.by_model || [];
  barChart("bookModelChart", byModel.map(m => m.label), byModel.map(m => m.value), cssVar("--blue"));

  const byConsultant = booking.by_consultant || [];
  barChart("bookConsultantChart", byConsultant.map(m => m.label), byConsultant.map(m => m.value), cssVar("--purple"));

  const trend = booking.daily_trend || [];
  lineChart("bookTrendChart", trend.map(t => fmtShortDate(t.date)), trend.map(t => t.value), cssVar("--green"));

  await loadBreakdownFor("book", "booking", f);
}

async function loadSales() {
  await populateFilterBar("sales");
  const f = readFilterBar("sales");
  const qs = apiParams(f);

  const [kpis, sales, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/sales?${qs}`),
    fetchComparisonFor("sales"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("salesKpiGrid", [
    { label: "Units retailed", value: fmtInt(kpis.total_retail), color: "var(--purple)",
      sub: deltaHtml(cmp["Total Retail (Units Sold)"]?.change_pct, cmp["Total Retail (Units Sold)"]?.direction) },
    { label: "Revenue", value: fmtMoney(kpis.total_revenue), color: "var(--green)",
      sub: deltaHtml(cmp["Total Revenue"]?.change_pct, cmp["Total Revenue"]?.direction) },
    { label: "Avg. delivery time", value: `${sales.avg_delivery_days} days`, color: "var(--blue)",
      sub: "invoice to delivery, this selection" },
  ]);

  const revByModel = sales.revenue_by_model || [];
  barChart("salesRevenueChart", revByModel.map(m => m.label), revByModel.map(m => m.value), cssVar("--green"));

  const unitsByModel = sales.units_by_model || [];
  barChart("salesUnitsChart", unitsByModel.map(m => m.label), unitsByModel.map(m => m.value), cssVar("--purple"));

  const trend = sales.daily_trend || [];
  lineChart("salesTrendChart", trend.map(t => fmtShortDate(t.date)), trend.map(t => t.value), cssVar("--green"));

  const bySource = sales.by_source || [];
  doughnutChart("salesSourceChart", bySource.map(s => s.label), bySource.map(s => s.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), "#999"]);

  await loadBreakdownFor("sales", "sales", f);
}

function renderComparisonTable(comparison) {
  document.getElementById("comparisonTitle").textContent =
    `${comparison.current_period_label} vs ${comparison.previous_period_label}`;
  document.getElementById("colPrev").textContent = comparison.previous_period_label;
  document.getElementById("colCurr").textContent = comparison.current_period_label;

  const moneyRows = new Set(["Total Revenue"]);
  const pctRows = new Set(["Test Drive Rate (%)", "Enquiry to Booking Conv. (%)",
    "Enquiry to Retail Conv. (%)", "Booking to Retail Conv. (%)", "Test Drive to Booking Conv. (%)"]);

  const fmtCell = (metric, v) => {
    if (moneyRows.has(metric)) return fmtMoney(v);
    if (pctRows.has(metric)) return fmtPct(v);
    return fmtInt(v);
  };

  const tbody = document.querySelector("#comparisonTable tbody");
  tbody.innerHTML = comparison.rows.map(r => {
    const arrow = r.direction === "up" ? "▲" : r.direction === "down" ? "▼" : "•";
    const changeText = r.change_pct === null
      ? `<span class="delta up">new</span>`
      : `<span class="delta ${r.direction}">${arrow} ${r.change_pct > 0 ? "+" : ""}${r.change_pct.toFixed(1)}%</span>`;
    return `
      <tr>
        <td>${r.metric}</td>
        <td>${fmtCell(r.metric, r.previous)}</td>
        <td>${fmtCell(r.metric, r.current)}</td>
        <td>${changeText}</td>
      </tr>`;
  }).join("");

  const f = comparison.filters || {};
  const activeFilters = ["model", "consultant", "source"].filter(k => f[k] && f[k] !== "all");
  const filterNote = activeFilters.length
    ? ` (filtered by ${activeFilters.map(k => f[k]).join(", ")})`
    : "";

  document.getElementById("comparisonHint").textContent =
    comparison.rows.every(r => r.previous === 0)
      ? `No data was found for ${comparison.previous_period_label}${filterNote} yet — once a new month's export is uploaded, this comparison fills in automatically.`
      : "";
}

async function fetchComparisonFor(prefix) {
  const f = readFilterBar(prefix);
  const params = new URLSearchParams({ month: f.month, model: f.model, consultant: f.consultant, source: f.source });
  return getJSON(`/api/comparison?${params.toString()}`);
}

async function refreshComparisonView() {
  const comparison = await fetchComparisonFor("cmp");
  renderComparisonTable(comparison);
}

async function loadComparison() {
  await populateFilterBar("cmp");
  await refreshComparisonView();
}

/* Populate the Month / Model / Consultant / Source dropdowns for a given
   prefix (e.g. "ov", "td", "cmp"). Rebuilt fresh every time a section
   (re)loads — e.g. after an upload adds a new month or model — while
   preserving the user's current selection when it's still valid. Each
   section's filters are independent of every other section's. */
async function populateFilterBar(prefix) {
  if (!document.getElementById(`${prefix}Month`)) return; // section has no filter bar
  if (!state.filterOptions) {
    state.filterOptions = await getJSON("/api/filters");
  }

  const fillSelect = (sel, values, allLabel) => {
    if (!sel) return;
    const previous = sel.value;
    sel.innerHTML = "";
    if (allLabel) sel.appendChild(new Option(allLabel, "all"));
    values.forEach(v => sel.appendChild(new Option(v.label ?? v, v.value ?? v)));
    const stillValid = Array.from(sel.options).some(o => o.value === previous);
    sel.value = stillValid ? previous : (sel.options[0]?.value ?? "");
  };

  const monthSel = document.getElementById(`${prefix}Month`);
  const wasEmpty = !monthSel.value;
  fillSelect(monthSel, state.meta.available_periods.slice().reverse(), null);
  if (wasEmpty) monthSel.value = state.meta.current_period;

  fillSelect(document.getElementById(`${prefix}Model`), state.filterOptions.models, "All models");
  fillSelect(document.getElementById(`${prefix}Consultant`), state.filterOptions.consultants, "All consultants");
  fillSelect(document.getElementById(`${prefix}Source`), state.filterOptions.sources, "All sources");
}

/* Read a section's current filter selections straight from its <select>s. */
function readFilterBar(prefix) {
  return {
    month: document.getElementById(`${prefix}Month`)?.value || state.meta.current_period,
    model: document.getElementById(`${prefix}Model`)?.value || "all",
    consultant: document.getElementById(`${prefix}Consultant`)?.value || "all",
    source: document.getElementById(`${prefix}Source`)?.value || "all",
  };
}

/* Wire the change events + Reset button for one section's filter bar. Called
   once at boot for every section that has one. */
function attachFilterBar(prefix, onChange) {
  if (!document.getElementById(`${prefix}Month`)) return;
  ["Month", "Model", "Consultant", "Source"].forEach(suffix => {
    document.getElementById(`${prefix}${suffix}`).addEventListener("change", onChange);
  });
  const resetBtn = document.getElementById(`${prefix}Reset`);
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      document.getElementById(`${prefix}Model`).value = "all";
      document.getElementById(`${prefix}Consultant`).value = "all";
      document.getElementById(`${prefix}Source`).value = "all";
      document.getElementById(`${prefix}Month`).value = state.meta.current_period;
      await onChange();
    });
  }
}

/* ---------------------------------------------------------------------- */
/* Breakdown tables (Model wise / Consultant wise / Source wise)           */
/* ---------------------------------------------------------------------- */
function renderBreakdownTable(prefix, section, data, activeDim) {
  const head = document.getElementById(`${prefix}BreakdownHead`);
  const body = document.getElementById(`${prefix}BreakdownBody`);
  if (!head || !body) return;

  const cols = BREAKDOWN_COLUMNS[section];
  const rows = (data && data[`by_${activeDim}`]) || [];

  head.innerHTML = `<tr><th>${DIM_LABELS[activeDim]}</th>${cols.map(c => `<th>${c.label}</th>`).join("")}</tr>`;

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${cols.length + 1}" class="empty-cell">No data for this selection.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(r => `
    <tr>
      <td>${r.label}</td>
      ${cols.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("")}
    </tr>
  `).join("");
}

async function loadBreakdownFor(prefix, section, filters) {
  const params = new URLSearchParams({
    section, period: filters.month, model: filters.model,
    consultant: filters.consultant, source: filters.source,
  });
  const data = await getJSON(`/api/breakdown?${params.toString()}`);
  state.breakdownCache[prefix] = data;
  const activeBtn = document.querySelector(`[data-tabgroup="${prefix}"] .tab-btn.active`);
  renderBreakdownTable(prefix, section, data, activeBtn ? activeBtn.dataset.dim : "model");
}

const SECTION_LOADERS = {
  overview: loadOverview,
  testdrive: loadTestDrive,
  enquiry: loadEnquiry,
  booking: loadBooking,
  sales: loadSales,
  comparison: loadComparison,
};

const SECTION_TITLES = {
  overview: ["Overview", "Performance snapshot for the selected period"],
  testdrive: ["Test drive analytics", "Enquiry sheet column O — Y (done) vs N (not done)"],
  enquiry: ["Enquiry analytics", "Status, sources, ageing and lost reasons"],
  booking: ["Booking analytics", "Mode of purchase, consultants and trend"],
  sales: ["Retail analytics", "Retail revenue, units and delivery performance"],
  comparison: ["Month comparison", "Current month vs the previous month, metric by metric"],
};

/* ---------------------------------------------------------------------- */
/* Navigation                                                               */
/* ---------------------------------------------------------------------- */
async function showSection(name, { force = false } = {}) {
  document.querySelectorAll(".nav-link").forEach(b => b.classList.toggle("active", b.dataset.section === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
  const [title, subtitle] = SECTION_TITLES[name];
  document.getElementById("pageTitle").textContent = title;
  document.getElementById("pageSubtitle").textContent = subtitle;

  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebarBackdrop").classList.remove("open");

  if (force || !state.loadedSections.has(name)) {
    await SECTION_LOADERS[name]();
    state.loadedSections.add(name);
  }
}

async function reloadAllLoadedSections() {
  const loaded = Array.from(state.loadedSections);
  state.loadedSections.clear();
  const active = document.querySelector(".nav-link.active")?.dataset.section || "overview";
  await showSection(active, { force: true });
  // Warm the rest quietly in the background so switching tabs feels instant
  for (const name of loaded) {
    if (name !== active) {
      SECTION_LOADERS[name]().then(() => state.loadedSections.add(name)).catch(() => {});
    }
  }
}

/* ---------------------------------------------------------------------- */
/* Meta / sync status                                                       */
/* ---------------------------------------------------------------------- */
async function loadMeta() {
  const meta = await getJSON("/api/meta");
  state.meta = meta;

  document.getElementById("lastSync").textContent = `Loaded ${fmtStamp(meta.last_loaded)}`;
  document.getElementById("dealerCode").textContent =
    `Unnati Hyundai · ${fmtInt(meta.row_counts.enquiry)} enquiries · ${fmtInt(meta.row_counts.booking)} bookings · ${fmtInt(meta.row_counts.sales)} retails`;
}

/* ---------------------------------------------------------------------- */
/* Theme toggle                                                             */
/* ---------------------------------------------------------------------- */
function initTheme() {
  const saved = localStorage.getItem("dash-theme") || "light";
  if (saved === "dark") document.documentElement.setAttribute("data-theme", "dark");
  document.getElementById("themeToggle").addEventListener("click", () => {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    if (isDark) {
      document.documentElement.removeAttribute("data-theme");
      localStorage.setItem("dash-theme", "light");
    } else {
      document.documentElement.setAttribute("data-theme", "dark");
      localStorage.setItem("dash-theme", "dark");
    }
    // Re-render the active section so chart colors pick up the new theme
    const active = document.querySelector(".nav-link.active")?.dataset.section || "overview";
    state.gaugeLength = null;
    SECTION_LOADERS[active]();
  });
}

/* ---------------------------------------------------------------------- */
/* Upload modal                                                            */
/* ---------------------------------------------------------------------- */
function initUploadModal() {
  const modal = document.getElementById("uploadModal");
  document.getElementById("uploadBtn").addEventListener("click", () => modal.classList.add("open"));
  document.getElementById("cancelUpload").addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });

  document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = new FormData(form);
    const msg = document.getElementById("uploadMsg");
    const submitBtn = document.getElementById("submitUpload");

    const hasFile = ["enquiry", "booking", "sales"].some(k => data.get(k) && data.get(k).size > 0);
    if (!hasFile) {
      msg.textContent = "Choose at least one file to upload.";
      msg.className = "modal-msg err";
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Uploading…";
    msg.textContent = "";
    try {
      const res = await fetch("/api/upload", { method: "POST", body: data });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || "Upload failed");
      msg.textContent = `Updated: ${json.updated.join(", ")}. Recalculating dashboard…`;
      msg.className = "modal-msg ok";
      await loadMeta();
      await reloadAllLoadedSections();
      setTimeout(() => { modal.classList.remove("open"); form.reset(); msg.textContent = ""; }, 1200);
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "modal-msg err";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Upload & recalculate";
    }
  });
}

/* ---------------------------------------------------------------------- */
/* Boot                                                                     */
/* ---------------------------------------------------------------------- */
async function boot() {
  initTheme();
  initUploadModal();

  document.querySelectorAll(".nav-link").forEach(btn => {
    btn.addEventListener("click", () => showSection(btn.dataset.section));
  });

  document.getElementById("menuToggle").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("open");
    document.getElementById("sidebarBackdrop").classList.toggle("open");
  });

  document.getElementById("sidebarBackdrop").addEventListener("click", () => {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebarBackdrop").classList.remove("open");
  });

  document.getElementById("refreshBtn").addEventListener("click", async () => {
    const dot = document.getElementById("syncDot");
    dot.classList.add("stale");
    await fetch("/api/refresh", { method: "POST" });
    await loadMeta();
    await reloadAllLoadedSections();
    dot.classList.remove("stale");
  });

  // Every page's own Month / Model / Consultant / Source filter bar
  attachFilterBar("ov", loadOverview);
  attachFilterBar("enq", loadEnquiry);
  attachFilterBar("td", loadTestDrive);
  attachFilterBar("book", loadBooking);
  attachFilterBar("sales", loadSales);
  attachFilterBar("cmp", refreshComparisonView);

  // Model / Consultant / Source tabs under each page's breakdown table
  document.querySelectorAll(".tab-row[data-tabgroup]").forEach(row => {
    row.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab-btn");
      if (!btn) return;
      row.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const prefix = row.dataset.tabgroup;
      const section = PREFIX_TO_SECTION[prefix];
      const cached = state.breakdownCache[prefix];
      if (cached) renderBreakdownTable(prefix, section, cached, btn.dataset.dim);
    });
  });

  await loadMeta();
  await showSection("overview");
}

document.addEventListener("DOMContentLoaded", boot);
