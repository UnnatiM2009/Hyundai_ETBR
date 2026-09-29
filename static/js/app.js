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
  conv: "conversion", ex: "exchange",
};

/* Column layout for the Model/Consultant/Source breakdown table on each page —
   the metrics that matter differ per section, so each gets its own column set. */
const BREAKDOWN_COLUMNS = {
  overview: [
    { key: "enquiries", label: "Enquiries", fmt: (v) => fmtInt(v) },
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
  const p = { period: f.month, model: f.model, consultant: f.consultant, source: f.source };
  if (f.age && f.age !== "all") p.age = f.age;      // Enquiry page only
  return new URLSearchParams(p).toString();
}

/* "Enquiry aging days" dropdown on the Enquiry page: buckets first, then each exact age in days. */
function populateAgeSelect() {
  const sel = document.getElementById("enqAge");
  if (!sel || !state.filterOptions?.ages) return;
  const previous = sel.value;
  const ages = state.filterOptions.ages;
  sel.innerHTML = "";
  sel.appendChild(new Option("All enquiry ages", "all"));
  const g1 = document.createElement("optgroup"); g1.label = "Age range";
  ages.buckets.forEach(b => g1.appendChild(new Option(b.label, b.value)));
  sel.appendChild(g1);
  const g2 = document.createElement("optgroup"); g2.label = "Exact age";
  ages.days.forEach(d => g2.appendChild(new Option(d === 1 ? "1 day" : `${d} days`, `d:${d}`)));
  sel.appendChild(g2);
  sel.value = Array.from(sel.options).some(o => o.value === previous) ? previous : "all";
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
  populateAgeSelect();
  const f = readFilterBar("enq");
  f.age = document.getElementById("enqAge")?.value || "all";
  const qs = apiParams(f);
  const ageOn = f.age !== "all";
  const ageText = ageOn ? document.getElementById("enqAge").selectedOptions[0].textContent : "";

  const [kpis, enquiry, comparison] = await Promise.all([
    getJSON(`/api/kpis?${qs}`),
    getJSON(`/api/enquiry?${qs}`),
    fetchComparisonFor("enq"),
  ]);
  const cmp = cmpLookup(comparison);

  renderKpiGrid("enqKpiGrid", [
    { label: "Total enquiries", value: fmtInt(kpis.total_enquiries), color: "var(--blue)",
      sub: ageOn ? `enquiry age: ${ageText}` : deltaHtml(cmp["Total Enquiries"]?.change_pct, cmp["Total Enquiries"]?.direction) },
    { label: "Enquiry → booking", value: fmtPct(kpis.enquiry_to_booking_rate), color: "var(--green)",
      sub: ageOn ? `enquiry age: ${ageText}` : deltaHtml(cmp["Enquiry to Booking Conv. (%)"]?.change_pct, cmp["Enquiry to Booking Conv. (%)"]?.direction) },
    { label: "Appointed enquiry", value: fmtInt(kpis.appointed_enquiries), color: "var(--purple)",
      sub: ageOn ? `enquiry age: ${ageText}` : "status = Appointed Enquiry, this selection" },
    { label: "Lost enquiries", value: fmtInt(kpis.lost_enquiries), color: "var(--red)",
      sub: ageOn ? `enquiry age: ${ageText}` : deltaHtml(cmp["Lost Enquiries"]?.change_pct, cmp["Lost Enquiries"]?.direction) },
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
function attachFilterBar(prefix, onChange, { extraFields = [], onReset = null } = {}) {
  if (!document.getElementById(`${prefix}Model`)) return;
  ["Month", "Model", "Consultant", "Source", ...extraFields].forEach(suffix => {
    const el = document.getElementById(`${prefix}${suffix}`);
    if (el) el.addEventListener("change", onChange);
  });
  const resetBtn = document.getElementById(`${prefix}Reset`);
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      document.getElementById(`${prefix}Model`).value = "all";
      document.getElementById(`${prefix}Consultant`).value = "all";
      document.getElementById(`${prefix}Source`).value = "all";
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
  if (filters.age && filters.age !== "all") params.set("age", filters.age);
  const data = await getJSON(`/api/breakdown?${params.toString()}`);
  state.breakdownCache[prefix] = data;
  const activeBtn = document.querySelector(`[data-tabgroup="${prefix}"] .tab-btn.active`);
  renderBreakdownTable(prefix, section, data, activeBtn ? activeBtn.dataset.dim : "model");
}

/* ---------------------------------------------------------------------- */
/* Enquiry Follow-up page                                                   */
/* ---------------------------------------------------------------------- */
const fuState = { scope: "today", date: null, data: null, cancelScope: "followup_cancel" };

function fuFilters() {
  const f = readFilterBar("fu");
  const asOf = document.getElementById("fuDate").value || todayISO();
  return { ...f, asOf };
}

function fuQuery(extra = {}) {
  const f = fuFilters();
  return new URLSearchParams({
    as_of: f.asOf, period: f.month, model: f.model, consultant: f.consultant, source: f.source, ...extra,
  }).toString();
}

function overdueBadge(days) {
  if (days === null || days === undefined) return badge("No date", "grey");
  if (days > 0) return badge(`${days} day${days > 1 ? "s" : ""} overdue`, "red");
  if (days === 0) return badge("Due today", "amber");
  return badge(`In ${-days} day${days < -1 ? "s" : ""}`, "blue");
}

async function loadFollowup() {
  await populateFilterBar("fu");
  const dateEl = document.getElementById("fuDate");
  if (!dateEl.value) dateEl.value = todayISO();

  const f = fuFilters();
  const data = await getJSON(`/api/followup?${fuQuery()}`);
  fuState.data = data;
  const k = data.kpis;
  const ref = fmtIsoLong(data.as_of);

  renderKpiGrid("fuKpiGrid", [
    { label: "Due today", value: fmtInt(k.due_today), color: "var(--amber)", sub: `follow-ups dated ${ref}` },
    { label: "Previous days pending", value: fmtInt(k.pending_previous), color: "var(--red)",
      sub: "follow-up date passed, still open" },
    { label: "Upcoming (next 7 days)", value: fmtInt(k.upcoming_7_days), color: "var(--blue)",
      sub: `${fmtInt(k.upcoming_total)} upcoming in total` },
    { label: "Open follow-ups", value: fmtInt(k.open_followups), color: "var(--green)",
      sub: k.no_followup_date ? `${fmtInt(k.no_followup_date)} have no follow-up date` : "status = Enquiry Follow up" },
    { label: "Enquiry follow up cancel", value: fmtInt(k.followup_cancel), color: "var(--red)",
      sub: `cancelled in ${state.meta.available_periods.find(p => p.value === f.month)?.label || f.month}` },
    { label: "Appointed enquiry cancel", value: fmtInt(k.appointed_cancel), color: "var(--red)",
      sub: `cancelled in ${state.meta.available_periods.find(p => p.value === f.month)?.label || f.month}` },
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
    `${data.window_days} days either side of ${ref}. Red = pending · Amber = today · Blue = upcoming. Click a bar to list that day.` +
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

  await Promise.all([loadFollowupList(), loadFollowupCancelList()]);
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
    model: f.model, consultant: f.consultant, source: f.source,
    status: document.getElementById("stStatus").value || "all",
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
    { label: "Live enquiries matched", value: fmtInt(s.enquiries), color: "var(--blue)", sub: "follow-up, appointed, lead, booked" },
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

  renderStockList();
  renderStockDemand();
  renderStockCoverage();
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
    `<th>Chassis · transit</th><th>Location</th><th class="num">Oldest age</th><th>Resolved stock variant</th><th>Note</th></tr>`;
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
      <td class="num">${dash(r.oldest_age)}</td><td>${dash(r.resolved_variant)}</td><td class="wrap">${dash(r.note)}</td>
    </tr>`).join("") : `<tr><td colspan="20" class="empty-cell">No enquiries for this selection.</td></tr>`;
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

const SECTION_LOADERS = {
  overview: loadOverview,
  testdrive: loadTestDrive,
  enquiry: loadEnquiry,
  exchange: loadExchange,
  followup: loadFollowup,
  booking: loadBooking,
  sales: loadSales,
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

    const hasFile = ["enquiry", "stock"].some(k => data.get(k) && data.get(k).size > 0);
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

  // Follow-up page: extra date field + list tabs
  attachFilterBar("fu", loadFollowup, {
    extraFields: [],
    onReset: () => { document.getElementById("fuDate").value = todayISO(); setFollowupScope("today"); },
  });
  document.getElementById("fuDate").addEventListener("change", () => {
    if (fuState.scope === "date") setFollowupScope("today");
    loadFollowup();
  });
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
    extraFields: ["Status"],
    onReset: () => { document.getElementById("stStatus").value = "all"; stState.match = "all"; stState.shown = 100; },
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
