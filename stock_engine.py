"""
stock_engine.py
----------------
Enquiry-wise stock matching for the Hyundai dashboard.

This is the dashboard version of the Mahindra "Enquiry_Wise_Stock" report. The
matching rules are the same ones that script used:

  * Every enquiry is matched to stock on  Model + Variant + Colour.
  * Enquiry and stock name the same variant differently ("Creta 1.5 MPi MT EX"
    vs "CRETA 1.5 MPI MT EX"), so variants are compared on their *trim codes*
    only - after dropping the model name and noise words - and an enquiry
    variant matches a stock variant when the enquiry's codes are a subset of
    the stock variant's codes. The closest stock variant (fewest extra codes)
    is the "Resolved Stock Variant".
  * Availability is a quantity. Nothing is reserved, so one unit can serve
    several enquiries. OPEN stock is "free"; ALLOCATED stock is reported apart.

What is new for Hyundai: every free unit is either PHYSICAL (standing in the
yard / showroom) or IN TRANSIT (invoiced / dispatched, not yet received), and
every quantity below is split that way.

Match statuses (an enquiry gets the first one that applies):

  Variant Not Captured             enquiry has no variant, cannot be matched
  Exact Match - Physical           model + variant + colour free, physically here
  Exact Match - In Transit         exact unit(s) only in transit
  Exact Match - Allocated Only     exact unit(s) exist but all are allocated
  Variant Available - Other Color  same variant free (physical/transit), other colour
  Model Available - Other Variant  same model free, but a different variant
  No Stock Available               nothing free for this model - needs indent

The Mahindra script's Selenium / OTP / Outlook automation is intentionally NOT
ported: the dashboard reads a stock workbook the same way it reads the enquiry
workbook (drop it in data/ or use "Update monthly data").
"""

from __future__ import annotations

import io
import os
import re
from collections import Counter
from typing import Optional

import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration - the things most likely to need tuning for your stock export
# --------------------------------------------------------------------------- #

FREE = "OPEN"
ALLOC = "ALLOCATED"
PHYSICAL = "Physical"
TRANSIT = "In Transit"

# Words dropped from a variant before comparing trim codes (model words are
# dropped automatically as well).
NOISE = {"BS6", "BS62", "BSVI", "NEW", "ALL", "HYUNDAI", "MY", "OPT"}

# Generation words dropped from model names so "New Creta" == "CRETA",
# "All New i20" == "I20" in the stock file.
MODEL_NOISE = {"NEW", "ALL", "HYUNDAI"}

# If your stock file spells a model in a way that cannot be reconciled
# automatically, map it here:  mkey(stock spelling) -> mkey(enquiry spelling)
# e.g. {"GRANDI10": "GRANDI10NIOS"}
MODEL_ALIASES: dict = {}

# Stock older than this many days is counted as "aged" on the page.
AGED_DAYS = 90

# Accepted column headings in the stock workbook (case-insensitive). The first
# one found wins. Add your own spelling here if a column is not picked up.
COLUMN_CANDIDATES = {
    "model": ["Model", "Model Name", "Model Group", "Product Family", "Vehicle Model", "Car Model"],
    "variant": ["Variant", "Variant Name", "Variant Description", "Model Variant",
                "Variant Desc", "Variant Code", "Vehicle Variant"],
    "color": ["Exterior Color Name", "Exterior Colour Name", "Color", "Colour", "Exterior Color",
              "Exterior Colour", "Body Color",
              "Paint", "Color Name", "Colour Name", "Xccelerate Color"],
    "chassis": ["VIN", "VIN No", "VIN No.", "VIN Number", "Chassis No.", "Chassis No",
                "Chassis Number", "Chasis No.", "Chasis No", "Chassis", "Chasis", "Frame No"],
    "stock_type": ["Stock Type", "Stock Status", "Stock Position", "Physical/Transit",
                   "Physical / Transit", "Physical Transit", "Stock Category", "Vehicle Status",
                   "Vehicle Status Today", "Status", "Stock Location Type", "Location Type"],
    "age": ["Stock Age", "Stock Age (Days)", "Age", "Age (Days)", "Ageing", "Aging",
            "Vehicle Age", "Vehicle Age as of Today", "Days in Stock", "Stock Days"],
    "location": ["Location", "Yard", "Yard Location", "Godown", "Vehicle Park Location",
                 "Parking Location", "Stock Location", "Warehouse", "Dealer Location", "Storage Location"],
    "allocated": ["Allocated To", "Allocated Customer Name", "Allocated Customer", "Allocation Status",
                  "Allocated", "Bkng No", "Booking No", "Booking No.", "Booking ID", "Booking Number",
                  "Cust Name", "Customer Name", "Cust ID"],
    # Optional extras - not used for matching, only for the Vehicle Stock inventory page.
    # Missing entirely is fine; that field just won't be shown/filterable there.
    "fuel_type": ["Fuel Type", "Fuel", "Fuel Emission"],
    "financier": ["Financier Name", "Financier", "DSA/Financier", "Finance Company"],
    "basic_price": ["Basic Price", "Basic Amount"],
    "invoice_amt": ["HMIL Invoice Amt", "HMI Invoice Amt", "Invoice Amount", "Invoice Price"],
}

# Words in the stock-type column that mean the unit is not yet at the dealership
_TRANSIT_WORDS = ("transit", "in-transit", "intransit", "dispatch", "on road", "onroad",
                  "en route", "enroute", "shipped", "invoiced")
# Words that mean it is here
_PHYSICAL_WORDS = ("physical", "in stock", "instock", "yard", "showroom", "available",
                   "received", "grn", "godown", "in hand", "on hand", "at dealer", "open")
# Words in the stock-type column that mean the unit is already committed
_ALLOC_WORDS = ("allocat", "reserved", "blocked", "booked", "sold")

_EMPTY = {"", "-", "--", "na", "n/a", "nan", "none", "null", "nil", "0",
          "no", "n", "open", "free", "unallocated", "unallotted", "not allocated",
          "not allotted", "unalloted", "not alloted"}


# --------------------------------------------------------------------------- #
# Key helpers (same idea as the Mahindra script)
# --------------------------------------------------------------------------- #

def mkey(x) -> str:
    """Model key: upper-case letters+digits, generation words removed."""
    words = re.sub(r"[^A-Z0-9]", " ", str(x).upper()).split()
    key = "".join(w for w in words if w not in MODEL_NOISE)
    return MODEL_ALIASES.get(key, key)


def ckey(c) -> str:
    """Colour key: letters only, so 'Atlas White' == 'ATLAS-WHITE'."""
    return re.sub(r"[^A-Z]", "", str(c or "").upper())


def chkey(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def toks(variant, model) -> frozenset:
    """Trim-code tokens of a variant, without the model name / noise / seat count."""
    u = str(variant).upper()
    u = re.sub(r"BS[\s_\-]*6\.?2?", " ", u)
    u = re.sub(r"BS[\s_\-]*VI", " ", u)
    u = re.sub(r"\b\d\s*(?:STR|SEATER|SEATS?)\b", " ", u)   # "7 STR" / "7 Seater"
    u = re.sub(r"\b[5-9]S\b", " ", u)                        # "7S"
    u = re.sub(r"(?<=\d)(?=[A-Z])", " ", u)                  # "1.2MT" -> "1.2 MT"
    u = re.sub(r"[^A-Z0-9]", " ", u)
    model_words = set(re.sub(r"[^A-Z0-9]", " ", str(model).upper()).split())
    model_words |= {mkey(model), "".join(re.sub(r"[^A-Z0-9]", " ", str(model).upper()).split())}
    return frozenset(t for t in u.split() if t and t not in NOISE and t not in model_words)


def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip().lower() in ("", "nan", "none", "null")


def _clean_text(v) -> str:
    return "" if _blank(v) else str(v).strip()


# --------------------------------------------------------------------------- #
# Reading the stock workbook
# --------------------------------------------------------------------------- #

def _all_hints() -> set:
    hints = set()
    for names in COLUMN_CANDIDATES.values():
        hints |= {n.strip().lower() for n in names}
    return hints


def _find_header_row(raw: pd.DataFrame) -> Optional[int]:
    """Stock exports often have a title line or two above the headings."""
    hints = _all_hints()
    best_row, best_score = None, 0
    for i in range(min(15, len(raw))):
        cells = [str(c).strip().lower() for c in raw.iloc[i].tolist() if not _blank(c)]
        score = sum(1 for c in cells if c in hints)
        if score > best_score:
            best_row, best_score = i, score
    return best_row if best_score >= 2 else None


def _dedupe(names: list) -> list:
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}.{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _read_one_sheet(xl: pd.ExcelFile, sheet: str):
    """Header-detected DataFrame for one sheet, or None if it has no usable header."""
    raw = xl.parse(sheet, header=None, dtype=object)
    if raw.empty:
        return None
    hdr = _find_header_row(raw)
    if hdr is None:
        return None
    df = raw.iloc[hdr + 1:].copy()
    df.columns = _dedupe([str(c).strip() for c in raw.iloc[hdr].tolist()])
    return df.dropna(how="all").reset_index(drop=True)


def typed_sheets(path: str) -> list:
    """Sheets whose NAME says what the stock is: 'Physical Stock' -> Physical,
    'In Transit' -> In Transit. When a workbook has these, the sheet name is the truth
    (the 'In Out Status' column in the DMS export says 'In Transit' on both sheets)."""
    try:
        names = pd.ExcelFile(path).sheet_names
    except Exception:
        return []
    out = []
    for n in names:
        low = n.strip().lower()
        if "transit" in low:
            out.append((n, TRANSIT))
        elif "physical" in low:
            out.append((n, PHYSICAL))
    return out


def has_stock_sheets(path: str) -> bool:
    return bool(typed_sheets(path))


def _read_stock_sheet(path: str):
    xl = pd.ExcelFile(path)
    for sheet in xl.sheet_names:
        df = _read_one_sheet(xl, sheet)
        if df is not None:
            return df, sheet
    raise ValueError(
        "Could not find a header row in the stock workbook. It needs at least a Model and a "
        "Variant column (plus ideally Colour, VIN/Chassis and a Physical/Transit column)."
    )


def _find_col(df: pd.DataFrame, key: str, exclude: tuple = ()) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns if c not in exclude}
    for want in COLUMN_CANDIDATES[key]:
        hit = lower.get(want.strip().lower())
        if hit is not None:
            return hit
    for want in COLUMN_CANDIDATES[key]:          # looser: heading contains the name
        w = want.strip().lower()
        if len(w) < 4:
            continue
        for low, col in lower.items():
            if w in low:
                return col
    return None


def _has_type_words(series: pd.Series) -> bool:
    vals = series.dropna().astype(str).str.lower()
    return bool(vals.str.contains("transit|physical", regex=True).any())


def _find_type_col(df: pd.DataFrame, exclude: tuple = ()) -> Optional[str]:
    """The Physical / Transit column: prefer a known heading whose values really say
    physical/transit; otherwise scan every column for those words."""
    lower = {str(c).strip().lower(): c for c in df.columns if c not in exclude}
    for want in COLUMN_CANDIDATES["stock_type"]:
        col = lower.get(want.strip().lower())
        if col is not None and _has_type_words(df[col]):
            return col
    for col in df.columns:
        if col in exclude:
            continue
        if not pd.api.types.is_numeric_dtype(df[col]) and _has_type_words(df[col]):
            return col
    for want in COLUMN_CANDIDATES["stock_type"]:   # heading only - values less clear
        col = lower.get(want.strip().lower())
        if col is not None:
            return col
    return None


def _stock_type(value) -> str:
    text = _clean_text(value).lower()
    if any(w in text for w in _TRANSIT_WORDS):
        return TRANSIT
    return PHYSICAL


def load_stock(path: str) -> tuple:
    """Read and normalise the stock.

    Two layouts are understood:
      * ONE workbook with a 'Physical Stock' sheet and an 'In Transit' sheet (the same
        workbook that holds the Enquiry sheet) - the sheet name decides Physical / Transit.
      * A single stock sheet with a Physical / Transit column (Stock.xlsx).

    Returns (DataFrame, info). DataFrame columns:
        Model, Variant, Color, Chassis, Stock Type (Physical / In Transit),
        Alloc (OPEN / ALLOCATED), Age, Location, Allocated To, MK, T, VK, CK
    info: file name, row count, which source column fed which field, warnings.
    """
    sheets = typed_sheets(path)
    if not sheets:
        raw, sheet = _read_stock_sheet(path)
        return _build_stock(raw, sheet, path, None)

    xl = pd.ExcelFile(path)
    frames, infos, per_sheet, warnings = [], [], {}, []
    for sheet, forced in sheets:
        raw = _read_one_sheet(xl, sheet)
        if raw is None:
            per_sheet[sheet] = 0
            continue
        try:
            df, info = _build_stock(raw, sheet, path, forced)
        except ValueError as exc:
            warnings.append(f"Sheet '{sheet}' skipped: {exc}")
            per_sheet[sheet] = 0
            continue
        per_sheet[sheet] = int(len(df))
        if len(df):
            frames.append(df)
            infos.append(info)
        warnings += info["warnings"]
    if not frames:
        raise ValueError("The 'Physical Stock' / 'In Transit' sheets have no vehicles in them "
                         "(or lack Model / Variant columns).")
    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    has_vin = df["Chassis"] != ""
    dup = has_vin & df.duplicated(subset=["Chassis"], keep="first")   # Physical sheet is first
    df = df[~dup].reset_index(drop=True)
    if before - len(df):
        warnings.append(f"{before - len(df)} VIN(s) appeared on both sheets - counted once (as Physical).")
    first = infos[0]
    info = dict(first)
    info.update({
        "rows": int(len(df)),
        "sheet": " + ".join(per_sheet),
        "sheet_rows": per_sheet,
        "from_sheet_names": True,
        "warnings": list(dict.fromkeys(warnings)),
    })
    return df, info


def _build_stock(raw: pd.DataFrame, sheet: str, path: str, forced_type: Optional[str]) -> tuple:

    model_c = _find_col(raw, "model")
    variant_c = _find_col(raw, "variant", exclude=(model_c,) if model_c else ())
    if model_c is None or variant_c is None:
        raise ValueError(
            "The stock workbook needs a Model and a Variant column. Found: "
            + ", ".join(map(str, list(raw.columns)[:20]))
        )
    used = (model_c, variant_c)
    color_c = _find_col(raw, "color", exclude=used)
    chassis_c = _find_col(raw, "chassis", exclude=used)
    type_c = None if forced_type else _find_type_col(raw, exclude=used)
    used2 = tuple(c for c in used + (color_c, chassis_c, type_c) if c)
    age_c = _find_col(raw, "age", exclude=used2)
    loc_c = _find_col(raw, "location", exclude=used2)
    alloc_c = _find_col(raw, "allocated", exclude=used2 + ((age_c,) if age_c else ()))
    # Optional extras for the Vehicle Stock inventory page - never required, never
    # used for matching, so a missing one just means that field is blank/0 there.
    fuel_c = _find_col(raw, "fuel_type", exclude=used2)
    financier_c = _find_col(raw, "financier", exclude=used2)
    basic_price_c = _find_col(raw, "basic_price", exclude=used2)
    invoice_amt_c = _find_col(raw, "invoice_amt", exclude=used2 + ((basic_price_c,) if basic_price_c else ()))

    warnings = []
    df = pd.DataFrame({
        "Model": raw[model_c].map(_clean_text),
        "Variant": raw[variant_c].map(_clean_text),
        "Color": raw[color_c].map(_clean_text) if color_c else "",
        "Chassis": raw[chassis_c].map(_clean_text) if chassis_c else "",
        "Location": raw[loc_c].map(_clean_text) if loc_c else "",
        "Fuel Type": raw[fuel_c].map(_clean_text) if fuel_c else "",
        "Financier Name": raw[financier_c].map(_clean_text) if financier_c else "",
        "Basic Price": (pd.to_numeric(raw[basic_price_c].astype(str).str.replace(",", "", regex=False),
                                       errors="coerce").fillna(0.0) if basic_price_c else 0.0),
        "HMIL Invoice Amt": (pd.to_numeric(raw[invoice_amt_c].astype(str).str.replace(",", "", regex=False),
                                            errors="coerce").fillna(0.0) if invoice_amt_c else 0.0),
    })
    df = df[(df["Model"] != "") | (df["Variant"] != "")].reset_index(drop=True)
    raw = raw.loc[raw[model_c].notna() | raw[variant_c].notna()].reset_index(drop=True)

    # Physical / In Transit
    if forced_type:
        type_series = pd.Series([""] * len(df))
        df["Stock Type"] = forced_type
    elif type_c:
        type_series = raw[type_c]
        df["Stock Type"] = type_series.map(_stock_type)
        unknown = sorted({
            _clean_text(v) for v in type_series
            if _clean_text(v) and not any(w in _clean_text(v).lower()
                                          for w in _TRANSIT_WORDS + _PHYSICAL_WORDS + _ALLOC_WORDS)
        })
        if unknown:
            warnings.append("These values in the '%s' column were not recognised as Physical or "
                            "Transit and were treated as Physical: %s" % (type_c, ", ".join(unknown[:8])))
    else:
        type_series = pd.Series([""] * len(df))
        df["Stock Type"] = PHYSICAL
        warnings.append("No Physical / In Transit column was found in the stock file - every unit is "
                        "being treated as Physical. Add a column such as 'Stock Type' with the values "
                        "Physical / In Transit.")

    # Allocated?  Blocked = Y, a booking / customer against the unit, or a status that says so.
    blocked_c = next((c for c in raw.columns if str(c).strip().lower() in ("blocked", "block status")), None)
    status_c = next((c for c in raw.columns if str(c).strip().lower() == "stock status"), None)

    def _is_allocated(i) -> bool:
        if blocked_c and _clean_text(raw[blocked_c].iloc[i]).lower() in ("y", "yes", "blocked", "true"):
            return True
        if status_c and any(w in _clean_text(raw[status_c].iloc[i]).lower() for w in _ALLOC_WORDS):
            return True
        if type_c and not forced_type and any(w in _clean_text(type_series.iloc[i]).lower() for w in _ALLOC_WORDS):
            return True
        if alloc_c:
            v = _clean_text(raw[alloc_c].iloc[i]).lower()
            return v not in _EMPTY
        return False

    df["Alloc"] = [ALLOC if _is_allocated(i) else FREE for i in range(len(df))]
    df["Allocated To"] = raw[alloc_c].map(_clean_text) if alloc_c else ""
    df["Age"] = pd.to_numeric(raw[age_c], errors="coerce") if age_c else pd.Series([float("nan")] * len(df))

    df["MK"] = df["Model"].map(mkey)
    df["T"] = [toks(v, m) for v, m in zip(df["Variant"], df["Model"])]
    df["VK"] = ["|".join(sorted(t)) for t in df["T"]]
    df["CK"] = df["Color"].map(ckey)

    if not color_c:
        warnings.append("No Colour column found - colours cannot be matched, so enquiries are matched "
                        "on Model + Variant only.")
    if not chassis_c:
        warnings.append("No VIN / Chassis column found - chassis numbers will be blank.")

    info = {
        "file": os.path.basename(path),
        "sheet": sheet,
        "rows": int(len(df)),
        "columns_used": {
            "model": model_c, "variant": variant_c, "color": color_c, "chassis": chassis_c,
            "stock_type": type_c or (f"sheet name ({forced_type})" if forced_type else None), "age": age_c, "location": loc_c, "allocated": alloc_c,
            "fuel_type": fuel_c, "financier": financier_c, "basic_price": basic_price_c, "invoice_amt": invoice_amt_c,
        },
        "warnings": warnings,
        "has_color": bool(color_c),
        "has_type": bool(type_c or forced_type),
    }
    return df, info


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

MATCH_STATUSES = [
    "Exact Match - Physical",
    "Exact Match - In Transit",
    "Exact Match - Allocated Only",
    "Variant Available - Other Color",
    "Model Available - Other Variant",
    "No Stock Available",
    "Variant Not Captured",
]

MATCH_MEANING = {
    "Exact Match - Physical": "Model, variant and colour are free and physically in stock",
    "Exact Match - In Transit": "Exact unit is only in transit - not yet received",
    "Exact Match - Allocated Only": "Exact unit exists but is already allocated",
    "Variant Available - Other Color": "Same variant is free, but not in the colour enquired",
    "Model Available - Other Variant": "Same model is free, but a different variant",
    "No Stock Available": "Nothing free for this model - needs indent",
    "Variant Not Captured": "Variant is blank in the enquiry - cannot be matched",
}


def _fmt_date(v) -> str:
    try:
        if pd.isna(v):
            return ""
        return pd.Timestamp(v).strftime("%d/%m/%Y")
    except Exception:
        return ""


def _pt_text(phys: int, transit: int) -> str:
    """'3 Physical, 1 Transit' / '' - used for the 'other colour / other variant' cells."""
    parts = []
    if phys:
        parts.append(f"{phys} Physical")
    if transit:
        parts.append(f"{transit} Transit")
    return ", ".join(parts)


def _join_unique(values) -> str:
    seen = []
    for v in values:
        v = str(v).strip()
        if v and v not in seen:
            seen.append(v)
    return ", ".join(seen)


def build_match(enq: pd.DataFrame, stk: pd.DataFrame, has_color: bool = True) -> pd.DataFrame:
    """One row per enquiry with its match status and physical / transit quantities."""
    e = pd.DataFrame({
        "Customer ID": enq.get("Customer ID"),
        "Enquiry Date": enq.get("Enquiry Date"),
        "Customer Name": enq.get("Name of the Customer"),
        "Phone": enq.get("Phone", enq.get("Contact Number")),
        "Consultant": enq.get("Consultant Name"),
        "Enquiry Status": enq.get("Enquiry Status"),
        "Source": enq.get("Source"),
        "Model": enq.get("Model"),
        "Variant": enq.get("Variant"),
        "Fuel": enq.get("Fuel type"),
        "Color": enq.get("Color"),
        "Next Followup": enq.get("Next Followup Date"),
    }).reset_index(drop=True)

    e["MK"] = e["Model"].map(mkey)
    e["CK"] = e["Color"].map(ckey) if has_color else ""
    e["any_color"] = (e["CK"] == "")   # colour not captured -> any colour will do

    free = stk[stk["Alloc"] == FREE]
    alloc = stk[stk["Alloc"] == ALLOC]

    # ---- precomputed stock counts (instead of scanning stock for every enquiry) ----
    model_ct = Counter(zip(free["MK"], free["Stock Type"]))
    variant_ct = Counter(zip(free["MK"], free["VK"], free["Stock Type"]))
    exact_ct = Counter(zip(free["MK"], free["VK"], free["CK"], free["Stock Type"]))
    alloc_variant_ct = Counter(zip(alloc["MK"], alloc["VK"]))
    alloc_exact_ct = Counter(zip(alloc["MK"], alloc["VK"], alloc["CK"]))

    stock_by_model = {k: g for k, g in stk.groupby("MK", sort=False)}
    free_by_exact = {k: g for k, g in free.groupby(["MK", "VK", "CK"], sort=False)}
    free_by_variant = {k: g for k, g in free.groupby(["MK", "VK"], sort=False)}

    res_cache = {}

    def resolve(mk, variant, model):
        """Which stock variant does this enquiry variant correspond to?"""
        key = (mk, str(variant), str(model))
        if key in res_cache:
            return res_cache[key]
        if _blank(variant):
            out = ("", "", "Variant not captured in enquiry")
        else:
            et = toks(variant, model)
            cand = stock_by_model.get(mk)
            if cand is None or len(cand) == 0:
                out = ("", "", "No stock of this model at all")
            else:
                ok = cand[[bool(et) and et <= t for t in cand["T"]]]
                if len(ok) == 0:
                    out = ("", "", "Variant not in stock file (model is)")
                else:
                    ok = ok.assign(extra=[len(t - et) for t in ok["T"]]).sort_values("extra", kind="stable")
                    best = ok.iloc[0]
                    n_vk = len(set(ok["VK"]))
                    note = "" if n_vk == 1 else "Enquiry variant matches %d stock variants; closest shown" % n_vk
                    out = (best["Variant"], best["VK"], note)
        res_cache[key] = out
        return out

    cols = {k: [] for k in (
        "Resolved Stock Variant", "VK", "Note", "Free Physical", "Free Transit", "Allocated",
        "Other Color Physical", "Other Color Transit", "Other Variant Physical", "Other Variant Transit",
        "Chassis Physical", "Chassis Transit", "Location", "Oldest Stock Age", "Match Status")}

    for mk, variant, model, ck, any_c in zip(e["MK"], e["Variant"], e["Model"], e["CK"], e["any_color"]):
        resolved, vk, note = resolve(mk, variant, model)
        blank_variant = _blank(variant)
        fp = ft = a = ocp = oct_ = ovp = ovt = 0
        ch_p = ch_t = locs = ""
        oldest = ""

        if vk:
            model_p, model_t = model_ct[(mk, PHYSICAL)], model_ct[(mk, TRANSIT)]
            var_p, var_t = variant_ct[(mk, vk, PHYSICAL)], variant_ct[(mk, vk, TRANSIT)]
            if any_c:                                   # colour not captured: any colour counts
                fp, ft = var_p, var_t
                a = alloc_variant_ct[(mk, vk)]
                ocp = oct_ = 0
                exact_units = free_by_variant.get((mk, vk))
                if not note:
                    note = "Colour not captured - any colour of this variant counted"
                else:
                    note += "; colour not captured - any colour counted"
            else:
                fp = exact_ct[(mk, vk, ck, PHYSICAL)]
                ft = exact_ct[(mk, vk, ck, TRANSIT)]
                a = alloc_exact_ct[(mk, vk, ck)]
                ocp, oct_ = var_p - fp, var_t - ft
                exact_units = free_by_exact.get((mk, vk, ck))
            ovp, ovt = model_p - var_p, model_t - var_t

            if exact_units is not None and len(exact_units):
                ph = exact_units[exact_units["Stock Type"] == PHYSICAL]
                tr = exact_units[exact_units["Stock Type"] == TRANSIT]
                ch_p = ", ".join(x for x in ph["Chassis"].astype(str) if x.strip())
                ch_t = ", ".join(x for x in tr["Chassis"].astype(str) if x.strip())
                locs = _join_unique(list(ph["Location"]) + list(tr["Location"]))
                ages = exact_units["Age"].dropna()
                oldest = int(ages.max()) if len(ages) else ""
        elif not blank_variant:
            # Variant given but not resolvable: still tell them what of the model is free.
            ovp, ovt = model_ct[(mk, PHYSICAL)], model_ct[(mk, TRANSIT)]

        if blank_variant:
            status = "Variant Not Captured"
        elif fp > 0:
            status = "Exact Match - Physical"
        elif ft > 0:
            status = "Exact Match - In Transit"
        elif a > 0:
            status = "Exact Match - Allocated Only"
        elif ocp + oct_ > 0:
            status = "Variant Available - Other Color"
        elif ovp + ovt > 0:
            status = "Model Available - Other Variant"
        else:
            status = "No Stock Available"

        for k, v in zip(cols, (resolved, vk, note, fp, ft, a, ocp, oct_, ovp, ovt,
                               ch_p, ch_t, locs, oldest, status)):
            cols[k].append(v)

    for k, v in cols.items():
        e[k] = v
    return e


# --------------------------------------------------------------------------- #
# Demand vs stock, stock without enquiry, summary
# --------------------------------------------------------------------------- #

def _demand_table(e: pd.DataFrame, stk: pd.DataFrame) -> list:
    free = stk[stk["Alloc"] == FREE]
    alloc = stk[stk["Alloc"] == ALLOC]

    demand = Counter()
    labels = {}
    for _, r in e[e["VK"] != ""].iterrows():
        k = (r["MK"], r["VK"], r["CK"])
        demand[k] += 1
        labels.setdefault(k, (r["Model"], r["Resolved Stock Variant"],
                              r["Color"] if r["CK"] else "(Colour not captured)"))
    for _, r in stk.iterrows():
        labels.setdefault((r["MK"], r["VK"], r["CK"]), (r["Model"], r["Variant"], r["Color"] or "(Not in file)"))

    fp = Counter(zip(free.loc[free["Stock Type"] == PHYSICAL, "MK"], free.loc[free["Stock Type"] == PHYSICAL, "VK"],
                     free.loc[free["Stock Type"] == PHYSICAL, "CK"]))
    ft = Counter(zip(free.loc[free["Stock Type"] == TRANSIT, "MK"], free.loc[free["Stock Type"] == TRANSIT, "VK"],
                     free.loc[free["Stock Type"] == TRANSIT, "CK"]))
    al = Counter(zip(alloc["MK"], alloc["VK"], alloc["CK"]))
    fp_v = Counter(zip(free.loc[free["Stock Type"] == PHYSICAL, "MK"], free.loc[free["Stock Type"] == PHYSICAL, "VK"]))
    ft_v = Counter(zip(free.loc[free["Stock Type"] == TRANSIT, "MK"], free.loc[free["Stock Type"] == TRANSIT, "VK"]))
    al_v = Counter(zip(alloc["MK"], alloc["VK"]))

    rows = []
    for k, (model, variant, color) in labels.items():
        mk, vk, ck = k
        if ck == "" and demand.get(k):        # colour-not-captured demand: variant-level stock
            p, t, a = fp_v[(mk, vk)], ft_v[(mk, vk)], al_v[(mk, vk)]
        else:
            p, t, a = fp[k], ft[k], al[k]
        d = demand.get(k, 0)
        gap_p, gap_all = p - d, p + t - d
        if d == 0 and p == 0 and t == 0:
            pos = "Allocated Only - No Action"
        elif d > 0 and p == 0 and t == 0:
            pos = "Demand - No Stock"
        elif d > 0 and p == 0 and t > 0:
            pos = "Demand - Only In Transit"
        elif d == 0:
            pos = "Stock - No Enquiry"
        elif gap_p < 0:
            pos = "Short of Physical Stock"
        elif gap_p == 0:
            pos = "Balanced"
        else:
            pos = "Surplus Stock"
        rows.append({
            "model": model, "variant": variant, "color": color,
            "enquiries": int(d), "physical": int(p), "transit": int(t), "allocated": int(a),
            "gap_physical": int(gap_p), "gap_total": int(gap_all), "position": pos,
        })
    order = {"Demand - No Stock": 0, "Demand - Only In Transit": 1, "Short of Physical Stock": 2,
             "Balanced": 3, "Surplus Stock": 4, "Stock - No Enquiry": 5, "Allocated Only - No Action": 6}
    rows.sort(key=lambda r: (order.get(r["position"], 9), str(r["model"]), str(r["variant"]), str(r["color"])))
    return rows


def _no_enquiry_table(e: pd.DataFrame, stk: pd.DataFrame) -> list:
    free = stk[stk["Alloc"] == FREE]
    demand = Counter(zip(e.loc[e["VK"] != "", "MK"], e.loc[e["VK"] != "", "VK"], e.loc[e["VK"] != "", "CK"]))
    demand_any = Counter(zip(e.loc[(e["VK"] != "") & (e["CK"] == ""), "MK"],
                             e.loc[(e["VK"] != "") & (e["CK"] == ""), "VK"]))
    out = []
    for _, r in free.iterrows():
        n = demand[(r["MK"], r["VK"], r["CK"])] + demand_any[(r["MK"], r["VK"])]
        age = r["Age"]
        out.append({
            "model": r["Model"], "variant": r["Variant"], "color": r["Color"],
            "chassis": r["Chassis"], "stock_type": r["Stock Type"], "age": "" if pd.isna(age) else int(age),
            "location": r["Location"], "enquiries": int(n),
        })
    out.sort(key=lambda x: (x["age"] == "", -(x["age"] if x["age"] != "" else 0)))
    return out


def _by_model(e: pd.DataFrame, stk: pd.DataFrame) -> list:
    free = stk[stk["Alloc"] == FREE]
    label = {}
    for _, r in e.iterrows():
        label.setdefault(r["MK"], r["Model"])
    for _, r in stk.iterrows():
        label.setdefault(r["MK"], r["Model"])
    enq_ct = Counter(e["MK"])
    ph = Counter(free.loc[free["Stock Type"] == PHYSICAL, "MK"])
    tr = Counter(free.loc[free["Stock Type"] == TRANSIT, "MK"])
    rows = [{"label": label[k], "enquiries": int(enq_ct[k]), "physical": int(ph[k]), "transit": int(tr[k])}
            for k in label if k]
    rows.sort(key=lambda r: (r["enquiries"], r["physical"] + r["transit"]), reverse=True)
    return rows[:12]


def compute(enq: pd.DataFrame, stk: pd.DataFrame, info: dict) -> dict:
    """Everything the Enquiry Wise Stock page needs, as JSON-ready structures."""
    e = build_match(enq, stk, has_color=info.get("has_color", True))
    demand = _demand_table(e, stk)
    no_enq = _no_enquiry_table(e, stk)
    by_model = _by_model(e, stk)

    free = stk[stk["Alloc"] == FREE]
    physical_free = int((free["Stock Type"] == PHYSICAL).sum())
    transit_free = int((free["Stock Type"] == TRANSIT).sum())
    aged = int(((free["Stock Type"] == PHYSICAL) & (free["Age"] > AGED_DAYS)).sum())

    counts = Counter(e["Match Status"])
    match_counts = [{"status": s, "count": int(counts.get(s, 0)), "meaning": MATCH_MEANING[s]}
                    for s in MATCH_STATUSES]

    rows = []
    for _, r in e.iterrows():
        rows.append({
            "date": _fmt_date(r["Enquiry Date"]),
            "customer_id": _clean_text(r["Customer ID"]),
            "customer": _clean_text(r["Customer Name"]),
            "phone": _clean_text(r["Phone"]),
            "consultant": _clean_text(r["Consultant"]),
            "enquiry_status": _clean_text(r["Enquiry Status"]),
            "source": _clean_text(r["Source"]),
            "model": _clean_text(r["Model"]),
            "variant": _clean_text(r["Variant"]),
            "fuel": _clean_text(r["Fuel"]),
            "color": _clean_text(r["Color"]),
            "next_followup": _fmt_date(r["Next Followup"]),
            "match_status": r["Match Status"],
            "resolved_variant": r["Resolved Stock Variant"],
            "free_physical": int(r["Free Physical"]),
            "free_transit": int(r["Free Transit"]),
            "allocated": int(r["Allocated"]),
            "other_color": _pt_text(int(r["Other Color Physical"]), int(r["Other Color Transit"])),
            "other_variant": _pt_text(int(r["Other Variant Physical"]), int(r["Other Variant Transit"])),
            "chassis_physical": r["Chassis Physical"],
            "chassis_transit": r["Chassis Transit"],
            "location": r["Location"],
            "oldest_age": r["Oldest Stock Age"],
            "note": r["Note"],
        })

    summary = {
        "enquiries": int(len(e)),
        "exact_physical": int(counts.get("Exact Match - Physical", 0)),
        "exact_transit": int(counts.get("Exact Match - In Transit", 0)),
        "no_stock": int(counts.get("No Stock Available", 0)),
        "stock_total": int(len(stk)),
        "stock_physical": physical_free,
        "stock_transit": transit_free,
        "stock_allocated": int((stk["Alloc"] == ALLOC).sum()),
        "stock_no_enquiry": int(sum(1 for r in no_enq if r["enquiries"] == 0)),
        "stock_aged": aged,
        "aged_days": AGED_DAYS,
    }
    return {
        "stock_loaded": True,
        "stock_info": info,
        "summary": summary,
        "match_counts": match_counts,
        "enquiries": rows,
        "demand": demand,
        "no_enquiry": no_enq,
        "by_model": by_model,
    }


def empty_result(info: Optional[dict] = None) -> dict:
    return {
        "stock_loaded": False,
        "stock_info": info or {},
        "summary": {}, "match_counts": [], "enquiries": [], "demand": [], "no_enquiry": [], "by_model": [],
    }


# --------------------------------------------------------------------------- #
# Excel export (the dashboard equivalent of the Mahindra report workbook)
# --------------------------------------------------------------------------- #

def export_workbook(result: dict, stock_units: Optional[pd.DataFrame] = None) -> bytes:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    s = result["summary"]
    summary_rows = [["Match Status", "No. of Enquiries", "What it means"]] + \
        [[m["status"], m["count"], m["meaning"]] for m in result["match_counts"]] + \
        [["Total Enquiries", s["enquiries"], ""], [], ["Stock Position", "Units", ""],
         ["Total stock units", s["stock_total"], ""],
         ["Free - Physical", s["stock_physical"], ""],
         ["Free - In Transit", s["stock_transit"], ""],
         ["Allocated", s["stock_allocated"], ""],
         ["Free stock with no matching enquiry", s["stock_no_enquiry"], ""],
         ["Physical stock older than %d days" % s["aged_days"], s["stock_aged"], ""]]

    enq_df = pd.DataFrame(result["enquiries"]).rename(columns={
        "date": "Enquiry Date", "customer_id": "Customer ID", "customer": "Customer Name",
        "phone": "Phone", "consultant": "Consultant", "enquiry_status": "Enquiry Status",
        "source": "Source", "model": "Model", "variant": "Variant (as enquired)", "fuel": "Fuel",
        "color": "Colour", "next_followup": "Next Follow-up", "match_status": "Match Status",
        "resolved_variant": "Resolved Stock Variant", "free_physical": "Free - Physical",
        "free_transit": "Free - In Transit", "allocated": "Allocated",
        "other_color": "Same Variant - Other Colour (Free)", "other_variant": "Same Model - Other Variant (Free)",
        "chassis_physical": "Chassis (Physical)", "chassis_transit": "Chassis (In Transit)",
        "location": "Location", "oldest_age": "Oldest Stock Age (Days)", "note": "Note"})
    dem_df = pd.DataFrame(result["demand"]).rename(columns={
        "model": "Model", "variant": "Variant", "color": "Colour", "enquiries": "Enquiries (Demand)",
        "physical": "Free - Physical", "transit": "Free - In Transit", "allocated": "Allocated",
        "gap_physical": "Gap (Physical - Enquiries)", "gap_total": "Gap (Physical + Transit - Enquiries)",
        "position": "Position"})
    no_df = pd.DataFrame(result["no_enquiry"]).rename(columns={
        "model": "Model", "variant": "Variant", "color": "Colour", "chassis": "Chassis No.",
        "stock_type": "Physical / Transit", "age": "Stock Age (Days)", "location": "Location",
        "enquiries": "Enquiries for this Combination"})

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(summary_rows).to_excel(xw, sheet_name="Summary", index=False, header=False)
        enq_df.to_excel(xw, sheet_name="Enquiry Stock Match", index=False)
        dem_df.to_excel(xw, sheet_name="Demand vs Stock", index=False)
        no_df.to_excel(xw, sheet_name="Free Stock - Coverage", index=False)
        if stock_units is not None and len(stock_units):
            su = stock_units[["Model", "Variant", "Color", "Chassis", "Stock Type", "Alloc", "Age",
                              "Location", "Allocated To"]].rename(columns={
                "Color": "Colour", "Chassis": "Chassis No.", "Stock Type": "Physical / Transit",
                "Alloc": "Allocation", "Age": "Stock Age (Days)"})
            su.to_excel(xw, sheet_name="Stock Data", index=False)

        head_fill = PatternFill("solid", fgColor="1C1C1C")
        for ws in xw.book.worksheets:
            ws.sheet_properties.tabColor = "002C5F"
            if ws.title == "Summary":
                ws.column_dimensions["A"].width = 40
                ws.column_dimensions["B"].width = 18
                ws.column_dimensions["C"].width = 64
                for cell in ws[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = head_fill
                continue
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = head_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions
            for j, col in enumerate(ws.columns, start=1):
                longest = max((len(str(c.value)) for c in list(col)[:200] if c.value is not None), default=8)
                ws.column_dimensions[get_column_letter(j)].width = min(max(longest + 2, 10), 46)
    return buf.getvalue()
