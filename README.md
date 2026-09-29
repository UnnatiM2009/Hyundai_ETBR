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
- **Enquiry Wise Stock** *(new)* — see below.
- **Month Comparison** — every key metric, previous vs current month, filterable.

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

---

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
