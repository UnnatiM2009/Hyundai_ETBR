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
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

import data_processor as dp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="Hyundai ETBR Analysis API", version="1.0.0")

app.add_middleware(GZipMiddleware, minimum_size=800)   # smaller downloads on slow / mobile networks
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from auth import install_auth

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
    sub_source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    return dp.compute_kpis(_period_param(period), model=model, consultant=consultant, source=source, age=age, sub_source=sub_source)


@app.get("/api/filters")
def api_filters():
    return dp.store.filter_options()


@app.get("/api/comparison")
def api_comparison(
    month: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    mtd: bool = Query(default=False),
):
    return dp.compute_comparison(period=month, model=model, consultant=consultant, source=source, mtd=mtd, sub_source=sub_source)


@app.get("/api/breakdown")
def api_breakdown(
    section: str = Query(...),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    if section not in ("overview", "enquiry", "testdrive", "booking", "sales", "conversion", "exchange"):
        raise HTTPException(400, f"Unknown section '{section}'")
    return dp.compute_breakdown_tables(section, _period_param(period), model=model, consultant=consultant,
                                       source=source, age=age, sub_source=sub_source)


@app.get("/api/model-variants")
def api_model_variants(
    model: str = Query(...),
    period: Optional[str] = Query(default="current_month"),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
    variant: Optional[List[str]] = Query(default=None),
    ages: Optional[List[str]] = Query(default=None),
    dim: Optional[str] = Query(default="model"),
    model_filter: Optional[str] = Query(default=None),
):
    """Variant-wise detail window on the Overview page.
    dim=model (default): `model` is the clicked model.
    dim=consultant / dim=source: `model` carries the clicked consultant / source name."""
    if not model or model == "all":
        raise HTTPException(400, "A specific value is required")
    if dim not in ("model", "consultant", "source"):
        raise HTTPException(400, "dim must be model, consultant or source")
    return dp.compute_model_variant_detail(model, _period_param(period), consultant=consultant, source=source,
                                           age=age, variants=variant, ages=ages,
                                           dim=dim, model_filter=model_filter, sub_source=sub_source)


@app.get("/api/model-variants/records")
def api_model_variants_records(
    model: str = Query(...),
    period: Optional[str] = Query(default="current_month"),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
    dim: Optional[str] = Query(default="model"),
):
    """One compact download that powers the whole detail window; the browser then filters
    by variant / ageing / model instantly with no further server calls."""
    if not model or model == "all":
        raise HTTPException(400, "A specific value is required")
    if dim not in ("model", "consultant", "source"):
        raise HTTPException(400, "dim must be model, consultant or source")
    return dp.compute_model_variant_records(model, _period_param(period), consultant=consultant,
                                            source=source, age=age, dim=dim, sub_source=sub_source)


@app.get("/api/test-drive")
def api_test_drive(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
):
    return dp.compute_test_drive_analytics(_period_param(period), model=model, consultant=consultant, source=source,
                                          sub_source=sub_source)


@app.get("/api/enquiry")
def api_enquiry(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    age: Optional[str] = Query(default=None),
):
    return dp.compute_enquiry_analytics(_period_param(period), model=model, consultant=consultant,
                                        source=source, age=age, sub_source=sub_source)


@app.get("/api/exchange")
def api_exchange(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    scope: str = Query(default="exchange"),
):
    if scope not in dp.EXCHANGE_SCOPES:
        raise HTTPException(400, f"Unknown scope '{scope}'")
    return dp.compute_exchange_analytics(_period_param(period), model=model, consultant=consultant,
                                         source=source, scope=scope, sub_source=sub_source)


@app.get("/api/booking")
def api_booking(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
):
    return dp.compute_booking_analytics(_period_param(period), model=model, consultant=consultant, source=source,
                                       sub_source=sub_source)


@app.get("/api/sales")
def api_sales(
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
):
    return dp.compute_sales_analytics(_period_param(period), model=model, consultant=consultant, source=source,
                                     sub_source=sub_source)


@app.get("/api/followup")
def api_followup(
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD; defaults to today"),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, start of the follow-up date range"),
    to_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, end of the follow-up date range"),
):
    return dp.compute_followup(as_of, _period_param(period), model=model, consultant=consultant, source=source,
                               from_date=from_date, to_date=to_date, sub_source=sub_source)


@app.get("/api/followup/list")
def api_followup_list(
    scope: str = Query(default="today"),
    as_of: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, used when scope=date"),
    period: Optional[str] = Query(default="current_month"),
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
):
    if scope not in ("today", "pending", "upcoming", "all", "date", "followup_cancel", "appointed_cancel"):
        raise HTTPException(400, f"Unknown scope '{scope}'")
    return dp.compute_followup_list(scope, as_of, date, _period_param(period),
                                    model=model, consultant=consultant, source=source,
                                    from_date=from_date, to_date=to_date, sub_source=sub_source)


@app.get("/api/followup/booked")
def api_followup_booked(
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    as_of: Optional[str] = Query(default=None, description="YYYY-MM-DD; the 'today' for open enquiries"),
):
    """Every enquiry by number of follow-ups (0 / 1 / 2 / 3 / 4+) - the booked cards, the Model /
    Consultant / Colour table and the full customer list for the pop-up."""
    return dp.compute_booked_followups(model=model, consultant=consultant, source=source, as_of=as_of,
                                      sub_source=sub_source)


@app.get("/api/enquiry-stock")
def api_enquiry_stock(
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="Enquiry Status to match; default = all live enquiries"),
    month: Optional[str] = Query(default=None, description="Enquiry month YYYY-MM; default = every month"),
):
    return dp.compute_enquiry_stock(model=model, consultant=consultant, source=source, status=status,
                                    sub_source=sub_source, month=month)


@app.get("/api/enquiry-stock/export")
def api_enquiry_stock_export(
    model: Optional[str] = Query(default=None),
    consultant: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sub_source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None),
):
    try:
        content = dp.export_enquiry_stock(model=model, consultant=consultant, source=source, status=status,
                                          sub_source=sub_source, month=month)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Hyundai_Enquiry_Wise_Stock.xlsx"'},
    )


@app.get("/api/vehicle-stock/filters")
def api_vehicle_stock_filters():
    return dp.store.vehicle_stock_filter_options()


@app.get("/api/vehicle-stock/kpis")
def api_vehicle_stock_kpis(
    model: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    fuel_type: Optional[str] = Query(default=None),
    financier: Optional[str] = Query(default=None),
):
    return dp.compute_vehicle_stock_kpis(model=model, stage=stage, fuel_type=fuel_type, financier=financier)


@app.get("/api/vehicle-stock/analytics")
def api_vehicle_stock_analytics(
    model: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    fuel_type: Optional[str] = Query(default=None),
    financier: Optional[str] = Query(default=None),
):
    return dp.compute_vehicle_stock_analytics(model=model, stage=stage, fuel_type=fuel_type, financier=financier)


@app.get("/api/vehicle-stock/breakdown")
def api_vehicle_stock_breakdown(
    model: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    fuel_type: Optional[str] = Query(default=None),
    financier: Optional[str] = Query(default=None),
):
    return dp.compute_vehicle_stock_breakdown(model=model, stage=stage, fuel_type=fuel_type, financier=financier)


@app.get("/api/vehicle-stock/units")
def api_vehicle_stock_units(
    model: Optional[str] = Query(default=None),
    stage: Optional[str] = Query(default=None),
    fuel_type: Optional[str] = Query(default=None),
    financier: Optional[str] = Query(default=None),
):
    """Unit-level stock list (invoice no/date, TAT, model, variant, colours, VIN, order no ...) for the pop-up."""
    return dp.compute_vehicle_stock_units(model=model, stage=stage, fuel_type=fuel_type, financier=financier)


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
    return {"status": "ok", "build": "V4"}


# --------------------------------------------------------------------------- #
# Frontend (static files)
# --------------------------------------------------------------------------- #

class _NoCacheStatic(StaticFiles):
    """Always revalidate the dashboard's own JS/CSS so an updated file is picked up on the next
    page load (browsers otherwise keep serving a stale app.js after an upgrade)."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


app.mount("/assets", _NoCacheStatic(directory=STATIC_DIR), name="assets")


@app.get("/logo.png")
def brand_logo():
    """Hyundai logo shown on the login page and the dashboard sidebar (file lives in data/Logo.png)."""
    return FileResponse(os.path.join(BASE_DIR, "data", "Logo.png"), media_type="image/png",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache, must-revalidate"})


@app.exception_handler(404)
async def not_found(request, exc):
    # Let API misses fail as JSON; anything else falls back to the SPA page
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

install_auth(app, STATIC_DIR)
