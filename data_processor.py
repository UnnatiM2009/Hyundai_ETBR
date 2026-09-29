"""
data_processor.py
------------------
All data-loading and metric-calculation logic for the Hyundai Test Drive /
Enquiry / Booking / Sales dashboard.

Design goals:
- Read the three dealership export files (Enquiry.xlsx, Booking.xlsx, SalesReport.xlsx)
- Be tolerant of Indian-style comma-formatted numbers ("1,23,456") and
  dd/mm/yyyy text dates, which is how the real exports are formatted.
- Automatically figure out "current month" vs "previous month" from the data
  itself (not just the server clock), so the comparison keeps working
  correctly every month as new exports are dropped into /data.
- Expose a set of get_* functions that return plain dict/list structures,
  ready to be JSON-serialised by FastAPI.
"""

from __future__ import annotations

import os
import datetime as dt
from typing import Optional

import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

ENQUIRY_FILE = os.path.join(DATA_DIR, "Enquiry.xlsx")
BOOKING_FILE = os.path.join(DATA_DIR, "Booking.xlsx")
SALES_FILE = os.path.join(DATA_DIR, "SalesReport.xlsx")

CANCEL_KEYWORDS = ("cancel",)  # substring match, case-insensitive, on Enquiry Status


# --------------------------------------------------------------------------- #
# Low level helpers
# --------------------------------------------------------------------------- #

def _clean_numeric(series: pd.Series) -> pd.Series:
    """Turn '1,23,456' / '681,610' / '' / NaN into a float Series (0.0 for missing)."""
    if series is None:
        return pd.Series(dtype=float)
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("₹", "", regex=False)
        .str.strip()
    )
    cleaned = cleaned.replace({"nan": np.nan, "None": np.nan, "": np.nan})
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _parse_date(series: pd.Series) -> pd.Series:
    """Parse dd/mm/yyyy text dates (the format every export in this project uses)."""
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
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


# --------------------------------------------------------------------------- #
# Main data container
# --------------------------------------------------------------------------- #

class DashboardData:
    """Holds the three cleaned dataframes and derived lookup fields, and knows
    how to (re)load itself from disk."""

    def __init__(self):
        self.enquiry: pd.DataFrame = pd.DataFrame()
        self.booking: pd.DataFrame = pd.DataFrame()
        self.sales: pd.DataFrame = pd.DataFrame()
        self.last_loaded: Optional[dt.datetime] = None
        self.current_period: str = dt.date.today().strftime("%Y-%m")
        self.previous_period: str = _shift_period(self.current_period, -1)
        self.reload()

    # ------------------------------------------------------------------- #
    def reload(self):
        self.enquiry = self._load_enquiry()
        self.booking = self._load_booking()
        self.sales = self._load_sales()
        self._compute_periods()
        self.last_loaded = dt.datetime.now()

    # ------------------------------------------------------------------- #
    def _load_enquiry(self) -> pd.DataFrame:
        if not os.path.exists(ENQUIRY_FILE):
            return pd.DataFrame()
        df = pd.read_excel(ENQUIRY_FILE)
        df.columns = [str(c).strip() for c in df.columns]

        df["Enquiry Date"] = _parse_date(df.get("Enquiry Date"))
        df["Next Followup Date"] = _parse_date(df.get("Next Followup Date"))
        df["Lost Date"] = _parse_date(df.get("Lost Date"))
        df["Booking Date"] = _parse_date(df.get("Booking Date"))
        df["Retail date"] = _parse_date(df.get("Retail date"))

        # Normalise the Test Drive Y/N flag (this is column O in the raw file)
        td_col = "Test Drive"
        df[td_col] = df[td_col].astype(str).str.strip().str.upper()
        df[td_col] = df[td_col].where(df[td_col].isin(["Y", "N"]), "N")

        df["Enquiry Status"] = df["Enquiry Status"].astype(str).str.strip()
        df["Model"] = df["Model"].astype(str).str.strip()
        df["Source"] = df["Source"].astype(str).str.strip()
        df["Consultant Name"] = df["Consultant Name"].fillna("Unassigned").astype(str).str.strip()
        df["City"] = df["City"].fillna("Unknown").astype(str).str.strip()

        df["enquiry aging days"] = pd.to_numeric(df.get("enquiry aging days"), errors="coerce").fillna(0)
        df["Month"] = df["Enquiry Date"].apply(lambda x: _period_str(x) if pd.notna(x) else None)
        df["is_lost"] = df["Enquiry Status"].str.lower().str.contains("|".join(CANCEL_KEYWORDS))
        return df

    # ------------------------------------------------------------------- #
    def _load_booking(self) -> pd.DataFrame:
        if not os.path.exists(BOOKING_FILE):
            return pd.DataFrame()
        df = pd.read_excel(BOOKING_FILE)
        df.columns = [str(c).strip() for c in df.columns]

        df["Booking Date"] = _parse_date(df.get("Booking Date"))
        df["Enquiry Date"] = _parse_date(df.get("Enquiry Date"))

        df["Amount Received"] = _clean_numeric(df.get("Amount Received"))
        df["Balance Payment"] = _clean_numeric(df.get("Balance Payment"))
        df["Loan Amount"] = _clean_numeric(df.get("Loan Amount"))

        df["Model"] = df["Model"].astype(str).str.strip()
        df["Mode of Purchase"] = df["Mode of Purchase"].fillna("Not Specified").astype(str).str.strip()
        df["Consultant Name"] = df["Consultant Name"].fillna("Unassigned").astype(str).str.strip()
        df["Main Source"] = df["Main Source"].fillna("Unknown").astype(str).str.strip()
        df["Booking Age"] = pd.to_numeric(df.get("Booking Age"), errors="coerce").fillna(0)

        df["Month"] = df["Booking Date"].apply(lambda x: _period_str(x) if pd.notna(x) else None)
        return df

    # ------------------------------------------------------------------- #
    def _load_sales(self) -> pd.DataFrame:
        if not os.path.exists(SALES_FILE):
            return pd.DataFrame()
        df = pd.read_excel(SALES_FILE)
        df.columns = [str(c).strip() for c in df.columns]

        df["Invoice Date"] = _parse_date(df.get("Invoice Date"))
        df["Booking Date"] = _parse_date(df.get("Booking Date"))
        df["Delivery Date"] = _parse_date(df.get("Delivery Date"))

        df["Invoice Price"] = _clean_numeric(df.get("Invoice Price"))
        df["Basic Amount"] = _clean_numeric(df.get("Basic Amount"))
        df["Other Discount"] = _clean_numeric(df.get("Other Discount"))
        df["Dealer Cash Discount"] = _clean_numeric(df.get("Dealer Cash Discount"))

        df["Model"] = df["Model"].astype(str).str.strip()
        df["Status"] = df["Status"].fillna("Unknown").astype(str).str.strip()
        df["Consultant Name"] = df["Consultant Name"].fillna("Unassigned").astype(str).str.strip()
        df["Source"] = df["Source"].fillna("Unknown").astype(str).str.strip()
        df["delivery in days"] = pd.to_numeric(df.get("delivery in days"), errors="coerce")

        df["Month"] = df["Invoice Date"].apply(lambda x: _period_str(x) if pd.notna(x) else None)
        return df

    # ------------------------------------------------------------------- #
    def _compute_periods(self):
        """current_period = the most-recent calendar month that actually has data
        (falls back to today's month if every file is empty). previous_period is
        simply the calendar month before that. This means the dashboard keeps
        comparing 'this export vs the one before it' automatically, however
        many months of history end up in the files."""
        all_dates = pd.concat(
            [
                self.enquiry.get("Enquiry Date", pd.Series(dtype="datetime64[ns]")),
                self.booking.get("Booking Date", pd.Series(dtype="datetime64[ns]")),
                self.sales.get("Invoice Date", pd.Series(dtype="datetime64[ns]")),
            ],
            ignore_index=True,
        ).dropna()

        if len(all_dates):
            latest = all_dates.max()
            self.current_period = _period_str(latest)
        else:
            self.current_period = dt.date.today().strftime("%Y-%m")
        self.previous_period = _shift_period(self.current_period, -1)

    # ------------------------------------------------------------------- #
    def available_periods(self) -> list:
        months = set()
        for df in (self.enquiry, self.booking, self.sales):
            if "Month" in df.columns:
                months |= set(df["Month"].dropna().unique().tolist())
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
                source: Optional[str] = None, source_col: str = "Source") -> pd.DataFrame:
        """Filter a dataframe by month plus the optional drill-down filters used
        on the Month Comparison page (and available anywhere else that wants
        them). `source_col` differs by file: Enquiry/Sales use 'Source',
        Booking uses 'Main Source'."""
        if df.empty:
            return df
        d = df
        if period is not None:
            d = d[d["Month"] == period]
        if model and model != "all" and "Model" in d.columns:
            d = d[d["Model"] == model]
        if consultant and consultant != "all" and "Consultant Name" in d.columns:
            d = d[d["Consultant Name"] == consultant]
        if source and source != "all" and source_col in d.columns:
            d = d[d[source_col] == source]
        return d

    # ------------------------------------------------------------------- #
    def filter_options(self) -> dict:
        """Distinct Model / Consultant / Source values across all three files,
        for populating the dropdown filters in the UI."""
        models, consultants, sources = set(), set(), set()
        for df in (self.enquiry, self.booking, self.sales):
            if "Model" in df.columns:
                models |= set(df["Model"].dropna().astype(str).str.strip().unique().tolist())
            if "Consultant Name" in df.columns:
                consultants |= set(df["Consultant Name"].dropna().astype(str).str.strip().unique().tolist())
        if "Source" in self.enquiry.columns:
            sources |= set(self.enquiry["Source"].dropna().astype(str).str.strip().unique().tolist())
        if "Main Source" in self.booking.columns:
            sources |= set(self.booking["Main Source"].dropna().astype(str).str.strip().unique().tolist())
        if "Source" in self.sales.columns:
            sources |= set(self.sales["Source"].dropna().astype(str).str.strip().unique().tolist())

        clean = lambda vals: sorted(v for v in vals if v and v.lower() not in ("nan", "none", ""))
        return {
            "models": clean(models),
            "consultants": clean(consultants),
            "sources": clean(sources),
        }


# A single shared instance the whole app reads from.
store = DashboardData()


# --------------------------------------------------------------------------- #
# Metric builders (all operate on `store`, filtered to an optional period plus
# optional Model / Consultant / Source drill-down filters)
# --------------------------------------------------------------------------- #

def compute_kpis(period: Optional[str], model: Optional[str] = None,
                  consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    enq = store._filter(store.enquiry, period, model, consultant, source, source_col="Source")
    book = store._filter(store.booking, period, model, consultant, source, source_col="Main Source")
    sales = store._filter(store.sales, period, model, consultant, source, source_col="Source")

    total_enquiries = len(enq)
    td_done = int((enq["Test Drive"] == "Y").sum()) if not enq.empty else 0
    td_not_done = total_enquiries - td_done
    total_bookings = len(book)
    total_retail = len(sales)
    total_revenue = float(sales["Invoice Price"].sum()) if not sales.empty else 0.0
    lost_enquiries = int(enq["is_lost"].sum()) if not enq.empty else 0

    # Test-drive -> booking conversion, matched by Customer ID (within the same filters)
    td_to_booking_rate = 0.0
    if not enq.empty and "Customer ID" in enq.columns:
        td_ids = set(enq.loc[enq["Test Drive"] == "Y", "Customer ID"].dropna())
        book_ids = set(book.get("Customer ID", pd.Series(dtype=object)).dropna())
        matched = len(td_ids & book_ids)
        td_to_booking_rate = _safe_div(matched, len(td_ids))

    avg_booking_age = float(book["Booking Age"].mean()) if not book.empty else 0.0

    return {
        "total_enquiries": total_enquiries,
        "test_drive_done": td_done,
        "test_drive_not_done": td_not_done,
        "test_drive_rate": _safe_div(td_done, total_enquiries),
        "total_bookings": total_bookings,
        "total_retail": total_retail,
        "total_revenue": round(total_revenue, 2),
        "lost_enquiries": lost_enquiries,
        "enquiry_to_booking_rate": _safe_div(total_bookings, total_enquiries),
        "booking_to_retail_rate": _safe_div(total_retail, total_bookings),
        "test_drive_to_booking_rate": td_to_booking_rate,
        "avg_booking_age_days": round(avg_booking_age, 1),
    }


def compute_comparison(period: Optional[str] = None, model: Optional[str] = None,
                        consultant: Optional[str] = None, source: Optional[str] = None) -> dict:
    """period, when given, is the 'current' month to compare ('YYYY-MM'); the
    previous month is always the calendar month right before it. Leaving it
    out uses the latest month actually present in the data (store.current_period).
    model / consultant / source narrow every figure below to that slice."""
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
        ("total_retail", "Total Retail (Units Sold)"),
        ("total_revenue", "Total Revenue"),
        ("enquiry_to_booking_rate", "Enquiry to Booking Conv. (%)"),
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


def compute_test_drive_analytics(period: Optional[str]) -> dict:
    enq = store._filter(store.enquiry, period)
    if enq.empty:
        return {
            "done_vs_not": {"Y": 0, "N": 0},
            "by_model": [],
            "by_consultant": [],
            "by_source": [],
            "daily_trend": [],
            "funnel": [],
        }

    done_vs_not = {
        "Y": int((enq["Test Drive"] == "Y").sum()),
        "N": int((enq["Test Drive"] == "N").sum()),
    }

    by_model = (
        enq.groupby("Model")["Test Drive"]
        .apply(lambda s: (s == "Y").sum())
        .sort_values(ascending=False)
        .reset_index(name="test_drives")
    )
    by_model = [
        {"label": r["Model"], "value": int(r["test_drives"])}
        for _, r in by_model.iterrows() if r["test_drives"] > 0
    ][:12]

    by_consultant = (
        enq.groupby("Consultant Name")["Test Drive"]
        .apply(lambda s: (s == "Y").sum())
        .sort_values(ascending=False)
        .reset_index(name="test_drives")
    )
    by_consultant = [
        {"label": r["Consultant Name"], "value": int(r["test_drives"])}
        for _, r in by_consultant.iterrows() if r["test_drives"] > 0
    ][:10]

    by_source = (
        enq.groupby("Source")["Test Drive"]
        .apply(lambda s: (s == "Y").sum())
        .sort_values(ascending=False)
        .reset_index(name="test_drives")
    )
    by_source = [
        {"label": r["Source"], "value": int(r["test_drives"])}
        for _, r in by_source.iterrows()
    ]

    td = enq[enq["Test Drive"] == "Y"].copy()
    daily_trend = []
    if not td.empty:
        td["day"] = td["Enquiry Date"].dt.strftime("%Y-%m-%d")
        counts = td.groupby("day").size().sort_index()
        daily_trend = [{"date": d, "value": int(v)} for d, v in counts.items()]

    # Funnel: Enquiries -> Test Drives -> Booked -> Retailed (matched by Customer ID)
    total_enq = len(enq)
    td_done = done_vs_not["Y"]
    td_ids = set(enq.loc[enq["Test Drive"] == "Y", "Customer ID"].dropna())
    book_ids = set(store.booking.get("Customer ID", pd.Series(dtype=object)).dropna())
    sales_ids = set(store.sales.get("CustomerID", pd.Series(dtype=object)).dropna())
    booked_from_td = len(td_ids & book_ids)
    retailed_from_td = len(td_ids & sales_ids)

    funnel = [
        {"stage": "Total Enquiries", "value": total_enq},
        {"stage": "Test Drives Completed", "value": td_done},
        {"stage": "Booked (of Test Drives)", "value": booked_from_td},
        {"stage": "Retailed (of Test Drives)", "value": retailed_from_td},
    ]

    return {
        "done_vs_not": done_vs_not,
        "by_model": by_model,
        "by_consultant": by_consultant,
        "by_source": by_source,
        "daily_trend": daily_trend,
        "funnel": funnel,
    }


def compute_enquiry_analytics(period: Optional[str]) -> dict:
    enq = store._filter(store.enquiry, period)
    if enq.empty:
        return {"status_breakdown": [], "source_breakdown": [], "lost_reasons": [],
                "city_breakdown": [], "aging_buckets": [], "model_breakdown": []}

    status_breakdown = [
        {"label": k, "value": int(v)} for k, v in enq["Enquiry Status"].value_counts().items()
    ]
    source_breakdown = [
        {"label": k, "value": int(v)} for k, v in enq["Source"].value_counts().items()
    ]
    model_breakdown = [
        {"label": k, "value": int(v)} for k, v in enq["Model"].value_counts().head(10).items()
    ]

    lost = enq[enq["is_lost"] | enq["Lost Date"].notna()]
    lost_reasons = []
    if not lost.empty and "lost reason" in lost.columns:
        vc = lost["lost reason"].dropna().astype(str).str.strip()
        vc = vc[vc != ""].value_counts()
        lost_reasons = [{"label": k, "value": int(v)} for k, v in vc.items()]

    city_breakdown = [
        {"label": k, "value": int(v)}
        for k, v in enq["City"].value_counts().head(10).items()
    ]

    bins = [-1, 7, 15, 30, 10_000]
    labels = ["0-7 days", "8-15 days", "16-30 days", "31+ days"]
    bucketed = pd.cut(enq["enquiry aging days"], bins=bins, labels=labels)
    aging_buckets = [
        {"label": str(k), "value": int(v)} for k, v in bucketed.value_counts().reindex(labels).items()
    ]

    return {
        "status_breakdown": status_breakdown,
        "source_breakdown": source_breakdown,
        "lost_reasons": lost_reasons,
        "city_breakdown": city_breakdown,
        "aging_buckets": aging_buckets,
        "model_breakdown": model_breakdown,
    }


def compute_booking_analytics(period: Optional[str]) -> dict:
    book = store._filter(store.booking, period)
    if book.empty:
        return {"mode_of_purchase": [], "by_consultant": [], "daily_trend": [],
                "total_amount_received": 0, "total_balance_payment": 0, "by_model": []}

    mode_of_purchase = [
        {"label": k, "value": int(v)} for k, v in book["Mode of Purchase"].value_counts().items()
    ]
    by_consultant = [
        {"label": k, "value": int(v)} for k, v in book["Consultant Name"].value_counts().head(10).items()
    ]
    by_model = [
        {"label": k, "value": int(v)} for k, v in book["Model"].value_counts().items()
    ]

    daily = book.copy()
    daily["day"] = daily["Booking Date"].dt.strftime("%Y-%m-%d")
    counts = daily.dropna(subset=["day"]).groupby("day").size().sort_index()
    daily_trend = [{"date": d, "value": int(v)} for d, v in counts.items()]

    return {
        "mode_of_purchase": mode_of_purchase,
        "by_consultant": by_consultant,
        "by_model": by_model,
        "daily_trend": daily_trend,
        "total_amount_received": round(float(book["Amount Received"].sum()), 2),
        "total_balance_payment": round(float(book["Balance Payment"].sum()), 2),
    }


def compute_sales_analytics(period: Optional[str]) -> dict:
    sales = store._filter(store.sales, period)
    if sales.empty:
        return {"revenue_by_model": [], "units_by_model": [], "daily_trend": [],
                "avg_delivery_days": 0, "total_revenue": 0, "by_source": []}

    revenue_by_model = (
        sales.groupby("Model")["Invoice Price"].sum().sort_values(ascending=False).reset_index()
    )
    revenue_by_model = [
        {"label": r["Model"], "value": round(float(r["Invoice Price"]), 2)}
        for _, r in revenue_by_model.iterrows()
    ]
    units_by_model = [
        {"label": k, "value": int(v)} for k, v in sales["Model"].value_counts().items()
    ]
    by_source = [
        {"label": k, "value": int(v)} for k, v in sales["Source"].value_counts().items()
    ]

    daily = sales.copy()
    daily["day"] = daily["Invoice Date"].dt.strftime("%Y-%m-%d")
    counts = daily.dropna(subset=["day"]).groupby("day")["Invoice Price"].sum().sort_index()
    daily_trend = [{"date": d, "value": round(float(v), 2)} for d, v in counts.items()]

    avg_delivery = sales["delivery in days"].mean()
    avg_delivery = round(float(avg_delivery), 1) if pd.notna(avg_delivery) else 0.0

    return {
        "revenue_by_model": revenue_by_model,
        "units_by_model": units_by_model,
        "by_source": by_source,
        "daily_trend": daily_trend,
        "avg_delivery_days": avg_delivery,
        "total_revenue": round(float(sales["Invoice Price"].sum()), 2),
    }


def compute_meta() -> dict:
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
            "enquiry": len(store.enquiry),
            "booking": len(store.booking),
            "sales": len(store.sales),
        },
    }
