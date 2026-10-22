"""
daily_report.py
---------------
The Daily report page (Full-access users only): what happened on one chosen day and in the month
up to that day, plus a day-by-day history of the month.

It is the Hyundai version of the Skoda ETBR dashboard's Daily report. Skoda reads six separate CRM
reports and groups them by Location x Model; Hyundai has one Enquiry sheet and one showroom, so the
same report is grouped by Consultant x Model instead.

NOTHING here redefines a business rule. Every figure comes from the same `store.view()` slices the
Enquiry / Test Drive / Booking / Retails pages already use, so the Daily report always ties to them:

    Enquiries        Enquiry Status = any, counted on Enquiry Date
    Test drives      Enquiries with Test Drive = Y, counted on Enquiry Date   (as on the Test Drive page)
    Bookings         Enquiry Status = Booked, counted on Booking Date
    Retails          Enquiry Status = Retail, counted on Retail date
    Booking cancels  Enquiry Status = Booking Cancel, counted on Lost Date

"That day"  = records dated exactly the chosen day.
"Month to date" = records dated from the 1st of the month up to and including the chosen day.
A booked / retailed / cancelled record that has no date at all cannot be placed on a day, so it is
left out of the daily figures and reported in `undated` (the month pages still count it).

The same Month / Model / Consultant / Source / Sub-source filters as every other page apply.
Team Leader scoping needs no code here: `store.enquiry` is already limited to the signed-in user.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional

import pandas as pd

import data_processor as dp

store = dp.store

# key, store.view kind, date column, extra rule, label
METRICS = [
    ("enquiry", "enquiry", "Enquiry Date", None, "Enquiries"),
    ("testdrive", "enquiry", "Enquiry Date", "td", "Test drives"),
    ("booking", "booked", "Booking Date", None, "Bookings"),
    ("retail", "retail", "Retail date", None, "Retails"),
    ("cancel", "booking_cancel", "Lost Date", None, "Booking cancels"),
]
METRIC_KEYS = [m[0] for m in METRICS]
LABELS = {m[0]: m[4] for m in METRICS}
GRID_KEYS = ["enquiry", "testdrive", "booking", "retail"]       # the Consultant x Model tables
CONSULTANT, MODEL = "Consultant Name", "Model"

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _month_bounds(period: Optional[str]):
    if not period or not _PERIOD_RE.match(period):
        period = store.current_period
    start = pd.Timestamp(period + "-01")
    return period, start, start + pd.offsets.MonthEnd(0)


def _slice(key: str, period, model, consultant, source, sub_source):
    """(rows, real dates) of one metric for the month and the filters. Dates are midnight-normalised;
    a row with no date has NaT and is never placed on a day."""
    _, kind, col, extra, _ = next(m for m in METRICS if m[0] == key)
    df = store.view(kind, period, model, consultant, source, sub_source=sub_source)
    if not df.empty and extra == "td":
        df = df[df["Test Drive"] == "Y"]
    dates = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    return df, dates


def _all_slices(period, model, consultant, source, sub_source) -> Dict:
    return {k: _slice(k, period, model, consultant, source, sub_source) for k in METRIC_KEYS}


def _parse_day(day: Optional[str], lo: pd.Timestamp, hi: pd.Timestamp) -> Optional[pd.Timestamp]:
    """The requested day if it is a real date inside [lo, hi], otherwise None."""
    if day:
        try:
            d = pd.Timestamp(day).normalize()
            if lo <= d <= hi:
                return d
        except (ValueError, TypeError):
            pass
    return None


def _last_active_day(slices: Dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
    """Latest day of the month with any enquiry / test drive / booking / retail / cancel (the default day)."""
    last = [d.max() for _, d in slices.values() if d.notna().any()]
    return max(min(max(last), end), start) if last else end


def _counts_by_day(dates: pd.Series) -> Dict:
    ok = dates.dropna()
    return ok.value_counts().to_dict() if len(ok) else {}


# --------------------------------------------------------------------------- #
# the Consultant x Model grids
# --------------------------------------------------------------------------- #
def _grid(key: str, slices: Dict, start: pd.Timestamp, pick: pd.Timestamp) -> Dict:
    df, dates = slices[key]
    keep = dates.notna() & (dates >= start) & (dates <= pick)
    mtd, d_mtd = df[keep], dates[keep]
    today = mtd[d_mtd == pick]

    def tally(frame):
        if frame.empty:
            return {}
        return frame.groupby([CONSULTANT, MODEL]).size().to_dict()

    m_cnt, t_cnt = tally(mtd), tally(today)

    # model columns: only models that appeared this month, biggest first
    by_model: Dict[str, int] = {}
    by_cons: Dict[str, int] = {}
    for (c, mo), n in m_cnt.items():
        by_model[mo] = by_model.get(mo, 0) + int(n)
        by_cons[c] = by_cons.get(c, 0) + int(n)
    models = [m for m, _ in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
    consultants = [c for c, _ in sorted(by_cons.items(), key=lambda kv: (-kv[1], kv[0].lower()))]

    def cell(cnt, cons=None):
        row = {}
        for mo in models:
            row[mo] = int(sum(v for (c, m2), v in cnt.items() if m2 == mo and (cons is None or c == cons)))
        row["Total"] = int(sum(row.values()))
        return row

    return {"key": key, "label": LABELS[key], "models": models,
            "rows": [{"name": c, "t": cell(t_cnt, c), "m": cell(m_cnt, c)} for c in consultants],
            "total": {"name": "Group total", "t": cell(t_cnt), "m": cell(m_cnt)}}


# --------------------------------------------------------------------------- #
# Consultant scorecard: every metric for every consultant, that day and month to date
# --------------------------------------------------------------------------- #
def _scorecard(slices: Dict, start: pd.Timestamp, pick: pd.Timestamp) -> Dict:
    per: Dict[str, Dict] = {}

    def row_for(name):
        return per.setdefault(name, {"name": name, "t": {k: 0 for k in METRIC_KEYS}, "m": {k: 0 for k in METRIC_KEYS}})

    total = {"name": "Group total", "t": {k: 0 for k in METRIC_KEYS}, "m": {k: 0 for k in METRIC_KEYS}}
    for key in METRIC_KEYS:
        df, dates = slices[key]
        keep = dates.notna() & (dates >= start) & (dates <= pick)
        if not keep.any():
            continue
        names, d = df.loc[keep, CONSULTANT], dates[keep]
        for name, n in names.value_counts().items():
            row_for(name)["m"][key] = int(n)
            total["m"][key] += int(n)
        for name, n in names[d == pick].value_counts().items():
            row_for(name)["t"][key] = int(n)
            total["t"][key] += int(n)
    rows = sorted(per.values(), key=lambda r: (-r["m"]["enquiry"], -r["m"]["booking"], -r["m"]["retail"], r["name"].lower()))
    return {"rows": rows, "total": total}


# --------------------------------------------------------------------------- #
# public: the whole page in one call
# --------------------------------------------------------------------------- #
def compute_daily(period: Optional[str], day: Optional[str] = None, model: Optional[str] = None,
                  consultant: Optional[str] = None, source: Optional[str] = None,
                  sub_source: Optional[str] = None) -> Dict:
    period, start, end = _month_bounds(period)
    slices = _all_slices(period, model, consultant, source, sub_source)

    # the days that have any activity decide how far the month is shown (no trailing empty days)
    hi = _last_active_day(slices, start, end)
    pick = _parse_day(day, start, hi) or hi

    per_day = {k: _counts_by_day(slices[k][1]) for k in METRIC_KEYS}
    days = []
    for d in pd.date_range(start, hi):
        days.append({"date": _iso(d), **{k: int(per_day[k].get(d, 0)) for k in METRIC_KEYS}})

    kpis = {}
    for k in METRIC_KEYS:
        counts = per_day[k]
        kpis[k] = {"label": LABELS[k], "day": int(counts.get(pick, 0)),
                   "mtd": int(sum(v for d, v in counts.items() if start <= d <= pick))}

    undated = {k: int(slices[k][1].isna().sum()) for k in ("booking", "retail", "cancel")}
    undated = {k: v for k, v in undated.items() if v}

    return {
        "period": period, "period_label": dp._month_label(period),
        "date": _iso(pick), "min_date": _iso(start), "max_date": _iso(hi),
        "empty": not any(len(slices[k][0]) for k in METRIC_KEYS),
        "metrics": [{"key": k, "label": LABELS[k]} for k in METRIC_KEYS],
        "kpis": kpis,
        "scorecard": _scorecard(slices, start, pick),
        "grids": [_grid(k, slices, start, pick) for k in GRID_KEYS],
        "days": days,
        "undated": undated,
    }


# --------------------------------------------------------------------------- #
# the customer rows behind a number (and the CSV export)
# --------------------------------------------------------------------------- #
RECORD_DATE_COL = {m[0]: m[2] for m in METRICS}
RECORD_SCOPES = ("day", "mtd")

_FIELDS = [  # key, CSV heading, dataframe column / callable
    ("date", "Date", None),
    ("customer_id", "Customer ID", "Customer ID"),
    ("customer", "Customer", "Name of the Customer"),
    ("phone", "Phone", "Phone"),
    ("model", "Model", "Model"),
    ("variant", "Variant", "Variant"),
    ("color", "Colour", "Color"),
    ("consultant", "Consultant", CONSULTANT),
    ("source", "Source", "Source"),
    ("sub_source", "Sub-source", "Sub-source"),
    ("status", "Enquiry status", "Enquiry Status"),
    ("test_drive", "Test drive", "Test Drive"),
    ("enquiry_date", "Enquiry date", "Enquiry Date"),
    ("booking_date", "Booking date", "Booking Date"),
    ("retail_date", "Retail date", "Retail date"),
    ("lost_date", "Lost date", "Lost Date"),
    ("lost_reason", "Lost reason", "lost reason"),
    ("remarks", "Consultant remarks", "Consultant Remarks"),
]
_DATE_FIELDS = {"enquiry_date", "booking_date", "retail_date", "lost_date"}


def _record_frame(metric: str, period, day, scope, model, consultant, source, sub_source):
    if metric not in RECORD_DATE_COL:
        raise ValueError(f"Unknown metric '{metric}'")
    if scope not in RECORD_SCOPES:
        raise ValueError(f"Unknown scope '{scope}'")
    period, start, end = _month_bounds(period)
    df, dates = _slice(metric, period, model, consultant, source, sub_source)
    # the page always sends the day it is showing; only fall back to the latest active day when none is given
    pick = _parse_day(day, start, end)
    if pick is None:
        pick = _last_active_day(_all_slices(period, model, consultant, source, sub_source), start, end)
    lo = pick if scope == "day" else start
    keep = dates.notna() & (dates >= lo) & (dates <= pick)
    out = df[keep].assign(_when=dates[keep])
    out = out.sort_values(["_when", CONSULTANT, "Name of the Customer"], ascending=[False, True, True], kind="stable")
    return out, pick, lo


def _row(r, metric: str) -> Dict:
    row = {}
    for key, _, col in _FIELDS:
        if key == "date":
            row[key] = dp._fmt_date(r["_when"])
        elif key in _DATE_FIELDS:
            row[key] = dp._fmt_date(r[col])
        else:
            row[key] = dp._text(r[col])
    return row


def daily_records(metric: str, period, day, scope, model=None, consultant=None, source=None,
                  sub_source=None, limit: int = 500) -> Dict:
    df, pick, lo = _record_frame(metric, period, day, scope, model, consultant, source, sub_source)
    rows = [_row(r, metric) for _, r in df.head(limit).iterrows()]
    return {"metric": metric, "label": LABELS[metric], "scope": scope, "date": _iso(pick), "from": _iso(lo),
            "total": int(len(df)), "shown": len(rows), "rows": rows}


def _safe_cell(value) -> str:
    """Stop a customer name like '=HYPERLINK(...)' from running as a formula when the CSV is opened in Excel."""
    s = str(value if value is not None else "")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_daily_records(metric: str, period, day, scope, model=None, consultant=None, source=None,
                         sub_source=None) -> Dict:
    df, pick, lo = _record_frame(metric, period, day, scope, model, consultant, source, sub_source)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for _, h, _ in _FIELDS])
    for _, r in df.iterrows():
        row = _row(r, metric)
        w.writerow([_safe_cell(row[k]) for k, _, _ in _FIELDS])
    name = f"hyundai_daily_{metric}_{scope}_{_iso(pick)}.csv"
    return {"filename": name, "content": buf.getvalue()}
