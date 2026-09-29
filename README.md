# Hyundai ETBR Analysis
### Unnati Hyundai — Dealership Performance Dashboard

A self-hosted analytics dashboard for your **Enquiry**, **Booking** and **Sales Report**
exports, built around **Test Drive tracking** (the Y/N flag in column O of the Enquiry
file), with automatic **current-month vs previous-month** comparison.

Stack: **Python + FastAPI** (backend/API) · **HTML/CSS/JavaScript + Chart.js** (frontend).
No database. Works fully offline on your own machine, or deployed to the web (see
Section 8) — either way, mobile and desktop browsers are both fully supported.

---

## 1. What's inside

```
hyundai_dashboard/
├── main.py                 FastAPI app: API routes + serves the frontend
├── data_processor.py       All data-loading & metric logic (pandas)
├── run.py                  Local launcher — starts the server AND opens your browser
├── render.yaml             Render deployment blueprint (see Section 8)
├── requirements.txt        Python dependencies
├── .gitignore              Keeps your real customer data out of git (see Section 8)
├── start_dashboard.bat     Windows: double-click to install deps + run
├── start_dashboard.sh      macOS/Linux: same, from a terminal
├── data/                   Put your 3 workbooks here (already pre-loaded with yours)
│   ├── Enquiry.xlsx
│   ├── Booking.xlsx
│   └── SalesReport.xlsx
└── static/                 Frontend
    ├── index.html
    ├── css/style.css
    └── js/
        ├── app.js
        └── vendor/chart.umd.js   (bundled locally — no CDN dependency)
```

## 2. Requirements

- Python 3.9 or newer
- That's it. Chart.js is bundled in the project (`static/js/vendor/`), so the dashboard
  works with no internet connection at all once installed — including on a phone
  connected to the same Wi-Fi as the machine it's running on.

## 3. Running it locally

**Windows** — double-click `start_dashboard.bat`.

**macOS / Linux**
```bash
cd hyundai_dashboard
chmod +x start_dashboard.sh      # first time only
./start_dashboard.sh
```

**Manually, on any OS**
```bash
cd hyundai_dashboard
pip install -r requirements.txt
python run.py
```

Your browser opens automatically at `http://127.0.0.1:8000`. Leave the terminal window
open — closing it stops the dashboard. Press `Ctrl+C` in the terminal to stop it
yourself.

If port 8000 is busy, `run.py` automatically tries the next free port and opens the
browser to the right one.

**To open it from your phone on the same Wi-Fi:** start it with `HOST=0.0.0.0 python
run.py`, then find your computer's local IP (Windows: `ipconfig`, look for IPv4 Address;
Mac: `ifconfig | grep inet`) and visit `http://<that-ip>:8000` from your phone's browser.

## 4. Mobile support

Every screen — KPI cards, charts, the test-drive gauge, the comparison table, the
upload dialog — is responsive down to small phone widths. On narrow screens the sidebar
becomes a slide-out menu (tap ☰ to open, tap outside it or a menu item to close), KPI
cards drop to a 2- or 1-column grid, and wide tables scroll horizontally within their
own box instead of the whole page. No separate "mobile site" — it's the same dashboard.

## 5. Updating data every month ("henceforth")

You do **not** need to touch any code each month. Two ways to refresh:

1. **In the dashboard:** click **Update monthly data** in the sidebar, pick the new
   `Enquiry.xlsx` / `Booking.xlsx` / `SalesReport.xlsx` (you can upload just one, two,
   or all three), and click **Upload & recalculate**. Every chart, KPI and the
   month-comparison table updates immediately.
2. **On disk:** replace the files inside the `data/` folder yourself and click
   **Refresh** in the sidebar.

### How the month comparison works
The dashboard doesn't use today's calendar date to decide "current month" — it looks at
the **latest date actually present** across your three files and treats that as the
current month; the calendar month before it is "previous month." So the very first time
you run this (with only September data, no August data), the comparison table will
correctly show **August = 0** for everything, with a note explaining that. The moment
you upload a file that contains — or is replaced by — the next month's export, the
comparison starts working with real numbers on both sides, with no setup needed.

The **Month Comparison** page also has its own **Month / Model / Consultant / Source**
dropdowns, so you can compare e.g. just New Creta enquiries, or just one consultant's
numbers, month over month — independent of whatever the rest of the dashboard is showing.

**Tip:** for the comparison to have real history, either (a) re-export each workbook so
it cumulatively contains all months to date, or (b) simply keep uploading each new
month's file — the dashboard always compares the newest month it can see against the
one before it.

## 6. What each section shows

- **Overview** — headline KPIs (with vs-last-month deltas), the enquiry → booking →
  retail funnel, a test-drive completion gauge, top models, and source mix.
- **Enquiry** — status breakdown, ageing buckets, top cities, lost reasons.
- **Test Drive** (the core ask) — done (Y) vs not-done (N) counts, daily trend, breakdown
  by model / consultant / source, and a funnel showing how many test-driven customers
  went on to book and to buy (matched by Customer ID across the three files).
- **Booking** — mode of purchase, by consultant, by model, daily trend.
- **Retails** — revenue and units by model, revenue trend, retail source mix.
- **Month Comparison** — every key metric, previous month vs current month, with %
  change, filterable by month / model / consultant / source.

Use the **period selector** in the sidebar to switch any section between *Current
month*, *Last month*, or *All available data* — the KPI cards' vs-last-month badges
always compare the two most recent months, regardless of which period you're browsing.

## 7. Data assumptions (edit `data_processor.py` if yours differ)

- Dates in all three files are text in `dd/mm/yyyy` format.
- Money fields may contain commas (`"681,610"`) — these are cleaned automatically.
- `Test Drive` column in Enquiry.xlsx holds only `Y` / `N`; anything else is treated as `N`.
- Enquiry ↔ Booking ↔ Sales rows are linked by `Customer ID` / `CustomerID` for the
  test-drive-to-sale funnel.
- "Lost" enquiries are rows whose `Enquiry Status` contains the word "Cancel".

If your exports use different column names, the loader functions in `data_processor.py`
(`_load_enquiry`, `_load_booking`, `_load_sales`) are the only place you need to edit —
everything downstream reads from the cleaned dataframes they produce.

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
data** in the sidebar, and upload your three files there. They now live on Render's
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
