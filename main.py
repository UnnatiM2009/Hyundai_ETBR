"""
main.py
-------
FastAPI backend for the Hyundai Enquiry / Test-Drive / Booking / Sales dashboard.

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

app = FastAPI(title="Unnati Hyundai Dealership Dashboard API", version="1.0.0")

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
            headers={"WWW-Authenticate": 'Basic realm="Unnati Hyundai Dashboard"'},
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
):
    return dp.compute_kpis(_period_param(period), model=model, consultant=consultant, source=source)


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
):
    if section not in ("overview", "enquiry", "testdrive", "booking", "sales", "conversion"):
        raise HTTPException(400, f"Unknown section '{section}'")
    return dp.compute_breakdown_tables(section, _period_param(period), model=model, consultant=consultant, source=source)


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
):
    return dp.compute_enquiry_analytics(_period_param(period), model=model, consultant=consultant, source=source)


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


@app.post("/api/refresh")
def api_refresh():
    dp.store.reload()
    return {"status": "ok", "message": "Data reloaded from disk.", "meta": dp.compute_meta()}


@app.post("/api/upload")
async def api_upload(
    enquiry: Optional[UploadFile] = File(default=None),
    booking: Optional[UploadFile] = File(default=None),
    sales: Optional[UploadFile] = File(default=None),
):
    """Replace one or more of the source workbooks, then reload everything.
    This is how the dealership pushes each new month's export into the
    dashboard going forward, without touching any code."""
    saved = []
    targets = {
        "enquiry": (enquiry, dp.ENQUIRY_FILE),
        "booking": (booking, dp.BOOKING_FILE),
        "sales": (sales, dp.SALES_FILE),
    }
    for name, (upload, dest_path) in targets.items():
        if upload is None:
            continue
        if not upload.filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(400, f"'{name}' must be an .xlsx file, got '{upload.filename}'")
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved.append(name)

    if not saved:
        raise HTTPException(400, "No files were provided.")

    dp.store.reload()
    return {"status": "ok", "updated": saved, "meta": dp.compute_meta()}


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
