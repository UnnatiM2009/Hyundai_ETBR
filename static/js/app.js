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
  inventoryFilterOptions: null,
  inventoryBreakdownCache: null,
  cmpMtd: false,         // Month Comparison page's MTD toggle - pressed = on
};

/* Every section that has its own Month / Model / Consultant / Source filter
   bar, keyed by the id prefix used in its <select> elements, mapped to the
   section key used elsewhere (SECTION_LOADERS, /api/breakdown?section=...). */
const PREFIX_TO_SECTION = {
  ov: "overview", enq: "enquiry", td: "testdrive", book: "booking", sales: "sales",
  conv: "conversion", ex: "exchange",
};

/* Column layout for the Model/Consultant/Source breakdown table on each page —
   the metrics that matter differ per section, so each gets its own column set. */
const BREAKDOWN_COLUMNS = {
  overview: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "test_drives", label: "Test Drives", fmt: (v) => fmtInt(v) },
    { key: "bookings", label: "Bookings", fmt: (v) => fmtInt(v) },
    { key: "retail", label: "Retail", fmt: (v) => fmtInt(v) },
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
    { key: "booking_cancel", label: "Booking Cancel", fmt: (v) => fmtInt(v) },
    { key: "avg_booking_age", label: "Avg. Booking Age", fmt: (v) => `${v} days` },
  ],
  sales: [
    { key: "units", label: "Units", fmt: (v) => fmtInt(v) },
    { key: "avg_booking_to_retail_days", label: "Avg. Booking → Retail", fmt: (v) => `${v} days` },
  ],
  exchange: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "exchange", label: "Exchange Opted", fmt: (v) => fmtInt(v) },
    { key: "exchange_rate", label: "Exchange %", fmt: (v) => fmtPct(v) },
    { key: "scrap", label: "Scrap (Y)", fmt: (v) => fmtInt(v) },
    { key: "present_car", label: "Present Car (Y)", fmt: (v) => fmtInt(v) },
  ],
  conversion: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
    { key: "test_drives", label: "Test Drives", fmt: (v) => fmtInt(v) },
    { key: "bookings", label: "Bookings", fmt: (v) => fmtInt(v) },
    { key: "retail", label: "Retails", fmt: (v) => fmtInt(v) },
    { key: "e2t", label: "E2T %", fmt: (v) => fmtPct(v) },
    { key: "e2b", label: "E2B %", fmt: (v) => fmtPct(v) },
    { key: "e2r", label: "E2R %", fmt: (v) => fmtPct(v) },
    { key: "b2r", label: "B2R %", fmt: (v) => fmtPct(v) },
  ],
};

const DIM_LABELS = { model: "Model", consultant: "Consultant", source: "Source" };

/* Breakdown tables whose "By Model" rows open the Model window (variant details). */
const MODEL_WINDOW_PREFIXES = ["ov", "conv"];

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

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const badge = (text, tone) => `<span class="badge ${tone}">${esc(text)}</span>`;
const dash = (v) => (v === "" || v === null || v === undefined ? "—" : esc(v));
const fmtIsoLong = (iso) => {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" })
    .format(new Date(iso + "T00:00:00"));
};
function todayISO() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

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
        x: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 } },
        y: { grid: { display: false }, ticks: { color: baseInkColor(), precision: 0 } },
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
        y: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 }, beginAtZero: true },
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

function stackedBarChart(canvasId, labels, datasets) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  state.charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: datasets.map(d => ({ label: d.label, data: d.values, backgroundColor: d.color, borderWidth: 0, maxBarThickness: 90 })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { color: baseInkColor(), boxWidth: 12, padding: 14 } },
        tooltip: { mode: "index", intersect: false },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: baseInkColor() } },
        y: { stacked: true, beginAtZero: true, grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 } },
      },
    },
  });
}

function groupedBarChart(canvasId, labels, datasets) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  state.charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: datasets.map(d => ({ label: d.label, data: d.values, backgroundColor: d.color, borderRadius: 4, maxBarThickness: 22 })),
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { color: baseInkColor(), boxWidth: 12, padding: 14 } } },
      scales: {
        x: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 }, beginAtZero: true },
        y: { grid: { display: false }, ticks: { color: baseInkColor(), autoSkip: false } },
      },
    },
  });
}

/* Bars coloured per point (pending / today / upcoming); clicking a bar calls onPick(index). */
function colouredBarChart(canvasId, labels, values, colors, onPick) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId).getContext("2d");
  state.charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4, maxBarThickness: 26 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      onClick: (evt, els) => { if (els.length && onPick) onPick(els[0].index); },
      onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? "pointer" : "default"; },
      scales: {
        x: { grid: { display: false }, ticks: { color: baseInkColor(), maxRotation: 60, autoSkip: true } },
        y: { grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 }, beginAtZero: true },
      },
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
  const p = { period: f.month, model: f.model, consultant: f.consultant, source: f.source,
              sub_source: f.sub_source || "all" };
  if (f.age && f.age !== "all") p.age = f.age;      // Enquiry page only
  return new URLSearchParams(p).toString();
}

/* "Enquiry date" dropdown on the Enquiry page: the dates of the SELECTED MONTH that have enquiries.
   It is rebuilt whenever the Month changes (and falls back to "All dates" if the chosen date is not in that month).
   The id stays "enqAge" - the value travels to the server as age=date:YYYY-MM-DD. */
function populateAgeSelect() {
  const sel = document.getElementById("enqAge");
  if (!sel || !state.filterOptions) return;
  const month = document.getElementById("enqMonth")?.value || state.meta.current_period;
  const dates = (state.filterOptions.enquiry_dates || {})[month] || [];
  const previous = sel.value;
  sel.innerHTML = "";
  sel.appendChild(new Option(dates.length ? `All dates in ${monthLabelOf(month)}` : "No enquiries this month", "all"));
  const wd = new Intl.DateTimeFormat("en-GB", { weekday: "short" });
  dates.forEach(iso => sel.appendChild(new Option(`${fmtIsoLong(iso)} · ${wd.format(new Date(iso + "T00:00:00"))}`, `date:${iso}`)));
  sel.value = Array.from(sel.options).some(o => o.value === previous) ? previous : "all";
}
const monthLabelOf = (m) => (state.meta.available_periods.find(p => p.value === m)?.label) || m;

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
    { label: "Booking cancel", value: fmtInt(kpis.booking_cancel), color: "var(--amber)",
      sub: deltaHtml(cmp["Booking Cancel"]?.change_pct, cmp["Booking Cancel"]?.direction) },
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

  await Promise.all([loadBreakdownFor("ov", "overview", f), loadBreakdownFor("conv", "conversion", f)]);
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
  populateAgeSelect();
  const f = readFilterBar("enq");
  f.age = document.getElementById("enqAge")?.value || "all";
  const qs = apiParams(f);
  const ageOn = f.age !== "all";
  const ageText = ageOn ? document.getElementById("enqAge").selectedOptions[0].textContent.split(" · ")[0] : "";

  const [kpis, enquiry, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/enquiry?${qs}`),
    fetchComparisonFor("enq"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("enqKpiGrid", [
    { label: "Total enquiries", value: fmtInt(kpis.total_enquiries), color: "var(--blue)",
      sub: ageOn ? `enquiry date: ${ageText}` : deltaHtml(cmp["Total Enquiries"]?.change_pct, cmp["Total Enquiries"]?.direction) },
    { label: "Enquiry → booking", value: fmtPct(kpis.enquiry_to_booking_rate), color: "var(--green)",
      sub: ageOn ? `enquiry date: ${ageText}` : deltaHtml(cmp["Enquiry to Booking Conv. (%)"]?.change_pct, cmp["Enquiry to Booking Conv. (%)"]?.direction) },
    { label: "Appointed enquiry", value: fmtInt(kpis.appointed_enquiries), color: "var(--purple)",
      sub: ageOn ? `enquiry date: ${ageText}` : "status = Appointed Enquiry, this selection" },
    { label: "Lost enquiries", value: fmtInt(kpis.lost_enquiries), color: "var(--red)",
      sub: ageOn ? `enquiry date: ${ageText}` : deltaHtml(cmp["Lost Enquiries"]?.change_pct, cmp["Lost Enquiries"]?.direction) },
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

/* ---------------------------------------------------------------------- */
/* Exchange page                                                            */
/* ---------------------------------------------------------------------- */
const exState = { scope: "exchange" };

async function loadExchange() {
  await populateFilterBar("ex");
  const f = readFilterBar("ex");
  const qs = apiParams(f);
  const data = await getJSON(`/api/exchange?${qs}&scope=${exState.scope}`);
  const k = data.kpis;
  const palette = [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), cssVar("--red"), "#999"];

  renderKpiGrid("exKpiGrid", [
    { label: "Exchange opted (Y)", value: fmtInt(k.exchange_opted), color: "var(--blue)",
      sub: `${fmtPct(k.exchange_rate)} of ${fmtInt(k.total_enquiries)} enquiries` },
    { label: "Present car owners", value: fmtInt(k.present_car), color: "var(--amber)",
      sub: "Present Car = Y" },
    { label: "Scrap (Y)", value: fmtInt(k.scrap_yes), color: "var(--red)",
      sub: `${fmtInt(k.scrap_no)} said No · among exchange customers` },
    { label: "Scrap through Hyundai (Y)", value: fmtInt(k.scrap_hyundai_yes), color: "var(--purple)",
      sub: "Scrap Through Hyundai Y/N = Y" },
    { label: "Exchange → booked / retail", value: fmtInt(k.exchange_converted), color: "var(--green)",
      sub: "exchange customers now Booked or Retail" },
    { label: "Avg. present car age", value: `${k.avg_car_age} yrs`, color: "var(--blue)",
      sub: "from Model Year, present car owners" },
  ]);

  doughnutChart("exSplitChart", ["Exchange opted (Y)", "Not opted (N)"],
    [data.exchange_split.Y, data.exchange_split.N], [cssVar("--blue"), cssVar("--border")]);
  doughnutChart("exPresentChart", ["Has present car (Y)", "No present car (N)"],
    [data.present_split.Y, data.present_split.N], [cssVar("--amber"), cssVar("--border")]);

  const sc = data.scrap_split || [];
  doughnutChart("exScrapChart", sc.map(x => x.label), sc.map(x => x.value),
    [cssVar("--red"), cssVar("--green"), cssVar("--border")]);
  const sh = data.scrap_hyundai_split || [];
  doughnutChart("exScrapHyundaiChart", sh.map(x => x.label), sh.map(x => x.value),
    [cssVar("--purple"), cssVar("--amber"), cssVar("--border")]);

  const mk = data.by_maker || [];
  barChart("exMakerChart", mk.map(x => x.label), mk.map(x => x.value), cssVar("--blue"));
  const mm = data.by_maker_model || [];
  barChart("exMakerModelChart", mm.map(x => x.label), mm.map(x => x.value), cssVar("--purple"));
  const yr = data.by_year || [];
  barChart("exYearChart", yr.map(x => x.label), yr.map(x => x.value), cssVar("--amber"), false);
  const bm = data.by_model || [];
  barChart("exModelChart", bm.map(x => x.label), bm.map(x => x.value), cssVar("--green"));

  renderExchangeList(data);
  await loadBreakdownFor("ex", "exchange", f);
}

function renderExchangeList(data) {
  const yn = (v) => v === "Y" ? badge("Yes", "green") : v === "N" ? badge("No", "grey") : "—";
  const body = document.getElementById("exListBody");
  const rows = data.rows || [];
  body.innerHTML = rows.length ? rows.map(r => `<tr>
    <td>${dash(r.date)}</td><td>${dash(r.name)}<br><span class="muted-sm">${dash(r.customer_id)}</span></td>
    <td>${dash(r.phone)}</td><td>${dash(r.consultant)}</td><td>${dash(r.status)}</td>
    <td>${dash(r.model)}</td><td>${yn(r.exchange)}</td><td>${yn(r.scrap)}</td><td>${yn(r.scrap_hyundai)}</td>
    <td>${yn(r.present_car)}</td><td>${dash(r.maker)}</td><td>${dash(r.maker_model)}</td>
    <td>${dash(r.model_year)}</td><td>${dash(r.car_age)}</td></tr>`).join("")
    : `<tr><td colspan="14" class="empty-cell">No customers for this selection.</td></tr>`;
  const label = { exchange: "exchange-opted customers", present: "present car owners", all: "enquiries" }[data.scope];
  document.getElementById("exListHint").textContent =
    `Showing ${fmtInt(rows.length)} of ${fmtInt(data.row_total)} ${label}, newest enquiry first.`;
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
    { label: "Booking cancel", value: fmtInt(kpis.booking_cancel), color: "var(--red)",
      sub: deltaHtml(cmp["Booking Cancel"]?.change_pct, cmp["Booking Cancel"]?.direction) },
    { label: "Booking → retail", value: fmtPct(kpis.booking_to_retail_rate), color: "var(--purple)",
      sub: deltaHtml(cmp["Booking to Retail Conv. (%)"]?.change_pct, cmp["Booking to Retail Conv. (%)"]?.direction) },
    { label: "Avg. booking age", value: `${kpis.avg_booking_age_days} days`, color: "var(--blue)",
      sub: "days from enquiry to booking, this selection" },
  ]);

  const bySrc = booking.by_source || [];
  doughnutChart("bookSourceChart", bySrc.map(m => m.label), bySrc.map(m => m.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green"), cssVar("--purple"), cssVar("--red"), "#999"]);

  const byModel = booking.by_model || [];
  barChart("bookModelChart", byModel.map(m => m.label), byModel.map(m => m.value), cssVar("--blue"));

  const byConsultant = booking.by_consultant || [];
  barChart("bookConsultantChart", byConsultant.map(m => m.label), byConsultant.map(m => m.value), cssVar("--purple"));

  const trend = booking.daily_trend || [];
  lineChart("bookTrendChart", trend.map(t => fmtShortDate(t.date)), trend.map(t => t.value), cssVar("--green"));

  const cReasons = booking.cancel_reasons || [];
  barChart("bookCancelReasonChart", cReasons.map(m => m.label), cReasons.map(m => m.value), cssVar("--red"));
  const cModels = booking.cancel_by_model || [];
  barChart("bookCancelModelChart", cModels.map(m => m.label), cModels.map(m => m.value), cssVar("--amber"));

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
    { label: "Enquiry → retail", value: fmtPct(kpis.enquiry_to_retail_rate), color: "var(--green)",
      sub: deltaHtml(cmp["Enquiry to Retail Conv. (%)"]?.change_pct, cmp["Enquiry to Retail Conv. (%)"]?.direction) },
    { label: "Avg. booking → retail", value: `${sales.avg_booking_to_retail_days} days`, color: "var(--blue)",
      sub: "booking date to retail date, this selection" },
  ]);

  const byCons = sales.by_consultant || [];
  barChart("salesConsultantChart", byCons.map(m => m.label), byCons.map(m => m.value), cssVar("--green"));

  const unitsByModel = sales.units_by_model || [];
  barChart("salesUnitsChart", unitsByModel.map(m => m.label), unitsByModel.map(m => m.value), cssVar("--purple"));

  const trend = sales.daily_trend || [];
  lineChart("salesTrendChart", trend.map(t => fmtShortDate(t.date)), trend.map(t => t.value), cssVar("--purple"));

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

  document.getElementById("comparisonSubtitle").textContent = comparison.mtd
    ? `Month-to-Date — same day range (1-${comparison.mtd_day}) in both months, so a part-way-through month isn't unfairly compared to a full one`
    : "How this period compares with the one before it";

  const mtdBtn = document.getElementById("cmpMtd");
  if (mtdBtn) mtdBtn.setAttribute("aria-pressed", comparison.mtd ? "true" : "false");

  const moneyRows = new Set();
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
  const activeFilters = ["model", "consultant", "source", "sub_source"].filter(k => f[k] && f[k] !== "all");
  const filterNote = activeFilters.length
    ? ` (filtered by ${activeFilters.map(k => f[k]).join(", ")})`
    : "";

  document.getElementById("comparisonHint").textContent =
    comparison.rows.every(r => r.previous === 0)
      ? `No data was found for ${comparison.previous_period_label}${filterNote} yet — once a new month's export is uploaded, this comparison fills in automatically.`
      : "";
}

async function fetchComparisonFor(prefix, mtd = false) {
  const f = readFilterBar(prefix);
  const params = new URLSearchParams({ month: f.month, model: f.model, consultant: f.consultant, source: f.source,
                                      sub_source: f.sub_source || "all" });
  if (mtd) params.set("mtd", "true");
  return getJSON(`/api/comparison?${params.toString()}`);
}

async function refreshComparisonView() {
  const comparison = await fetchComparisonFor("cmp", state.cmpMtd);
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
  if (!document.getElementById(`${prefix}Model`)) return; // section has no filter bar
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
  if (monthSel) {
    const wasEmpty = !monthSel.value;
    fillSelect(monthSel, state.meta.available_periods.slice().reverse(), null);
    if (wasEmpty) monthSel.value = state.meta.current_period;
  }

  fillSelect(document.getElementById(`${prefix}Model`), state.filterOptions.models, "All models");
  fillSelect(document.getElementById(`${prefix}Consultant`), state.filterOptions.consultants, "All consultants");
  fillSelect(document.getElementById(`${prefix}Source`), state.filterOptions.sources, "All sources");
  fillSubSource(prefix);
}

/* Sub-source dropdown. Every Sub-source belongs to exactly one Source (e.g. Website sits under Digital),
   so when a Source is picked the list shows only that Source's sub-sources; with "All sources" it
   shows them all. The current pick is kept when it is still valid, otherwise it falls back to "All". */
function subSourceChoices(source) {
  const fo = state.filterOptions || {};
  if (source && source !== "all") return (fo.sub_source_map && fo.sub_source_map[source]) || [];
  return fo.sub_sources || [];
}
function fillSubSource(prefix) {
  const sel = document.getElementById(`${prefix}SubSource`);
  if (!sel) return;
  const source = document.getElementById(`${prefix}Source`)?.value || "all";
  const previous = sel.value;
  sel.innerHTML = "";
  sel.appendChild(new Option("All sub-sources", "all"));
  subSourceChoices(source).forEach(v => sel.appendChild(new Option(v, v)));
  sel.value = Array.from(sel.options).some(o => o.value === previous) ? previous : "all";
}

/* Read a section's current filter selections straight from its <select>s. */
function readFilterBar(prefix) {
  return {
    month: document.getElementById(`${prefix}Month`)?.value || state.meta.current_period,
    model: document.getElementById(`${prefix}Model`)?.value || "all",
    consultant: document.getElementById(`${prefix}Consultant`)?.value || "all",
    source: document.getElementById(`${prefix}Source`)?.value || "all",
    sub_source: document.getElementById(`${prefix}SubSource`)?.value || "all",
  };
}

/* Wire the change events + Reset button for one section's filter bar. Called
   once at boot for every section that has one. */
function attachFilterBar(prefix, onChange, { extraFields = [], onReset = null } = {}) {
  if (!document.getElementById(`${prefix}Model`)) return;
  ["Month", "Model", "Consultant", "Source", "SubSource", ...extraFields].forEach(suffix => {
    const el = document.getElementById(`${prefix}${suffix}`);
    if (!el) return;
    // Picking a Source narrows the Sub-source list FIRST, then the page reloads (listeners run in the order added).
    if (suffix === "Source") el.addEventListener("change", () => fillSubSource(prefix));
    el.addEventListener("change", onChange);
  });
  const resetBtn = document.getElementById(`${prefix}Reset`);
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      document.getElementById(`${prefix}Model`).value = "all";
      document.getElementById(`${prefix}Consultant`).value = "all";
      document.getElementById(`${prefix}Source`).value = "all";
      fillSubSource(prefix);
      const subEl = document.getElementById(`${prefix}SubSource`);
      if (subEl) subEl.value = "all";
      const monthEl = document.getElementById(`${prefix}Month`);
      if (monthEl) monthEl.value = state.meta.current_period;
      if (onReset) onReset();
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
  // Overview page: a model row opens the Model window (variant details)
  const clickable = MODEL_WINDOW_PREFIXES.includes(prefix);   // Model, Consultant and Source rows all open the window
  body.innerHTML = rows.map(r => `
    <tr${clickable ? ` class="mv-row" tabindex="0" data-mv-prefix="${prefix}" data-mv-dim="${activeDim}" data-mv-model="${esc(r.label)}" title="Click to see variant details"` : ""}>
      <td>${r.label}</td>
      ${cols.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("")}
    </tr>
  `).join("");
}

async function loadBreakdownFor(prefix, section, filters) {
  const params = new URLSearchParams({
    section, period: filters.month, model: filters.model,
    consultant: filters.consultant, source: filters.source, sub_source: filters.sub_source || "all",
  });
  if (filters.age && filters.age !== "all") params.set("age", filters.age);
  const data = await getJSON(`/api/breakdown?${params.toString()}`);
  state.breakdownCache[prefix] = data;
  const activeBtn = document.querySelector(`[data-tabgroup="${prefix}"] .tab-btn.active`);
  renderBreakdownTable(prefix, section, data, activeBtn ? activeBtn.dataset.dim : "model");
}

/* ---------------------------------------------------------------------- */
/* Model window — variant-wise detail for one model (Hyundai palette)       */
/* Opened by clicking a model row in the Overview Breakdown / Conversion    */
/* tables. Uses the page's current Month / Consultant / Source filters.     */
/* ---------------------------------------------------------------------- */
const HY = {
  navy: "#002C5F", blue: "#00AAD2", mid: "#4A7BB7", sky: "#9FDCEE", sand: "#E4DCD3", orange: "#DD7B2E",
};
const mvState = {
  open: false, model: "", dim: "model", prefix: "ov", filters: null, modelPick: null,
  variants: new Set(), ages: new Set(), data: null, seq: 0,
  rec: null,                       // the compact record set the whole window is computed from
};
const mvCache = new Map();         // key -> Promise of a record set (also filled when you hover a row)
const mvChartData = { labels: [], values: [], buckets: [], total: 0 };   // read live by the chart callbacks

const mvEl = (id) => document.getElementById(id);

/* --- records: ONE download per window (cached; also pre-loaded when you hover a row) --- */
function mvRecordsQuery(model, dim, filters) {
  const p = new URLSearchParams({ model, period: filters.month, consultant: filters.consultant, source: filters.source,
                                  sub_source: filters.sub_source || "all" });
  if (dim !== "model") p.set("dim", dim);
  if (filters.age && filters.age !== "all") p.set("age", filters.age);
  return p.toString();
}
function mvLoadRecords(model, dim, filters) {
  const qs = mvRecordsQuery(model, dim, filters);
  if (!mvCache.has(qs)) {
    const pr = getJSON(`/api/model-variants/records?${qs}`);
    pr.catch(() => mvCache.delete(qs));          // never keep a failed download
    mvCache.set(qs, pr);
    if (mvCache.size > 40) mvCache.delete(mvCache.keys().next().value);
  }
  return mvCache.get(qs);
}

const MV_TAGS = { model: "MODEL WINDOW", consultant: "CONSULTANT WINDOW", source: "SOURCE WINDOW" };
const MV_KIND = { model: "Model Group", consultant: "Consultant", source: "Source" };

async function openModelWindow(prefix, model, dim = "model") {
  mvState.prefix = prefix;
  mvState.model = model;
  mvState.dim = dim;
  mvEl("mvTag").textContent = MV_TAGS[dim] || MV_TAGS.model;
  mvState.filters = readFilterBar(prefix);
  mvState.modelPick = null;
  mvEl("mvModelSelect").innerHTML = "";
  renderMvMonthSelect();
  mvState.variants = new Set();
  mvState.ages = new Set();
  mvState.data = null;
  mvState.rec = null;
  mvEl("mvTitle").textContent = model;
  mvEl("mvSub").textContent = "Loading variant details…";
  mvEl("mvKpis").innerHTML = "";
  mvEl("mvTableHead").innerHTML = "";
  mvEl("mvTableBody").innerHTML = "";
  mvEl("mvTableFoot").innerHTML = "";
  mvEl("mvWaitBody").innerHTML = "";
  mvEl("mvWaitHead").innerHTML = "";
  mvEl("mvFilterNote").hidden = true;
  const modal = mvEl("mvModal");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  mvState.open = true;
  mvEl("mvClose").focus();
  const seq = ++mvState.seq;
  try {
    const rec = await mvLoadRecords(model, dim, mvState.filters);
    if (seq !== mvState.seq || !mvState.open) return;       // closed or re-opened meanwhile
    mvState.rec = rec;
    refreshModelWindow();
  } catch (err) {
    if (seq !== mvState.seq) return;
    mvEl("mvSub").textContent = "Could not load variant details. Please try again.";
  }
}

function closeModelWindow() {
  const modal = mvEl("mvModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  mvState.open = false;
  destroyChart("mvVariantChart");
  destroyChart("mvAgeChart");
}

/* Everything after the download is computed here, in the browser - no server call, so it is instant. */
function refreshModelWindow() {
  if (!mvState.rec) return;
  const data = mvCompute();
  mvState.data = data;
  renderModelWindow(data);
}

function mvSelectedModel() {
  if (mvState.dim === "model") return mvState.model;
  const f = mvState.filters;
  const m = mvState.modelPick !== null ? mvState.modelPick : f.model;
  return m || "all";
}

const MV_HIDDEN_MODELS = new Set(["", "nan", "none", "unknown"]);

/* Same rules as the server (compute_model_variant_detail): enquiries / bookings / retails each come from
   their own view; the two charts ignore their own selection; KPIs and the table honour both. */
function mvCompute() {
  const R = mvState.rec, V = R.vocab;
  const selModel = mvSelectedModel();
  const modelOk = (r) => selModel === "all" || V.models[r[1]] === selModel;
  const buckets = R.age_buckets;
  const agesSel = buckets.filter(b => mvState.ages.has(b[0]));
  const ageOk = (r) => !agesSel.length || agesSel.some(b => r[3] >= b[2] && r[3] <= b[3]);
  const vIdx = new Set();
  if (mvState.variants.size) V.variants.forEach((v, i) => { if (mvState.variants.has(v)) vIdx.add(i); });
  const varOk = (r) => !vIdx.size || vIdx.has(r[0]);
  // Same rounding as the server's Python round(x, 1): exact ties (e.g. 6.25) go to the even digit (6.2),
  // so the window always shows the very same percentages as the Conversion rates table.
  const r1 = (x) => {
    const q = x * 4;
    if (Number.isInteger(q) && q % 2 !== 0) { const f = Math.floor(x * 10); return (f % 2 === 0 ? f : f + 1) / 10; }
    return Number(x.toFixed(1));
  };
  const pct = (n, d) => (d ? r1((n / d) * 100) : 0);

  const enqBase = R.enq.filter(modelOk), bookBase = R.book.filter(modelOk), retBase = R.retail.filter(modelOk);
  const enq = enqBase.filter(r => ageOk(r) && varOk(r));
  const book = bookBase.filter(r => ageOk(r) && varOk(r));
  const retail = retBase.filter(r => ageOk(r) && varOk(r));

  // ---- variant rows ----
  const rows = new Map();
  const get = (i) => {
    let o = rows.get(i);
    if (!o) { o = { v: i, enq: 0, td: 0, lost: 0, book: 0, retail: 0, fuels: new Map(), models: new Map() }; rows.set(i, o); }
    return o;
  };
  const tally = (map, key) => { if (key !== undefined) map.set(key, (map.get(key) || 0) + 1); };
  const fuelOk = (i) => (V.fuels[i] || "").trim() !== "";
  enq.forEach(r => { const o = get(r[0]); o.enq++; o.td += r[4]; o.lost += r[5]; if (fuelOk(r[2])) tally(o.fuels, r[2]); tally(o.models, r[1]); });
  book.forEach(r => { const o = get(r[0]); o.book++; if (fuelOk(r[2])) tally(o.fuels, r[2]); tally(o.models, r[1]); });
  retail.forEach(r => { const o = get(r[0]); o.retail++; if (fuelOk(r[2])) tally(o.fuels, r[2]); tally(o.models, r[1]); });
  const top = (map) => { let best, bn = 0; map.forEach((n, k) => { if (n > bn) { best = k; bn = n; } }); return best; };
  const list = Array.from(rows.values()).map(o => {
    const f = top(o.fuels), m = top(o.models);
    return {
      variant: V.variants[o.v], model: m === undefined ? "" : V.models[m], fuel: f === undefined ? "" : V.fuels[f],
      enquiries: o.enq, test_drives: o.td, bookings: o.book, retail: o.retail, lost: o.lost,
      e2t: pct(o.td, o.enq), e2b: pct(o.book, o.enq), e2r: pct(o.retail, o.enq), b2r: pct(o.retail, o.book + o.retail),
    };
  });
  list.sort((a, b) => (b.enquiries - a.enquiries) || (b.bookings - a.bookings) || (b.retail - a.retail));

  // ---- KPI cards ----
  const e = enq.length, td = enq.reduce((a, r) => a + r[4], 0), lost = enq.reduce((a, r) => a + r[5], 0);
  const b = book.length, rt = retail.length;

  // ---- chart 1: variant-wise enquiries (ignores the variant selection) ----
  const vc = new Map();
  enqBase.filter(ageOk).forEach(r => vc.set(r[0], (vc.get(r[0]) || 0) + 1));
  const variant_chart = Array.from(vc.entries()).sort((a, b2) => b2[1] - a[1]).map(([i, n]) => ({ label: V.variants[i], value: n }));

  // ---- chart 2: ageing buckets (ignores the ageing selection) ----
  const ageBase = enqBase.filter(varOk);
  const ageing = buckets.map(([value, label, lo, hi]) => ({
    value, label, count: ageBase.reduce((a, r) => a + (r[3] >= lo && r[3] <= hi ? 1 : 0), 0),
  }));

  // ---- Model dropdown: every model with activity (ignores model / variant / ageing choices) ----
  const mo = new Map();
  const bump = (arr, key) => arr.forEach(r => {
    const name = V.models[r[1]];
    if (MV_HIDDEN_MODELS.has(String(name).toLowerCase())) return;
    let o = mo.get(name); if (!o) { o = { label: name, enquiries: 0, bookings: 0, retail: 0 }; mo.set(name, o); }
    o[key]++;
  });
  bump(R.enq, "enquiries"); bump(R.book, "bookings"); bump(R.retail, "retail");
  const model_options = Array.from(mo.values()).sort((a, b2) =>
    (b2.enquiries - a.enquiries) || (b2.bookings - a.bookings) || (b2.retail - a.retail));

  // ---- waiting for delivery: the very same Booked rows the Bookings card counts, longest wait first ----
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const waiting = book.map(r => {
    const bd = r[7] || "";
    const days = bd ? Math.max(0, Math.round((today - new Date(bd + "T00:00:00")) / 86400000)) : null;
    return { customer: r[4] || "", variant: V.variants[r[0]], model: V.models[r[1]], color: r[5] || "", consultant: r[6] || "", booked: bd, days };
  }).sort((a, b2) => ((b2.days ?? -1) - (a.days ?? -1)) || (a.customer < b2.customer ? -1 : 1));

  return {
    waiting,
    model: mvState.model, dim: mvState.dim, model_options, selected_model: selModel,
    period: R.period, period_label: R.period_label,
    kpis: {
      enquiries: e, test_drives: td, test_drive_rate: pct(td, e), bookings: b, retail: rt, lost,
      e2b: pct(b, e), e2r: pct(rt, e), b2r: pct(rt, b + rt), variant_count: list.length,
      top_variant: list.length ? list[0].variant : "",
    },
    variants: list, variant_chart, ageing,
  };
}

function toggleInSet(set, value) {
  if (set.has(value)) set.delete(value); else set.add(value);
}

function renderModelWindow(d) {
  const k = d.kpis;
  const f = mvState.filters;
  const filterBits = [];
  if (mvState.dim !== "model" && d.selected_model && d.selected_model !== "all") filterBits.push(d.selected_model);
  if (mvState.dim !== "consultant" && f.consultant && f.consultant !== "all") filterBits.push(f.consultant);
  if (mvState.dim !== "source" && f.source && f.source !== "all") filterBits.push(f.source);
  if (f.sub_source && f.sub_source !== "all") filterBits.push(f.sub_source);
  mvEl("mvSub").textContent =
    `${MV_KIND[mvState.dim] || "Model Group"} · ${d.period_label} · ${fmtInt(k.variant_count)} variant(s) · ${fmtInt(k.enquiries)} enquiries` +
    (filterBits.length ? ` · ${filterBits.join(" · ")}` : "");

  // Plain-language cards. Delivered + Pending always add up to Total booked, so the numbers can be checked at a glance.
  const booked = k.bookings + k.retail;          // everyone who has booked: delivered + still pending
  const plural = (n, w) => `${fmtInt(n)} ${w}${n === 1 ? "" : "s"}`;
  const cards = [
    { label: "Total enquiries", value: fmtInt(k.enquiries), color: HY.navy,
      sub: k.top_variant ? `Most asked for: ${esc(k.top_variant)}` : "No enquiries" },
    { label: "Test drives", value: fmtInt(k.test_drives), color: HY.blue,
      sub: `${fmtInt(k.test_drives)} of ${fmtInt(k.enquiries)} enquiries took a test drive (${fmtPct(k.test_drive_rate)})` },
    { label: "Total booked", value: fmtInt(booked), color: HY.mid,
      sub: booked ? `${plural(booked, "customer")} booked a car: ${fmtInt(k.retail)} delivered + ${fmtInt(k.bookings)} pending` : "Nobody has booked yet" },
    { label: "Delivered (Retail)", value: fmtInt(k.retail), color: "#1C9165",
      sub: booked ? `${plural(k.retail, "customer")} already received the car` : "No delivery yet" },
    { label: "Pending for delivery (Booked)", value: fmtInt(k.bookings), color: HY.orange,
      sub: booked ? `${plural(k.bookings, "customer")} booked but still waiting for the car (list below)` : "Nothing pending" },
    { label: "Delivery rate", value: booked ? fmtPct(k.b2r) : "\u2014", color: HY.navy,
      sub: booked ? `${fmtInt(k.retail)} of ${fmtInt(booked)} booked customers have received their car` : "Needs at least one booking" },
  ];
  mvEl("mvKpis").innerHTML = cards.map(c => `
    <div class="mv-kpi" style="--k:${c.color}">
      <div class="mv-kpi-label">${c.label}</div>
      <div class="mv-kpi-value">${c.value}</div>
      <div class="mv-kpi-sub">${c.sub}</div>
    </div>`).join("");

  renderMvModelSelect(d);
  renderMvVariantSelect(d);
  renderMvVariantChart(d);
  renderMvAgeChart(d);
  renderMvTable(d);
  renderMvWait(d);

  const note = mvEl("mvFilterNote");
  const parts = [];
  if (mvState.variants.size) parts.push(`${mvState.variants.size} variant(s)`);
  if (mvState.ages.size) {
    const labels = d.ageing.filter(a => mvState.ages.has(a.value)).map(a => a.label);
    parts.push(`age: ${labels.join(", ")}`);
  }
  note.hidden = parts.length === 0;
  mvEl("mvFilterText").textContent = parts.length ? `Filtered by ${parts.join(" · ")}` : "";
}

/* Model dropdown: "All models" (consultant / source windows only) + every model with activity. */
function renderMvModelSelect(d) {
  const sel = mvEl("mvModelSelect");
  const opts = d.model_options || [];
  const html = [];
  if (mvState.dim !== "model") html.push(`<option value="all">All models</option>`);
  opts.forEach(o => html.push(
    `<option value="${esc(o.label)}">${esc(o.label)} — ${fmtInt(o.enquiries)} enq · ${fmtInt(o.bookings)} bkg · ${fmtInt(o.retail)} retail</option>`));
  sel.innerHTML = html.join("");
  const want = d.selected_model || "all";
  sel.value = Array.from(sel.options).some(o => o.value === want) ? want : (sel.options[0]?.value ?? "");
  mvEl("mvToolbarHint").textContent = mvState.dim === "model"
    ? "Switch to another model to see its variant details"
    : `Pick a model to see ${mvState.model}'s model-wise variant details`;
}

function onMvModelChange() {
  const v = mvEl("mvModelSelect").value;
  mvState.variants = new Set();           // variants belong to a model - start fresh
  mvState.ages = new Set();
  if (mvState.dim === "model") {
    mvState.model = v;
    mvEl("mvTitle").textContent = v;
  } else {
    mvState.modelPick = v;
  }
  refreshModelWindow();
}

/* Variant dropdown: "All variants" + every variant of the CURRENTLY selected
   model (d.variant_chart already ignores the variant selection itself, so
   the option list doesn't shrink to just whatever is picked). A dropdown
   pick is a precise single choice; it doesn't replace the chart's own
   click-to-multi-select - the two work on the same mvState.variants set. */
function renderMvVariantSelect(d) {
  const sel = mvEl("mvVariantSelect");
  const items = d.variant_chart || [];
  const html = ['<option value="all">All variants</option>'];
  items.forEach(i => html.push(`<option value="${esc(i.label)}">${esc(i.label)} — ${fmtInt(i.value)} enq</option>`));
  sel.innerHTML = html.join("");
  const current = mvState.variants.size === 1 ? Array.from(mvState.variants)[0] : "all";
  sel.value = Array.from(sel.options).some(o => o.value === current) ? current : "all";
}

function onMvVariantChange() {
  const v = mvEl("mvVariantSelect").value;
  mvState.variants = v === "all" ? new Set() : new Set([v]);
  refreshModelWindow();
}

/* Month dropdown: populated once per window-open from the same months list
   every other filter bar uses. Unlike Model/Variant (pure client-side slices
   of the one record set already downloaded), changing month needs a fresh
   download, since the record set itself is scoped to one month server-side. */
function renderMvMonthSelect() {
  const sel = mvEl("mvMonthSelect");
  const periods = (state.meta && state.meta.available_periods) || [];
  sel.innerHTML = periods.slice().reverse().map(p => `<option value="${esc(p.value)}">${esc(p.label)}</option>`).join("");
  const want = mvState.filters.month;
  sel.value = Array.from(sel.options).some(o => o.value === want) ? want : (sel.options[0]?.value ?? "");
}

async function onMvMonthChange() {
  const v = mvEl("mvMonthSelect").value;
  mvState.filters.month = v;
  mvState.variants = new Set();      // a different month's variants/ages are a fresh slate
  mvState.ages = new Set();
  mvEl("mvSub").textContent = "Loading variant details…";
  const seq = ++mvState.seq;
  try {
    const rec = await mvLoadRecords(mvState.model, mvState.dim, mvState.filters);
    if (seq !== mvState.seq || !mvState.open) return;
    mvState.rec = rec;
    refreshModelWindow();
  } catch (err) {
    if (seq !== mvState.seq) return;
    mvEl("mvSub").textContent = "Could not load variant details. Please try again.";
  }
}

function renderMvVariantChart(d) {
  const items = d.variant_chart || [];
  const labels = items.map(i => i.label);
  const values = items.map(i => i.value);
  const sel = mvState.variants;
  const colors = labels.map(l => (!sel.size || sel.has(l)) ? HY.navy : "rgba(0,44,95,0.22)");
  const sameBars = state.charts["mvVariantChart"] && mvChartData.labels.join("\u0001") === labels.join("\u0001");
  mvChartData.labels = labels; mvChartData.values = values;

  if (sameBars) {                                   // clicking a bar: just recolour, no rebuild
    const ch = state.charts["mvVariantChart"];
    ch.data.datasets[0].data = values;
    ch.data.datasets[0].backgroundColor = colors;
    ch.update("none");
    return;
  }
  const box = mvEl("mvBarBox");
  box.style.height = `${Math.max(200, items.length * 30 + 40)}px`;
  destroyChart("mvVariantChart");
  if (!items.length) { return; }
  const ctx = mvEl("mvVariantChart").getContext("2d");
  state.charts["mvVariantChart"] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4, maxBarThickness: 20 }] },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      animation: false,
      layout: { padding: { right: 30 } },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { title: (it) => it[0].label, label: (it) => ` ${it.raw} enquiries` } },
      },
      onClick: (evt, els) => {
        if (!els.length) return;
        toggleInSet(mvState.variants, mvChartData.labels[els[0].index]);
        refreshModelWindow();
      },
      onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? "pointer" : "default"; },
      scales: {
        x: { beginAtZero: true, grid: { color: baseGridColor() }, ticks: { color: baseInkColor(), precision: 0 } },
        y: {
          grid: { display: false },
          ticks: {
            color: baseInkColor(), autoSkip: false, font: { size: 11 },
            callback: function (v) { const t = this.getLabelForValue(v); return t.length > 30 ? t.slice(0, 29) + "…" : t; },
          },
        },
      },
    },
    plugins: [{
      id: "mvBarValues",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        c.save(); c.font = "700 11px Inter, sans-serif"; c.fillStyle = HY.navy; c.textBaseline = "middle";
        const ink = cssVar("--ink");
        chart.getDatasetMeta(0).data.forEach((bar, i) => {
          c.fillStyle = ink;
          c.fillText(String(mvChartData.values[i]), bar.x + 6, bar.y);
        });
        c.restore();
      },
    }],
  });
}

function renderMvAgeChart(d) {
  const buckets = d.ageing || [];
  const total = buckets.reduce((a, b) => a + b.count, 0);
  const palette = [HY.blue, HY.mid, HY.navy, HY.orange];
  const sel = mvState.ages;
  const colors = buckets.map((b, i) => (!sel.size || sel.has(b.value)) ? palette[i % palette.length] : palette[i % palette.length] + "40");
  mvChartData.buckets = buckets; mvChartData.total = total;

  const existing = state.charts["mvAgeChart"];
  if (existing) {                                   // clicking a slice / bar: update numbers + colours only
    existing.data.labels = buckets.map(b => b.label);
    existing.data.datasets[0].data = buckets.map(b => b.count);
    existing.data.datasets[0].backgroundColor = colors;
    existing.update("none");
    return;
  }
  const ctx = mvEl("mvAgeChart").getContext("2d");
  state.charts["mvAgeChart"] = new Chart(ctx, {
    type: "doughnut",
    data: { labels: buckets.map(b => b.label), datasets: [{ data: buckets.map(b => b.count), backgroundColor: colors, borderColor: cssVar("--surface"), borderWidth: 2 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "52%",
      animation: false,
      plugins: {
        legend: { position: "right", labels: { color: baseInkColor(), boxWidth: 12, padding: 12 } },
        tooltip: { callbacks: { label: (it) => ` ${it.raw} enquiries (${mvChartData.total ? ((it.raw / mvChartData.total) * 100).toFixed(0) : 0}%)` } },
      },
      onClick: (evt, els) => {
        if (!els.length) return;
        toggleInSet(mvState.ages, mvChartData.buckets[els[0].index].value);
        refreshModelWindow();
      },
      onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? "pointer" : "default"; },
    },
    plugins: [{
      id: "mvSliceLabels",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        const meta = chart.getDatasetMeta(0);
        const bk = mvChartData.buckets, tot = mvChartData.total;
        c.save(); c.textAlign = "center"; c.textBaseline = "middle"; c.fillStyle = "#fff";
        meta.data.forEach((arc, i) => {
          const n = bk[i] ? bk[i].count : 0;
          if (!n || !tot) return;
          const pct = Math.round((n / tot) * 100);
          if (pct < 5) return;
          const pos = arc.tooltipPosition();
          c.font = "700 12px Inter, sans-serif";
          c.fillText(String(n), pos.x, pos.y - 7);
          c.font = "600 11px Inter, sans-serif";
          c.fillText(`${pct}%`, pos.x, pos.y + 8);
        });
        c.restore();
      },
    }],
  });
}

const MV_COLS = [
  { key: "enquiries", label: "Enquiries", fmt: fmtInt },
  { key: "test_drives", label: "Test Drives", fmt: fmtInt },
  { key: "bookings", label: "Bookings (pending)", fmt: fmtInt },
  { key: "retail", label: "Retails (delivered)", fmt: fmtInt },
  { key: "e2t", label: "E2T %", fmt: fmtPct },
  { key: "e2b", label: "E2B %", fmt: fmtPct },
  { key: "e2r", label: "E2R %", fmt: fmtPct },
  { key: "b2r", label: "B2R %", fmt: fmtPct },
];

function renderMvTable(d) {
  const rows = d.variants || [];
  mvEl("mvTableCount").textContent = `(${rows.length} variant${rows.length === 1 ? "" : "s"})`;
  const showModel = mvState.dim !== "model";
  mvEl("mvTableHead").innerHTML =
    `<tr><th>Variant</th>${showModel ? "<th>Model</th>" : ""}<th>Fuel</th>${MV_COLS.map(c => `<th>${c.label}</th>`).join("")}</tr>`;
  if (!rows.length) {
    mvEl("mvTableBody").innerHTML = `<tr><td colspan="${MV_COLS.length + 2 + (showModel ? 1 : 0)}" class="empty-cell">No variants for this selection.</td></tr>`;
    mvEl("mvTableFoot").innerHTML = "";
    return;
  }
  mvEl("mvTableBody").innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.variant)}</td>
      ${showModel ? `<td>${esc(r.model)}</td>` : ""}
      <td>${r.fuel ? `<span class="mv-fuel">${esc(r.fuel)}</span>` : "—"}</td>
      ${MV_COLS.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("")}
    </tr>`).join("");
  const k = d.kpis;
  const tot = { enquiries: k.enquiries, test_drives: k.test_drives, bookings: k.bookings, retail: k.retail,
                e2t: k.test_drive_rate, e2b: k.e2b, e2r: k.e2r, b2r: k.b2r };
  mvEl("mvTableFoot").innerHTML =
    `<tr><td>Total</td>${showModel ? "<td></td>" : ""}<td></td>${MV_COLS.map(c => `<td>${c.fmt(tot[c.key])}</td>`).join("")}</tr>`;
}

function renderMvWait(d) {
  const rows = d.waiting || [];
  const showModel = mvState.dim !== "model", showCons = mvState.dim !== "consultant";
  const withDays = rows.filter(r => r.days !== null);
  const oldest = withDays.length ? withDays[0].days : null;
  const avg = withDays.length ? withDays.reduce((a, r) => a + r.days, 0) / withDays.length : null;
  mvEl("mvWaitCount").textContent = `(${rows.length} customer${rows.length === 1 ? "" : "s"}` +
    (oldest !== null ? ` · oldest ${oldest} day${oldest === 1 ? "" : "s"} · average ${avg.toFixed(1)} days` : "") + ")";
  mvEl("mvWaitNote").textContent =
    "Customers who have booked a car but have not received it yet. The number matches the " +
    "\u201cPending for delivery\u201d card above. Days waiting = days since the booking date, up to today.";
  mvEl("mvWaitHead").innerHTML = `<tr><th>Customer</th><th>Variant</th>${showModel ? "<th>Model</th>" : ""}<th>Color</th>` +
    `${showCons ? "<th>Consultant</th>" : ""}<th>Booking date</th><th>Days waiting</th></tr>`;
  const cols = 5 + (showModel ? 1 : 0) + (showCons ? 1 : 0);
  if (!rows.length) {
    mvEl("mvWaitBody").innerHTML = `<tr><td colspan="${cols}" class="empty-cell">Nobody is pending for delivery in this selection.</td></tr>`;
    return;
  }
  mvEl("mvWaitBody").innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.customer) || "—"}</td>
      <td>${esc(r.variant)}</td>
      ${showModel ? `<td>${esc(r.model)}</td>` : ""}
      <td>${esc(r.color) || "—"}</td>
      ${showCons ? `<td>${esc(r.consultant) || "—"}</td>` : ""}
      <td data-sort="${esc(r.booked)}">${r.booked ? esc(fmtIsoLong(r.booked)) : "—"}</td>
      <td data-sort="${r.days ?? ""}">${r.days === null ? "—" : `<span class="mv-days${r.days >= 15 ? " long" : ""}">${r.days}</span>`}</td>
    </tr>`).join("");
}

function exportWaitingList() {
  const d = mvState.data;
  if (!d) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const header = ["Customer", "Variant", "Model", "Color", "Consultant", "Booking date", "Days waiting"];
  const lines = [header.map(q).join(",")];
  (d.waiting || []).forEach(r => lines.push([r.customer, r.variant, r.model, r.color, r.consultant, r.booked, r.days ?? ""].map(q).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  const picked = mvState.dim !== "model" && d.selected_model && d.selected_model !== "all" ? `_${d.selected_model}` : "";
  const safe = (d.model + picked).replace(/[^\w\-]+/g, "_");
  a.href = URL.createObjectURL(blob);
  a.download = `${safe}_pending_for_delivery_${d.period || "all"}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function exportModelWindow() {
  const d = mvState.data;
  if (!d) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lead = mvState.dim === "consultant" ? "Consultant" : mvState.dim === "source" ? "Source" : "Model";
  const showModel = mvState.dim !== "model";
  const header = [lead, ...(showModel ? ["Model"] : []), "Variant", "Fuel", ...MV_COLS.map(c => c.label)];
  const lines = [header.map(q).join(",")];
  d.variants.forEach(r => {
    lines.push([d.model, ...(showModel ? [r.model] : []), r.variant, r.fuel, ...MV_COLS.map(c => (typeof r[c.key] === "number" ? r[c.key] : ""))].map(q).join(","));
  });
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  const picked = mvState.dim !== "model" && d.selected_model && d.selected_model !== "all" ? `_${d.selected_model}` : "";
  const safe = (d.model + picked).replace(/[^\w\-]+/g, "_");
  a.href = URL.createObjectURL(blob);
  a.download = `${safe}_variants_${d.period || "all"}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

(function initModelWindow() {
  const wire = () => {
    // open: click (or Enter / Space) on a model row
    document.addEventListener("click", (e) => {
      const row = e.target.closest("tr.mv-row");
      if (row) openModelWindow(row.dataset.mvPrefix, row.dataset.mvModel, row.dataset.mvDim || "model");
    });
    document.addEventListener("keydown", (e) => {
      if (mvState.open && e.key === "Escape") { closeModelWindow(); return; }
      if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches("tr.mv-row")) {
        e.preventDefault();
        openModelWindow(e.target.dataset.mvPrefix, e.target.dataset.mvModel, e.target.dataset.mvDim || "model");
      }
    });
    // Pre-load the data the moment the mouse is over a row (or a finger touches it): by the time you click, it is ready
    const warm = (e) => {
      const row = e.target.closest && e.target.closest("tr.mv-row");
      if (row && !row._mvWarm) {
        row._mvWarm = true;
        mvLoadRecords(row.dataset.mvModel, row.dataset.mvDim || "model", readFilterBar(row.dataset.mvPrefix)).catch(() => {});
        setTimeout(() => { row._mvWarm = false; }, 4000);
      }
    };
    document.addEventListener("mouseover", warm);
    document.addEventListener("touchstart", warm, { passive: true });
    mvEl("mvClose").addEventListener("click", closeModelWindow);
    mvEl("mvModal").addEventListener("click", (e) => { if (e.target === mvEl("mvModal")) closeModelWindow(); });
    mvEl("mvExport").addEventListener("click", exportModelWindow);
    mvEl("mvWaitExport").addEventListener("click", exportWaitingList);
    mvEl("mvModelSelect").addEventListener("change", onMvModelChange);
    mvEl("mvVariantSelect").addEventListener("change", onMvVariantChange);
    mvEl("mvMonthSelect").addEventListener("change", onMvMonthChange);
    mvEl("mvClear").addEventListener("click", () => {
      mvState.variants = new Set(); mvState.ages = new Set(); refreshModelWindow();
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();

/* ---------------------------------------------------------------------- */
/* Enquiry Follow-up page                                                   */
/* ---------------------------------------------------------------------- */
const fuState = { scope: "today", date: null, data: null, cancelScope: "followup_cancel" };

/* Follow-up date range. From = the first day, To = the last day (both default to today).
   Due = dated from..to | Previous pending = before From | Upcoming = after To. */
function fuFilters() {
  const f = readFilterBar("fu");
  let from = document.getElementById("fuFrom").value || todayISO();
  let to = document.getElementById("fuTo").value || from;
  if (to < from) [from, to] = [to, from];
  return { ...f, asOf: from, from, to };
}

function fuQuery(extra = {}) {
  const f = fuFilters();
  return new URLSearchParams({
    as_of: f.from, from_date: f.from, to_date: f.to,
    period: f.month, model: f.model, consultant: f.consultant, source: f.source,
    sub_source: f.sub_source || "all", ...extra,
  }).toString();
}

const fuRangeLabel = (d) => (d.from === d.to ? fmtIsoLong(d.from) : `${fmtIsoLong(d.from)} – ${fmtIsoLong(d.to)}`);

function overdueBadge(days) {
  if (days === null || days === undefined) return badge("No date", "grey");
  if (days > 0) return badge(`${days} day${days > 1 ? "s" : ""} overdue`, "red");
  if (days === 0) return badge("Due today", "amber");
  return badge(`In ${-days} day${days < -1 ? "s" : ""}`, "blue");
}

async function loadFollowup() {
  await populateFilterBar("fu");
  const fromEl = document.getElementById("fuFrom"), toEl = document.getElementById("fuTo");
  if (!fromEl.value) fromEl.value = todayISO();
  if (!toEl.value) toEl.value = fromEl.value;
  fromEl.max = toEl.value; toEl.min = fromEl.value;           // the range can never run backwards

  const f = fuFilters();
  const data = await getJSON(`/api/followup?${fuQuery()}`);
  fuState.data = data;
  const k = data.kpis;
  const ref = fuRangeLabel(data);
  const isRange = data.from !== data.to;
  document.getElementById("fuDueHead").textContent = isRange ? "Due in range" : "Due today";
  document.getElementById("fuTodayTab").textContent = isRange ? "Due in selected dates" : "Due today";
  FU_LIST_TITLES.today = isRange ? "Follow-ups due in the selected dates" : "Follow-ups due today";

  renderKpiGrid("fuKpiGrid", [
    { label: isRange ? "Due in selected dates" : "Due today", value: fmtInt(k.due_today), color: "var(--amber)", sub: `follow-ups dated ${ref}` },
    { label: "Previous days pending", value: fmtInt(k.pending_previous), color: "var(--red)",
      sub: isRange ? `dated before ${fmtIsoLong(data.from)}, still open` : "follow-up date passed, still open" },
    { label: isRange ? "Upcoming (7 days after To date)" : "Upcoming (next 7 days)", value: fmtInt(k.upcoming_7_days), color: "var(--blue)",
      sub: `${fmtInt(k.upcoming_total)} upcoming in total` },
    { label: "Open follow-ups", value: fmtInt(k.open_followups), color: "var(--green)",
      sub: k.no_followup_date ? `${fmtInt(k.no_followup_date)} have no follow-up date` : "status = Enquiry Follow up" },
    { label: "Enquiry follow up cancel", value: fmtInt(k.followup_cancel), color: "var(--red)",
      sub: `${isRange ? "cancelled between" : "cancelled on"} ${ref}` },
    { label: "Appointed enquiry cancel", value: fmtInt(k.appointed_cancel), color: "var(--red)",
      sub: `${isRange ? "cancelled between" : "cancelled on"} ${ref}` },
  ]);

  // ---- day-wise chart ----
  const tone = { pending: cssVar("--red"), today: cssVar("--amber"), upcoming: cssVar("--blue") };
  colouredBarChart("fuDayChart",
    data.chart.map(c => fmtShortDate(c.date)), data.chart.map(c => c.value),
    data.chart.map(c => tone[c.kind]),
    (i) => selectFollowupDate(data.chart[i].date));
  const extra = [];
  if (data.older_pending) extra.push(`${fmtInt(data.older_pending)} more pending from before this window`);
  if (data.later_upcoming) extra.push(`${fmtInt(data.later_upcoming)} more after it`);
  document.getElementById("fuDayNote").textContent =
    `${data.window_days} days either side of ${ref}. Red = pending · Amber = ${isRange ? "selected dates" : "today"} · Blue = upcoming. Click a bar to list that day.` +
    (extra.length ? ` (${extra.join("; ")} — see the date-wise schedule.)` : "");

  // ---- pending age ----
  barChart("fuAgeChart", data.age_buckets.map(a => a.label), data.age_buckets.map(a => a.value), cssVar("--red"), false);

  // ---- date-wise schedule ----
  const kindBadge = { pending: ["Pending", "red"], today: ["Today", "amber"], upcoming: ["Upcoming", "blue"] };
  const dateBody = document.getElementById("fuDateBody");
  dateBody.innerHTML = data.date_table.length ? data.date_table.map(r => `
    <tr class="clickable ${fuState.scope === "date" && fuState.date === r.date ? "selected" : ""}" data-date="${r.date}">
      <td>${esc(fmtIsoLong(r.date))}</td><td>${esc(r.weekday)}</td>
      <td><strong>${fmtInt(r.value)}</strong></td>
      <td>${badge(...kindBadge[r.kind])}</td>
    </tr>`).join("") : `<tr><td colspan="4" class="empty-cell">No follow-ups scheduled.</td></tr>`;

  // ---- consultant-wise ----
  document.getElementById("fuConsultantBody").innerHTML = data.by_consultant.length
    ? data.by_consultant.map(r => `
      <tr><td>${esc(r.label)}</td>
      <td>${r.pending_previous ? `<span class="delta down">${fmtInt(r.pending_previous)}</span>` : "0"}</td>
      <td>${fmtInt(r.due_today)}</td><td>${fmtInt(r.upcoming)}</td><td><strong>${fmtInt(r.total)}</strong></td></tr>`).join("")
    : `<tr><td colspan="5" class="empty-cell">No open follow-ups for this selection.</td></tr>`;

  await Promise.all([loadFollowupList(), loadFollowupCancelList(), loadBookedFollowups()]);
}

function setFollowupScope(scope, date = null) {
  fuState.scope = scope;
  fuState.date = date;
  document.querySelectorAll("#fuScopeTabs .tab-btn").forEach(b => b.classList.toggle("active", b.dataset.scope === scope));
  const dateTab = document.getElementById("fuDateTab");
  if (scope === "date" && date) {
    dateTab.style.display = "";
    dateTab.textContent = `Date: ${fmtIsoLong(date)}`;
  } else if (scope !== "date") {
    dateTab.style.display = "none";
  }
}

async function selectFollowupDate(iso) {
  setFollowupScope("date", iso);
  document.querySelectorAll("#fuDateBody tr").forEach(tr => tr.classList.toggle("selected", tr.dataset.date === iso));
  await loadFollowupList();
  document.getElementById("fuListTitle").scrollIntoView({ behavior: "smooth", block: "start" });
}

const FU_LIST_TITLES = {
  today: "Follow-ups due today", pending: "Previous days' pending follow-ups (most overdue first)",
  upcoming: "Upcoming follow-ups", all: "All open follow-ups", date: "Follow-ups on the selected date",
};

async function loadFollowupList() {
  const scope = fuState.scope;
  const extra = { scope };
  if (scope === "date") extra.date = fuState.date;
  const data = await getJSON(`/api/followup/list?${fuQuery(extra)}`);

  document.getElementById("fuListTitle").textContent =
    scope === "date" ? `Follow-ups on ${fmtIsoLong(fuState.date)}` : FU_LIST_TITLES[scope];
  document.getElementById("fuListHead").innerHTML =
    `<tr><th>Customer</th><th>Phone</th><th>Model</th><th>Variant</th><th>Consultant</th><th>Source</th>` +
    `<th>Follow-up date</th><th>Status</th><th>Test drive</th><th>Remarks</th></tr>`;
  const body = document.getElementById("fuListBody");
  body.innerHTML = data.rows.length ? data.rows.map(r => `
    <tr>
      <td>${esc(r.customer)}</td><td>${dash(r.phone)}</td><td>${esc(r.model)}</td><td>${dash(r.variant)}</td>
      <td>${esc(r.consultant)}</td><td>${esc(r.source)}</td><td>${dash(r.next_followup)}</td>
      <td>${overdueBadge(r.days_overdue)}</td><td class="num">${r.test_drive === "Y" ? badge("Done", "green") : "—"}</td>
      <td class="wrap">${dash(r.remarks)}</td>
    </tr>`).join("") : `<tr><td colspan="10" class="empty-cell">No follow-ups for this selection.</td></tr>`;
  document.getElementById("fuListHint").textContent =
    `${fmtInt(data.total)} follow-up${data.total === 1 ? "" : "s"}` + (data.shown < data.total ? ` (showing first ${fmtInt(data.shown)})` : "");
}

async function loadFollowupCancelList() {
  const scope = fuState.cancelScope;
  const data = await getJSON(`/api/followup/list?${fuQuery({ scope })}`);
  document.getElementById("fuCancelHead").innerHTML =
    `<tr><th>Customer</th><th>Phone</th><th>Model</th><th>Variant</th><th>Consultant</th><th>Enquiry date</th>` +
    `<th>Lost date</th><th>Lost reason</th><th>Remark</th></tr>`;
  document.getElementById("fuCancelBody").innerHTML = data.rows.length ? data.rows.map(r => `
    <tr>
      <td>${esc(r.customer)}</td><td>${dash(r.phone)}</td><td>${esc(r.model)}</td><td>${dash(r.variant)}</td>
      <td>${esc(r.consultant)}</td><td>${dash(r.enquiry_date)}</td><td>${dash(r.lost_date)}</td>
      <td>${r.lost_reason ? badge(r.lost_reason, "red") : "—"}</td><td class="wrap">${dash(r.lost_remark || r.remarks)}</td>
    </tr>`).join("") : `<tr><td colspan="9" class="empty-cell">No cancelled enquiries for this selection.</td></tr>`;
}


/* ---------------------------------------------------------------------- */
/* Follow-up page > Booked enquiries by number of follow-ups               */
/*   4 cards (0 / 1 / 2 / 3 follow-ups), one Model / Consultant / Colour   */
/*   table, and a pop-up with the full customer details (no charts).       */
/*   Same Hyundai-styled window as the Overview page's model window.       */
/* ---------------------------------------------------------------------- */
const fbState = {
  data: null, dim: "model",
  // what the pop-up is showing. variant/month are independent, additive filters on
  // top of dim/label/bucket/bookedOnly - picking a variant doesn't lose which
  // model/consultant/colour row you drilled into, and vice versa.
  open: false, win: { dim: "all", label: "", bucket: "all", bookedOnly: false, variant: "", month: "" },
};
const FB_DIM_LABEL = { model: "Model", consultant: "Consultant", color: "Colour" };
const FB_DIM_TAG = { all: "FOLLOW-UP WINDOW", model: "MODEL WINDOW", consultant: "CONSULTANT WINDOW", color: "COLOUR WINDOW" };
const FB_DIM_KEY = { model: "model", consultant: "consultant", color: "color" };
const FB_BUCKETS = [0, 1, 2, 3, 4];
const FB_TONE = { 0: "var(--green)", 1: "var(--blue)", 2: "var(--amber)", 3: "var(--red)", 4: "var(--red)" };
const fbEl = (id) => document.getElementById(id);
const fbBucketText = (b) => (b === 4 ? "4+" : String(b));
const fbFollowText = (b) => (b === 4 ? "4 or more follow-ups" : b === 0 ? "0 follow-up" : b === 1 ? "1 follow-up" : `${b} follow-ups`);
const fbPct = (n, total) => (total ? `${((n / total) * 100).toFixed(1)}%` : "0.0%");

function fbBasisText(d) {
  if (!d) return "";
  return d.basis === "column"
    ? `Follow-ups are taken from the “${d.basis_column}” column of the workbook.`
    : `Estimated: the workbook has no follow-up history, so each enquiry gets 1 follow-up per ${d.cadence_days} days from the enquiry date ` +
      `to its booking date (follow-ups stop once booked; retail closes it) or cancel date, or today if still open; a new Lead = 0. Add a “Follow up Count” column to the Enquiry sheet to use real numbers.`;
}

async function loadBookedFollowups() {
  const f = fuFilters();
  let d;
  try {
    d = await getJSON(`/api/followup/booked?${new URLSearchParams({ model: f.model, consultant: f.consultant, source: f.source,
      sub_source: f.sub_source || "all", as_of: f.to })}`);
  } catch (err) {
    fbEl("fuBookNote").textContent = "Could not load booked follow-up details. Please refresh.";
    return;
  }
  fbState.data = d;
  const k = d.kpis;

  // ---- the 4 cards ----
  const cards = [0, 1, 2, 3].map(b => ({
    b,
    label: `Booked · ${fbFollowText(b)}`,
    value: fmtInt(k[`b${b}`]),
    color: FB_TONE[b],
    sub: b === 3 && k.b4
      ? `${fbPct(k.b3, k.booked)} of ${fmtInt(k.booked)} booked · +${fmtInt(k.b4)} with 4 or more`
      : `${fbPct(k[`b${b}`], k.booked)} of ${fmtInt(k.booked)} booked`,
  }));
  fbEl("fuBookKpiGrid").innerHTML = cards.map(c => `
    <div class="kpi-card" role="button" tabindex="0" data-fb-bucket="${c.b}" style="--bar-color:${c.color}"
         title="Click to see the customers">
      <div class="kpi-label">${esc(c.label)}</div>
      <div class="kpi-value">${c.value}</div>
      <div class="kpi-sub">${esc(c.sub)}</div>
    </div>`).join("");
  fbEl("fuBookNote").textContent = `Cards: the ${fmtInt(k.booked)} enquiries that reached booking (${fmtInt(k.booked - k.retailed)} booked + ${fmtInt(k.retailed)} retailed) out of ${fmtInt(k.enquiries)} total · click a card for the customer list. ` + fbBasisText(d);

  renderBookedTable();
}

function renderBookedTable() {
  const d = fbState.data;
  const dim = fbState.dim;
  const head = fbEl("fuBookHead"), body = fbEl("fuBookBody"), foot = fbEl("fuBookFoot");
  const rows = (d && d[`by_${dim}`]) || [];
  const k = d ? d.kpis : null;
  const show4 = !!(k && k.c4);                                   // the 4+ column appears only when it has data
  const buckets = show4 ? FB_BUCKETS : [0, 1, 2, 3];
  const ncols = 2 + buckets.length;
  head.innerHTML = `<tr><th>${FB_DIM_LABEL[dim]}</th><th>Total enquiries</th>` +
    buckets.map(b => `<th>${fbBucketText(b)} follow-up${b <= 1 ? "" : "s"}</th>`).join("") + `</tr>`;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${ncols}" class="empty-cell">No enquiries for this selection.</td></tr>`;
    foot.innerHTML = "";
    return;
  }
  const cell = (r, b) => {
    const n = r[`c${b}`];
    return `<td class="fb-cell ${n ? "has" : "zero"}" ${n ? `data-fb-dim="${dim}" data-fb-label="${esc(r.label)}" data-fb-bucket="${b}"` : ""}>${fmtInt(n)}</td>`;
  };
  body.innerHTML = rows.map(r => `
    <tr class="fb-row" tabindex="0" data-fb-dim="${dim}" data-fb-label="${esc(r.label)}" data-fb-bucket="all"
        title="Click to see all ${fmtInt(r.enquiries)} enquir${r.enquiries === 1 ? "y" : "ies"} of ${esc(r.label)}">
      <td>${esc(r.label)}</td>
      <td><strong>${fmtInt(r.enquiries)}</strong></td>
      ${buckets.map(b => cell(r, b)).join("")}
    </tr>`).join("");
  const tcell = (b) => `<td class="fb-cell" ${k[`c${b}`] ? `data-fb-dim="all" data-fb-label="" data-fb-bucket="${b}"` : ""}>${fmtInt(k[`c${b}`])}</td>`;
  foot.innerHTML = `<tr><td>Total</td><td>${fmtInt(k.enquiries)}</td>${buckets.map(tcell).join("")}</tr>`;
}

/* ---------- pop-up ---------- */

/* dim / label / bookedOnly only - this is the scope the Model/Variant/Month
   dropdown OPTIONS are built from, so picking a variant doesn't shrink its
   own dropdown down to just itself. */
function fbScopedRows() {
  const d = fbState.data;
  if (!d) return [];
  const w = fbState.win;
  return d.rows.filter(r => {
    if (w.bookedOnly && !r.is_booked) return false;
    if (w.dim !== "all") {
      const v = r[FB_DIM_KEY[w.dim]];
      if (v !== w.label) return false;
    }
    return true;
  });
}

const FB_DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})$/;          // enquiry_date is dd/mm/yyyy
function fbRowMonth(r) {
  const m = FB_DATE_RE.exec(r.enquiry_date || "");
  return m ? `${m[3]}-${m[2]}` : "";
}

/* fbScopedRows() + the Variant and Month dropdowns (additive, independent of dim). */
function fbWindowRows() {
  const w = fbState.win;
  return fbScopedRows().filter(r => {
    if (w.variant && r.variant !== w.variant) return false;
    if (w.month && fbRowMonth(r) !== w.month) return false;
    return true;
  });
}

function fbCountBy(rows, keyFn) {
  const map = new Map();
  rows.forEach(r => { const k = keyFn(r); if (k) map.set(k, (map.get(k) || 0) + 1); });
  return map;
}

/* Model dropdown looks at ALL loaded rows (not fbScopedRows()) - it has its own
   "All models" option and should list every model regardless of which dim the
   window happens to be scoped to right now. */
function fbModelOptions() {
  const d = fbState.data;
  if (!d) return [];
  const map = fbCountBy(d.rows, r => r.model);
  return Array.from(map.entries()).map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n);
}
function fbVariantOptions() {
  const map = fbCountBy(fbScopedRows(), r => r.variant);
  return Array.from(map.entries()).map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n);
}
function fbMonthOptions() {
  const map = fbCountBy(fbScopedRows(), fbRowMonth);
  return Array.from(map.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));   // newest first
}

function renderFbToolbar() {
  const w = fbState.win;

  const modelSel = fbEl("fbModelSelect");
  modelSel.innerHTML = ['<option value="all">All models</option>']
    .concat(fbModelOptions().map(o => `<option value="${esc(o.label)}">${esc(o.label)} — ${fmtInt(o.n)}</option>`)).join("");
  const wantModel = w.dim === "model" ? w.label : "all";
  modelSel.value = Array.from(modelSel.options).some(o => o.value === wantModel) ? wantModel : "all";

  const variantSel = fbEl("fbVariantSelect");
  variantSel.innerHTML = ['<option value="">All variants</option>']
    .concat(fbVariantOptions().map(o => `<option value="${esc(o.label)}">${esc(o.label)} — ${fmtInt(o.n)}</option>`)).join("");
  variantSel.value = Array.from(variantSel.options).some(o => o.value === w.variant) ? w.variant : "";

  const monthSel = fbEl("fbMonthSelect");
  monthSel.innerHTML = ['<option value="">All months</option>']
    .concat(fbMonthOptions().map(([v, n]) => `<option value="${v}">${esc(monthLabelOf(v))} — ${fmtInt(n)}</option>`)).join("");
  monthSel.value = Array.from(monthSel.options).some(o => o.value === w.month) ? w.month : "";
}

function onFbModelChange() {
  const v = fbEl("fbModelSelect").value;
  fbState.win.dim = v === "all" ? "all" : "model";
  fbState.win.label = v === "all" ? "" : v;
  renderFollowupWindow();
}
function onFbVariantChange() {
  fbState.win.variant = fbEl("fbVariantSelect").value;
  renderFollowupWindow();
}
function onFbMonthChange() {
  fbState.win.month = fbEl("fbMonthSelect").value;
  renderFollowupWindow();
}

function openFollowupWindow(dim, label, bucket, bookedOnly = false) {
  if (!fbState.data) return;
  fbState.win = { dim: dim || "all", label: label || "", bookedOnly: !!bookedOnly, variant: "", month: "",
                  bucket: bucket === "all" || bucket === undefined ? "all" : Number(bucket) };
  const modal = fbEl("fbModal");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  fbState.open = true;
  renderFollowupWindow();
  fbEl("fbClose").focus();
  fbEl("fbModal").querySelector(".mv-body").scrollTop = 0;
}

function closeFollowupWindow() {
  const modal = fbEl("fbModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  if (!mvState.open) document.body.style.overflow = "";
  fbState.open = false;
}

function fbVisibleRows() {
  const scoped = fbWindowRows();
  const b = fbState.win.bucket;
  return b === "all" ? scoped : scoped.filter(r => r.bucket === b);
}

const FB_STAGE_TONE = { "Booked": "amber", "Retailed · Closed": "green", "Cancelled": "red",
  "Appointed": "blue", "Lead": "grey", "In follow-up": "blue" };
const fbStageBadge = (st) => badge(st || "—", FB_STAGE_TONE[st] || "grey");
const fbShort = (d) => (d ? d.slice(0, 5) : "");          // dd/mm/yyyy -> dd/mm

/* The enquiry's story: follow-ups (up to the booking / cancel date, or today), then Booked, then Retailed = closed. */
function fbHistoryCell(r) {
  const parts = [];
  (r.history || []).forEach((d, i) => parts.push(`<span class="fb-chip f" title="Follow-up ${i + 1} (estimated) on ${esc(d)}">F${i + 1} · ${esc(fbShort(d))}</span>`));
  if (!parts.length && r.followups === 0) parts.push(`<span class="fb-chip n">No follow-up</span>`);
  if (!parts.length && r.followups > 0) parts.push(`<span class="fb-chip n">${r.followups} follow-up${r.followups === 1 ? "" : "s"}</span>`);
  if ((r.followups || 0) > (r.history || []).length && (r.history || []).length) parts.push(`<span class="fb-chip n">+${r.followups - r.history.length} more</span>`);
  if (r.booking_date) parts.push(`<span class="fb-chip b" title="Booking date">Booked · ${esc(fbShort(r.booking_date))}</span>`);
  if (r.retail_date) parts.push(`<span class="fb-chip r" title="Retail date - enquiry closed">Retailed · ${esc(fbShort(r.retail_date))} ✓ closed</span>`);
  if (r.stage === "Cancelled" && r.lost_date) parts.push(`<span class="fb-chip x" title="Cancelled on">Cancelled · ${esc(fbShort(r.lost_date))}</span>`);
  return parts.join(" ");
}

function renderFollowupWindow() {
  const d = fbState.data, w = fbState.win;
  renderFbToolbar();
  const scoped = fbWindowRows();
  const rows = fbVisibleRows();
  const f = fuFilters();
  const nBooked = scoped.filter(r => r.is_booked).length;
  const noun = w.bookedOnly ? "booked enquiry" : "enquiry";
  const nouns = w.bookedOnly ? "booked enquiries" : "enquiries";

  // ---- header ----
  fbEl("fbTitle").textContent = w.dim !== "all" ? w.label
    : (w.bucket !== "all" ? (w.bookedOnly ? `Booked · ${fbFollowText(w.bucket)}` : `Enquiries · ${fbFollowText(w.bucket)}`)
                          : (w.bookedOnly ? "Booked enquiries" : "All enquiries"));
  fbEl("fbTag").textContent = FB_DIM_TAG[w.dim] || FB_DIM_TAG.all;
  const bits = [];
  if (w.dim !== "all") bits.push(FB_DIM_LABEL[w.dim]);
  bits.push(`${fmtInt(scoped.length)} ${scoped.length === 1 ? noun : nouns}`);
  if (!w.bookedOnly) bits.push(`${fmtInt(nBooked)} booked`);
  if (w.dim !== "model" && f.model && f.model !== "all") bits.push(f.model);
  if (w.dim !== "consultant" && f.consultant && f.consultant !== "all") bits.push(f.consultant);
  if (f.source && f.source !== "all") bits.push(f.source);
  if (f.sub_source && f.sub_source !== "all") bits.push(f.sub_source);
  fbEl("fbSub").textContent = bits.join(" · ");

  // ---- clickable tiles: total, then 0 / 1 / 2 / 3 (/ 4+) follow-ups - they add up to the total ----
  const cnt = { all: scoped.length };
  FB_BUCKETS.forEach(b => { cnt[b] = scoped.filter(r => r.bucket === b).length; });
  const tiles = [{ key: "all", label: w.bookedOnly ? "Booked enquiries" : "Total enquiries", color: "#002C5F" },
    ...FB_BUCKETS.filter(b => b < 4 || cnt[4]).map(b => ({
      key: b, label: `${fbBucketText(b)} follow-up${b <= 1 ? "" : "s"}`,
      color: b === 0 ? "#1E9E6A" : b === 1 ? "#00AAD2" : b === 2 ? "#DD7B2E" : "#C73E3E" }))];
  fbEl("fbKpis").style.gridTemplateColumns = `repeat(${tiles.length}, 1fr)`;
  fbEl("fbKpis").innerHTML = tiles.map(t => `
    <div class="mv-kpi ${String(w.bucket) === String(t.key) ? "active" : ""}" role="button" tabindex="0"
         data-fb-tile="${t.key}" style="--k:${t.color}">
      <div class="mv-kpi-label">${esc(t.label)}</div>
      <div class="mv-kpi-value">${fmtInt(cnt[t.key])}</div>
      <div class="mv-kpi-sub">${t.key === "all" ? (w.bookedOnly ? "in this window" : `${fmtInt(nBooked)} booked`) : `${fbPct(cnt[t.key], scoped.length)} of ${fmtInt(scoped.length)}`}</div>
    </div>`).join("");

  const extraBits = [];
  if (w.variant) extraBits.push(`variant: ${w.variant}`);
  if (w.month) extraBits.push(`month: ${monthLabelOf(w.month)}`);
  const hasExtra = extraBits.length > 0;

  const note = fbEl("fbFilterNote");
  note.hidden = w.bucket === "all" && !w.bookedOnly && !hasExtra;
  fbEl("fbFilterText").textContent = `Showing ${w.bookedOnly ? "only booked enquiries" : "all enquiries"}` +
    (w.bucket === "all" ? "" : ` with ${fbFollowText(w.bucket)}`) +
    (hasExtra ? ` · ${extraBits.join(" · ")}` : "");
  fbEl("fbClear").textContent = "Show all enquiries";
  fbEl("fbClear").hidden = w.bucket === "all" && !w.bookedOnly && !hasExtra;

  // ---- full detail table ----
  fbEl("fbTableHead").innerHTML =
    `<tr><th>Enquiry No.</th><th>Customer</th><th>Phone</th><th>Model</th><th>Variant</th><th>Colour</th><th>Fuel</th>` +
    `<th>Consultant</th><th>Source</th><th>Enquiry date</th><th>Follow-ups</th><th>Follow-up history</th>` +
    `<th>Booking date</th><th>Retail date</th><th>Stage</th><th>Days to book</th>` +
    `<th>Test drive</th><th>Next follow-up</th><th>Status</th><th>Remarks</th></tr>`;
  fbEl("fbTableBody").innerHTML = rows.length ? rows.map(r => `
    <tr>
      <td class="fb-enq">${dash(r.enq_no)}</td>
      <td>${esc(r.customer)}</td><td>${dash(r.phone)}</td><td>${esc(r.model)}</td><td>${dash(r.variant)}</td>
      <td>${esc(r.color)}</td><td>${r.fuel ? `<span class="mv-fuel">${esc(r.fuel)}</span>` : "—"}</td>
      <td>${esc(r.consultant)}</td><td>${dash(r.source)}</td><td>${dash(r.enquiry_date)}</td>
      <td>${r.bucket === null || r.followups === null ? "—" : `<span class="fb-pill p${r.bucket}">${r.followups}</span>`}</td>
      <td class="wrap fb-hist">${fbHistoryCell(r)}</td>
      <td>${dash(r.booking_date)}</td><td>${dash(r.retail_date)}</td>
      <td>${fbStageBadge(r.stage)}</td>
      <td>${r.days_to_book === null ? "—" : fmtInt(r.days_to_book)}</td>
      <td>${r.test_drive === "Y" ? badge("Done", "green") : "—"}</td><td>${dash(r.next_followup)}</td>
      <td>${dash(r.status)}</td>
      <td class="wrap">${dash(r.remarks)}</td>
    </tr>`).join("")
    : `<tr><td colspan="20" class="empty-cell">${scoped.length === 0 && w.dim !== "all"
        ? `${esc(w.label)} has no enquiry for this selection.`
        : "No enquiries for this selection."}</td></tr>`;
  fbEl("fbTableCount").textContent = `(${fmtInt(rows.length)} ${rows.length === 1 ? noun : nouns})`;
}

function exportFollowupWindow() {
  const rows = fbVisibleRows();
  if (!rows.length) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const header = ["Enquiry No.", "Customer", "Phone", "Model", "Variant", "Colour", "Fuel", "Consultant", "Source", "Enquiry date",
    "Follow-ups", "Follow-up history (est.)", "Booking date", "Retail date", "Stage", "Days to book", "Test drive", "Next follow-up", "Status", "Remarks"];
  const lines = [header.map(q).join(",")];
  rows.forEach(r => lines.push([r.enq_no, r.customer, r.phone, r.model, r.variant, r.color, r.fuel, r.consultant, r.source,
    r.enquiry_date, r.followups, (r.history || []).map((d, i) => `F${i + 1} ${d}`).join(" | "), r.booking_date, r.retail_date,
    r.stage, r.days_to_book, r.test_drive === "Y" ? "Done" : "", r.next_followup, r.status, r.remarks].map(q).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  const w = fbState.win;
  const part = [w.dim === "all" ? "enquiries" : w.label, w.bucket === "all" ? "all" : `booked_${fbBucketText(w.bucket)}_followups`].join("_");
  a.href = URL.createObjectURL(blob);
  a.download = `${part.replace(/[^\w\-]+/g, "_")}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

(function initFollowupWindow() {
  const wire = () => {
    const openFrom = (el) => openFollowupWindow(el.dataset.fbDim || "all", el.dataset.fbLabel || "", el.dataset.fbBucket);
    document.addEventListener("click", (e) => {
      const card = e.target.closest("#fuBookKpiGrid [data-fb-bucket]");
      if (card) { openFollowupWindow("all", "", card.dataset.fbBucket, true); return; }
      const cell = e.target.closest("#fuBookTable td[data-fb-bucket]");          // a number: that row + that group
      if (cell) { openFrom(cell); return; }
      const row = e.target.closest("#fuBookTable tr.fb-row");                    // the row: every follow-up group
      if (row) { openFrom(row); return; }
      const tile = e.target.closest("#fbKpis [data-fb-tile]");                   // tiles inside the pop-up
      if (tile) {
        const t = tile.dataset.fbTile;
        const next = t === "all" ? "all" : Number(t);
        fbState.win.bucket = (String(fbState.win.bucket) === String(next)) ? "all" : next;
        renderFollowupWindow();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (fbState.open && e.key === "Escape") { closeFollowupWindow(); return; }
      if ((e.key === "Enter" || e.key === " ") && e.target.matches) {
        if (e.target.matches("#fuBookKpiGrid [data-fb-bucket]")) { e.preventDefault(); openFollowupWindow("all", "", e.target.dataset.fbBucket, true); }
        else if (e.target.matches("#fuBookTable tr.fb-row")) { e.preventDefault(); openFrom(e.target); }
        else if (e.target.matches("#fbKpis [data-fb-tile]")) { e.preventDefault(); e.target.click(); }
      }
    });
    fbEl("fuBookTabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".tab-btn");
      if (!btn) return;
      fbState.dim = btn.dataset.dim;
      document.querySelectorAll("#fuBookTabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
      renderBookedTable();
    });
    fbEl("fbClose").addEventListener("click", closeFollowupWindow);
    fbEl("fbModal").addEventListener("click", (e) => { if (e.target === fbEl("fbModal")) closeFollowupWindow(); });
    fbEl("fbExport").addEventListener("click", exportFollowupWindow);
    fbEl("fbClear").addEventListener("click", () => {
      fbState.win.bucket = "all"; fbState.win.bookedOnly = false;
      fbState.win.variant = ""; fbState.win.month = "";          // new dropdowns reset the same way bucket/bookedOnly always did
      renderFollowupWindow();
    });
    fbEl("fbModelSelect").addEventListener("change", onFbModelChange);
    fbEl("fbVariantSelect").addEventListener("change", onFbVariantChange);
    fbEl("fbMonthSelect").addEventListener("change", onFbMonthChange);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();


/* ---------------------------------------------------------------------- */
/* Vehicle Stock page > pop-up with the unit-by-unit details               */
/*   Open it from a Model / Variant / Colour row (or a Colour's model row)  */
/*   or from the Total / Physical / In transit / Aged 60+ cards.            */
/*   Shows HMI Invoice No & Date, TAT as on today, Model, Variant, colours, */
/*   VIN, Order No, Stock Status, Fuel Type and Order Type.                 */
/* ---------------------------------------------------------------------- */
const svState = {
  open: false, data: null, seq: 0,
  // dim/label/model/stage/aged: existing click-driven scope (card/row clicks), unchanged.
  // fModel/fVariant/fMonth: the new toolbar dropdowns - independent, additive, combinable.
  win: { dim: "all", label: "", model: "", stage: "all", aged: false, fModel: "all", fVariant: "all", fMonth: "" },
};
const svCache = new Map();
const svEl = (id) => document.getElementById(id);
const SV_AGED_DAYS = 60;                                           // same as the "Aged 60+ days" card
const SV_DIM_LABEL = { all: "Vehicle stock", model: "Model", variant: "Variant", color: "Colour" };
const SV_DIM_TAG = { all: "STOCK WINDOW", model: "MODEL WINDOW", variant: "VARIANT WINDOW", color: "COLOUR WINDOW" };
const SV_COLS = [
  { key: "inv_no", label: "HMI Invoice No", cls: "sv-id" },
  { key: "inv_date", label: "HMI Invoice Date", sort: "date" },
  { key: "tat", label: "TAT (days)", num: true },
  { key: "model", label: "Model" },
  { key: "variant", label: "Variant" },
  { key: "color", label: "Exterior Color Name" },
  { key: "interior", label: "Interior Color Desc" },
  { key: "vin", label: "Vin Number", cls: "sv-vin" },
  { key: "order_no", label: "Order No" },
  { key: "status", label: "Stock Status" },
  { key: "fuel", label: "Fuel Type" },
  { key: "order_type", label: "Order Type" },
  { key: "stage", label: "Stage" },
];

function svTagKpiCards() {
  // Total stock, Physical stock, In transit and Aged 60+ days cards open the window
  const map = { 0: { stage: "all" }, 1: { stage: "Physical" }, 2: { stage: "In Transit" }, 5: { stage: "Physical", aged: true } };
  document.querySelectorAll("#invKpiGrid .kpi-card").forEach((card, i) => {
    if (!map[i]) return;
    card.classList.add("sv-click");
    card.setAttribute("role", "button");
    card.tabIndex = 0;
    card.dataset.svCard = JSON.stringify(map[i]);
    card.title = "Click to see the vehicles";
    const lab = card.querySelector(".kpi-label");
    if (lab && !lab.dataset.svArrow) { lab.dataset.svArrow = "1"; lab.insertAdjacentHTML("beforeend", ' <span class="sv-arrow">›</span>'); }
  });
}

function svLoadUnits() {
  const qs = inventoryApiParams(readInventoryFilters());
  if (!svCache.has(qs)) {
    const pr = getJSON(`/api/vehicle-stock/units?${qs}`);
    pr.catch(() => svCache.delete(qs));
    svCache.set(qs, pr);
    if (svCache.size > 12) svCache.delete(svCache.keys().next().value);
  }
  return svCache.get(qs);
}

async function openStockWindow(win) {
  svState.win = { dim: "all", label: "", model: "", stage: "all", aged: false, fModel: "all", fVariant: "all", fMonth: "", ...win };
  svState.data = null;
  const modal = svEl("svModal");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  svState.open = true;
  svEl("svTitle").textContent = svState.win.label || "Vehicle stock";
  svEl("svTag").textContent = SV_DIM_TAG[svState.win.dim] || SV_DIM_TAG.all;
  svEl("svSub").textContent = "Loading vehicle details…";
  svEl("svKpis").innerHTML = ""; svEl("svTableHead").innerHTML = ""; svEl("svTableBody").innerHTML = "";
  svEl("svClose").focus();
  const seq = ++svState.seq;
  try {
    const data = await svLoadUnits();
    if (seq !== svState.seq || !svState.open) return;
    svState.data = data;
    renderStockWindow();
  } catch (err) {
    if (seq !== svState.seq) return;
    svEl("svSub").textContent = "Could not load vehicle details. Please try again.";
  }
}

function closeStockWindow() {
  const modal = svEl("svModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  if (!fbState.open && !mvState.open) document.body.style.overflow = "";
  svState.open = false;
}

/* The original click-driven scope (card/row clicks) - unchanged. */
function svBaseRows() {
  const d = svState.data, w = svState.win;
  if (!d) return [];
  return d.rows.filter(r => {
    if (w.dim === "model" && r.model !== w.label) return false;
    if (w.dim === "variant" && r.variant !== w.label) return false;
    if (w.dim === "color") {
      if (r.color !== w.label) return false;
      if (w.model && r.model !== w.model) return false;
    }
    return true;
  });
}

const SV_DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})$/;          // inv_date is dd/mm/yyyy
function svRowMonth(r) {
  const m = SV_DATE_RE.exec(r.inv_date || "");
  return m ? `${m[3]}-${m[2]}` : "";
}

/* svBaseRows() + the new Model/Variant/Month toolbar dropdowns - independent
   of, and combinable with, the existing dim/label/model click-driven scope. */
function svScopeRows() {                                           // rows of the clicked group (before stage / aged filters)
  const w = svState.win;
  return svBaseRows().filter(r => {
    if (w.fModel && w.fModel !== "all" && r.model !== w.fModel) return false;
    if (w.fVariant && w.fVariant !== "all" && r.variant !== w.fVariant) return false;
    if (w.fMonth && svRowMonth(r) !== w.fMonth) return false;
    return true;
  });
}

function svCountBy(rows, keyFn) {
  const map = new Map();
  rows.forEach(r => { const k = keyFn(r); if (k) map.set(k, (map.get(k) || 0) + 1); });
  return map;
}

/* Model options come from the full click-scoped set (before fModel/fVariant/fMonth),
   so the Model list doesn't shrink to just whatever is already picked. Variant
   options narrow to the currently-picked Model (if any), and Month options narrow
   to both - each dropdown reflects what the ones "above" it already chose. */
function svModelOptions() { return svCountBy(svBaseRows(), r => r.model); }
function svVariantOptions() {
  const w = svState.win;
  const rows = svBaseRows().filter(r => !w.fModel || w.fModel === "all" || r.model === w.fModel);
  return svCountBy(rows, r => r.variant);
}
function svMonthOptions() {
  const w = svState.win;
  const rows = svBaseRows().filter(r =>
    (!w.fModel || w.fModel === "all" || r.model === w.fModel) &&
    (!w.fVariant || w.fVariant === "all" || r.variant === w.fVariant));
  return svCountBy(rows, svRowMonth);
}

function renderSvToolbar() {
  const w = svState.win;

  const modelSel = svEl("svModelSelect");
  const mOpts = Array.from(svModelOptions().entries()).sort((a, b) => b[1] - a[1]);
  modelSel.innerHTML = ['<option value="all">All models</option>']
    .concat(mOpts.map(([label, n]) => `<option value="${esc(label)}">${esc(label)} — ${fmtInt(n)}</option>`)).join("");
  modelSel.value = Array.from(modelSel.options).some(o => o.value === w.fModel) ? w.fModel : "all";

  const variantSel = svEl("svVariantSelect");
  const vOpts = Array.from(svVariantOptions().entries()).sort((a, b) => b[1] - a[1]);
  variantSel.innerHTML = ['<option value="all">All variants</option>']
    .concat(vOpts.map(([label, n]) => `<option value="${esc(label)}">${esc(label)} — ${fmtInt(n)}</option>`)).join("");
  variantSel.value = Array.from(variantSel.options).some(o => o.value === w.fVariant) ? w.fVariant : "all";

  const monthSel = svEl("svMonthSelect");
  const monOpts = Array.from(svMonthOptions().entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));
  monthSel.innerHTML = ['<option value="">All months</option>']
    .concat(monOpts.map(([v, n]) => `<option value="${v}">${esc(monthLabelOf(v))} — ${fmtInt(n)}</option>`)).join("");
  monthSel.value = Array.from(monthSel.options).some(o => o.value === w.fMonth) ? w.fMonth : "";
}

function onSvModelChange() {
  svState.win.fModel = svEl("svModelSelect").value;
  svState.win.fVariant = "all";     // a different model's variants are a fresh slate
  renderStockWindow();
}
function onSvVariantChange() {
  svState.win.fVariant = svEl("svVariantSelect").value;
  renderStockWindow();
}
function onSvMonthChange() {
  svState.win.fMonth = svEl("svMonthSelect").value;
  renderStockWindow();
}

function svVisibleRows() {                                          // oldest TAT first; click any heading to re-sort (app-wide sorter)
  const w = svState.win;
  let rows = svScopeRows();
  if (w.stage !== "all") rows = rows.filter(r => r.stage === w.stage);
  if (w.aged) rows = rows.filter(r => r.tat !== null && r.tat >= SV_AGED_DAYS);
  return rows;
}

const svTatPill = (t) => (t === null || t === undefined) ? "—"
  : `<span class="fb-pill ${t >= 90 ? "p3" : t >= 60 ? "p2" : t >= 30 ? "p1" : "p0"}">${fmtInt(t)}</span>`;

function renderStockWindow() {
  const d = svState.data, w = svState.win;
  renderSvToolbar();
  const scoped = svScopeRows();
  const rows = svVisibleRows();
  const f = readInventoryFilters();
  const physical = scoped.filter(r => r.stage === "Physical");
  const transit = scoped.filter(r => r.stage === "In Transit");
  const aged = scoped.filter(r => r.stage === "Physical" && r.tat !== null && r.tat >= SV_AGED_DAYS);
  const tats = scoped.map(r => r.tat).filter(t => t !== null);
  const avg = tats.length ? (tats.reduce((a, b) => a + b, 0) / tats.length) : 0;

  // ---- header ----
  svEl("svTitle").textContent = w.label ? (w.model ? `${w.label} · ${w.model}` : w.label) : "Vehicle stock";
  svEl("svTag").textContent = SV_DIM_TAG[w.dim] || SV_DIM_TAG.all;
  const bits = [];
  if (w.dim !== "all") bits.push(SV_DIM_LABEL[w.dim]);
  bits.push(`${fmtInt(scoped.length)} unit${scoped.length === 1 ? "" : "s"}`);
  if (f.model && f.model !== "all" && w.dim !== "model") bits.push(f.model);
  if (f.fuel_type && f.fuel_type !== "all") bits.push(f.fuel_type);
  if (f.financier && f.financier !== "all") bits.push(f.financier);
  bits.push(`TAT as on ${d.as_of}`);
  svEl("svSub").textContent = bits.join(" · ");

  // ---- tiles (click to filter) ----
  const tiles = [
    { key: "all", label: "Total units", value: scoped.length, sub: "in this window", color: "#002C5F", active: w.stage === "all" && !w.aged },
    { key: "Physical", label: "Physical", value: physical.length, sub: "on the ground", color: "#DD7B2E", active: w.stage === "Physical" && !w.aged },
    { key: "In Transit", label: "In transit", value: transit.length, sub: "despatched, not arrived", color: "#6B4FBB", active: w.stage === "In Transit" },
    { key: "aged", label: `Aged ${SV_AGED_DAYS}+ days`, value: aged.length, sub: "physical, TAT ≥ " + SV_AGED_DAYS, color: "#C73E3E", active: w.aged },
    { key: "avg", label: "Avg. TAT", value: `${avg.toFixed(1)} d`, sub: "days since HMI invoice", color: "#00AAD2", active: false, still: true },
  ];
  svEl("svKpis").style.gridTemplateColumns = `repeat(${tiles.length}, 1fr)`;
  svEl("svKpis").innerHTML = tiles.map(t => `
    <div class="mv-kpi ${t.active ? "active" : ""}" ${t.still ? "" : `role="button" tabindex="0" data-sv-tile="${t.key}" `}style="--k:${t.color};${t.still ? "cursor:default" : ""}">
      <div class="mv-kpi-label">${esc(t.label)}</div>
      <div class="mv-kpi-value">${typeof t.value === "number" ? fmtInt(t.value) : esc(t.value)}</div>
      <div class="mv-kpi-sub">${esc(t.sub)}</div>
    </div>`).join("");

  const extraBits = [];
  if (w.fModel && w.fModel !== "all") extraBits.push(`model: ${w.fModel}`);
  if (w.fVariant && w.fVariant !== "all") extraBits.push(`variant: ${w.fVariant}`);
  if (w.fMonth) extraBits.push(`month: ${monthLabelOf(w.fMonth)}`);
  const hasExtra = extraBits.length > 0;

  const filtered = w.stage !== "all" || w.aged || hasExtra;
  svEl("svFilterNote").hidden = !filtered;
  const stageText = w.aged ? `Showing only physical units aged ${SV_AGED_DAYS}+ days`
    : (w.stage !== "all" ? `Showing only ${w.stage === "Physical" ? "physical" : "in-transit"} units` : "");
  svEl("svFilterText").textContent = [stageText, hasExtra ? extraBits.join(" · ") : ""].filter(Boolean).join(" · ");

  // ---- table ----
  svEl("svTableHead").innerHTML = `<tr>${SV_COLS.map(c =>
    `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>`;
  svEl("svTableBody").innerHTML = rows.length ? rows.map(r => `
    <tr>
      ${SV_COLS.map(c => {
        if (c.key === "tat") return `<td class="num">${svTatPill(r.tat)}</td>`;
        if (c.key === "fuel") return `<td>${r.fuel ? `<span class="mv-fuel">${esc(r.fuel)}</span>` : "—"}</td>`;
        if (c.key === "stage") return `<td>${badge(r.stage === "Physical" ? "Physical" : "In Transit", r.stage === "Physical" ? "amber" : "blue")}</td>`;
        return `<td class="${c.cls || ""}">${dash(r[c.key])}</td>`;
      }).join("")}
    </tr>`).join("")
    : `<tr><td colspan="${SV_COLS.length}" class="empty-cell">No vehicles for this selection.</td></tr>`;
  svEl("svTableCount").textContent = `(${fmtInt(rows.length)} unit${rows.length === 1 ? "" : "s"})`;
}

function exportStockWindow() {
  const rows = svVisibleRows();
  if (!rows.length) return;
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [SV_COLS.map(c => q(c.label)).join(",")];
  rows.forEach(r => lines.push(SV_COLS.map(c => q(r[c.key])).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  const w = svState.win;
  const part = ["vehicle_stock", w.label || "all", w.model, w.aged ? "aged" : (w.stage !== "all" ? w.stage : "")].filter(Boolean).join("_");
  a.href = URL.createObjectURL(blob);
  a.download = `${part.replace(/[^\w\-]+/g, "_")}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

(function initStockWindow() {
  const wire = () => {
    const fromRow = (row) => openStockWindow({ dim: row.dataset.svDim, label: row.dataset.svLabel, model: row.dataset.svModel || "" });
    document.addEventListener("click", (e) => {
      if (e.target.closest(".inv-expand-btn")) return;                        // the chevron only expands the colour row
      const row = e.target.closest("#invBreakdownBody tr.sv-row");
      if (row) { fromRow(row); return; }
      const card = e.target.closest("#invKpiGrid .kpi-card[data-sv-card]");
      if (card) { openStockWindow(JSON.parse(card.dataset.svCard)); return; }
      const tile = e.target.closest("#svKpis [data-sv-tile]");
      if (tile) {
        const t = tile.dataset.svTile, w = svState.win;
        if (t === "all") { w.stage = "all"; w.aged = false; }
        else if (t === "aged") { w.aged = !w.aged; w.stage = w.aged ? "Physical" : "all"; }
        else { w.aged = false; w.stage = (w.stage === t) ? "all" : t; }
        renderStockWindow(); return;
      }
    });
    document.addEventListener("keydown", (e) => {
      if (svState.open && e.key === "Escape") { closeStockWindow(); return; }
      if ((e.key === "Enter" || e.key === " ") && e.target.matches) {
        if (e.target.matches("#invBreakdownBody tr.sv-row")) { e.preventDefault(); fromRow(e.target); }
        else if (e.target.matches("#invKpiGrid .kpi-card[data-sv-card]")) { e.preventDefault(); openStockWindow(JSON.parse(e.target.dataset.svCard)); }
        else if (e.target.matches("#svKpis [data-sv-tile]")) { e.preventDefault(); e.target.click(); }
      }
    });
    svEl("svClose").addEventListener("click", closeStockWindow);
    svEl("svModal").addEventListener("click", (e) => { if (e.target === svEl("svModal")) closeStockWindow(); });
    svEl("svExport").addEventListener("click", exportStockWindow);
    svEl("svClear").addEventListener("click", () => {
      svState.win.stage = "all"; svState.win.aged = false;
      svState.win.fModel = "all"; svState.win.fVariant = "all"; svState.win.fMonth = "";
      renderStockWindow();
    });
    svEl("svModelSelect").addEventListener("change", onSvModelChange);
    svEl("svVariantSelect").addEventListener("change", onSvVariantChange);
    svEl("svMonthSelect").addEventListener("change", onSvMonthChange);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();

/* ---------------------------------------------------------------------- */
/* Enquiry Wise Stock page (Physical vs In Transit)                         */
/* ---------------------------------------------------------------------- */
const stState = { data: null, match: "all", shown: 100, cover: "all" };

const MATCH_TONE = {
  "Exact Match - Physical": ["green", "--green"],
  "Exact Match - In Transit": ["amber", "--amber"],
  "Exact Match - Allocated Only": ["purple", "--purple"],
  "Variant Available - Other Color": ["blue", "--blue"],
  "Model Available - Other Variant": ["amber", "--amber"],
  "No Stock Available": ["red", "--red"],
  "Variant Not Captured": ["grey", "--ink-faint"],
};
const POSITION_TONE = {
  "Demand - No Stock": "red", "Demand - Only In Transit": "amber", "Short of Physical Stock": "amber",
  "Balanced": "green", "Surplus Stock": "grey", "Stock - No Enquiry": "purple", "Allocated Only - No Action": "grey",
};

function stQuery() {
  const f = readFilterBar("st");
  return new URLSearchParams({
    model: f.model, consultant: f.consultant, source: f.source, sub_source: f.sub_source || "all",
    status: document.getElementById("stStatus").value || "all",
    month: document.getElementById("stEnqMonth").value || "all",
  }).toString();
}

async function loadStock() {
  await populateFilterBar("st");
  const data = await getJSON(`/api/enquiry-stock?${stQuery()}`);
  stState.data = data;

  const statusSel = document.getElementById("stStatus");
  const prev = statusSel.value;
  statusSel.innerHTML = "";
  statusSel.appendChild(new Option("All live enquiries", "all"));
  (data.status_options || []).forEach(s => statusSel.appendChild(new Option(s, s)));
  statusSel.value = Array.from(statusSel.options).some(o => o.value === prev) ? prev : "all";

  // Enquiry-month dropdown: every month that has enquiries (newest first), with how many are matched in it.
  const monthSel = document.getElementById("stEnqMonth");
  const prevMonth = monthSel.value;
  monthSel.innerHTML = "";
  monthSel.appendChild(new Option("All months", "all"));
  (data.months || []).slice().reverse().forEach(m => monthSel.appendChild(new Option(`${m.label} — ${fmtInt(m.matched)}`, m.value)));
  monthSel.value = Array.from(monthSel.options).some(o => o.value === prevMonth) ? prevMonth : "all";

  const notice = document.getElementById("stNotice");
  const body = document.getElementById("stBody");
  const info = data.stock_info || {};

  if (!data.stock_loaded) {
    notice.style.display = "";
    notice.className = "notice";
    notice.innerHTML = `<strong>Stock file needed.</strong> ${esc(data.message)}<br>` +
      `It should have one row per vehicle with at least Model, Variant, Colour, VIN/Chassis and a ` +
      `Physical / In Transit column (any similar heading works — see the README).`;
    body.style.display = "none";
    document.getElementById("stKpiGrid").innerHTML = "";
    return;
  }

  body.style.display = "";
  const cols = info.columns_used || {};
  const used = Object.entries(cols).filter(([, v]) => v).map(([k, v]) => `${k} ← “${esc(v)}”`).join(" · ");
  const warns = (info.warnings || []).map(w => `<div>⚠ ${esc(w)}</div>`).join("");
  notice.style.display = "";
  notice.className = warns ? "notice" : "notice info";
  notice.innerHTML = `<strong>${esc(info.file)}</strong> — ${fmtInt(info.rows)} units. Columns read: ${used}.${warns ? "<br>" + warns : ""}`;

  const s = data.summary;
  renderKpiGrid("stKpiGrid", [
    { label: "Live enquiries matched", value: fmtInt(s.enquiries), color: "var(--blue)", sub: "follow-up, appointed, lead, booked" +
      (data.selected_month && data.selected_month !== "all"
        ? ` · ${(data.months || []).find(m => m.value === data.selected_month)?.label || data.selected_month}` : "") },
    { label: "Exact match · Physical", value: fmtInt(s.exact_physical), color: "var(--green)", sub: "car is here — can be offered now" },
    { label: "Exact match · In transit", value: fmtInt(s.exact_transit), color: "var(--amber)", sub: "car is on the way" },
    { label: "No stock · needs indent", value: fmtInt(s.no_stock), color: "var(--red)", sub: "nothing free for this model" },
    { label: "Physical stock (free)", value: fmtInt(s.stock_physical), color: "var(--green)", sub: `of ${fmtInt(s.stock_total)} units in the file` },
    { label: "In-transit stock (free)", value: fmtInt(s.stock_transit), color: "var(--amber)", sub: "not yet received" },
    { label: "Allocated units", value: fmtInt(s.stock_allocated), color: "var(--purple)", sub: "already committed to a customer" },
    { label: `Physical > ${s.aged_days} days`, value: fmtInt(s.stock_aged), color: "var(--red)", sub: `${fmtInt(s.stock_no_enquiry)} free units have no enquiry` },
  ]);

  // ---- match-status chips ----
  const total = data.match_counts.reduce((a, m) => a + m.count, 0);
  const chips = [{ status: "all", count: total, meaning: "Every live enquiry" }, ...data.match_counts];
  const chipRow = document.getElementById("stChips");
  chipRow.innerHTML = chips.map(m => `
    <button class="chip ${stState.match === m.status ? "active" : ""}" data-status="${esc(m.status)}"
            style="--chip-color:${m.status === "all" ? "var(--blue)" : `var(${MATCH_TONE[m.status][1]})`}" title="${esc(m.meaning)}">
      <span class="chip-count">${fmtInt(m.count)}</span>
      <span class="chip-label">${m.status === "all" ? "All enquiries" : esc(m.status)}</span>
    </button>`).join("");
  chipRow.querySelectorAll(".chip").forEach(btn => btn.addEventListener("click", () => {
    stState.match = btn.dataset.status;
    stState.shown = 100;
    chipRow.querySelectorAll(".chip").forEach(c => c.classList.toggle("active", c === btn));
    renderStockList();
  }));

  // ---- charts ----
  const mc = data.match_counts.filter(m => m.count > 0);
  doughnutChart("stStatusChart", mc.map(m => m.status), mc.map(m => m.count), mc.map(m => cssVar(MATCH_TONE[m.status][1])));
  const bm = data.by_model || [];
  groupedBarChart("stModelChart", bm.map(m => m.label), [
    { label: "Enquiries", values: bm.map(m => m.enquiries), color: cssVar("--blue") },
    { label: "Physical (free)", values: bm.map(m => m.physical), color: cssVar("--green") },
    { label: "In transit (free)", values: bm.map(m => m.transit), color: cssVar("--amber") },
  ]);

  renderStockMonths();
  renderStockList();
  renderStockDemand();
  renderStockCoverage();
}

/* ---- Month-wise analysis: one row per enquiry month, split by match status ---- */
const ST_MONTH_COLS = [
  ["Exact Match - Physical", "Exact · Physical"],
  ["Exact Match - In Transit", "Exact · In transit"],
  ["Exact Match - Allocated Only", "Exact · Allocated"],
  ["Variant Available - Other Color", "Other colour"],
  ["Model Available - Other Variant", "Other variant"],
  ["No Stock Available", "No stock"],
  ["Variant Not Captured", "Variant not captured"],
];

function renderStockMonths() {
  const d = stState.data;
  const panel = document.getElementById("stMonthPanel");
  const rows = d.month_rows || [];
  const tot = d.month_total;
  if (!rows.length || !tot) { panel.style.display = "none"; return; }
  panel.style.display = "";

  const meaning = Object.fromEntries((d.match_counts || []).map(m => [m.status, m.meaning]));
  document.getElementById("stMonthHead").innerHTML = `<tr><th>Enquiry month</th>` +
    `<th class="num" title="Every enquiry in the file for this month, whatever its status">In file</th>` +
    `<th class="num" title="Enquiries matched against stock on this page">Matched</th>` +
    `<th class="num" title="In the file but not matched here (Retail, cancelled and other statuses)">Not matched</th>` +
    ST_MONTH_COLS.map(([s, label]) => `<th class="num" title="${esc(meaning[s] || s)}">${label}</th>`).join("") +
    `<th class="num" title="Exact match, physically in stock ÷ matched">Can serve now</th>` +
    `<th class="num" title="Exact match, physical or in transit ÷ matched">Exact incl. transit</th>` +
    `<th class="num" title="No stock available for the model ÷ matched">Needs indent</th></tr>`;

  const cells = (r) => `<td class="num">${fmtInt(r.in_file)}</td><td class="num">${fmtInt(r.matched)}</td>` +
    `<td class="num">${fmtInt(r.other)}</td>` +
    ST_MONTH_COLS.map(([s]) => `<td class="num">${fmtInt(r.statuses[s] || 0)}</td>`).join("") +
    `<td class="num">${fmtPct(r.can_serve_pct)}</td><td class="num">${fmtPct(r.exact_incl_transit_pct)}</td>` +
    `<td class="num">${fmtPct(r.indent_pct)}</td>`;

  document.getElementById("stMonthBody").innerHTML = rows.map(r => {
    const sel = r.month && d.selected_month === r.month;
    return `<tr class="${r.month ? "st-month-row" : ""}${sel ? " st-month-selected" : ""}" data-month="${esc(r.month)}"` +
           `${r.month ? ` title="${sel ? "Click to show all months again" : "Click to filter this page to " + esc(r.label)}"` : ""}>` +
           `<td>${esc(r.label)}</td>${cells(r)}</tr>`;
  }).join("");
  document.getElementById("stMonthFoot").innerHTML = `<tr class="st-month-total"><td>${esc(tot.label)}</td>${cells(tot)}</tr>`;

  const first = rows.find(r => r.month), last = [...rows].reverse().find(r => r.month);
  const nMonths = rows.filter(r => r.month).length;
  const notMatched = (d.month_excluded || []).map(x => `${esc(x.status)} ${fmtInt(x.count)}`).join(" · ");
  document.getElementById("stMonthNote").innerHTML = first
    ? `Enquiries in the file run from <strong>${esc(first.label)}</strong>` +
      (nMonths > 1 ? ` to <strong>${esc(last.label)}</strong>` : "") +
      ` (${nMonths} month${nMonths === 1 ? "" : "s"}): <strong>${fmtInt(tot.in_file)}</strong> enquiries, ` +
      `<strong>${fmtInt(tot.matched)}</strong> matched here` +
      (tot.other ? `, <strong>${fmtInt(tot.other)}</strong> not matched${notMatched ? ` (${notMatched})` : ""}.` : ".")
    : "";

  const withData = ST_MONTH_COLS.filter(([s]) => rows.some(r => (r.statuses[s] || 0) > 0));
  stackedBarChart("stMonthChart", rows.map(r => r.label),
    withData.map(([s, label]) => ({ label, values: rows.map(r => r.statuses[s] || 0),
      // The page's tones give "In transit" and "Other variant" the same orange; in a stacked bar that makes two
      // segments indistinguishable, so "Other variant" gets a lighter tint of it (same family, clearly different).
      color: s === "Model Available - Other Variant" ? "#F2C48F" : cssVar(MATCH_TONE[s][1]) })));
}

function renderStockList() {
  const data = stState.data;
  const rows = data.enquiries.filter(r => stState.match === "all" || r.match_status === stState.match);
  document.getElementById("stListTitle").textContent =
    stState.match === "all" ? "Enquiry-wise stock — all live enquiries" : `Enquiry-wise stock — ${stState.match}`;
  document.getElementById("stListHead").innerHTML =
    `<tr><th>Enquiry date</th><th>Customer</th><th>Phone</th><th>Consultant</th><th>Status</th><th>Model</th>` +
    `<th>Variant (enquired)</th><th>Colour</th><th>Match</th><th class="num">Physical</th><th class="num">In transit</th>` +
    `<th class="num">Allocated</th><th>Other colour (free)</th><th>Other variant (free)</th><th>Chassis · physical</th>` +
    `<th>Chassis · transit</th><th>Location</th><th class="num">Oldest age</th><th>Resolved stock variant</th>` +
    `<th>Next variant (one step up)</th><th class="num">Next · physical</th><th class="num">Next · transit</th>` +
    `<th>Upsell</th><th>Note</th></tr>`;
  const shown = rows.slice(0, stState.shown);
  document.getElementById("stListBody").innerHTML = shown.length ? shown.map(r => `
    <tr>
      <td>${dash(r.date)}</td><td>${esc(r.customer)}</td><td>${dash(r.phone)}</td><td>${esc(r.consultant)}</td>
      <td>${esc(r.enquiry_status)}</td><td>${esc(r.model)}</td><td>${dash(r.variant)}</td><td>${dash(r.color)}</td>
      <td>${badge(r.match_status, MATCH_TONE[r.match_status][0])}</td>
      <td class="num">${r.free_physical ? `<strong>${r.free_physical}</strong>` : "0"}</td>
      <td class="num">${r.free_transit ? `<strong>${r.free_transit}</strong>` : "0"}</td>
      <td class="num">${r.allocated}</td><td>${dash(r.other_color)}</td><td>${dash(r.other_variant)}</td>
      <td>${dash(r.chassis_physical)}</td><td>${dash(r.chassis_transit)}</td><td>${dash(r.location)}</td>
      <td class="num">${dash(r.oldest_age)}</td><td>${dash(r.resolved_variant)}</td>
      <td>${dash(r.next_variant)}</td><td class="num">${r.nvk ? r.next_free_physical : "–"}</td>
      <td class="num">${r.nvk ? r.next_free_transit : "–"}</td>
      <td>${r.upsell_flag ? `<strong>${esc(r.upsell_flag)}</strong>` : "–"}</td><td class="wrap">${dash(r.note)}</td>
    </tr>`).join("") : `<tr><td colspan="24" class="empty-cell">No enquiries for this selection.</td></tr>`;
  document.getElementById("stListHint").textContent =
    `${fmtInt(rows.length)} enquir${rows.length === 1 ? "y" : "ies"}` +
    (shown.length < rows.length ? ` — showing ${fmtInt(shown.length)}. Use the filters above, or Download Excel for all.` : "");
  const more = document.getElementById("stMore");
  more.style.display = shown.length < rows.length ? "" : "none";
}

function renderStockDemand() {
  document.getElementById("stDemandHead").innerHTML =
    `<tr><th>Model</th><th>Variant</th><th>Colour</th><th class="num">Enquiries</th><th class="num">Physical</th>` +
    `<th class="num">In transit</th><th class="num">Allocated</th><th class="num">Gap (physical)</th>` +
    `<th class="num">Gap (phys + transit)</th><th>Position</th></tr>`;
  const rows = stState.data.demand;
  const gap = (v) => (v < 0 ? `<span class="delta down">${v}</span>` : v > 0 ? `<span class="delta up">+${v}</span>` : "0");
  document.getElementById("stDemandBody").innerHTML = rows.length ? rows.map(r => `
    <tr><td>${esc(r.model)}</td><td>${esc(r.variant)}</td><td>${dash(r.color)}</td>
    <td class="num">${r.enquiries}</td><td class="num">${r.physical}</td><td class="num">${r.transit}</td>
    <td class="num">${r.allocated}</td><td class="num">${gap(r.gap_physical)}</td><td class="num">${gap(r.gap_total)}</td>
    <td>${badge(r.position, POSITION_TONE[r.position] || "grey")}</td></tr>`).join("")
    : `<tr><td colspan="10" class="empty-cell">No data.</td></tr>`;
}

function renderStockCoverage() {
  document.getElementById("stCoverHead").innerHTML =
    `<tr><th>Model</th><th>Variant</th><th>Colour</th><th>Chassis</th><th>Stock</th><th class="num">Age (days)</th>` +
    `<th>Location</th><th class="num">Enquiries</th></tr>`;
  let rows = stState.data.no_enquiry;
  if (stState.cover === "Physical" || stState.cover === "In Transit") rows = rows.filter(r => r.stock_type === stState.cover);
  if (stState.cover === "noenq") rows = rows.filter(r => r.enquiries === 0);
  const shown = rows.slice(0, 300);
  document.getElementById("stCoverBody").innerHTML = shown.length ? shown.map(r => `
    <tr><td>${esc(r.model)}</td><td>${esc(r.variant)}</td><td>${dash(r.color)}</td><td>${dash(r.chassis)}</td>
    <td>${badge(r.stock_type, r.stock_type === "Physical" ? "green" : "amber")}</td>
    <td class="num">${r.age !== "" && r.age > stState.data.summary.aged_days && r.stock_type === "Physical"
      ? `<span class="delta down">${r.age}</span>` : dash(r.age)}</td>
    <td>${dash(r.location)}</td>
    <td class="num">${r.enquiries === 0 ? badge("0", "red") : r.enquiries}</td></tr>`).join("")
    : `<tr><td colspan="8" class="empty-cell">No units for this selection.</td></tr>`;
}

/* ---------------------------------------------------------------------- */
/* Vehicle Stock — plain inventory overview (Physical vs In Transit).      */
/* Distinct from Enquiry Wise Stock above: this is "what does our          */
/* inventory look like" (aging, value, model/fuel/color/financier mix)     */
/* rather than "which enquiry can this unit fulfil". Its filter bar is     */
/* Stage / Model / Fuel Type / Financier — not Month/Consultant/Source —   */
/* so it gets its own small set of bespoke functions rather than reusing   */
/* the generic filter-bar helpers above.                                   */
/* ---------------------------------------------------------------------- */

const INV_DIM_LABELS = { model: "Model", variant: "Variant", color: "Color" };
const INV_BREAKDOWN_COLUMNS = [
  { key: "physical", label: "Physical", fmt: (v) => fmtInt(v) },
  { key: "transit", label: "Transit", fmt: (v) => fmtInt(v) },
  { key: "total", label: "Total", fmt: (v) => fmtInt(v) },
  { key: "avg_age_days", label: "Avg. Age", fmt: (v) => `${v} days` },
  { key: "stock_value", label: "Stock Value", fmt: (v) => fmtMoney(v) },
  { key: "physical_basic_price", label: "Physical Basic Price", fmt: (v) => fmtMoney(v) },
  { key: "transit_basic_price", label: "Transit Basic Price", fmt: (v) => fmtMoney(v) },
  { key: "basic_price_total", label: "Total Basic Price", fmt: (v) => fmtMoney(v) },
  { key: "basic_price_count", label: "Basic Price Count", fmt: (v) => fmtInt(v) },
];

async function populateInventoryFilters() {
  if (!state.inventoryFilterOptions) {
    state.inventoryFilterOptions = await getJSON("/api/vehicle-stock/filters");
  }
  const fillSelect = (sel, values, allLabel) => {
    if (!sel) return;
    const previous = sel.value;
    sel.innerHTML = "";
    if (allLabel) sel.appendChild(new Option(allLabel, "all"));
    values.forEach(v => sel.appendChild(new Option(v, v)));
    const stillValid = Array.from(sel.options).some(o => o.value === previous);
    sel.value = stillValid ? previous : (sel.options[0]?.value ?? "all");
  };
  fillSelect(document.getElementById("invModel"), state.inventoryFilterOptions.models, "All models");
  fillSelect(document.getElementById("invFuel"), state.inventoryFilterOptions.fuel_types, "All fuel types");
  fillSelect(document.getElementById("invFinancier"), state.inventoryFilterOptions.financiers, "All financiers");
  // invStage keeps its three hardcoded options (all/Physical/In Transit).
}

function readInventoryFilters() {
  return {
    stage: document.getElementById("invStage")?.value || "all",
    model: document.getElementById("invModel")?.value || "all",
    fuel_type: document.getElementById("invFuel")?.value || "all",
    financier: document.getElementById("invFinancier")?.value || "all",
  };
}

function attachInventoryFilters(onChange) {
  if (!document.getElementById("invStage")) return;
  ["invStage", "invModel", "invFuel", "invFinancier"].forEach(id => {
    document.getElementById(id).addEventListener("change", onChange);
  });
  document.getElementById("invReset").addEventListener("click", async () => {
    document.getElementById("invStage").value = "all";
    document.getElementById("invModel").value = "all";
    document.getElementById("invFuel").value = "all";
    document.getElementById("invFinancier").value = "all";
    await onChange();
  });
}

function inventoryApiParams(f) {
  return new URLSearchParams({ stage: f.stage, model: f.model, fuel_type: f.fuel_type, financier: f.financier }).toString();
}

function renderInventoryBreakdownTable(data, activeDim) {
  const head = document.getElementById("invBreakdownHead");
  const body = document.getElementById("invBreakdownBody");
  const rows = (data && data[`by_${activeDim}`]) || [];

  head.innerHTML = `<tr><th>${INV_DIM_LABELS[activeDim]}</th>${INV_BREAKDOWN_COLUMNS.map(c => `<th>${c.label}</th>`).join("")}</tr>`;

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${INV_BREAKDOWN_COLUMNS.length + 1}" class="empty-cell">No stock matches this selection.</td></tr>`;
    return;
  }

  // By Color: every colour row has a dropdown that lists the models in that colour.
  if (activeDim === "color") {
    body.innerHTML = rows.map((r, i) => {
      const models = r.models || [];
      const parentRow = `
        <tr class="inv-color-row sv-row" tabindex="0" data-color-idx="${i}" data-sv-dim="color" data-sv-label="${esc(r.label)}"
            title="Click the row to see every vehicle in ${esc(r.label)}">
          <td>
            <button type="button" class="inv-expand-btn" aria-expanded="false" data-color-idx="${i}"
                    title="Show models in this colour" aria-label="Show models in ${esc(r.label)}">
              <span class="inv-chevron" aria-hidden="true">▸</span>
            </button>
            <span class="sv-name">${esc(r.label)}</span>
            <span class="inv-model-count">${models.length} model${models.length === 1 ? "" : "s"}</span>
          </td>
          ${INV_BREAKDOWN_COLUMNS.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("")}
        </tr>`;
      const modelRows = models.map(m => `
        <tr class="inv-sub-row sv-row" tabindex="0" data-parent-idx="${i}" data-sv-dim="color" data-sv-label="${esc(r.label)}"
            data-sv-model="${esc(m.label)}" title="Click to see ${esc(m.label)} in ${esc(r.label)}" hidden>
          <td><span class="inv-sub-label">${esc(m.label)}</span></td>
          ${INV_BREAKDOWN_COLUMNS.map(c => `<td>${c.fmt(m[c.key])}</td>`).join("")}
        </tr>`).join("");
      return parentRow + modelRows;
    }).join("");
    return;
  }

  body.innerHTML = rows.map(r => `
    <tr class="sv-row" tabindex="0" data-sv-dim="${activeDim}" data-sv-label="${esc(r.label)}"
        title="Click to see every vehicle in ${esc(r.label)}">
      <td>${esc(r.label)}</td>
      ${INV_BREAKDOWN_COLUMNS.map(c => `<td>${c.fmt(r[c.key])}</td>`).join("")}
    </tr>
  `).join("");
}

/* Expand / collapse the model list under a colour row (By Color tab). */
function toggleInventoryColorRow(btn) {
  const idx = btn.dataset.colorIdx;
  const open = btn.getAttribute("aria-expanded") !== "true";
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.classList.toggle("open", open);
  document.querySelectorAll(`#invBreakdownBody tr.inv-sub-row[data-parent-idx="${idx}"]`)
    .forEach(tr => { tr.hidden = !open; });
}

async function loadVehicleStock() {
  await populateInventoryFilters();
  const f = readInventoryFilters();
  const qs = inventoryApiParams(f);

  const [kpis, analytics, breakdown] = await Promise.all([
    getJSON(`/api/vehicle-stock/kpis?${qs}`),
    getJSON(`/api/vehicle-stock/analytics?${qs}`),
    getJSON(`/api/vehicle-stock/breakdown?${qs}`),
  ]);
  state.inventoryBreakdownCache = breakdown;

  const hasAnyStock = state.meta && state.meta.row_counts && state.meta.row_counts.stock > 0;
  document.getElementById("invEmptyHint").textContent = hasAnyStock ? "" :
    "No stock data found. Add 'Physical Stock' and 'In Transit' sheets to your Enquiry workbook " +
    "(or upload a separate Stock workbook) via \"Update monthly data\" to populate this page.";

  renderKpiGrid("invKpiGrid", [
    { label: "Total stock", value: fmtInt(kpis.total_stock), color: "var(--blue)", sub: "physical + in transit" },
    { label: "Physical stock", value: fmtInt(kpis.physical_count), color: "var(--amber)", sub: "on the ground now" },
    { label: "In transit", value: fmtInt(kpis.transit_count), color: "var(--purple)", sub: "despatched, not yet arrived" },
    { label: "Physical stock value", value: fmtMoney(kpis.physical_value), color: "var(--green)", sub: "HMIL invoice value" },
    { label: "Avg. stock age", value: `${kpis.avg_stock_age_days} days`, color: "var(--blue)", sub: "days since HMI invoice · physical stock only" },
    { label: "Aged 60+ days", value: fmtInt(kpis.aged_60_plus), color: "var(--red)",
      sub: `${fmtPct(kpis.aged_60_plus_rate)} of physical stock` },
    { label: "Physical basic price", value: fmtMoney(kpis.physical_basic_price), color: "var(--green)",
      sub: `${fmtInt(kpis.physical_basic_count)} units with basic price` },
    { label: "In transit basic price", value: fmtMoney(kpis.transit_basic_price), color: "var(--purple)",
      sub: `${fmtInt(kpis.transit_basic_count)} units with basic price` },
    { label: "Total basic price", value: fmtMoney(kpis.total_basic_price), color: "var(--blue)",
      sub: `${fmtInt(kpis.total_basic_count)} units · physical + in transit` },
  ]);

  svTagKpiCards();

  const stage = analytics.stage_split || { Physical: 0, Transit: 0 };
  doughnutChart("invStageChart", ["Physical", "Transit"], [stage.Physical, stage.Transit],
    [cssVar("--amber"), cssVar("--purple")]);

  const aging = analytics.aging_buckets || [];
  barChart("invAgingChart", aging.map(a => a.label), aging.map(a => a.value), cssVar("--red"), false);

  const byModel = analytics.by_model || [];
  barChart("invModelChart", byModel.map(m => m.label), byModel.map(m => m.value), cssVar("--blue"));

  const fuel = analytics.fuel_breakdown || [];
  doughnutChart("invFuelChart", fuel.map(x => x.label), fuel.map(x => x.value),
    [cssVar("--blue"), cssVar("--amber"), cssVar("--green")]);

  const colors = analytics.color_breakdown || [];
  barChart("invColorChart", colors.map(c => c.label), colors.map(c => c.value), cssVar("--purple"));

  const financiers = analytics.financier_breakdown || [];
  barChart("invFinancierChart", financiers.map(x => x.label), financiers.map(x => x.value), cssVar("--green"), false);

  const activeBtn = document.querySelector("#invBreakdownTabs .tab-btn.active");
  renderInventoryBreakdownTable(breakdown, activeBtn ? activeBtn.dataset.dim : "model");
}

const SECTION_LOADERS = {
  overview: loadOverview,
  testdrive: loadTestDrive,
  enquiry: loadEnquiry,
  exchange: loadExchange,
  followup: loadFollowup,
  booking: loadBooking,
  sales: loadSales,
  inventory: loadVehicleStock,
  stock: loadStock,
  comparison: loadComparison,
};

const SECTION_TITLES = {
  overview: ["Overview", "Performance snapshot for the selected period"],
  testdrive: ["Test drive analytics", "Enquiry sheet column O — Y (done) vs N (not done)"],
  enquiry: ["Enquiry analytics", "Status, appointed enquiries, sources, ageing and lost reasons"],
  exchange: ["Exchange analytics", "Exchange opted, scrap, present car, maker, model and model year from the Enquiry sheet"],
  followup: ["Enquiry follow-up", "Day-wise and previous-days pending follow-ups, date-wise schedule, follow-up and appointed cancels"],
  booking: ["Booking analytics", "Enquiry Status = Booked — sources, consultants, trend and booking cancels"],
  sales: ["Retail analytics", "Enquiry Status = Retail — vehicles sold, by model, consultant and source"],
  inventory: ["Vehicle stock", "Physical stock and in-transit vehicles — inventory overview (aging, value, mix)"],
  stock: ["Enquiry wise stock", "Every live enquiry matched to Physical and In-Transit stock (Model + Variant + Colour)"],
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
  mvCache.clear();                      // detail-window data may be stale after a Refresh / upload
  state.filterOptions = null;           // new consultants / models / enquiry dates may have arrived
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

  document.getElementById("lastSync").textContent = `Loaded ${fmtStamp(meta.last_loaded)} · Build V11`;
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

    const hasFile = data.get("enquiry") && data.get("enquiry").size > 0;
    if (!hasFile) {
      msg.textContent = "Choose the Enquiry Excel file to upload.";
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
      msg.textContent = `Updated: ${json.updated.join(", ")}. Recalculating dashboard…` +
        (json.warning ? `  ⚠ ${json.warning}` : "");
      msg.className = json.warning ? "modal-msg err" : "modal-msg ok";
      await loadMeta();
      await reloadAllLoadedSections();
      setTimeout(() => { modal.classList.remove("open"); form.reset(); msg.textContent = ""; }, json.warning ? 4500 : 1200);
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

  // Logout: /logout clears the browser's saved login (press Cancel if a sign-in box appears)
  document.getElementById("logoutBtn").addEventListener("click", () => {
    window.location.href = "/logout";
  });

  // Every page's own Month / Model / Consultant / Source filter bar
  attachFilterBar("ov", loadOverview);
  attachFilterBar("enq", loadEnquiry, {
    extraFields: ["Age"],
    onReset: () => { const a = document.getElementById("enqAge"); if (a) a.value = "all"; },
  });
  attachFilterBar("ex", loadExchange);
  document.getElementById("exScopeTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    exState.scope = btn.dataset.scope;
    document.querySelectorAll("#exScopeTabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    loadExchange();
  });
  attachFilterBar("td", loadTestDrive);
  attachFilterBar("book", loadBooking);
  attachFilterBar("sales", loadSales);
  attachFilterBar("cmp", refreshComparisonView);

  // MTD toggle: press it to compare the same day-range in both months
  // (e.g. 1st-3rd vs 1st-3rd); press again to go back to full months.
  document.getElementById("cmpMtd").addEventListener("click", async (e) => {
    state.cmpMtd = !state.cmpMtd;
    e.currentTarget.setAttribute("aria-pressed", state.cmpMtd ? "true" : "false");
    await refreshComparisonView();
  });

  // Follow-up page: extra date field + list tabs
  attachFilterBar("fu", loadFollowup, {
    extraFields: [],
    onReset: () => {
      document.getElementById("fuFrom").value = todayISO();
      document.getElementById("fuTo").value = todayISO();
      setFollowupScope("today");
    },
  });
  const onFuDateChange = (which) => () => {
    const fromEl = document.getElementById("fuFrom"), toEl = document.getElementById("fuTo");
    if (which === "from" && toEl.value && toEl.value < fromEl.value) toEl.value = fromEl.value;   // keep From <= To
    if (which === "to" && fromEl.value && fromEl.value > toEl.value) fromEl.value = toEl.value;
    if (fuState.scope === "date") setFollowupScope("today");
    loadFollowup();
  };
  document.getElementById("fuFrom").addEventListener("change", onFuDateChange("from"));
  document.getElementById("fuTo").addEventListener("change", onFuDateChange("to"));
  document.getElementById("fuScopeTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    setFollowupScope(btn.dataset.scope, btn.dataset.scope === "date" ? fuState.date : null);
    document.querySelectorAll("#fuDateBody tr").forEach(tr => tr.classList.toggle("selected", fuState.scope === "date" && tr.dataset.date === fuState.date));
    loadFollowupList();
  });
  document.getElementById("fuDateBody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-date]");
    if (tr) selectFollowupDate(tr.dataset.date);
  });
  document.getElementById("fuCancelTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    fuState.cancelScope = btn.dataset.scope;
    document.querySelectorAll("#fuCancelTabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    loadFollowupCancelList();
  });

  // Enquiry Wise Stock page
  attachFilterBar("st", loadStock, {
    extraFields: ["Status", "EnqMonth"],
    onReset: () => {
      document.getElementById("stStatus").value = "all"; document.getElementById("stEnqMonth").value = "all";
      stState.match = "all"; stState.shown = 100;
    },
  });
  document.getElementById("stMonthBody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr.st-month-row");
    if (!tr || !tr.dataset.month) return;
    const sel = document.getElementById("stEnqMonth");
    sel.value = sel.value === tr.dataset.month ? "all" : tr.dataset.month;
    stState.shown = 100;
    loadStock();
  });
  document.getElementById("stMore").addEventListener("click", () => { stState.shown += 100; renderStockList(); });
  document.getElementById("stExport").addEventListener("click", () => {
    window.location.href = `/api/enquiry-stock/export?${stQuery()}`;
  });
  document.getElementById("stCoverTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    stState.cover = btn.dataset.type;
    document.querySelectorAll("#stCoverTabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    renderStockCoverage();
  });

  // Vehicle Stock page (own filter set: Stage/Model/Fuel Type/Financier)
  attachInventoryFilters(loadVehicleStock);
  document.getElementById("invBreakdownTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    document.querySelectorAll("#invBreakdownTabs .tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (state.inventoryBreakdownCache) renderInventoryBreakdownTable(state.inventoryBreakdownCache, btn.dataset.dim);
  });

  // Vehicle Stock > By Color: click a colour to open / close its model list
  document.getElementById("invBreakdownBody").addEventListener("click", (e) => {
    const btn = e.target.closest(".inv-expand-btn");
    if (btn) toggleInventoryColorRow(btn);
  });

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

/* ---------------------------------------------------------------------- */
/* Sortable tables — click any column header to sort ascending, click      */
/* again for descending. Applies to EVERY table in the dashboard, including */
/* tables that are re-rendered by filters (the chosen sort is re-applied   */
/* after each refresh). Numbers (₹ / % / days), dates and text are all     */
/* detected automatically per column; empty cells ("—") always sort last.  */
/* Expandable groups (Vehicle Stock > By Color) keep their model rows      */
/* attached to their colour row, and the model rows are sorted within it.  */
/* ---------------------------------------------------------------------- */
(function initSortableTables() {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

  const cellText = (cell) => {
    if (!cell) return "";
    if (cell.dataset && cell.dataset.sort !== undefined) return cell.dataset.sort;
    return cell.textContent.replace(/\s+/g, " ").trim();
  };
  const isBlank = (t) => t === "" || /^[—–-]+$/.test(t);

  function parseNumber(t) {
    const s = t.replace(/[₹$%,▲▼•\s]/g, "").replace(/(days?|yrs?|years?|hrs?)$/i, "");
    return /^[-+]?\d*\.?\d+$/.test(s) ? parseFloat(s) : null;
  }

  function parseDate(t) {
    let m;
    if ((m = t.match(/^(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?,?\s+(\d{4})(?:[,\s]+(\d{1,2}):(\d{2}))?$/))) {
      const mon = MONTHS[m[2].toLowerCase()];
      if (mon !== undefined) return Date.UTC(+m[3], mon, +m[1], +(m[4] || 0), +(m[5] || 0));
    }
    if ((m = t.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?/))) {
      return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
    }
    if ((m = t.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})$/))) {          // dd-mm-yyyy
      return Date.UTC(+m[3], +m[2] - 1, +m[1]);
    }
    if ((m = t.match(/^([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})$/))) {              // Sep 2026
      const mon = MONTHS[m[1].toLowerCase()];
      if (mon !== undefined) return Date.UTC(+m[2], mon, 1);
    }
    return null;
  }

  /* Decide column type from all of its values, then build comparable keys. */
  function columnKeys(texts) {
    const filled = texts.filter(t => !isBlank(t));
    const nums = filled.filter(t => parseNumber(t) !== null).length;
    if (filled.length && nums > 0 && nums / filled.length >= 0.5) {
      return { type: "num", keys: texts.map(t => (isBlank(t) ? null : parseNumber(t))) };
    }
    const dates = filled.filter(t => parseDate(t) !== null).length;
    if (filled.length && dates / filled.length >= 0.5) {
      return { type: "date", keys: texts.map(t => (isBlank(t) ? null : parseDate(t))) };
    }
    return { type: "text", keys: texts.map(t => (isBlank(t) ? null : t)) };
  }

  const compareKeys = (type, a, b, dir) => {
    if (a === null && b === null) return 0;
    if (a === null) return 1;             // blanks always last, whatever the direction
    if (b === null) return -1;
    const r = type === "text" ? collator.compare(a, b) : a - b;
    return dir === "asc" ? r : -r;
  };

  /* Split tbody rows into groups: a normal row + any rows that belong under it. */
  function buildGroups(tbody) {
    const groups = [];
    for (const row of Array.from(tbody.rows)) {
      const isChild = row.classList.contains("inv-sub-row") || row.hasAttribute("data-parent-idx");
      if (isChild && groups.length) groups[groups.length - 1].children.push(row);
      else groups.push({ row, children: [] });
    }
    return groups;
  }

  const headSignature = (table) => {
    const ths = table.tHead ? Array.from(table.tHead.rows[table.tHead.rows.length - 1]?.cells || []) : [];
    return `${ths.length}|${ths[0] ? ths[0].textContent.trim() : ""}`;
  };

  function sortBody(tbody, colIdx, dir) {
    if (!tbody.rows.length || tbody.querySelector(".empty-cell")) return false;
    const groups = buildGroups(tbody);

    const parents = columnKeys(groups.map(g => cellText(g.row.cells[colIdx])));
    groups.forEach((g, i) => { g.key = parents.keys[i]; });
    groups.sort((a, b) => compareKeys(parents.type, a.key, b.key, dir));

    const frag = document.createDocumentFragment();
    for (const g of groups) {
      frag.appendChild(g.row);
      if (g.children.length) {
        const kids = columnKeys(g.children.map(r => cellText(r.cells[colIdx])));
        const order = g.children.map((r, i) => ({ r, k: kids.keys[i] }));
        order.sort((a, b) => compareKeys(kids.type, a.k, b.k, dir));
        order.forEach(o => frag.appendChild(o.r));
      }
    }
    tbody.appendChild(frag);
    return true;
  }

  function paintHeaders(table, colIdx, dir) {
    if (!table.tHead) return;
    const headRow = table.tHead.rows[table.tHead.rows.length - 1];
    if (!headRow) return;
    Array.from(headRow.cells).forEach((th, i) => {
      th.classList.add("sortable");
      th.setAttribute("tabindex", "0");
      th.setAttribute("title", "Click to sort ascending / descending");
      th.setAttribute("aria-sort",
        i === colIdx ? (dir === "asc" ? "ascending" : "descending") : "none");
    });
  }

  function applySort(table) {
    const st = table._sort;
    if (!st) return;
    Array.from(table.tBodies).forEach(tb => sortBody(tb, st.idx, st.dir));
  }

  /* Re-decorate headers and re-apply the current sort after a table is (re)rendered. */
  function processTable(table) {
    if (!table.tHead || !table.tHead.rows.length) return;
    const sig = headSignature(table);
    if (table._sort && table._sort.sig !== sig) table._sort = null;   // different column set -> reset
    if (table._sort) {
      applySort(table);
      paintHeaders(table, table._sort.idx, table._sort.dir);
    } else {
      paintHeaders(table, -1, "asc");
    }
  }

  function handleSortClick(th) {
    const table = th.closest("table");
    if (!table || table.hasAttribute("data-nosort")) return;
    const idx = th.cellIndex;
    const prev = table._sort;
    const dir = prev && prev.idx === idx && prev.dir === "asc" ? "desc" : "asc";
    table._sort = { idx, dir, sig: headSignature(table) };
    applySort(table);
    paintHeaders(table, idx, dir);
    observer.takeRecords();               // ignore the DOM moves we just made
  }

  document.addEventListener("click", (e) => {
    const th = e.target.closest("table thead th");
    if (th) handleSortClick(th);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const th = e.target.closest && e.target.closest("table thead th.sortable");
    if (th) { e.preventDefault(); handleSortClick(th); }
  });

  /* Watch for tables being (re)rendered by the dashboard's own code. */
  let queued = new Set();
  let scheduled = false;
  const flush = () => {
    scheduled = false;
    const tables = Array.from(queued);
    queued = new Set();
    tables.forEach(processTable);
    observer.takeRecords();
  };
  const observer = new MutationObserver((records) => {
    for (const rec of records) {
      const host = rec.target.nodeType === 1 ? rec.target : rec.target.parentElement;
      const own = host && host.closest ? host.closest("table") : null;
      if (own) queued.add(own);
      rec.addedNodes.forEach(n => {
        if (n.nodeType !== 1) return;
        if (n.tagName === "TABLE") queued.add(n);
        else if (n.querySelectorAll) n.querySelectorAll("table").forEach(t => queued.add(t));
      });
    }
    if (queued.size && !scheduled) { scheduled = true; requestAnimationFrame(flush); }
  });

  const start = () => {
    document.querySelectorAll("table").forEach(processTable);
    observer.observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
