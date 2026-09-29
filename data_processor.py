"""
data_processor.py
------------------
All data-loading and metric-calculation logic for the Hyundai Enquiry /
Test Drive / Booking / Retail / Follow-up / Enquiry-wise-Stock dashboard.

ONE source of truth: the Enquiry workbook. Booking and Retail are no longer
separate exports - they are read from the `Enquiry Status` column:

    Enquiry Status              meaning
    ------------------------    -----------------------------------------
    Booked                      a booking            (dated by Booking Date)
    Retail                      a vehicle sold       (dated by Retail date)
    Booking Cancel              a cancelled booking  (dated by Lost Date)
    Enquiry Follow up           open follow-up       (Next Followup Date)
    Enquiry Follow up Cancel    lost at follow-up    (dated by Lost Date)
    Appointed Enquiry           appointment fixed
    Appointed Enquiry Cancel    appointment cancelled (dated by Lost Date)
    Lead                        new lead

The optional Stock workbook (Physical / In Transit) feeds the Enquiry Wise
Stock page - see stock_engine.py.

Design goals (unchanged from earlier versions):
- Be tolerant of dd/mm/yyyy text dates and comma-formatted numbers.
- Work out "current month" / "previous month" from the data itself, so the
  month comparison keeps working as new exports are dropped into /data.
- Expose compute_* functions returning plain dict/list structures that
  FastAPI can JSON-serialise.
"""

from __future__ import annotations

import os
import datetime as dt
from typing import Optional

import pandas as pd
import numpy as np

import stock_engine as se

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

ENQUIRY_FILE = os.path.join(DATA_DIR, "Enquiry.xlsx")
STOCK_FILE = os.path.join(DATA_DIR, "Stock.xlsx")

CANCEL_KEYWORDS = ("cancel",)  # substring match, case-insensitive, on Enquiry Status


# --------------------------------------------------------------------------- #
# Enquiry Status -> business meaning.  Edit these if the DMS renames a status.
# (Compared case-insensitively, extra spaces ignored.)
# --------------------------------------------------------------------------- #

def _skey(value) -> str:
    return " ".join(str(value).lower().split())


BOOKING_STATUSES = {_skey("Booked")}
RETAIL_STATUSES = {_skey("Retail")}
BOOKING_CANCEL_STATUSES = {_skey("Booking Cancel")}
FOLLOWUP_STATUSES = {_skey("Enquiry Follow up")}
FOLLOWUP_CANCEL_STATUSES = {_skey("Enquiry Follow up Cancel")}
APPOINTED_STATUSES = {_skey("Appointed Enquiry")}
APPOINTED_CANCEL_STATUSES = {_skey("Appointed Enquiry Cancel")}

# Enquiries that are still "live" and therefore worth matching against stock
# (a booked customer is waiting for a car too). Sold and cancelled are excluded.
STOCK_MATCH_STATUSES = ["Enquiry Follow up", "Appointed Enquiry", "Lead", "Booked"]

# Columns the rest of the code relies on. Missing ones are created blank so an
# export with a slightly different layout degrades gracefully instead of crashing.
_TEXT_COLUMNS = {
    "Customer ID": "", "Name of the Customer": "", "Contact Number": "", "Model": "Unknown",
    "Variant": "", "Fuel type": "", "Color": "", "Enquiry Status": "Unknown", "Source": "Unknown",
    "Consultant Name": "Unassigned", "City": "Unknown", "lost reason": "", "Lost Remark": "",
    "Consultant Remarks": "",
}
_DATE_COLUMNS = ["Enquiry Date", "Next Followup Date", "Lost Date", "Booking Date", "Retail date"]


# --------------------------------------------------------------------------- #
# Low level helpers
# --------------------------------------------------------------------------- #

def pick_enquiry_sheet(path: str):
    """The sheet holding the enquiries. The workbook may also carry 'Physical Stock' and
    'In Transit' sheets, so choose by name, then by the Enquiry Status heading, then first."""
    xl = pd.ExcelFile(path)
    names = xl.sheet_names
    for n in names:
        if n.strip().lower() == "enquiry":
            return n
    for n in names:
        if "enquiry" in n.strip().lower() or "enquiery" in n.strip().lower():
            return n
    for n in names:
        try:
            head = xl.parse(n, nrows=1)
        except Exception:
            continue
        if "Enquiry Status" in {str(c).strip() for c in head.columns}:
            return n
    return names[0]


def _parse_date(series: Optional[pd.Series]) -> pd.Series:
    """Parse dd/mm/yyyy text dates (the format the DMS export uses)."""
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    parsed = pd.to_datetime(series, format="%d/%m/%Y", errors="coerce")
    # Fallback for any stray rows that used a different but still day-first format
    missing = parsed.isna() & series.notna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(series[missing], dayfirst=True, errors="coerce")
    return parsed


def _period_str(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m")


def _month_label(period: str) -> str:
    """'2026-09' -> 'September 2026'"""
    return dt.datetime.strptime(period, "%Y-%m").strftime("%B %Y")


def _shift_period(period: str, months: int) -> str:
    d = dt.datetime.strptime(period, "%Y-%m")
    y, m = d.year, d.month + months
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


def pct_change(current: float, previous: float) -> Optional[float]:
    """Return % change, or None when it cannot be meaningfully computed (0 -> 0)."""
    if previous == 0 and current == 0:
        return 0.0
    if previous == 0:
        return None  # signals "new" / infinite growth to the frontend
    return round(((current - previous) / previous) * 100, 1)


def _safe_div(n: float, d: float) -> float:
    return round((n / d) * 100, 1) if d else 0.0


def _month_of(dates: pd.Series, fallback: pd.Series) -> pd.Series:
    """'YYYY-MM' of `dates`, falling back to `fallback` where the date is blank."""
    own = dates.dt.strftime("%Y-%m")
    return own.where(dates.notna(), fallback)


def _fmt_date(value) -> str:
    try:
        if pd.isna(value):
            return ""
        return pd.Timestamp(value).strftime("%d/%m/%Y")
    except Exception:
        return ""


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _empty_enquiry() -> pd.DataFrame:
    cols = list(_TEXT_COLUMNS) + _DATE_COLUMNS + [
        "Test Drive", "enquiry aging days", "Month", "Booking Month", "Retail Month", "Lost Month",
        "Status Key", "is_lost", "is_booked", "is_retail", "is_booking_cancel", "is_followup",
        "is_followup_cancel", "is_appointed", "is_appointed_cancel", "booking_days", "retail_days", "Phone",
    ]
    return pd.DataFrame(columns=cols)


# --------------------------------------------------------------------------- #
# Main data container
# --------------------------------------------------------------------------- #

class DashboardData:
    """Holds the cleaned Enquiry dataframe (and the optional stock dataframe),
    and knows how to (re)load itself from disk."""

    def __init__(self):
        self.enquiry: pd.DataFrame = _empty_enquiry()
        self.stock: pd.DataFrame = pd.DataFrame()
        self.stock_info: dict = {}
        self.last_loaded: Optional[dt.datetime] = None
        self.current_period: str = dt.date.today().strftime("%Y-%m")
        self.previous_period: str = _shift_period(self.current_period, -1)
        self.reload()

    # ------------------------------------------------------------------- #
    def reload(self):
        self.enquiry = self._load_enquiry()
        self._load_stock()
        self._compute_periods()
        self.last_loaded = dt.datetime.now()

    # ------------------------------------------------------------------- #
    def _load_enquiry(self) -> pd.DataFrame:
        if not os.path.exists(ENQUIRY_FILE):
            return _empty_enquiry()
        df = pd.read_excel(ENQUIRY_FILE, sheet_name=pick_enquiry_sheet(ENQUIRY_FILE))
        df.columns = [str(c).strip() for c in df.columns]

        for name, default in _TEXT_COLUMNS.items():
            if name not in df.columns:
                df[name] = default
        for name in _DATE_COLUMNS:
            df[name] = _parse_date(df[name]) if name in df.columns else pd.NaT

        # Normalise the Test Drive Y/N flag
        td = df["Test Drive"].astype(str).str.strip().str.upper() if "Test Drive" in df.columns \
            else pd.Series("N", index=df.index)
        df["Test Drive"] = td.where(td.isin(["Y", "N"]), "N")

        for name in ("Enquiry Status", "Model", "Source", "Variant", "Color", "Fuel type"):
            df[name] = df[name].map(_text)
        df["Enquiry Status"] = df["Enquiry Status"].replace("", "Unknown")
        df["Model"] = df["Model"].replace("", "Unknown")
        df["Source"] = df["Source"].replace("", "Unknown")
        df["Consultant Name"] = df["Consultant Name"].fillna("Unassigned").astype(str).str.strip().replace("", "Unassigned")
        df["City"] = df["City"].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
        df["Customer ID"] = df["Customer ID"].map(_text)
        df["Name of the Customer"] = df["Name of the Customer"].map(_text)
        df["Consultant Remarks"] = df["Consultant Remarks"].map(_text)
        df["lost reason"] = df["lost reason"].map(_text)
        df["Lost Remark"] = df["Lost Remark"].map(_text)
        df["Phone"] = (df["Contact Number"].astype(str).str.replace(r"\.0$", "", regex=True)
                       .map(_text))

        df["enquiry aging days"] = pd.to_numeric(df.get("enquiry aging days"), errors="coerce").fillna(0)

        # ---- status flags: the whole Booking / Retail / Follow-up story lives here ----
        sk = df["Enquiry Status"].map(_skey)
        df["Status Key"] = sk
        df["is_booked"] = sk.isin(BOOKING_STATUSES)
        df["is_retail"] = sk.isin(RETAIL_STATUSES)
        df["is_booking_cancel"] = sk.isin(BOOKING_CANCEL_STATUSES)
        df["is_followup"] = sk.isin(FOLLOWUP_STATUSES)
        df["is_followup_cancel"] = sk.isin(FOLLOWUP_CANCEL_STATUSES)
        df["is_appointed"] = sk.isin(APPOINTED_STATUSES)
        df["is_appointed_cancel"] = sk.isin(APPOINTED_CANCEL_STATUSES)
        df["is_lost"] = sk.str.contains("|".join(CANCEL_KEYWORDS))

        # ---- each event is counted in the month it actually happened ----
        df["Month"] = df["Enquiry Date"].dt.strftime("%Y-%m")
        df["Booking Month"] = _month_of(df["Booking Date"], df["Month"])
        df["Retail Month"] = _month_of(df["Retail date"], df["Month"])
        df["Lost Month"] = _month_of(df["Lost Date"], df["Month"])

        # ---- durations derived from the dates (no separate booking / sales file) ----
        df["booking_days"] = (df["Booking Date"] - df["Enquiry Date"]).dt.days.clip(lower=0)      # enquiry -> booking
        df["retail_days"] = (df["Retail date"] - df["Booking Date"]).dt.days.clip(lower=0)         # booking -> retail
        return df

    # ------------------------------------------------------------------- #
    def _load_stock(self):
        """Stock comes from a separate Stock.xlsx if one was uploaded, otherwise from the
        'Physical Stock' and 'In Transit' sheets of the Enquiry workbook itself."""
        self.stock = pd.DataFrame()
        self.stock_info = {}
        source = None
        if os.path.exists(STOCK_FILE):
            source = STOCK_FILE
        elif os.path.exists(ENQUIRY_FILE) and se.has_stock_sheets(ENQUIRY_FILE):
            source = ENQUIRY_FILE
        if source is None:
            return
        try:
            self.stock, self.stock_info = se.load_stock(source)
        except Exception as exc:  # a bad stock file must never take the dashboard down
            self.stock = pd.DataFrame()
            self.stock_info = {"file": os.path.basename(source), "error": str(exc)}

    # ------------------------------------------------------------------- #
    def _compute_periods(self):
        """current_period = the most-recent calendar month that actually has data
        (falls back to today's month if the file is empty). previous_period is
        simply the calendar month before that."""
        e = self.enquiry
        if e.empty:
            self.current_period = dt.date.today().strftime("%Y-%m")
        else:
            all_dates = pd.concat(
                [e["Enquiry Date"], e["Booking Date"], e["Retail date"], e["Lost Date"]],
                ignore_index=True,
            ).dropna()
            self.current_period = (_period_str(all_dates.max()) if len(all_dates)
                                   else dt.date.today().strftime("%Y-%m"))
        self.previous_period = _shift_period(self.current_period, -1)

    # ------------------------------------------------------------------- #
    def available_periods(self) -> list:
        months = set()
        for col in ("Month", "Booking Month", "Retail Month", "Lost Month"):
            if col in self.enquiry.columns:
                months |= set(self.enquiry[col].dropna().unique().tolist())
        months.add(self.current_period)
        months.add(self.previous_period)
        return sorted(months)

    # ------------------------------------------------------------------- #
    def resolve_period(self, period: Optional[str]) -> Optional[str]:
        """Turn the ?period= query param into an actual 'YYYY-MM' or None (=all-time)."""
        if not period or period == "all":
            return None
        if period == "current_month":
            return self.current_period
        if period == "last_month":
            return self.previous_period
        return period  # assume already 'YYYY-MM'

    # ------------------------------------------------------------------- #
    def _filter(self, df: pd.DataFrame, period: Optional[str],
                model: Optional[str] = None, consultant: Optional[str] = None,
                source: Optional[str] = None, month_col: str = "Month") -> pd.DataFrame:
        """Filter by month (on `month_col`) plus the optional Model / Consultant /
        Source drill-down filters."""
        if df.empty:
            return df
        d = df
        if period is not None:
            d = d[d[month_col] == period]
        if model and model != "all":
            d = d[d["Model"] == model]
        if consultant and consultant != "all":
            d = d[d["Consultant Name"] == consultant]
        if source and source != "all":
            d = d[d["Source"] == source]
        return d

    # Each kind of event is a slice of the one Enquiry sheet, counted by its own date.
    _KIND_SPEC = {
        "enquiry": (None, "Month"),
        "appointed": ("is_appointed", "Month"),
        "booked": ("is_booked", "Booking Month"),
        "retail": ("is_retail", "Retail Month"),
        "booking_cancel": ("is_booking_cancel", "Lost Month"),
        "followup_cancel": ("is_followup_cancel", "Lost Month"),
        "appointed_cancel": ("is_appointed_cancel", "Lost Month"),
    }

    def view(self, kind: str, period: Optional[str], model: Optional[str] = None,
             consultant: Optional[str] = None, source: Optional[str] = None) -> pd.DataFrame:
        flag, month_col = self._KIND_SPEC[kind]
        df = self.enquiry
        if df.empty:
            return df
        if flag:
            df = df[df[flag]]
        return self._filter(df, period, model, consultant, source, month_col=month_col)

    # ------------------------------------------------------------------- #
    def filter_options(self) -> dict:
        """Distinct Model / Consultant / Source values, for the UI dropdown filters."""
        e = self.enquiry
        clean = lambda vals: sorted(v for v in vals if v and v.lower() not in ("nan", "none", "", "unknown"))
        if e.empty:
            return {"models": [], "consultants": [], "sources": []}
        return {
            "models": clean(set(e["Model"].dropna().astype(str).str.strip())),
            "consultants": clean(set(e["Consultant Name"].dropna().astype(str).str.strip())),
            "sources": clean(set(e["Source"].dropna().astype(str).str.strip())),
        }


# A single shared instance the whole app reads from.
store = DashboardData()


# --------------------------------------------------------------------------- #
# Metric builders
# --------------------------------------------------------------------------- #

def compute_kpis(period: Optional[str], model: Optional[str] = None,
                 consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    enq = store.view("enquiry", period, model, consultant, source)
    booked = store.view("booked", period, model, consultant, source)
    retail = store.view("retail", period, model, consultant, source)
    b_cancel = store.view("booking_cancel", period, model, consultant, source)
    f_cancel = store.view("followup_cancel", period, model, consultant, source)
    a_cancel = store.view("appointed_cancel", period, model, consultant, source)

    total_enquiries = len(enq)
    td_done = int((enq["Test Drive"] == "Y").sum()) if not enq.empty else 0
    total_bookings = len(booked)          # Enquiry Status = Booked
    total_retail = len(retail)            # Enquiry Status = Retail (vehicle sold)
    lost_enquiries = int(enq["is_lost"].sum()) if not enq.empty else 0
    appointed = int(enq["is_appointed"].sum()) if not enq.empty else 0

    # Test drive -> booking: of this period's test-driven enquiries, how many are Booked
    td_to_booking_rate = 0.0
    if not enq.empty:
        td = enq[enq["Test Drive"] == "Y"]
        td_to_booking_rate = _safe_div(int(td["is_booked"].sum()), len(td))

    avg_booking_age = float(booked["booking_days"].mean()) if not booked.empty else 0.0
    if pd.isna(avg_booking_age):
        avg_booking_age = 0.0

    return {
        "total_enquiries": total_enquiries,
        "test_drive_done": td_done,
        "test_drive_not_done": total_enquiries - td_done,
        "test_drive_rate": _safe_div(td_done, total_enquiries),
        "total_bookings": total_bookings,
        "total_retail": total_retail,
        "lost_enquiries": lost_enquiries,
        "booking_cancel": len(b_cancel),
        "appointed_enquiries": appointed,
        "followup_cancel": len(f_cancel),
        "appointed_cancel": len(a_cancel),
        "enquiry_to_booking_rate": _safe_div(total_bookings, total_enquiries),
        "enquiry_to_retail_rate": _safe_div(total_retail, total_enquiries),
        # Booked customers still waiting + those already sold = every booking that is still alive
        "booking_to_retail_rate": _safe_div(total_retail, total_bookings + total_retail),
        "test_drive_to_booking_rate": td_to_booking_rate,
        "avg_booking_age_days": round(avg_booking_age, 1),
    }


def compute_comparison(period: Optional[str] = None, model: Optional[str] = None,
                       consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    """period, when given, is the 'current' month to compare ('YYYY-MM'); the
    previous month is always the calendar month right before it."""
    current_period = period or store.current_period
    previous_period = _shift_period(current_period, -1)

    current = compute_kpis(current_period, model, consultant, source)
    previous = compute_kpis(previous_period, model, consultant, source)

    metrics = [
        ("total_enquiries", "Total Enquiries"),
        ("test_drive_done", "Test Drives Completed"),
        ("test_drive_not_done", "Test Drives Pending (Not Done)"),
        ("test_drive_rate", "Test Drive Rate (%)"),
        ("total_bookings", "Total Bookings"),
        ("booking_cancel", "Booking Cancel"),
        ("total_retail", "Total Retail (Units Sold)"),
        ("enquiry_to_booking_rate", "Enquiry to Booking Conv. (%)"),
        ("enquiry_to_retail_rate", "Enquiry to Retail Conv. (%)"),
        ("booking_to_retail_rate", "Booking to Retail Conv. (%)"),
        ("test_drive_to_booking_rate", "Test Drive to Booking Conv. (%)"),
        ("lost_enquiries", "Lost Enquiries"),
    ]
    rows = []
    for key, label in metrics:
        c, p = current[key], previous[key]
        change = pct_change(c, p)
        rows.append({
            "metric": label,
            "current": c,
            "previous": p,
            "change_pct": change,
            "direction": "up" if (change or 0) > 0 else ("down" if (change or 0) < 0 else "flat"),
        })

    return {
        "current_period": current_period,
        "current_period_label": _month_label(current_period),
        "previous_period": previous_period,
        "previous_period_label": _month_label(previous_period),
        "filters": {"model": model or "all", "consultant": consultant or "all", "source": source or "all"},
        "rows": rows,
    }


def _by_count(df: pd.DataFrame, col: str, limit: Optional[int] = None) -> list:
    if df.empty:
        return []
    vc = df[col].value_counts()
    if limit:
        vc = vc.head(limit)
    return [{"label": k, "value": int(v)} for k, v in vc.items()]


def _daily_counts(df: pd.DataFrame, date_col: str) -> list:
    if df.empty:
        return []
    days = df[date_col].dropna().dt.strftime("%Y-%m-%d")
    counts = days.groupby(days).size().sort_index()
    return [{"date": d, "value": int(v)} for d, v in counts.items()]


def compute_test_drive_analytics(period: Optional[str], model: Optional[str] = None,
                                 consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    enq = store.view("enquiry", period, model, consultant, source)
    if enq.empty:
        return {"done_vs_not": {"Y": 0, "N": 0}, "by_model": [], "by_consultant": [],
                "by_source": [], "daily_trend": [], "funnel": []}

    done_vs_not = {
        "Y": int((enq["Test Drive"] == "Y").sum()),
        "N": int((enq["Test Drive"] == "N").sum()),
    }
    td = enq[enq["Test Drive"] == "Y"]

    def _td_by(col, limit=None):
        counts = td[col].value_counts()
        if limit:
            counts = counts.head(limit)
        return [{"label": k, "value": int(v)} for k, v in counts.items() if v > 0]

    daily_trend = _daily_counts(td, "Enquiry Date")

    # Funnel: Enquiries -> Test Drives -> Booked -> Retailed (straight from Enquiry Status)
    funnel = [
        {"stage": "Total Enquiries", "value": len(enq)},
        {"stage": "Test Drives Completed", "value": done_vs_not["Y"]},
        {"stage": "Booked (of Test Drives)", "value": int(td["is_booked"].sum())},
        {"stage": "Retailed (of Test Drives)", "value": int(td["is_retail"].sum())},
    ]

    return {
        "done_vs_not": done_vs_not,
        "by_model": _td_by("Model", 12),
        "by_consultant": _td_by("Consultant Name", 10),
        "by_source": _td_by("Source"),
        "daily_trend": daily_trend,
        "funnel": funnel,
    }


def compute_enquiry_analytics(period: Optional[str], model: Optional[str] = None,
                              consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    enq = store.view("enquiry", period, model, consultant, source)
    if enq.empty:
        return {"status_breakdown": [], "source_breakdown": [], "lost_reasons": [],
                "city_breakdown": [], "aging_buckets": [], "model_breakdown": []}

    lost = enq[enq["is_lost"] | enq["Lost Date"].notna()]
    lost_reasons = []
    if not lost.empty:
        vc = lost["lost reason"][lost["lost reason"] != ""].value_counts()
        lost_reasons = [{"label": k, "value": int(v)} for k, v in vc.items()]

    bins = [-1, 7, 15, 30, 10_000]
    labels = ["0-7 days", "8-15 days", "16-30 days", "31+ days"]
    bucketed = pd.cut(enq["enquiry aging days"], bins=bins, labels=labels)
    aging_buckets = [
        {"label": str(k), "value": int(v)} for k, v in bucketed.value_counts().reindex(labels).items()
    ]

    return {
        "status_breakdown": _by_count(enq, "Enquiry Status"),
        "source_breakdown": _by_count(enq, "Source"),
        "lost_reasons": lost_reasons,
        "city_breakdown": _by_count(enq, "City", 10),
        "aging_buckets": aging_buckets,
        "model_breakdown": _by_count(enq, "Model", 10),
    }


def compute_booking_analytics(period: Optional[str], model: Optional[str] = None,
                              consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    """Bookings = Enquiry Status 'Booked' (by Booking Date).
    Booking cancels = 'Booking Cancel' (by Lost Date)."""
    book = store.view("booked", period, model, consultant, source)
    cancel = store.view("booking_cancel", period, model, consultant, source)

    cancel_reasons = []
    if not cancel.empty:
        vc = cancel["lost reason"].replace("", "Not specified").value_counts()
        cancel_reasons = [{"label": k, "value": int(v)} for k, v in vc.items()]

    return {
        "by_source": _by_count(book, "Source"),
        "by_consultant": _by_count(book, "Consultant Name", 10),
        "by_model": _by_count(book, "Model"),
        "daily_trend": _daily_counts(book, "Booking Date"),
        "total_cancelled": int(len(cancel)),
        "cancel_reasons": cancel_reasons,
        "cancel_by_model": _by_count(cancel, "Model"),
        "cancel_daily_trend": _daily_counts(cancel, "Lost Date"),
    }


def compute_sales_analytics(period: Optional[str], model: Optional[str] = None,
                            consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    """Retails = Enquiry Status 'Retail' - a vehicle sold (by Retail date)."""
    sales = store.view("retail", period, model, consultant, source)
    avg_days = float(sales["retail_days"].mean()) if not sales.empty else 0.0
    if pd.isna(avg_days):
        avg_days = 0.0
    return {
        "units_by_model": _by_count(sales, "Model"),
        "by_consultant": _by_count(sales, "Consultant Name", 10),
        "by_source": _by_count(sales, "Source"),
        "daily_trend": _daily_counts(sales, "Retail date"),
        "avg_booking_to_retail_days": round(avg_days, 1),
        "total_retail": int(len(sales)),
    }


def compute_meta() -> dict:
    e = store.enquiry
    stock_rows = int(len(store.stock)) if not store.stock.empty else 0
    return {
        "current_period": store.current_period,
        "current_period_label": _month_label(store.current_period),
        "previous_period": store.previous_period,
        "previous_period_label": _month_label(store.previous_period),
        "available_periods": [
            {"value": p, "label": _month_label(p)} for p in store.available_periods()
        ],
        "last_loaded": store.last_loaded.isoformat() if store.last_loaded else None,
        "row_counts": {
            "enquiry": int(len(e)),
            "booking": int(e["is_booked"].sum()) if not e.empty else 0,   # status = Booked
            "sales": int(e["is_retail"].sum()) if not e.empty else 0,     # status = Retail
            "stock": stock_rows,
        },
    }


# --------------------------------------------------------------------------- #
# Breakdown tables - the "Model wise / Consultant wise / Source wise" detail
# tables shown on every page, underneath the charts.
# --------------------------------------------------------------------------- #

_DIM_COLUMN = {"model": "Model", "consultant": "Consultant Name", "source": "Source"}


def _keys(dim: str, *frames) -> set:
    col = _DIM_COLUMN[dim]
    keys = set()
    for f in frames:
        if not f.empty and col in f.columns:
            keys |= set(f[col].dropna().unique().tolist())
    return keys


def _breakdown_rows_overview(enq, book, retail, dimension, limit):
    col = _DIM_COLUMN[dimension]
    rows = []
    for k in _keys(dimension, enq, book, retail):
        rows.append({
            "label": k,
            "enquiries": int((enq[col] == k).sum()) if not enq.empty else 0,
            "bookings": int((book[col] == k).sum()) if not book.empty else 0,
            "retail": int((retail[col] == k).sum()) if not retail.empty else 0,
        })
    rows.sort(key=lambda r: (r["enquiries"], r["retail"]), reverse=True)
    return rows[:limit]


def _breakdown_rows_enquiry(enq, dimension, limit):
    col = _DIM_COLUMN[dimension]
    if enq.empty:
        return []
    rows = []
    for k, sub in enq.groupby(col):
        cnt = len(sub)
        td = int((sub["Test Drive"] == "Y").sum())
        lost = int(sub["is_lost"].sum())
        rows.append({
            "label": k, "enquiries": cnt, "test_drives": td,
            "test_drive_rate": _safe_div(td, cnt), "lost": lost, "lost_rate": _safe_div(lost, cnt),
        })
    rows.sort(key=lambda r: r["enquiries"], reverse=True)
    return rows[:limit]


def _breakdown_rows_testdrive(enq, dimension, limit):
    col = _DIM_COLUMN[dimension]
    if enq.empty:
        return []
    rows = []
    for k, sub in enq.groupby(col):
        cnt = len(sub)
        td_sub = sub[sub["Test Drive"] == "Y"]
        td = len(td_sub)
        rows.append({
            "label": k, "enquiries": cnt, "test_drives": td,
            "test_drive_rate": _safe_div(td, cnt), "booked_from_td": int(td_sub["is_booked"].sum()),
        })
    rows.sort(key=lambda r: r["test_drives"], reverse=True)
    return rows[:limit]


def _breakdown_rows_booking(book, cancel, dimension, limit):
    col = _DIM_COLUMN[dimension]
    rows = []
    for k in _keys(dimension, book, cancel):
        sub = book[book[col] == k] if not book.empty else book
        avg_age = sub["booking_days"].mean() if len(sub) else float("nan")
        rows.append({
            "label": k, "bookings": int(len(sub)),
            "booking_cancel": int((cancel[col] == k).sum()) if not cancel.empty else 0,
            "avg_booking_age": round(float(avg_age), 1) if pd.notna(avg_age) else 0.0,
        })
    rows.sort(key=lambda r: (r["bookings"], r["booking_cancel"]), reverse=True)
    return rows[:limit]


def _breakdown_rows_sales(retail, dimension, limit):
    col = _DIM_COLUMN[dimension]
    if retail.empty:
        return []
    rows = []
    for k, sub in retail.groupby(col):
        avg_days = sub["retail_days"].mean()
        rows.append({
            "label": k, "units": int(len(sub)),
            "avg_booking_to_retail_days": round(float(avg_days), 1) if pd.notna(avg_days) else 0.0,
        })
    rows.sort(key=lambda r: r["units"], reverse=True)
    return rows[:limit]


def _breakdown_rows_conversion(enq, book, retail, dimension, limit):
    """E2T / E2B / E2R / B2R funnel conversion per Model / Consultant / Source."""
    col = _DIM_COLUMN[dimension]
    rows = []
    for k in _keys(dimension, enq, book, retail):
        e_sub = enq[enq[col] == k] if not enq.empty else enq
        e_cnt = int(len(e_sub))
        td_cnt = int((e_sub["Test Drive"] == "Y").sum()) if e_cnt else 0
        b_cnt = int((book[col] == k).sum()) if not book.empty else 0
        s_cnt = int((retail[col] == k).sum()) if not retail.empty else 0
        rows.append({
            "label": k, "enquiries": e_cnt,
            "e2t": _safe_div(td_cnt, e_cnt),
            "e2b": _safe_div(b_cnt, e_cnt),
            "e2r": _safe_div(s_cnt, e_cnt),
            "b2r": _safe_div(s_cnt, b_cnt + s_cnt),
        })
    rows.sort(key=lambda r: r["enquiries"], reverse=True)
    return rows[:limit]


def compute_breakdown_tables(section: str, period: Optional[str], model: Optional[str] = None,
                             consultant: Optional[str] = None, source: Optional[str] = None,
                             limit: int = 20) -> dict:
    """Returns {'by_model': [...], 'by_consultant': [...], 'by_source': [...]}
    with section-appropriate columns, for the 'Breakdown' table on every page."""
    enq = store.view("enquiry", period, model, consultant, source)
    book = store.view("booked", period, model, consultant, source)
    retail = store.view("retail", period, model, consultant, source)
    cancel = store.view("booking_cancel", period, model, consultant, source)

    result = {}
    for dimension in ("model", "consultant", "source"):
        if section == "overview":
            rows = _breakdown_rows_overview(enq, book, retail, dimension, limit)
        elif section == "enquiry":
            rows = _breakdown_rows_enquiry(enq, dimension, limit)
        elif section == "testdrive":
            rows = _breakdown_rows_testdrive(enq, dimension, limit)
        elif section == "booking":
            rows = _breakdown_rows_booking(book, cancel, dimension, limit)
        elif section == "sales":
            rows = _breakdown_rows_sales(retail, dimension, limit)
        elif section == "conversion":
            rows = _breakdown_rows_conversion(enq, book, retail, dimension, limit)
        else:
            rows = []
        result[f"by_{dimension}"] = rows
    return result


# --------------------------------------------------------------------------- #
# Enquiry Follow-up page
#   day-wise follow-ups, pending from previous days, date-wise schedule, and
#   the two "cancel" statuses (Enquiry Follow up Cancel / Appointed Enquiry Cancel)
# --------------------------------------------------------------------------- #

def _as_of(as_of: Optional[str]) -> pd.Timestamp:
    """The 'today' the follow-up page measures against (defaults to the real today)."""
    try:
        if as_of:
            return pd.Timestamp(as_of).normalize()
    except Exception:
        pass
    return pd.Timestamp(dt.date.today())


def _followup_base(model, consultant, source) -> pd.DataFrame:
    """Open follow-ups: rows whose status is still 'Enquiry Follow up' - every month."""
    df = store.enquiry
    if df.empty:
        return df
    return store._filter(df[df["is_followup"]], None, model, consultant, source)


def _kind_for(date: pd.Timestamp, ref: pd.Timestamp) -> str:
    return "pending" if date < ref else ("today" if date == ref else "upcoming")


def compute_followup(as_of: Optional[str], period: Optional[str], model: Optional[str] = None,
                     consultant: Optional[str] = None, source: Optional[str] = None,
                     window_days: int = 14) -> dict:
    ref = _as_of(as_of)
    fu = _followup_base(model, consultant, source)
    nd = fu["Next Followup Date"] if not fu.empty else pd.Series(dtype="datetime64[ns]")

    today_df = fu[nd == ref] if not fu.empty else fu
    pending = fu[nd < ref] if not fu.empty else fu
    upcoming = fu[nd > ref] if not fu.empty else fu
    next7 = upcoming[upcoming["Next Followup Date"] <= ref + pd.Timedelta(days=7)] if not upcoming.empty else upcoming
    no_date = fu[nd.isna()] if not fu.empty else fu

    f_cancel = store.view("followup_cancel", period, model, consultant, source)
    a_cancel = store.view("appointed_cancel", period, model, consultant, source)

    kpis = {
        "open_followups": int(len(fu)),
        "due_today": int(len(today_df)),
        "pending_previous": int(len(pending)),
        "upcoming_7_days": int(len(next7)),
        "upcoming_total": int(len(upcoming)),
        "no_followup_date": int(len(no_date)),
        "followup_cancel": int(len(f_cancel)),
        "appointed_cancel": int(len(a_cancel)),
    }

    # ---- day-wise chart: contiguous window around the reference date ----
    counts = {}
    if not fu.empty:
        dated = fu["Next Followup Date"].dropna()
        counts = dated.groupby(dated).size().to_dict()
    start, end = ref - pd.Timedelta(days=window_days), ref + pd.Timedelta(days=window_days)
    chart = []
    d = start
    while d <= end:
        chart.append({"date": d.strftime("%Y-%m-%d"), "value": int(counts.get(d, 0)), "kind": _kind_for(d, ref)})
        d += pd.Timedelta(days=1)
    older_pending = int(sum(v for k, v in counts.items() if k < start))
    later_upcoming = int(sum(v for k, v in counts.items() if k > end))

    # ---- full date-wise schedule (every date that has follow-ups) ----
    date_table = [
        {"date": k.strftime("%Y-%m-%d"), "weekday": k.strftime("%a"), "value": int(v), "kind": _kind_for(k, ref),
         "days_from_ref": int((k - ref).days)}
        for k, v in sorted(counts.items())
    ]

    # ---- how old is the pending backlog ----
    age_buckets = []
    if not pending.empty:
        overdue = (ref - pending["Next Followup Date"]).dt.days
        bins = [0, 3, 7, 14, 30, 100000]
        labels = ["1-3 days", "4-7 days", "8-14 days", "15-30 days", "31+ days"]
        cut = pd.cut(overdue, bins=bins, labels=labels)
        age_buckets = [{"label": str(k), "value": int(v)} for k, v in cut.value_counts().reindex(labels).items()]
    else:
        age_buckets = [{"label": l, "value": 0} for l in ["1-3 days", "4-7 days", "8-14 days", "15-30 days", "31+ days"]]

    # ---- consultant-wise ----
    consultant_rows = []
    if not fu.empty:
        for name, sub in fu.groupby("Consultant Name"):
            s_nd = sub["Next Followup Date"]
            consultant_rows.append({
                "label": name,
                "pending_previous": int((s_nd < ref).sum()),
                "due_today": int((s_nd == ref).sum()),
                "upcoming": int((s_nd > ref).sum()),
                "total": int(len(sub)),
            })
        consultant_rows.sort(key=lambda r: (r["pending_previous"], r["due_today"], r["total"]), reverse=True)

    return {
        "as_of": ref.strftime("%Y-%m-%d"),
        "as_of_label": ref.strftime("%d %b %Y"),
        "kpis": kpis,
        "chart": chart,
        "older_pending": older_pending,
        "later_upcoming": later_upcoming,
        "window_days": window_days,
        "date_table": date_table,
        "age_buckets": age_buckets,
        "by_consultant": consultant_rows,
    }


def compute_followup_list(scope: str, as_of: Optional[str], date: Optional[str], period: Optional[str],
                          model: Optional[str] = None, consultant: Optional[str] = None,
                          source: Optional[str] = None, limit: int = 1000) -> dict:
    """The customer rows behind a follow-up number.

    scope: today | pending | upcoming | all | date | followup_cancel | appointed_cancel
    """
    ref = _as_of(as_of)
    cancel_scopes = {"followup_cancel", "appointed_cancel"}

    if scope in cancel_scopes:
        df = store.view(scope, period, model, consultant, source)
        if not df.empty:
            df = df.sort_values("Lost Date", ascending=False, na_position="last")
    else:
        df = _followup_base(model, consultant, source)
        if not df.empty:
            nd = df["Next Followup Date"]
            if scope == "today":
                df = df[nd == ref].sort_values(["Consultant Name", "Name of the Customer"])
            elif scope == "pending":
                df = df[nd < ref].sort_values("Next Followup Date")           # most overdue first
            elif scope == "upcoming":
                df = df[nd > ref].sort_values("Next Followup Date")
            elif scope == "date":
                try:
                    day = pd.Timestamp(date).normalize()
                except Exception:
                    day = ref
                df = df[nd == day].sort_values(["Consultant Name", "Name of the Customer"])
            else:  # all open
                df = df.sort_values("Next Followup Date", na_position="last")

    total = int(len(df))
    rows = []
    for _, r in df.head(limit).iterrows():
        nxt = r["Next Followup Date"]
        overdue = int((ref - nxt).days) if pd.notna(nxt) else None
        rows.append({
            "customer_id": r["Customer ID"],
            "customer": r["Name of the Customer"],
            "phone": r["Phone"],
            "model": r["Model"],
            "variant": r["Variant"],
            "color": r["Color"],
            "consultant": r["Consultant Name"],
            "source": r["Source"],
            "status": r["Enquiry Status"],
            "test_drive": r["Test Drive"],
            "enquiry_date": _fmt_date(r["Enquiry Date"]),
            "next_followup": _fmt_date(nxt),
            "days_overdue": overdue,
            "remarks": r["Consultant Remarks"],
            "lost_date": _fmt_date(r["Lost Date"]),
            "lost_reason": r["lost reason"],
            "lost_remark": r["Lost Remark"],
        })
    return {"scope": scope, "as_of": ref.strftime("%Y-%m-%d"), "total": total,
            "shown": len(rows), "rows": rows}


# --------------------------------------------------------------------------- #
# Enquiry Wise Stock page (Physical vs In Transit) - matching lives in stock_engine.py
# --------------------------------------------------------------------------- #

def _stock_enquiries(model, consultant, source, status) -> pd.DataFrame:
    df = store.enquiry
    if df.empty:
        return df
    wanted = {_skey(s) for s in STOCK_MATCH_STATUSES}
    if status and status != "all":
        wanted = {_skey(status)}
    df = df[df["Status Key"].isin(wanted)]
    return store._filter(df, None, model, consultant, source)


def compute_enquiry_stock(model: Optional[str] = None, consultant: Optional[str] = None,
                          source: Optional[str] = None, status: Optional[str] = None) -> dict:
    info = store.stock_info or {}
    if store.stock.empty:
        result = se.empty_result(info)
        result["message"] = (
            info.get("error")
            or "No stock loaded yet. Upload the workbook that has the 'Physical Stock' and 'In Transit' "
               "sheets with 'Update monthly data' (or drop it in the data/ folder as Enquiry.xlsx and press Refresh)."
        )
        result["status_options"] = STOCK_MATCH_STATUSES
        return result

    enq = _stock_enquiries(model, consultant, source, status)
    result = se.compute(enq, store.stock, info)
    result["message"] = ""
    result["status_options"] = STOCK_MATCH_STATUSES
    return result


def export_enquiry_stock(model: Optional[str] = None, consultant: Optional[str] = None,
                         source: Optional[str] = None, status: Optional[str] = None) -> bytes:
    result = compute_enquiry_stock(model, consultant, source, status)
    if not result["stock_loaded"]:
        raise ValueError(result.get("message") or "No stock file loaded.")
    return se.export_workbook(result, store.stock)
