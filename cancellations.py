"""
cancellations.py
----------------
Booking Cancellations page of the Hyundai ETBR dashboard.

Built from the Enquiry sheet (rows whose Enquiry Status is "Booking Cancel"): one row per cancelled booking with the customer,
the sales consultant, the model / variant / colour, the booking date, the cancel date (the sheet's "Lost Date"), the cancel
reason ("lost reason"), the lost remark and the consultant's remarks.  Nothing here changes the numbers of any other page.

Rules
    * Month / date filters apply to the CANCEL date ("cancelled in the period" - the same basis as the Overview "Booking cancel"
      card), or - with the page's switch - to the BOOKING date ("booked in the period").  Model / Consultant / Source /
      Sub-source filters apply either way.
    * "% of bookings cancelled" is only shown for the booking-date basis, where it is exact: cancelled bookings that were made in
      the period / all bookings made in the period (Booked + Retail + Booking Cancel, on Booking Date).
    * Days to cancel = cancel date - booking date.
    * "Invoice Cancel" (an invoice cancelled after retail) is a different status and is counted on the Lost enquiries card, not
      here; the page only tells you how many there are.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

import pandas as pd

import data_processor as dp

DIMENSIONS = {
    "consultant": ("Consultant Name", "Sales consultant"),
    "model": ("Model", "Model"),
    "variant": ("Variant", "Variant"),
    "color": ("Color", "Colour"),
    "source": ("Source", "Source"),
    "reason": ("lost reason", "Cancel reason"),
}
QUICK_DAYS = 7


def _txt(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat") else t


def _day(v) -> str:
    return "" if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")


def _gross_bookings(df: pd.DataFrame) -> pd.DataFrame:
    """Every booking MADE (Booked + Retail + Booking Cancel), dated by Booking Date (column 'Booking Month')."""
    return df[df["is_booked"] | df["is_retail"] | df["is_booking_cancel"]]


def _month_label(m: str) -> str:
    return pd.Timestamp(m + "-01").strftime("%b %Y")


def _prepare(period: Optional[str], basis: str, model, consultant, source, sub_source):
    st = dp.store
    e = st.enquiry
    month_col = "Lost Month" if basis == "cancel" else "Booking Month"
    canc_all = e[e["is_booking_cancel"]]
    canc_all = st._filter(canc_all, None, model, consultant, source, month_col=month_col, sub_source=sub_source)
    canc = canc_all if period is None else canc_all[canc_all[month_col] == period]
    gross_all = st._filter(_gross_bookings(e), None, model, consultant, source, month_col="Booking Month", sub_source=sub_source)
    return e, canc_all, canc, gross_all, month_col


def _days_to_cancel(df: pd.DataFrame) -> pd.Series:
    return (df["Lost Date"] - df["Booking Date"]).dt.days.clip(lower=0)


def _agg(df: pd.DataFrame, gross: pd.DataFrame, col: str, show_pct: bool) -> list:
    rows = []
    if df.empty:
        return rows
    total = len(df)
    dtc = _days_to_cancel(df)
    gross_counts = gross[col].astype(str).str.strip().value_counts() if (show_pct and col in gross.columns) else {}
    for key, sub in df.groupby(df[col].astype(str).str.strip()):
        label = key if key and key.lower() not in ("nan", "none") else "Not captured"
        d = dtc.loc[sub.index].dropna()
        r = {
            "label": label,
            "cancelled": int(len(sub)),
            "share": round(len(sub) / total * 100, 1),
            "avg_days": round(float(d.mean()), 1) if len(d) else None,
            "quick": int((d <= QUICK_DAYS).sum()),
            "quick_pct": round(float((d <= QUICK_DAYS).sum()) / len(d) * 100, 1) if len(d) else None,
        }
        if show_pct:
            g = int(gross_counts.get(key, 0)) if hasattr(gross_counts, "get") else 0
            r["bookings"] = g
            r["cancel_pct"] = round(len(sub) / g * 100, 1) if g else None
        rows.append(r)
    rows.sort(key=lambda r: (-r["cancelled"], r["label"]))
    return rows


def _records(df: pd.DataFrame, limit: int) -> list:
    out = []
    if df.empty:
        return out
    dtc = _days_to_cancel(df)
    d = df.sort_values(["Lost Date", "Booking Date"], ascending=[False, False]).head(limit)
    for idx, r in d.iterrows():
        out.append({
            "customer": _txt(r.get("Name of the Customer")), "mobile": _txt(r.get("Contact Number")) or _txt(r.get("Phone")),
            "customer_id": _txt(r.get("Customer ID")),
            "consultant": _txt(r.get("Consultant Name")), "model": _txt(r.get("Model")), "variant": _txt(r.get("Variant")),
            "color": _txt(r.get("Color")), "source": _txt(r.get("Source")), "sub_source": _txt(r.get("Sub-source")),
            "city": _txt(r.get("City")), "enquiry_date": _day(r.get("Enquiry Date")), "booking_date": _day(r.get("Booking Date")),
            "cancel_date": _day(r.get("Lost Date")),
            "days": (int(dtc.loc[idx]) if pd.notna(dtc.loc[idx]) else None),
            "reason": _txt(r.get("lost reason")), "lost_remark": _txt(r.get("Lost Remark")),
            "remarks": _txt(r.get("Consultant Remarks")),
        })
    return out


def compute_cancellations(period: Optional[str], basis: str = "cancel", model: Optional[str] = None,
                          consultant: Optional[str] = None, source: Optional[str] = None,
                          sub_source: Optional[str] = None, dim: str = "consultant", limit: int = 2000) -> dict:
    basis = "booking" if basis == "booking" else "cancel"
    if dim not in DIMENSIONS:
        dim = "consultant"
    e = dp.store.enquiry
    if e.empty:
        return {"empty": True, "message": "No enquiry data loaded yet."}
    base, canc_all, canc, gross_all, month_col = _prepare(period, basis, model, consultant, source, sub_source)
    show_pct = basis == "booking"
    gross = gross_all if period is None else gross_all[gross_all["Booking Month"] == period]

    dtc = _days_to_cancel(canc).dropna()
    reasons = canc["lost reason"].astype(str).str.strip()
    reasons = reasons[~reasons.str.lower().isin(["", "nan", "none"])].value_counts()
    top_reason = (reasons.index[0], int(reasons.iloc[0])) if len(reasons) else None
    by_cons = canc["Consultant Name"].astype(str).str.strip().value_counts()
    top_cons = (by_cons.index[0], int(by_cons.iloc[0])) if len(by_cons) else None

    kpis = {
        "cancelled": int(len(canc)),
        "bookings_made": int(len(gross)) if show_pct else None,
        "cancel_pct": round(len(canc) / len(gross) * 100, 1) if (show_pct and len(gross)) else None,
        "avg_days": round(float(dtc.mean()), 1) if len(dtc) else None,
        "median_days": round(float(dtc.median()), 1) if len(dtc) else None,
        "quick": int((dtc <= QUICK_DAYS).sum()),
        "quick_pct": round(float((dtc <= QUICK_DAYS).sum()) / len(dtc) * 100, 1) if len(dtc) else None,
        "late": int((dtc > 30).sum()),
        "top_reason": {"label": top_reason[0], "count": top_reason[1]} if top_reason else None,
        "top_consultant": {"label": top_cons[0], "count": top_cons[1]} if top_cons else None,
        "consultants": int(by_cons.size),
        "exchange_related": int(canc["Exchange opted"].astype(str).str.upper().eq("Y").sum()),
    }

    # every month, within the Model / Consultant / Source filters (the Month filter does not apply to this chart)
    by_month = []
    counts = canc_all[month_col].value_counts().sort_index()
    gcounts = gross_all["Booking Month"].value_counts()
    for m, v in counts.items():
        row = {"month": m, "label": _month_label(m), "cancelled": int(v)}
        if show_pct:
            g = int(gcounts.get(m, 0))
            row["bookings"] = g
            row["cancel_pct"] = round(v / g * 100, 1) if g else None
        by_month.append(row)

    col = DIMENSIONS[dim][0]
    tables = {k: _agg(canc, gross, DIMENSIONS[k][0], show_pct) for k in DIMENSIONS}

    inv = base[base["Status Key"] == "invoice cancel"]
    inv = dp.store._filter(inv, None, model, consultant, source, month_col="Lost Month", sub_source=sub_source)
    if period is not None:
        inv = inv[inv["Lost Month"] == period]

    # the Overview card, for the reconciliation line
    k = dp.compute_kpis(period, model=model, consultant=consultant, source=source, sub_source=sub_source) if basis == "cancel" and period else None
    recon = None
    if k is not None:
        recon = {"overview_card": int(k["booking_cancel"]), "this_page": int(len(canc)), "agree": int(k["booking_cancel"]) == int(len(canc))}

    return {
        "empty": False, "basis": basis, "period": period, "kpis": kpis, "by_month": by_month,
        "reasons": [{"label": k2, "value": int(v)} for k2, v in reasons.items()],
        "tables": tables, "dim": dim, "dim_labels": {k2: v[1] for k2, v in DIMENSIONS.items()},
        "rows": _records(canc, limit), "row_total": int(len(canc)),
        "invoice_cancel": int(len(inv)), "recon": recon,
        "show_pct": show_pct,
    }


def export_csv(period: Optional[str], basis: str, model, consultant, source, sub_source) -> str:
    _, _, canc, _, _ = _prepare(period, "booking" if basis == "booking" else "cancel", model, consultant, source, sub_source)
    rows = _records(canc, 100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    head = ["Customer", "Mobile", "Consultant", "Model", "Variant", "Colour", "Source", "Sub-source", "City", "Enquiry date",
            "Booking date", "Cancel date", "Days to cancel", "Cancel reason", "Lost remark", "Consultant remarks"]
    w.writerow(head)
    for r in rows:
        w.writerow([r["customer"], r["mobile"], r["consultant"], r["model"], r["variant"], r["color"], r["source"], r["sub_source"],
                    r["city"], r["enquiry_date"], r["booking_date"], r["cancel_date"], "" if r["days"] is None else r["days"],
                    r["reason"], r["lost_remark"], r["remarks"]])
    return "\ufeff" + buf.getvalue()
