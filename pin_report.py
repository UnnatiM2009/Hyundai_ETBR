"""
pin_report.py
-------------
PIN Code page: Enquiry / Test Drive / Booking / Retail / Cancel Booking counted PIN code wise,
from the `Pin No.` column of the Enquiry sheet.

It only READS the existing views of data_processor.store (the same Enquiry / Booked / Retail /
Booking-cancel slices every other page uses, each counted in its own month), so the numbers tie
to the Enquiry, Test Drive, Booking and Retails pages and Team Leader scoping applies automatically.
data_processor.py is not modified.
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

import data_processor as dp

PIN_BLANK = "Not specified"
PIN_COL = "Pin No."

# metric key -> (store view kind, date column shown in the record list, extra row mask)
AREA_FILE = os.path.join(dp.DATA_DIR, "Pin_Areas.xlsx")   # PIN | Area Name  (India Post names; edit freely)
_area_cache = {"mtime": None, "map": {}}


def _areas() -> dict:
    """PIN -> area name(s), read from data/Pin_Areas.xlsx (reloaded when the file changes)."""
    try:
        mtime = os.path.getmtime(AREA_FILE)
    except OSError:
        return {}
    if _area_cache["mtime"] != mtime:
        try:
            df = pd.read_excel(AREA_FILE, dtype=str)
            df.columns = [str(c).strip().lower() for c in df.columns]
            pc = next(c for c in df.columns if c.startswith("pin"))
            ac = next(c for c in df.columns if c.startswith("area"))
            m = {str(a).strip(): str(b).strip() for a, b in zip(df[pc], df[ac])
                 if str(a).strip() and str(b).strip().lower() not in ("", "nan", "none")}
        except Exception:
            m = {}
        _area_cache.update(mtime=mtime, map=m)
    return _area_cache["map"]


METRICS = {
    "enquiry": ("enquiry", "Enquiry Date", None),
    "testdrive": ("enquiry", "Enquiry Date", lambda d: d["Test Drive"] == "Y"),
    "booking": ("booked", "Booking Date", None),
    "retail": ("retail", "Retail date", None),
    "cancel": ("booking_cancel", "Lost Date", None),
}
METRIC_LABELS = {"enquiry": "Enquiry", "testdrive": "Test Drive", "booking": "Booking",
                 "retail": "Retails", "cancel": "Cancel Booking"}


def _pin_series(df: pd.DataFrame) -> pd.Series:
    """Pin No. as a clean text value: 440024.0 -> '440024', blank / junk -> 'Not specified'."""
    if PIN_COL not in df.columns:
        return pd.Series(PIN_BLANK, index=df.index)

    def one(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return PIN_BLANK
        s = str(v).strip()
        if s.lower() in ("", "nan", "none", "0", "0.0"):
            return PIN_BLANK
        if s.endswith(".0"):
            s = s[:-2]
        digits = "".join(ch for ch in s if ch.isdigit())
        return digits if digits else PIN_BLANK

    return df[PIN_COL].map(one)


def _slice(metric: str, period, model, consultant, source, sub_source) -> pd.DataFrame:
    kind, _date, mask = METRICS[metric]
    df = dp.store.view(kind, period, model, consultant, source, sub_source=sub_source)
    if df.empty:
        return df
    if mask is not None:
        df = df[mask(df)]
    df = df.copy()
    df["_pin"] = _pin_series(df)
    return df


def _city_by_pin(frames) -> dict:
    parts = [f[["_pin", "City"]] for f in frames if not f.empty]
    if not parts:
        return {}
    allr = pd.concat(parts, ignore_index=True)
    allr = allr[~allr["City"].str.lower().isin(["", "unknown", "nan", "none"])]
    out = {}
    for pin, g in allr.groupby("_pin"):
        out[pin] = g["City"].str.title().value_counts().index[0] if len(g) else ""
    return out


def compute_pin_summary(period: Optional[str], model=None, consultant=None, source=None,
                        sub_source=None, pin: Optional[str] = None) -> dict:
    frames = {m: _slice(m, period, model, consultant, source, sub_source) for m in METRICS}

    counts: dict = {}
    for m, df in frames.items():
        if df.empty:
            continue
        for p, n in df["_pin"].value_counts().items():
            counts.setdefault(p, {k: 0 for k in METRICS})[m] = int(n)
    cities = _city_by_pin(frames.values())
    areas = _areas()

    def sort_key(item):
        p, c = item
        return (p == PIN_BLANK, -c["enquiry"], -c["booking"], p)

    ordered = sorted(counts.items(), key=sort_key)
    options = [{"pin": p, "city": cities.get(p, ""), "area": areas.get(p, ""), "enquiry": c["enquiry"]} for p, c in ordered]

    shown = [(p, c) for p, c in ordered if not pin or pin == "all" or p == pin]
    rows = [{"pin": p, "area": areas.get(p, ""), "city": cities.get(p, ""), **c} for p, c in shown]
    totals = {m: sum(r[m] for r in rows) for m in METRICS}
    return {
        "rows": rows,
        "totals": totals,
        "options": options,
        "pin_count": len([r for r in rows if r["pin"] != PIN_BLANK]),
        "labels": METRIC_LABELS,
    }


def compute_pin_records(metric: str, period: Optional[str], model=None, consultant=None, source=None,
                        sub_source=None, pin: Optional[str] = None) -> dict:
    if metric not in METRICS:
        raise ValueError(metric)
    df = _slice(metric, period, model, consultant, source, sub_source)
    date_col = METRICS[metric][1]
    if not df.empty and pin and pin != "all":
        df = df[df["_pin"] == pin]
    rows = []
    areas = _areas()
    if not df.empty:
        df = df.sort_values(date_col, ascending=False, na_position="last")
        for _, r in df.iterrows():
            rows.append({
                "pin": r["_pin"], "area": areas.get(r["_pin"], ""), "date": dp._fmt_date(r[date_col]),
                "customer": r["Name of the Customer"], "customer_id": r["Customer ID"],
                "phone": r["Phone"], "city": r["City"], "model": r["Model"], "variant": r["Variant"],
                "consultant": r["Consultant Name"], "source": r["Source"], "status": r["Enquiry Status"],
                "test_drive": r["Test Drive"],
            })
    return {"metric": metric, "label": METRIC_LABELS[metric], "date_label": {
        "Enquiry Date": "Enquiry date", "Booking Date": "Booking date",
        "Retail date": "Retail date", "Lost Date": "Cancel date"}[date_col], "rows": rows}
