# Hyundai ETBR Analysis
### Unnati Hyundai — Dealership Performance Dashboard

A self-hosted analytics dashboard built from **one Enquiry workbook** (plus an optional
**Stock workbook**). Booking and Retail are no longer separate exports — they are read from
the Enquiry sheet's **Enquiry Status** column. Around that it gives you **Test Drive
tracking** (the Y/N flag), an **Enquiry Follow-up** desk, an **Enquiry Wise Stock** page
(Physical vs In Transit), and automatic **current-month vs previous-month** comparison.

Stack: **Python + FastAPI** (backend/API) · **HTML/CSS/JavaScript + Chart.js** (frontend).
No database. Works fully offline on your own machine, or deployed to the web (see
Section 8) — either way, mobile and desktop browsers are both fully supported.

---

## 1. What's inside

```
hyundai_dashboard/
├── main.py                 FastAPI app: API routes + serves the frontend
├── data_processor.py       Enquiry loading, status logic, KPIs, follow-up logic (pandas)
├── stock_engine.py         Enquiry-wise stock matching (ported from the Mahindra script)
├── run.py                  Local launcher — starts the server AND opens your browser
├── render.yaml             Render deployment blueprint (see Section 8)
├── requirements.txt        Python dependencies
├── Stock_Template.xlsx     Headings the stock workbook should have (no data)
├── .gitignore              Keeps your real customer data out of git (see Section 8)
├── start_dashboard.bat     Windows: double-click to install deps + run
├── start_dashboard.sh      macOS/Linux: same, from a terminal
├── data/
│   ├── Enquiry.xlsx        ONE workbook: sheets 'Enquiry', 'Physical Stock', 'In Transit'
│   └── Stock.xlsx          (optional) only if stock is kept in a separate file
└── static/                 Frontend (index.html, css/style.css, js/app.js, js/vendor/chart.umd.js)
```

## 2. Requirements

- Python 3.9 or newer. Chart.js is bundled, so nothing needs the internet once installed.

## 3. Running it locally

**Windows** — double-click `start_dashboard.bat`.
**macOS / Linux** — `chmod +x start_dashboard.sh && ./start_dashboard.sh`
**Manually** — `pip install -r requirements.txt` then `python run.py`

Your browser opens at `http://127.0.0.1:8000`. To open it from a phone on the same Wi-Fi:
`HOST=0.0.0.0 python run.py`, then visit `http://<your-computer-ip>:8000`.

## 4. Mobile support

Every screen is responsive down to small phone widths: the sidebar becomes a slide-out
menu, KPI cards drop to 2 or 1 columns, and wide tables scroll inside their own box.

## 5. Updating data ("henceforth")

No code changes are needed. Either:

1. **In the dashboard:** sidebar → **Update monthly data**, pick the new workbook
   (Enquiry + Physical Stock + In Transit sheets), **Upload & recalculate**. A separate Stock.xlsx is optional. Each file is checked *before* it replaces
   the current one — a wrong file is rejected with a message and the existing data stays.
2. **On disk:** replace the files in `data/` and click **Refresh**.

**Month comparison:** the dashboard doesn't use today's date to decide "current month" —
it takes the latest date present in the Enquiry sheet (enquiry, booking, retail or lost
date) and compares it with the calendar month before. With only September data, August
correctly shows 0 until an export containing August is loaded.

## 6. How Enquiry Status drives everything

| Enquiry Status | Meaning | Counted in the month of |
|---|---|---|
| **Booked** | a booking | Booking Date |
| **Retail** | a vehicle sold | Retail date |
| **Booking Cancel** | cancelled booking | Lost Date |
| **Enquiry Follow up** | open follow-up | (Next Followup Date drives the follow-up page) |
| **Enquiry Follow up Cancel** | lost at follow-up | Lost Date |
| **Appointed Enquiry** | appointment fixed | Enquiry Date |
| **Appointed Enquiry Cancel** | appointment cancelled | Lost Date |
| **Lead** | new lead | Enquiry Date |

If a Booking/Retail/Lost date is blank, the enquiry date is used instead. The mapping
lives at the top of `data_processor.py` (`BOOKING_STATUSES`, `RETAIL_STATUSES`, …) — edit
it there if the DMS ever renames a status.

**Definitions used**
- *Total bookings* = status **Booked**. *Units retailed* = status **Retail**.
- *Booking → Retail %* = Retail ÷ (Booked + Retail) — Booked customers still waiting plus
  those already sold. (Booked alone would exceed 100% once more cars are sold than are
  currently on order.)
- *Lost enquiries* = every status containing "Cancel" (booking, follow-up and appointed).
- *Avg. booking age* = days from Enquiry Date to Booking Date.
  *Avg. booking → retail* = days from Booking Date to Retail date.

**Not available any more** (they only existed in the old Booking / SalesReport files):
revenue, amount received, mode of purchase, invoice-to-delivery days.

## 7. What each page shows

Every page that has a **Source** filter also has a **Sub-source** filter next to it (see V18 changes).

- **Overview** — KPIs (with vs-last-month deltas), Enquiry → Booking → Retail funnel,
  test-drive gauge, top models, source mix, conversion tables.
- **Enquiry** — status breakdown, **Appointed Enquiry** card, ageing, cities, lost reasons.
  The **Enquiry aging days** dropdown (from the `enquiry aging days` column) narrows the whole
  page to an age range (0-7 / 8-15 / 16-30 / 31+ days) or to one exact age in days; the cards,
  charts and breakdown table all follow it (month-on-month deltas are hidden while it is set).
- **Enquiry Follow-up** *(new)* — set the "Follow-up date (today)" (defaults to today):
  - cards: **Due today**, **Previous days pending**, **Upcoming (7 days)**, **Open follow-ups**,
    **Enquiry Follow up Cancel**, **Appointed Enquiry Cancel**
  - **day-wise chart** (red = pending from previous days, amber = today, blue = upcoming;
    click a bar to list that day) and **pending-by-age** chart
  - **customer list** with tabs: Due today · Previous days pending (most overdue first) ·
    Upcoming · All open · any single date you click
  - **date-wise schedule** (every follow-up date, click to drill in) and **consultant-wise** table
  - **cancelled enquiries** tabs for Follow up Cancel / Appointed Enquiry Cancel (with lost reason)
- **Test Drive** — done (Y) vs not done (N), trend, by model / consultant / source, and how far
  test-driven customers progressed to Booked / Retail.
- **Booking** — Total bookings, **Booking Cancel** card, booking → retail, by source / model /
  consultant, daily trend, booking-cancel reasons and cancels by model.
- **Retails** — vehicles sold by model, consultant, source and daily trend.
- **Exchange** *(new)* — read from the Enquiry sheet's exchange columns (AM onwards): **Exchange
  opted**, **Scrap Y/N**, **Scrap Through Hyundai Y/N**, **Present Car**, **Maker Name**, **Maker
  Model**, **Model Year**. Cards (exchange opted and %, present car owners, scrap, scrap through
  Hyundai, exchange customers who went on to Book/Retail, average present-car age), Y/N charts,
  maker / maker-model / model-year charts, exchange by the model enquired, a customer-wise
  detail table (Exchange opted · All present car owners · All enquiries) and the usual
  Model / Consultant / Source breakdown. Maker spellings are merged (HONDA = Honda).
  Note: the scrap questions are only filled for exchange customers, so Scrap charts are
  counted among exchange-opted customers.
- **Vehicle Stock** *(new)* — plain inventory overview of the same Physical Stock / In
  Transit sheets: KPI cards (Total Stock, Physical, In Transit, Physical Stock Value,
  Avg. Stock Age, Aged 60+ days), charts (Physical vs Transit split, ageing buckets,
  by model, fuel-type mix, top colors, financier mix), and a Model / Variant / Color
  breakdown table (Physical, Transit, Total, Avg. Age, Stock Value). Its filter bar is
  **Stage / Model / Fuel Type / Financier** rather than Month/Consultant/Source, since
  stock is a point-in-time snapshot. This is a different question from the page below:
  Vehicle Stock asks "what does our inventory look like", Enquiry Wise Stock asks "which
  enquiry can this unit fulfil" — they read the same underlying stock data, just for
  different purposes.
- **Enquiry Wise Stock** *(new)* — see below.
- **Month Comparison** — every key metric, previous vs current month, filterable, with an
  **MTD toggle** to compare the same day-range in both months instead of a full month
  against one that's barely started (see V16 changes below).

## 7a. Enquiry Wise Stock (Physical vs In Transit)

Ported from the Mahindra *Enquiry_Wise_Stock* script. Every **live** enquiry (Enquiry Follow
up, Appointed Enquiry, Lead, Booked — sold and cancelled are excluded; change
`STOCK_MATCH_STATUSES` in `data_processor.py` to alter that) is matched to stock on
**Model + Variant + Colour**:

| Match status | Meaning |
|---|---|
| Exact Match - Physical | model + variant + colour free and physically in stock |
| Exact Match - In Transit | exact unit only in transit |
| Exact Match - Allocated Only | exact unit exists but is already allocated |
| Variant Available - Other Color | same variant free, other colour |
| Model Available - Other Variant | same model free, other variant |
| No Stock Available | nothing free for the model — needs indent |
| Variant Not Captured | enquiry has no variant, can't be matched |

Enquiry and stock spell variants differently, so variants are compared on their **trim
codes** (model name, emission norm and seat count are removed) and an enquiry variant matches
a stock variant when its codes are a subset — the closest one is shown as *Resolved Stock
Variant*. Availability is a **quantity**; nothing is reserved, so one car can serve several
enquiries. If an enquiry has no colour, any colour of that variant is counted (noted in the row).

The page shows KPI cards, clickable match-status chips, a status chart, demand-vs-stock by
model, the enquiry-wise table (chassis numbers, location, oldest stock age), a **Demand vs
Stock** table by Model/Variant/Colour (with "Short of physical stock", "Only in transit"…),
and free stock oldest-first with how many enquiries each unit could serve.
**Download Excel** exports the same result.

**Stock:** it lives in the **same workbook as the Enquiry sheet**, on two sheets named
**Physical Stock** and **In Transit** (see `Stock_Template.xlsx`). The **sheet name** decides
Physical vs In Transit — the *In Out Status* column of the DMS export says "In Transit" on
both sheets, so it is deliberately ignored. Columns are detected automatically
(case-insensitive) and the page shows which column it read for each field: Model, Variant,
**Exterior Color Name** (not *Color Type*), **Vin Number**, **Stock Age**, **Stock Location**.
A unit counts as **Allocated** when *Blocked* = Y, *Stock Status* says allocated/booked, or
*Bkng No* / *Cust Name* is filled (the factory *Order No* is not treated as allocation).
A VIN on both sheets is counted once, as Physical. Uploading a new workbook replaces any
older separate Stock.xlsx; a separate Stock.xlsx (single sheet with a Physical / In Transit
column) is still accepted if you prefer. If a model is spelled differently in stock and
enquiry and can't be reconciled automatically, add it to `MODEL_ALIASES` in `stock_engine.py`.

For the **Vehicle Stock** page specifically, four more columns are detected the same
fuzzy, case-insensitive way if present — **Fuel Type**, **Financier Name**, **Basic
Price**, **HMIL Invoice Amt** — none of them required, and none used by the matching
engine above. A stock file without them still works; those specific charts/KPIs just
show 0 or are omitted.

---

### Enquiry Wise Stock - Excel download (Mahindra layout + Kia Next Variant)

The **Download Excel** button now produces the same workbook layout as the Mahindra report:

- **Summary** with clickable counts - click a count (or the status name) to jump to a sheet listing exactly those
  enquiries, each with a **<< Back to Summary** link. Counts are live formulas over the other sheets.
- Sheets: Summary, Stock Data, Enquiry Stock Match, Demand vs Stock, Ageing Stock - No Enquiry, one sheet per match
  status, and **Variant Ladder**. Match Status, Free - Physical / In Transit / Allocated, the Demand vs Stock counts
  and the Summary are formulas over *Stock Data* (grey *Key:* columns), so editing the stock rows recalculates them.
- **Next Variant (one step up)** columns, as in the Kia report: the next trim up on the same engine / gearbox / fuel, its
  free stock (physical and in transit) in the colour the customer asked for, its chassis numbers and an **Upsell Flag**
  (*UPSELL - only upgrade in stock* / *Upgrade also in stock*). Hyundai has no price master here, so the ladder follows
  the trim order in `TRIM_LADDERS` (top of `stock_engine.py`) - edit it when the trim line-up changes. Corporate, SE and
  unlisted trims are left out of the ladder.
- **Test Drive is not used** in the matching or the workbook (same as Kia). Every live enquiry is matched.

## 8. Putting it on GitHub and Render (so it's reachable from anywhere)

### ⚠️ First — a data privacy note

`Enquiry.xlsx`, `Booking.xlsx` and `SalesReport.xlsx` contain **real customer names,
phone numbers, addresses and PAN numbers**. GitHub repositories are public by default
unless you explicitly make them private, and even a private repo's history is still
sensitive to leave lying around.

This project's `.gitignore` already excludes `data/*.xlsx` for exactly this reason — git
will simply skip those three files when you commit, even though they're still sitting
on your own machine and the local dashboard keeps working normally. **Don't override
that.** For the deployed copy, load your real data in through the app itself (Section 5,
method 1) after it's live, not through git.

If you'd rather keep things simpler and are comfortable with the data being in git, make
the GitHub repository **Private**, and go to Section 8.3 to password-protect the site too
— but the recommended path is: empty `data/` folder in git, real files uploaded through
the running app.

### 8.1 Push this project to GitHub

```bash
cd hyundai_dashboard
git init
git add .
git commit -m "Hyundai ETBR Analysis dashboard"
```

Then create a new repository on [github.com/new](https://github.com/new) (Private
recommended), and push:

```bash
git remote add origin https://github.com/<your-username>/<your-repo>.git
git branch -M main
git push -u origin main
```

(If `git` isn't installed, install it from [git-scm.com](https://git-scm.com/downloads),
or use GitHub Desktop's "Add existing folder" instead of the commands above.)

### 8.2 Deploy to Render

1. Sign in at [render.com](https://render.com) (a GitHub login is quickest) and click
   **New +** → **Blueprint**.
2. Pick the repository you just pushed. Render reads `render.yaml` in this project and
   pre-fills everything: build command, start command, and a free plan.
3. Click **Apply** / **Create**. The first build takes a couple of minutes — watch the
   log for `Uvicorn running on http://0.0.0.0:$PORT`, which means it's live.
4. Render gives you a public URL like `https://unnati-hyundai-dashboard.onrender.com` —
   that's your dashboard, reachable from any phone, tablet or computer with internet.

If you'd rather not use the Blueprint, create a **Web Service** manually and set:
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`

**Load your real data onto the live site:** open the Render URL, click **Update monthly
data** in the sidebar, and upload your Enquiry workbook (and Stock workbook) there. They now live on Render's
server, not in your git repo.

**Free-tier caveats worth knowing:**
- Render's free web services spin down after ~15 minutes of no traffic and take 30–60
  seconds to wake back up on the next visit — normal, not a bug.
- Free-tier disk is *ephemeral*: any file uploaded through "Update monthly data" is
  lost when the service restarts or redeploys, and needs re-uploading. For data that
  survives restarts, add a **Persistent Disk** to the service (Render dashboard →
  your service → Disks) mounted at `/opt/render/project/src/data`, which is a small
  paid add-on.

### 8.3 Put a login on it

Because the deployed URL is reachable by anyone who has it, set a username and password
before sharing it around:

1. In the Render dashboard, open your service → **Environment**.
2. Add two environment variables: `DASHBOARD_USER` and `DASHBOARD_PASSWORD` (pick your
   own values).
3. Save — Render redeploys automatically. Visiting the site now prompts for that
   username/password (a standard browser login box) before showing anything.

Leaving these two variables unset (the default) keeps the site open with no login —
fine for local use, not recommended once it's public.

### 8.4 Updating the live site later

Whenever you want to ship a code change (not a data update — that's the upload button):
```bash
git add .
git commit -m "describe the change"
git push
```
Render watches the GitHub repo and redeploys automatically on every push to `main`.

## 9. Troubleshooting

- **"Address already in use"** (local) — another program is using port 8000; `run.py`
  will hop to 8001, 8002, etc. automatically.
- **A number looks wrong** — check the raw value in the relevant `.xlsx` file first;
  the dashboard recalculates live from whatever is in `data/`, so a bad number usually
  means the source export needs a closer look.
- **Uploaded file rejected** — only `.xlsx` / `.xlsm` files are accepted for upload.
- **Render: site takes ~30–60s to load the first time** — normal free-tier cold start
  after a period of inactivity; it's fast again once awake.
- **Render: my uploaded data disappeared** — expected on the free plan's ephemeral disk;
  see the caveats in Section 8.2. Add a Persistent Disk, or re-upload after each restart.
- **Render: dashboard asks for a password I didn't set** — someone (maybe a past you)
  set `DASHBOARD_USER` / `DASHBOARD_PASSWORD` in the Environment tab; edit or remove
  them there to change or disable the login.

## V12 changes

**Enquiry Follow-up page - "Booked enquiries by number of follow-ups"**
- 4 cards: Booked with 0 / 1 / 2 / 3 follow-ups (4+ is shown in the table and under the 3rd card).
- One table with Model wise / Consultant wise / Colour wise tabs.
- Click a card, a row, or a number to open a pop-up window with the full customer details
  (no charts) - click the tiles inside the pop-up to switch follow-up group, Export to CSV.
- Endpoint: `GET /api/followup/booked`  (code: `compute_booked_followups` in `data_processor.py`).
- The Enquiry sheet has no follow-up history, so the count is *estimated* from the enquiry -> booking gap
  (`FOLLOWUP_CADENCE_DAYS = 7` in `data_processor.py`). If you add a column such as **Follow up Count**
  to the Enquiry sheet, it is picked up automatically and the real number is used.

**Vehicle Stock - Avg. stock age fix**
- The workbook's `Stock Age` column counts from the plant *Sign Off Date* (it equals `Sign Age`), but the page
  says "Days since HMI invoice". Age is now worked out live as today - HMI Invoice Date
  (`stock_age_days()` in `stock_engine.py`; set `STOCK_AGE_BASIS = "file"` there to go back to the old way).
- Affects the Vehicle Stock page only (Avg. stock age, Aged 60+, Stock ageing chart, avg age in the tables).

**Follow-up table (latest):** the first number column is *Total enquiries* (all statuses, all months) per
Model / Consultant / Colour; the 0 / 1 / 2 / 3 / 4+ columns place EVERY enquiry in a follow-up group, so each row
adds up to its total. The 4 cards at the top count only the booked enquiries. Follow-ups are estimated (one per
`FOLLOWUP_CADENCE_DAYS` = 7 days from the enquiry date to the booking / retail / cancel date, or to the page date
if still open; a Lead = 0) unless the Enquiry sheet has a "Follow up Count" column, which is then used as-is.

**Life cycle (follow-up page):** Enquiry -> Booked (Booking Date filled) -> Retailed (Retail date filled = closed).
"Booked" = reached the booking stage and not cancelled, so vehicles that were booked and then retailed still count
(`BOOKED_EXCLUDES_CANCELLED` in `data_processor.py`). Follow-ups are counted up to the booking date (or cancel date, or
today while still open). The popup shows Stage, Booking date, Retail date and a Follow-up history timeline per enquiry.

## V13 changes

**Enquiry Follow-up - From / To date**
- The single "Follow-up date (today)" box is now **Follow-up from date** and **Follow-up to date** (both default to today;
  the To date can never be before the From date). API: `from_date` / `to_date` on `/api/followup` and `/api/followup/list`
  (the old `as_of` still works and means from = to = that day).
- Due = follow-ups dated from..to | Previous days pending = dated before the From date | Upcoming = dated after the To date.
  With From = To the numbers are exactly the same as before. Labels switch to "Due in selected dates" for a range.

**Vehicle Stock - unit-by-unit pop-up**
- Click a Model / Variant / Colour row (or a model under a colour, or the Total stock / Physical / In transit / Aged 60+ cards).
- Columns: HMI Invoice No, HMI Invoice Date, TAT (days since HMI invoice, as on today), Model, Variant, Exterior Color Name,
  Interior Color Desc, Vin Number, Order No, Stock Status, Fuel Type, Order Type (+ Stage). Click a heading to sort; Export to CSV.
- Endpoint: `GET /api/vehicle-stock/units` (`compute_vehicle_stock_units` in `data_processor.py`).

## V14 changes

**Enquiry page - "Enquiry date" filter** replaces "Enquiry aging days". It lists the dates (with enquiries) of the
selected Month and is rebuilt whenever the Month changes (back to "All dates" if the chosen date is not in that month).
It filters the KPI cards, charts and the Model / Consultant / Source tables. Backend: `age=date:YYYY-MM-DD`
(`_age_mask` in `data_processor.py`); the date list comes from `/api/filters` -> `enquiry_dates`.
The filter options are now re-read after Refresh / Update monthly data, so new months, consultants and dates appear at once.

**V15 - Follow-up page:** the separate "Cancels in month" dropdown is removed. The two cancel cards and the
"Cancelled enquiries" list now count enquiries whose Lost Date falls between the From and To dates, so every number on
the page follows the same dates (`_cancel_view` in `data_processor.py`).

## V16 changes

**Month Comparison - MTD (Month-to-Date) toggle**
- New **MTD** button next to Reset filters. Off (default) = exactly the previous full-month-vs-full-month
  comparison, completely unchanged. Pressed = both months are cut off at **today's day-of-month** before
  comparing - e.g. on 3 Oct, that's the 1st-3rd of October against the 1st-3rd of September, not a full
  September against three days of October. This is what fixes the misleading "-97.8%"-style drops that show
  up for a few days right after a new month starts, when the real story is often the opposite (a new month
  can easily be *ahead* of where the previous one was at the same point).
- The table headers, the title, and the subtitle all relabel themselves while MTD is on (e.g. "October 2026
  (1-3)"), so it's never ambiguous which numbers you're looking at. Model / Consultant / Source filters and
  the Month dropdown keep working exactly as before and compose with MTD normally.
- Scoped to the Month Comparison page only - the vs-last-month delta badges on Overview, Enquiry, Test Drive,
  Booking and Retails are untouched and keep comparing full months, exactly as before.
- Backend: `compute_comparison(..., mtd: bool)` in `data_processor.py`, which forwards a `mtd_day` cutoff
  through `compute_kpis()` -> `DashboardData.view()` -> `DashboardData._filter()`, which filters each row's
  *actual date* (Enquiry Date / Booking Date / Retail date / Lost Date, via `_MONTH_COL_TO_DATE_COL`) to
  `day <= mtd_day`, on top of the existing month match. API: `GET /api/comparison?...&mtd=true`.

## V17 changes

**Model / Variant / Month dropdowns in every pop-up window**

All three pop-ups - the Model Window (Overview/Enquiry/Test Drive/Booking/Retails), the Enquiry Follow-up
window, and the Vehicle Stock window - now have a toolbar with **Model**, **Variant** and **Month** dropdowns,
so you can drill into a precise slice without closing the window and clicking a different row. Each pop-up's
existing way of opening and filtering (clicking a bar, a KPI card, a table row or cell) is completely
unchanged - the three dropdowns are an additional, independent way to narrow the same data, and they combine
with whatever the window was already scoped to:

- **Model Window (`mvModal`)** already had a Model dropdown. Variant is now a second dropdown next to it
  (in addition to the existing click-a-bar-on-the-chart multi-select, which still works exactly as before -
  the dropdown is a quick, precise single pick; the chart is still there for multi-select). Variant switching
  is instant (pure client-side, same cached record set). Month switching re-downloads the record set for the
  new month (the only one of the three that needs a server round-trip, since the record set itself is
  scoped to one month) - everything else in the window (KPIs, charts, tables) then recomputes exactly as it
  already did for a model/variant change.
- **Enquiry Follow-up window (`fbModal`)** had no dropdowns before. Its underlying data already includes
  every month and every model (only Model/Consultant/Source and the as-of date were ever sent to the
  server), so all three new dropdowns are pure client-side filters - no backend change at all.
  `fbWindowRows()` keeps its original dim/label/bookedOnly scoping (`fbScopedRows()`, verbatim) and layers
  Variant/Month on top; the Model dropdown sets the same `dim`/`label` a row click already would.
- **Vehicle Stock window (`svModal`)** had no dropdowns before, though it already had an internal
  Model/Variant/Colour "dim" driven by clicking a table row. The new dropdowns are a *separate*, combinable
  layer (`fModel`/`fVariant`/`fMonth` on `svState.win`) on top of that existing click-driven scope
  (`svBaseRows()`, verbatim) - so you can, for example, click a Colour row and then use the dropdowns to
  additionally narrow to one model and one month. Month is derived from each unit's HMI Invoice Date.
- In all three, picking a value from one dropdown narrows what the next one offers (e.g. picking a Model
  narrows the Variant list to that model's variants), and every dropdown always has an "All ..." option to
  go back. Nothing about the pages behind the pop-ups, or any other part of the dashboard, changed.

## V18 changes

**Sub-source dropdown on every page**

- A **Sub-source** dropdown now sits right after **Source** in the filter bar of Overview, Enquiry, Enquiry
  Follow-up, Test Drive, Booking, Retails, Exchange, Enquiry Wise Stock and Month Comparison. It reads the
  `Sub-source` column of the Enquiry sheet (27 values in the current file). Vehicle Stock has no Source filter
  (stock has no enquiry data), so it has no Sub-source either, and the three pop-up windows do not get the dropdown.
- **It follows Source.** Every Sub-source belongs to exactly one Source (Website sits under Digital, Walkin under
  Walkin, SC own source under Field Generation ...). Pick a Source and the Sub-source list narrows to that Source's
  sub-sources; pick "All sources" and the full list returns. If you change the Source and the Sub-source you had
  chosen no longer belongs to it, the Sub-source goes back to "All sub-sources" and the page reloads. The Source ->
  Sub-source map is built from your data (`/api/filters` -> `sub_source_map`), nothing is hard-coded.
- **It combines with the other filters** (Month, Model, Consultant, Source, Enquiry date ...) and "Reset filters" clears it.
  It works through the same code path as Source: `sub_source` is accepted by every API route that accepts `source`
  (`DashboardData._filter()` / `view()` do the filtering), including the Enquiry Wise Stock Excel download, so the
  file you download matches what is on screen. A workbook with no `Sub-source` column simply shows an empty list.
- **Pop-ups:** no dropdown inside them, but they describe the same filtered page. If the page is filtered to
  "Website" and you open the Model Window, its numbers match the page and its subtitle names "Website".
- **Filter bars now use an aligned grid** (`.filter-bar` in `style.css`): the dropdowns share each row evenly and,
  when the bar has to wrap, keep their columns aligned (it used to wrap into ragged rows of different widths). On
  phones it keeps the earlier 2-column layout with Reset / MTD / Download on their own full-width rows.
- Nothing else changed: with Sub-source left on "All", every API response is identical to V17 apart from one new
  `sub_source` entry in the Month Comparison `filters` block.

## V19 changes

**Enquiry Wise Stock - month-wise analysis**

- The page used to match every live enquiry from every month together, with no way to see which months were in
  the file. It now shows them. In the current file the enquiries cover **September 2026** (734 in the file, 687
  matched) and **October 2026** (59, all matched): 793 enquiries, 746 matched, 47 not matched.
- **Enquiry month** dropdown (first in the filter bar): "All months" plus every month that has enquiries, newest
  first, each with its matched count. "All months" is the default and gives exactly the numbers the page gave
  before. Picking a month filters the whole page - cards, charts, the enquiry list, Demand vs Stock, free-stock
  coverage - and the **Download Excel** file, so the download always matches the screen. It combines with Status,
  Model, Consultant, Source and Sub-source, and "Reset filters" clears it.
- **Month-wise analysis** panel (new, right under the KPI cards): one row per enquiry month and an "All months"
  total row - enquiries in the file, matched, not matched, how the matched ones split across the seven match
  statuses (Exact Physical / In transit / Allocated, Other colour, Other variant, No stock, Variant not captured),
  and three rates: **Can serve now** (exact car physically here), **Exact incl. transit**, **Needs indent**
  (no stock for the model). A stacked chart shows the same split per month, and a note spells out what was
  not matched (for example Retail 30, Appointed Enquiry Cancel 6 ...). Click a month row to filter the page to
  it; click it again to go back. The panel always shows every month, even while one is picked.
- Stock is today's snapshot, so an older month's enquiries are checked against the stock held now ("could we
  serve September's enquiries today?"). Matching rules are unchanged - the month figures come from the same
  `build_match()` as the rest of the page, so a month's row equals what the page shows when filtered to it.
- Backend: `/api/enquiry-stock` and `/api/enquiry-stock/export` accept `month=YYYY-MM`; the response gains
  `selected_month`, `months`, `month_rows`, `month_total`, `month_excluded` (everything it returned before is
  untouched). The per-month counts are remembered per filter set (`_MONTH_MATCH_CACHE`) so clicking between
  months is instant after the page has opened.
