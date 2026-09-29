"""
main.py
-------
FastAPI backend for the Hyundai Enquiry / Test-Drive / Booking / Retail / Follow-up /
Enquiry-wise-Stock dashboard. Booking and Retail come from the Enquiry sheet's `Enquiry Status`.

Run with:  python run.py     (auto-opens the browser)
       or:  uvicorn main:app --reload

Deployment (Render, etc.): the start command is
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
import base64
import secrets
import shutil
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

import data_processor as dp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="Hyundai ETBR Analysis API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Optional password protection
#
# Once this app is hosted somewhere public (Render, etc.) its URL is reachable
# by anyone — and the data behind it is real customer names, phone numbers,
# addresses and revenue figures. Setting DASHBOARD_USER and DASHBOARD_PASSWORD
# as environment variables locks the whole site behind a login prompt.
# Leave them unset (the default for local use) and the app behaves exactly as
# before, with no login required.
# --------------------------------------------------------------------------- #
DASHBOARD_USER = os.environ.get("DASHBOARD_USER")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not DASHBOARD_USER or not DASHBOARD_PASSWORD:
            return await call_next(request)  # auth not configured — open access

        if request.url.path == "/api/health":
            return await call_next(request)  # always open, for uptime/health checks

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                user, _, pwd = decoded.partition(":")
                if secrets.compare_digest(user, DASHBOARD_USER) and secrets.compare_digest(pwd, DASHBOARD_PASSWORD):
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            status_code=401,
            content="Authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="Hyundai ETBR Analysis"'},
        )


if DASHBOARD_USER and DASHBOARD_PASSWORD:
    app.add_middleware(BasicAuthMiddleware)


def _period_param(period: Optional[str]) -> Optional[str]:
    return dp.store.resolve_period(period)


# --------------------------------------------------------------------------- #
# API routes
# --------------------------------------------------------------------------- #

@app.get("/api/meta")
def api_meta():
    return dp.compute_meta()


@app.get("/api/kpis")
def api_kpis(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    return dp.compute_kpis(_period_param(period), model=model, consultant=consultant, source=source, age=age)


@app.get("/api/filters")
def api_filters():
    return dp.store.filter_options()


@app.get("/api/comparison")
def api_comparison(
    month: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return dp.compute_comparison(period=month, model=model, consultant=consultant, source=source)


@app.get("/api/breakdown")
def api_breakdown(
    section: str = Query(...),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    if section not in ("overview", "enquiry", "testdrive", "booking", "sales", "conversion", "exchange"):
        raise HTTPException(400, f"Unknown section '{section}'")
    return dp.compute_breakdown_tables(section, _period_param(period), model=model, consultant=consultant,
                                       source=source, age=age)


@app.get("/api/test-drive")
def api_test_drive(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return dp.compute_test_drive_analytics(_period_param(period), model=model, consultant=consultant, source=source)


@app.get("/api/enquiry")
def api_enquiry(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    return dp.compute_enquiry_analytics(_period_param(period), model=model, consultant=consultant,
                                        source=source, age=age)


@app.get("/api/exchange")
def api_exchange(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    scope: str = Query(default="exchange"),
):
    if scope not in dp.EXCHANGE_SCOPES:
        raise HTTPException(400, f"Unknown scope '{scope}'")
    return dp.compute_exchange_analytics(_period_param(period), model=model, consultant=consultant,
                                         source=source, scope=scope)


@app.get("/api/booking")
def api_booking(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return dp.compute_booking_analytics(_period_param(period), model=model, consultant=consultant, source=source)


@app.get("/api/sales")
def api_sales(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return dp.compute_sales_analytics(_period_param(period), model=model, consultant=consultant, source=source)


@app.get("/api/followup")
def api_followup(
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD; defaults to today"),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return dp.compute_followup(as_of, _period_param(period), model=model, consultant=consultant, source=source)


@app.get("/api/followup/list")
def api_followup_list(
    scope: str = Query(default="today"),
    as_of: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, used when scope=date"),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    if scope not in ("today", "pending", "upcoming", "all", "date", "followup_cancel", "appointed_cancel"):
        raise HTTPException(400, f"Unknown scope '{scope}'")
    return dp.compute_followup_list(scope, as_of, date, _period_param(period),
                                    model=model, consultant=consultant, source=source)


@app.get("/api/enquiry-stock")
def api_enquiry_stock(
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="Enquiry Status to match; default = all live enquiries"),
):
    return dp.compute_enquiry_stock(model=model, consultant=consultant, source=source, status=status)


@app.get("/api/enquiry-stock/export")
def api_enquiry_stock_export(
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    try:
        content = dp.export_enquiry_stock(model=model, consultant=consultant, source=source, status=status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Hyundai_Enquiry_Wise_Stock.xlsx"'},
    )


@app.post("/api/refresh")
def api_refresh():
    dp.store.reload()
    return {"status": "ok", "message": "Data reloaded from disk.", "meta": dp.compute_meta()}


def _validate_enquiry_file(path: str):
    """The Enquiry sheet must carry the columns the whole dashboard is derived from.
    The same workbook may also hold 'Physical Stock' and 'In Transit' sheets."""
    import pandas as pd
    sheet = dp.pick_enquiry_sheet(path)
    df = pd.read_excel(path, sheet_name=sheet)
    cols = {str(c).strip() for c in df.columns}
    missing = [c for c in ("Enquiry Date", "Enquiry Status", "Model") if c not in cols]
    if missing:
        raise ValueError("this doesn't look like the Enquiry export - missing column(s): " + ", ".join(missing))
    if df.dropna(how="all").empty:
        raise ValueError(f"the '{sheet}' sheet has no rows")


def _validate_stock_file(path: str):
    import stock_engine as se
    se.load_stock(path)   # raises ValueError with a readable message if unusable


@app.post("/api/upload")
async def api_upload(
    enquiry: Optional[UploadFile] = File(default=None),
    stock: Optional[UploadFile] = File(default=None),
):
    """Replace the Enquiry workbook and/or the Stock workbook, then reload everything.
    Booking and Retail need no file of their own - they come from the Enquiry sheet's
    `Enquiry Status` column (Booked / Retail).

    Each file is checked BEFORE it replaces the current one, so a wrong file can never
    wipe out the data that is already loaded."""
    saved = []
    stock_from_workbook = False
    targets = {
        "enquiry": (enquiry, dp.ENQUIRY_FILE, _validate_enquiry_file),
        "stock": (stock, dp.STOCK_FILE, _validate_stock_file),
    }
    os.makedirs(dp.DATA_DIR, exist_ok=True)
    for name, (upload, dest_path, validate) in targets.items():
        if upload is None or not upload.filename:
            continue
        if not upload.filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(400, f"'{name}' must be an .xlsx file, got '{upload.filename}'")
        tmp_path = dest_path + ".uploading"
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        try:
            validate(tmp_path)
        except Exception as exc:
            os.remove(tmp_path)
            raise HTTPException(400, f"'{upload.filename}' was not loaded ({name}): {exc}. "
                                     f"The previous {name} file is unchanged.")
        os.replace(tmp_path, dest_path)
        saved.append(name)
        if name == "enquiry":
            import stock_engine as se
            if se.has_stock_sheets(dest_path):
                # the new workbook brings its own stock: an older separate Stock.xlsx must not shadow it
                # (a Stock.xlsx uploaded in the same request is saved afterwards and wins)
                if os.path.exists(dp.STOCK_FILE):
                    os.remove(dp.STOCK_FILE)
                stock_from_workbook = True

    if not saved:
        raise HTTPException(400, "No files were provided.")

    dp.store.reload()
    warning = None
    if ("stock" in saved or stock_from_workbook) and dp.store.stock_info.get("warnings"):
        warning = " ".join(dp.store.stock_info["warnings"])
    if stock_from_workbook and "stock" not in saved:
        if dp.store.stock.empty:
            warning = ((warning + " ") if warning else "") + \
                "The workbook's stock sheets could not be read: " + str(dp.store.stock_info.get("error", "no vehicles found"))
        else:
            saved.append("stock (from the workbook)")
    return {"status": "ok", "updated": saved, "warning": warning, "meta": dp.compute_meta()}


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Frontend (static files)
# --------------------------------------------------------------------------- #

app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.exception_handler(404)
async def not_found(request, exc):
    # Let API misses fail as JSON; anything else falls back to the SPA page
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
