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

# How the Vehicle Stock page works out a unit's age:
#   "invoice" -> days from the HMI Invoice Date to today (what the page says: "Days since HMI invoice")
#   "file"    -> use the 'Stock Age' number exactly as it appears in the workbook
# The DMS 'Stock Age' column counts from the plant Sign Off Date (it is identical to 'Sign Age'),
# which is earlier than the invoice date, so it over-states how long the car has been the dealer's.
STOCK_AGE_BASIS = "invoice"

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
    "invoice_date": ["HMI Invoice Date", "HMIL Invoice Date", "Invoice Date", "Invoice Dt"],
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


def _parse_stock_date(series: pd.Series) -> pd.Series:
    """dd/mm/yyyy text (or real Excel dates) -> datetime; blanks / junk -> NaT."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    out = pd.to_datetime(series, format="%d/%m/%Y", errors="coerce")
    miss = out.isna() & series.notna()
    if miss.any():
        out.loc[miss] = pd.to_datetime(series[miss], dayfirst=True, errors="coerce")
    return out


def stock_age_days(df: pd.DataFrame, today=None) -> pd.Series:
    """Age in days of every unit, worked out live (so it keeps growing as the days pass).
    Uses the HMI Invoice Date when STOCK_AGE_BASIS == "invoice"; any unit without a usable
    invoice date falls back to the workbook's own 'Stock Age' figure."""
    file_age = pd.to_numeric(df["Age"], errors="coerce") if "Age" in df.columns \
        else pd.Series(float("nan"), index=df.index)
    if STOCK_AGE_BASIS != "invoice" or "Invoice Date" not in df.columns:
        return file_age
    ref = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    live = (ref - pd.to_datetime(df["Invoice Date"], errors="coerce")).dt.days.clip(lower=0)
    return live.where(live.notna(), file_age)


def with_live_age(stk: pd.DataFrame) -> pd.DataFrame:
    """Copy of the stock with 'Age' = days since the HMI invoice date as of today (STOCK_AGE_BASIS), the same
    basis as the Vehicle Stock page. The workbook's own 'Stock Age' counts from the plant sign-off date and
    over-states how long the car has been with the dealer."""
    if stk is None or stk.empty:
        return stk
    out = stk.copy()
    out["Age"] = stock_age_days(out)
    return out


def _exact_col(df: pd.DataFrame, names: tuple) -> Optional[str]:
    """First column whose heading equals one of `names` (case-insensitive, exact - never a partial match)."""
    lower = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        hit = lower.get(n.strip().lower())
        if hit is not None:
            return hit
    return None


def _id_text(v) -> str:
    """Invoice / order numbers: '126925359' (never '126925359.0')."""
    t = _clean_text(v)
    return t[:-2] if t.endswith(".0") and t[:-2].isdigit() else t


# Extra detail columns shown in the Vehicle Stock pop-up (all optional).
DETAIL_COLUMNS = {
    "HMI Invoice No": ("HMI Invoice No", "HMIL Invoice No", "HMI Invoice Number", "Invoice No", "Invoice No."),
    "Interior Color": ("Interior Color Desc", "Interior Colour Desc", "Interior Color", "Interior Colour",
                       "Interior Color Name", "Interior Colour Name"),
    "Order No": ("Order No", "Order No.", "Order Number"),
    "Stock Status": ("Stock Status", "Status of Stock"),
    "Order Type": ("Order Type",),
}


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
    invoice_date_c = _find_col(raw, "invoice_date", exclude=used2)

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
    df["Invoice Date"] = _parse_stock_date(raw[invoice_date_c]) if invoice_date_c else pd.NaT
    for out_name, names in DETAIL_COLUMNS.items():
        c = _exact_col(raw, names)
        df[out_name] = raw[c].map(_id_text) if c else ""

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
            "invoice_date": invoice_date_c,
        },
        "warnings": warnings,
        "has_color": bool(color_c),
        "has_type": bool(type_c or forced_type),
    }
    return df, info


# --------------------------------------------------------------------------- #
# Upgrade ladder ("Next Variant - one step up", ported from the Kia report)
# --------------------------------------------------------------------------- #
# Kia has a price master, so its ladder is "next higher ex-showroom price". Hyundai has
# no price master in this dashboard, so the ladder is built from the TRIM ORDER below
# (lowest -> highest) for each model. Two variants are on the same ladder only when
# everything except the trim (engine, gearbox, fuel / CNG) is identical, so the next
# variant is always the same engine + gearbox + fuel, one trim higher.
#
# Edit TRIM_LADDERS if Hyundai changes the trim line-up. "HX#" means the numbered HX
# trims (HX 2, 4, 5, 5+, 6, 6T, 8, 10 ...), ordered by number.
# Corporate, SE (special edition) and anything not listed here are kept OUT of the
# ladder: they are never suggested as an upgrade and show "Not in upgrade ladder".
TRIM_LADDERS = {
    "AURA":          [["E", "S", "SX"]],
    "ALCAZAR":       [["Executive", "Prestige", "Platinum", "Signature"]],
    "I20":           [["Magna Executive", "Magna", "Sportz", "Sportz(O)", "Asta", "Asta(O)"]],
    "GRANDI10NIOS":  [["Era", "Magna", "Sportz", "Sportz(O)", "Asta"]],
    "CRETA":         [["E", "EX", "EX(O)", "S", "S(O)", "SX", "SX Tech", "SX Premium", "SX(O)", "King"]],
    "CRETANLINE":    [["N6", "N8", "N10"]],
    "VENUENLINE":    [["N6", "N8", "N10"]],
    "I20NLINE":      [["N6", "N8", "N10"]],
    "EXTER":         [["HX#"]],
    "VENUE":         [["HX#"]],
    "VERNA":         [["HX#"], ["EX", "SX", "SX(O)"]],
}
ANY_COLOR = "(ANY)"      # key used in the Excel export when a colour is not captured / not in the file
# Trims that look like a ladder trim but are a different car (kept out of the ladder)
LADDER_EXCLUDE = {"GRANDI10NIOS": [r"SPORTZ V\b"]}
TOP_VARIANT = "Top variant - no upgrade"
NOT_IN_LADDER = "Not in upgrade ladder"
_LADDER_NOISE = {"KAPPA", "BS6", "BS62", "BSVI", "NEW", "ALL", "HYUNDAI", "MY", "OPT"}


def _ladder_norm(variant, model) -> str:
    u = str(variant).upper()
    model_words = set(re.sub(r"[^A-Z0-9]", " ", str(model).upper()).split())
    model_words |= {mkey(model)} | _LADDER_NOISE
    u = " ".join(w for w in re.sub(r"[^A-Z0-9()+.]", " ", u).split() if w not in model_words)
    u = re.sub(r"\bDUAL\s*CNG", "CNG", u)
    u = re.sub(r"\s*\(\s*O\s*\)", "(O)", u)
    u = re.sub(r"(?<=\d)(?=[A-Z])", " ", u)                  # "1.2MT" -> "1.2 MT", "HX6DUALCNGSE" -> "HX6 DUAL..."
    u = re.sub(r"(?<=\bCNG)(?=SE\b)", " ", u)
    return re.sub(r"\s+", " ", u).strip()


def ladder_position(mk: str, variant, model):
    """-> (group_key, rank) for a variant on its model's upgrade ladder, or None."""
    ladders = TRIM_LADDERS.get(mk)
    if not ladders or _blank(variant):
        return None
    u = _ladder_norm(variant, model)
    if re.search(r"(?<![A-Z0-9])SE(?![A-Z0-9])|CNGSE$|DUALSE$", u) or "CORPORATE" in u:
        return None
    if any(re.search(x, u) for x in LADDER_EXCLUDE.get(mk, [])):
        return None
    for li, ladder in enumerate(ladders):
        found = None
        if ladder == ["HX#"]:
            m = re.search(r"(?<![A-Z0-9])HX\s*(\d+)\s*(\+|T(?![A-Z]))?", u)
            if m:
                rank = int(m.group(1)) + (0.5 if m.group(2) == "+" else 0.2 if m.group(2) == "T" else 0)
                found = (m.group(0), rank)
        else:
            for ri in sorted(range(len(ladder)), key=lambda i: -len(ladder[i])):
                lab = re.sub(r"\s*\(\s*O\s*\)", "(O)", ladder[ri].upper())
                pat = r"(?<![A-Z0-9])" + re.escape(lab).replace(r"\ ", r"\s+") + r"(?![A-Z0-9(])"
                m = re.search(pat, u)
                if m:
                    found = (m.group(0), ri)
                    break
        if found:
            rest = u.replace(found[0], " ", 1)
            rest = re.sub(r"\b[5-9]\s*S\b", " ", rest)                     # seat count: 6S / 7S
            rest = re.sub(r"[^A-Z0-9.+]", " ", rest)
            rest = " ".join(w for w in rest.split() if w not in _LADDER_NOISE)
            return (f"{mk}|{li}|{rest}", found[1])
    return None


def build_ladder(variant_pool: list) -> tuple:
    """variant_pool: [(mk, model, variant, in_stock_bool)] -> (next_of, rows)
    next_of[(mk, variant_text)] = next variant text, TOP_VARIANT or NOT_IN_LADDER (missing = not in ladder)."""
    groups = {}
    for mk, model, variant, in_stock in variant_pool:
        pos = ladder_position(mk, variant, model)
        if pos is None:
            continue
        g = groups.setdefault(pos[0], {})
        names = g.setdefault(pos[1], {})
        names[str(variant)] = (names.get(str(variant), (False, model))[0] or in_stock, model)
    next_of, rows = {}, []
    for gkey, ranks in groups.items():
        ordered = sorted(ranks)
        mk = gkey.split("|")[0]
        for i, r in enumerate(ordered):
            # one display name per rank; prefer the spelling that is in the stock file
            names = ranks[r]
            shown = sorted(names, key=lambda n: (not names[n][0], n))[0]
            nxt = TOP_VARIANT
            if i + 1 < len(ordered):
                nn = ranks[ordered[i + 1]]
                nxt = sorted(nn, key=lambda n: (not nn[n][0], n))[0]
            rows.append({"model": names[shown][1], "variant": shown,
                         "ladder_group": gkey.split("|", 2)[2] or "-", "rank": i + 1, "next_variant": nxt,
                         "in_stock_file": "Yes" if names[shown][0] else "No", "_g": gkey})
            for n in names:
                next_of[(mk, n)] = nxt
    rows.sort(key=lambda r: (str(r["model"]), r["_g"], r["rank"]))
    return next_of, rows


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

    # ---- upgrade ladder over every variant seen in the enquiries and in stock ----
    pool = [(m, mo, v, True) for m, mo, v in zip(stk["MK"], stk["Model"], stk["Variant"])]
    pool += [(m, mo, v, False) for m, mo, v in zip(e["MK"], e["Model"], e["Variant"]) if not _blank(v)]
    next_of, _ = build_ladder(pool)

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
        "Chassis Physical", "Chassis Transit", "Location", "Oldest Stock Age", "Match Status",
        "Next Variant", "NVK", "Next Free Physical", "Next Free Transit", "Next Chassis", "Upsell Flag")}

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

        # ---- next variant (one step up) in the SAME colour the customer asked for ----
        nxt, nvk, nfp, nft, nch, upsell = "", "", 0, 0, "", ""
        if not blank_variant:
            nxt = next_of.get((mk, str(variant)), NOT_IN_LADDER)
            if nxt not in (TOP_VARIANT, NOT_IN_LADDER):
                nvk = resolve(mk, nxt, model)[1] or "NOSTOCK"
                if nvk != "NOSTOCK":
                    units = free_by_variant.get((mk, nvk))
                    if units is not None and not any_c:
                        units = units[units["CK"] == ck]
                    if units is not None and len(units):
                        nfp = int((units["Stock Type"] == PHYSICAL).sum())
                        nft = int((units["Stock Type"] == TRANSIT).sum())
                        parts = [str(c) for c in units.loc[units["Stock Type"] == PHYSICAL, "Chassis"] if str(c).strip()]
                        parts += [str(c) + " (In Transit)" for c in units.loc[units["Stock Type"] == TRANSIT, "Chassis"]
                                  if str(c).strip()]
                        nch = ", ".join(parts)
                if nfp + nft > 0:
                    upsell = "Upgrade also in stock" if fp + ft > 0 else "UPSELL - only upgrade in stock"

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
                               ch_p, ch_t, locs, oldest, status,
                               nxt, nvk, nfp, nft, nch, upsell)):
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
            "mk": mk, "vk": vk, "ck": ck or ANY_COLOR,
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
            "mk": r["MK"], "vk": r["VK"], "ck": r["CK"] or ANY_COLOR,
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
    stk = with_live_age(stk)
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
            "next_variant": r["Next Variant"],
            "next_free_physical": int(r["Next Free Physical"]),
            "next_free_transit": int(r["Next Free Transit"]),
            "next_chassis": r["Next Chassis"],
            "upsell_flag": r["Upsell Flag"],
            "mk": r["MK"], "vk": r["VK"], "ck": r["CK"] or ANY_COLOR, "nvk": r["NVK"],
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
        "upsell": int((e["Upsell Flag"] == "UPSELL - only upgrade in stock").sum()),
        "upgrade_also": int((e["Upsell Flag"] == "Upgrade also in stock").sum()),
    }
    pool = [(m, mo, v, True) for m, mo, v in zip(stk["MK"], stk["Model"], stk["Variant"])]
    pool += [(m, mo, v, False) for m, mo, v in zip(e["MK"], e["Model"], e["Variant"]) if not _blank(v)]
    ladder_rows = build_ladder(pool)[1]
    return {
        "stock_loaded": True,
        "stock_info": info,
        "summary": summary,
        "match_counts": match_counts,
        "enquiries": rows,
        "demand": demand,
        "no_enquiry": no_enq,
        "by_model": by_model,
        "ladder": [{k: v for k, v in r.items() if not k.startswith("_")} for r in ladder_rows],
    }


def empty_result(info: Optional[dict] = None) -> dict:
    return {
        "stock_loaded": False,
        "stock_info": info or {},
        "summary": {}, "match_counts": [], "enquiries": [], "demand": [], "no_enquiry": [], "by_model": [], "ladder": [],
    }


# --------------------------------------------------------------------------- #
# Excel export (the dashboard equivalent of the Mahindra report workbook)
# --------------------------------------------------------------------------- #

def export_workbook(result: dict, stock_units: Optional[pd.DataFrame] = None) -> bytes:
    """Excel report in the Mahindra layout (clickable Summary, one sheet per match status, live
    formulas over Stock Data) plus Kia's Next Variant / upsell columns. Test Drive plays no part."""
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as L
    from openpyxl.worksheet.hyperlink import Hyperlink

    BRAND = "002C5F"
    FONT = "Arial"
    thin = Side(style="thin", color="BFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1C1C1C")
    key_fill = PatternFill("solid", fgColor="EDEDED")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def fill(c):
        return PatternFill("solid", fgColor=c)

    TONE = {   # match status -> (fill, sheet name, short title)
        "Exact Match - Physical": ("D9EAD3", "Exact Match Physical"),
        "Exact Match - In Transit": ("CFE2F3", "Exact Match In Transit"),
        "Exact Match - Allocated Only": ("FFF2CC", "Exact Match Allocated"),
        "Variant Available - Other Color": ("FFF2CC", "Variant Avail Other Colour"),
        "Model Available - Other Variant": ("FCE5CD", "Model Avail Other Variant"),
        "No Stock Available": ("F4CCCC", "No Stock Available"),
        "Variant Not Captured": ("EFEFEF", "Variant Not Captured"),
    }
    s = result["summary"]
    info = result.get("stock_info") or {}
    enq = result["enquiries"]
    demand = result["demand"]
    cover = result["no_enquiry"]
    ladder = result.get("ladder", [])
    n_enq, n_dem, n_cov = len(enq), len(demand), len(cover)

    su = with_live_age(stock_units) if stock_units is not None else pd.DataFrame()
    n_stk = len(su)
    FIRST = 5
    last_stk = max(FIRST + n_stk - 1, FIRST)
    last_enq = max(FIRST + n_enq - 1, FIRST)
    last_cov = max(FIRST + n_cov - 1, FIRST)
    last_dem = max(FIRST + n_dem - 1, FIRST)

    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_stk = wb.create_sheet("Stock Data")
    ws_enq = wb.create_sheet("Enquiry Stock Match")
    ws_dem = wb.create_sheet("Demand vs Stock")
    ws_cov = wb.create_sheet("Ageing Stock - No Enquiry")
    status_ws = {st: wb.create_sheet(TONE[st][1]) for st in MATCH_STATUSES}
    ws_lad = wb.create_sheet("Variant Ladder")
    for w in wb.worksheets:
        w.sheet_properties.tabColor = BRAND

    def title(ws, text, sub=None, link=False):
        ws["A1"] = text
        ws["A1"].font = Font(name=FONT, size=14, bold=True, color=BRAND)
        ws.row_dimensions[1].height = 17.4
        if link:
            c = ws["A2"]
            c.value = "<< Back to Summary"
            c.hyperlink = Hyperlink(ref="A2", location="Summary!A5", display="<< Back to Summary")
            c.font = Font(name=FONT, size=10, color="0563C1", underline="single")
        elif sub:
            ws["A2"] = sub
            ws["A2"].font = Font(name=FONT, size=9, color="555555")

    def header(ws, names, row=4, height=39.6, key_from=None):
        for j, name in enumerate(names, start=1):
            c = ws.cell(row=row, column=j, value=name)
            c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
            c.fill = head_fill
            c.alignment = center
            c.border = box
        ws.row_dimensions[row].height = height

    def body(ws, rows, ncols, key_from=None, start=FIRST):
        for i, vals in enumerate(rows):
            for j, v in enumerate(vals, start=1):
                c = ws.cell(row=start + i, column=j, value=(None if v == "" else v))
                c.font = Font(name=FONT, size=10)
                c.border = box
                if key_from and j >= key_from:
                    c.fill = key_fill

    def widths(ws, ws_widths):
        for j, w in enumerate(ws_widths, start=1):
            ws.column_dimensions[L(j)].width = w

    def tone_rules(ws, col, last, rules):
        for text, color in rules:
            ws.conditional_formatting.add(
                f"{col}{FIRST}:{col}{last}",
                FormulaRule(formula=[f'${col}{FIRST}="{text}"'], fill=fill(color), stopIfTrue=False))

    # ------------------------------------------------------------------ Stock Data
    stock_hdr = ["Model", "Variant", "Colour", "Chassis No.", "Physical / In Transit", "Allocation",
                 "Stock Age (Days)", "Location", "Allocated To"]
    title(ws_stk, "STOCK DATA (source: %s)" % (info.get("file") or "stock workbook"),
          "OPEN = free (can be offered), ALLOCATED = already committed. Physical = in the yard / showroom, "
          "In Transit = invoiced but not yet received.")
    header(ws_stk, stock_hdr, height=26.4)
    rows = []
    for _, r in su.iterrows():
        age = r["Age"]
        rows.append([r["Model"], r["Variant"], r["Color"], r["Chassis"], r["Stock Type"], r["Alloc"],
                     "" if pd.isna(age) else int(age), r["Location"], r["Allocated To"]])
    body(ws_stk, rows, 9)
    widths(ws_stk, [16, 38, 22, 22, 18, 13, 11, 22, 24])
    ws_stk.freeze_panes = "A5"
    ws_stk.auto_filter.ref = f"A4:I{last_stk}"

    def sd(col):
        return f"'Stock Data'!${col}${FIRST}:${col}${last_stk}"

    # ------------------------------------------------------------------ Enquiry Stock Match
    E = ["Enquiry Date", "Customer ID", "Customer Name", "Phone", "Consultant", "Enquiry Status", "Source",
         "Model", "Variant (as enquired)", "Fuel", "Colour", "Next Follow-up",
         "Match Status", "Resolved Stock Variant", "Free - Physical", "Free - In Transit", "Allocated",
         "Same Variant - Other Colour (Free)", "Same Model - Other Variant (Free)",
         "Next Variant (One Step Up)", "Next Variant Free - Same Colour (Physical)",
         "Next Variant Free - Same Colour (In Transit)", "Next Variant Chassis", "Upsell Flag",
         "Chassis (Physical)", "Chassis (In Transit)", "Location", "Oldest Stock Age (Days)", "Note"]
    col = {name: L(i) for i, name in enumerate(E, start=1)}
    cM, cVar = col["Match Status"], col["Variant (as enquired)"]
    cO, cP, cQ, cR, cS = col["Free - Physical"], col["Free - In Transit"], col["Allocated"], \
        col["Same Variant - Other Colour (Free)"], col["Same Model - Other Variant (Free)"]
    cNU, cNV, cNF = col["Next Variant Free - Same Colour (Physical)"], \
        col["Next Variant Free - Same Colour (In Transit)"], col["Upsell Flag"]

    title(ws_enq, "ENQUIRY-WISE STOCK MATCH REPORT",
          "Every live enquiry is matched to stock on Model + Variant + Colour (Test Drive is not used). Match Status and "
          "all quantities are calculated in Python (pandas), not Excel formulas. Next Variant = one trim "
          "higher on the same engine, gearbox and fuel, checked in the colour the customer asked for.")
    header(ws_enq, E)

    rows_by_status = {st: [] for st in MATCH_STATUSES}
    for i, r in enumerate(enq):
        n = FIRST + i
        nvk = r["nvk"]
        blank_v = (r["variant"] == "")
        vals = [r["date"], r["customer_id"], r["customer"], r["phone"], r["consultant"], r["enquiry_status"],
                r["source"], r["model"], "" if blank_v else r["variant"], r["fuel"], r["color"], r["next_followup"]]
        vals += [r["match_status"], r["resolved_variant"], r["free_physical"], r["free_transit"], r["allocated"],
                 r["other_color"], r["other_variant"], r["next_variant"],
                 (r["next_free_physical"] if r["nvk"] else ""), (r["next_free_transit"] if r["nvk"] else ""),
                 r["next_chassis"], r["upsell_flag"], r["chassis_physical"], r["chassis_transit"], r["location"],
                 r["oldest_age"], r["note"]]
        rows_by_status[r["match_status"]].append(r)
        for j, v in enumerate(vals, start=1):
            c = ws_enq.cell(row=n, column=j, value=(None if v == "" else v))
            c.font = Font(name=FONT, size=10)
            c.border = box
            if E[j - 1] in ("Free - Physical", "Free - In Transit", "Allocated", "Next Variant Free - Same Colour (Physical)",
                            "Next Variant Free - Same Colour (In Transit)"):
                c.alignment = Alignment(horizontal="center")
    widths(ws_enq, [12, 14, 24, 14, 22, 20, 14, 14, 30, 10, 18, 13, 30, 32, 10, 10, 10, 20, 20, 32, 14, 14, 36, 28,
                    36, 30, 18, 12, 36])
    ws_enq.freeze_panes = "D5"
    ws_enq.auto_filter.ref = f"A4:{L(len(E))}{last_enq}"
    tone_rules(ws_enq, cM, last_enq, [(k, v[0]) for k, v in TONE.items()])
    tone_rules(ws_enq, cNF, last_enq, [("UPSELL - only upgrade in stock", "FFE599")])

    def enq_rng(c):
        return f"'Enquiry Stock Match'!${c}${FIRST}:${c}${last_enq}"

    # ------------------------------------------------------------------ Demand vs Stock
    D = ["Model", "Variant", "Colour", "Enquiries (Demand)", "Free - Physical", "Free - In Transit", "Allocated",
         "Gap (Physical - Enquiries)", "Gap (Physical + Transit - Enquiries)", "Position"]
    title(ws_dem, "DEMAND vs STOCK - BY MODEL / VARIANT / COLOUR",
          "Negative gap = enquiries you cannot fulfil from free stock today (physical, or physical + in transit). "
          "Positive gap = free stock with no enquiry against it.")
    header(ws_dem, D, height=39.6)
    rows = []
    for i, r in enumerate(demand):
        n = FIRST + i
        rows.append([r["model"], r["variant"], r["color"], r["enquiries"], r["physical"], r["transit"],
                     r["allocated"], r["gap_physical"], r["gap_total"], r["position"]])
    body(ws_dem, rows, 10)
    for i in range(n_dem):
        for j in (4, 5, 6, 7, 8, 9):
            ws_dem.cell(row=FIRST + i, column=j).alignment = Alignment(horizontal="center")
    widths(ws_dem, [16, 38, 22, 14, 12, 12, 11, 16, 18, 28])
    ws_dem.freeze_panes = "A5"
    ws_dem.auto_filter.ref = f"A4:J{last_dem}"
    tone_rules(ws_dem, "J", last_dem, [("Demand - No Stock", "F4CCCC"), ("Demand - Only In Transit", "CFE2F3"),
                                        ("Short of Physical Stock", "FCE5CD"), ("Balanced", "D9EAD3"),
                                        ("Surplus Stock", "FFF2CC"), ("Stock - No Enquiry", "EFEFEF"),
                                        ("Allocated Only - No Action", "EFEFEF")])

    # ------------------------------------------------------------------ Ageing Stock - No Enquiry
    G = ["Model", "Variant", "Colour", "Chassis No.", "Physical / In Transit", "Stock Age (Days)", "Location",
         "Enquiries for this Combination"]
    title(ws_cov, "FREE STOCK - ENQUIRY COVERAGE (oldest first)",
          "Filter column H on 0 to see ageing free stock (physical or in transit) with no matching enquiry on "
          "Model + Variant + Colour.")
    header(ws_cov, G, height=39.6)
    rows = []
    for i, r in enumerate(cover):
        n = FIRST + i
        rows.append([r["model"], r["variant"], r["color"], r["chassis"], r["stock_type"], r["age"], r["location"],
                     r["enquiries"]])
    body(ws_cov, rows, 8)
    for i in range(n_cov):
        ws_cov.cell(row=FIRST + i, column=8).alignment = Alignment(horizontal="center")
    widths(ws_cov, [16, 38, 22, 22, 18, 11, 22, 18])
    ws_cov.freeze_panes = "A5"
    ws_cov.auto_filter.ref = f"A4:H{last_cov}"
    ws_cov.conditional_formatting.add(f"H{FIRST}:H{last_cov}",
                                      FormulaRule(formula=[f"$H{FIRST}=0"], fill=fill("F4CCCC")))

    # ------------------------------------------------------------------ one sheet per match status
    S_COLS = ["Enquiry Date", "Customer ID", "Customer Name", "Phone", "Consultant", "Enquiry Status", "Source",
              "Model", "Variant (as enquired)", "Fuel", "Colour", "Next Follow-up", "Resolved Stock Variant",
              "Free - Physical", "Free - In Transit", "Allocated", "Same Variant - Other Colour (Free)",
              "Same Model - Other Variant (Free)", "Next Variant (One Step Up)",
              "Next Variant Free - Same Colour (Physical)", "Next Variant Free - Same Colour (In Transit)",
              "Next Variant Chassis", "Upsell Flag", "Chassis (Physical)", "Chassis (In Transit)", "Location",
              "Oldest Stock Age (Days)", "Note"]
    S_KEYS = ["date", "customer_id", "customer", "phone", "consultant", "enquiry_status", "source", "model",
              "variant", "fuel", "color", "next_followup", "resolved_variant", "free_physical", "free_transit",
              "allocated", "other_color", "other_variant", "next_variant", "next_free_physical",
              "next_free_transit", "next_chassis", "upsell_flag", "chassis_physical", "chassis_transit",
              "location", "oldest_age", "note"]
    for st, ws in status_ws.items():
        lst = rows_by_status[st]
        title(ws, "HYUNDAI  -  %s  (%d enquiries)" % (st.upper(), len(lst)), link=True)
        header(ws, S_COLS)
        body(ws, [[r[k] for k in S_KEYS] for r in lst], len(S_COLS))
        widths(ws, [12, 14, 24, 14, 22, 20, 14, 14, 30, 10, 18, 13, 32, 10, 10, 10, 20, 20, 32, 14, 14, 36, 28,
                    36, 30, 18, 12, 36])
        ws.freeze_panes = "C5"
        ws.auto_filter.ref = f"A4:{L(len(S_COLS))}{max(FIRST + len(lst) - 1, FIRST)}"
        tone_rules(ws, "W", max(FIRST + len(lst) - 1, FIRST), [("UPSELL - only upgrade in stock", "FFE599")])

    # ------------------------------------------------------------------ Variant Ladder
    title(ws_lad, "VARIANT UPGRADE LADDER",
          "Hyundai has no price master here, so the ladder follows the trim order set in stock_engine.py "
          "(TRIM_LADDERS). Next Variant = the next trim up on the same engine, gearbox and fuel. Corporate, SE and "
          "unlisted trims are left out.")
    header(ws_lad, ["Model", "Variant", "Ladder Group (engine / gearbox / fuel)", "Ladder Rank",
                    "Next Variant (One Step Up)", "Variant in Stock File?"], height=39.6)
    body(ws_lad, [[r["model"], r["variant"], r["ladder_group"], r["rank"], r["next_variant"], r["in_stock_file"]]
                  for r in ladder], 6)
    widths(ws_lad, [18, 40, 30, 12, 40, 16])
    ws_lad.freeze_panes = "A5"
    ws_lad.auto_filter.ref = f"A4:F{max(FIRST + len(ladder) - 1, FIRST)}"

    # ------------------------------------------------------------------ Summary
    ws = ws_sum
    ws.sheet_view.showGridLines = False
    ws["A1"] = "HYUNDAI - ENQUIRY WISE STOCK REPORT"
    ws["A1"].font = Font(name=FONT, size=16, bold=True, color=BRAND)
    ws.row_dimensions[1].height = 21
    ws["A2"] = ("Matched on Model + Variant + Colour (Physical and In Transit stock shown separately).  "
                "Click any count below to see those enquiries.")
    ws["A2"].font = Font(name=FONT, size=10, color="555555")
    ws["A3"] = ("Source: %s (%d live enquiries, %d stock units: %d physical free, %d in transit free, %d allocated). "
                "Test Drive is not used in the matching." % (info.get("file") or "workbook", n_enq, s["stock_total"],
                                                              s["stock_physical"], s["stock_transit"],
                                                              s["stock_allocated"]))
    ws["A3"].font = Font(name=FONT, size=9, color="555555")
    for j, name in enumerate(["Match Status", "No. of Enquiries", "What it means"], start=1):
        c = ws.cell(row=5, column=j, value=name)
        c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        c.fill, c.alignment, c.border = head_fill, center, box
    for i, m in enumerate(result["match_counts"]):
        r = 6 + i
        st = m["status"]
        loc = "'%s'!A4" % TONE[st][1]
        a = ws.cell(row=r, column=1, value=st)
        a.fill = fill(TONE[st][0])
        b = ws.cell(row=r, column=2, value=m["count"])
        for c in (a, b):
            c.hyperlink = Hyperlink(ref=c.coordinate, location=loc, display=st)
            c.font = Font(name=FONT, size=10, color="0563C1", bold=(c is b), underline="single")
            c.border = box
        b.alignment = Alignment(horizontal="center")
        d = ws.cell(row=r, column=3, value=m["meaning"])
        d.font, d.border = Font(name=FONT, size=10), box
    last_m = 5 + len(result["match_counts"])
    tr = last_m + 1
    for j, v in enumerate(["Total Enquiries", sum(m["count"] for m in result["match_counts"]), ""], start=1):
        c = ws.cell(row=tr, column=j, value=v or None)
        c.font, c.fill, c.border = Font(name=FONT, size=10, bold=True), fill("D9D9D9"), box
        if j == 2:
            c.alignment = Alignment(horizontal="center")

    sp = tr + 2
    for j, name in enumerate(["Stock Position", "Count"], start=1):
        c = ws.cell(row=sp, column=j, value=name)
        c.font, c.fill, c.alignment, c.border = Font(name=FONT, size=10, bold=True, color="FFFFFF"), head_fill, center, box
    pos = [
        ("Total stock units", s["stock_total"]),
        ("Free - Physical", s["stock_physical"]),
        ("Free - In Transit", s["stock_transit"]),
        ("Allocated", s["stock_allocated"]),
        ("Free stock with no matching enquiry", s["stock_no_enquiry"]),
        ("Physical stock older than %d days" % s["aged_days"], s["stock_aged"]),
        ("Upsell opportunities (only the upgrade is in stock)", s["upsell"]),
        ("Upgrade also in stock alongside the exact car", s["upgrade_also"]),
    ]
    for i, (label, f) in enumerate(pos):
        a = ws.cell(row=sp + 1 + i, column=1, value=label)
        b = ws.cell(row=sp + 1 + i, column=2, value=f)
        a.font = b.font = Font(name=FONT, size=10)
        b.alignment = Alignment(horizontal="center")
        a.border = b.border = box
    r0 = sp + len(pos) + 2
    notes = [
        ("How to read this workbook", True),
        ("1. Summary - this sheet. Headline counts (calculated in Python / pandas - no Excel formulas).", False),
        ("2. Click any count in the table above to jump to a sheet listing exactly those enquiries (use << Back to Summary to return).", False),
        ("3. Enquiry Stock Match - one row per enquiry: match status, physical / in-transit quantity, chassis numbers and the next variant up.", False),
        ("4. Demand vs Stock - every Model/Variant/Colour combination: enquiries against free stock (physical and in transit), and the gap.", False),
        ("5. Ageing Stock - No Enquiry - free stock oldest first, with how many enquiries each unit could serve.", False),
        ("6. Variant Ladder - the upgrade order used for the Next Variant columns.", False),
        ("7. Stock Data - the stock export used for the matching.", False),
        ("", False),
        ("The Next Variant columns (T to X on Enquiry Stock Match)", True),
        ("Next Variant is the next trim up on the SAME engine, gearbox and fuel (see Variant Ladder).", False),
        ("Its stock is checked in the SAME colour the customer asked for, so the consultant can offer a car they can see today.", False),
        ('Upsell Flag reads "UPSELL - only upgrade in stock" when the exact car is not free (physical or in transit) but the next variant up is.', False),
        ("", False),
        ("How variants are matched", True),
        ("The enquiry and the stock name the same car differently (\"Creta 1.5 MPi MT EX\" vs \"CRETA 1.5 MPI MT EX\"), so variants are compared on", False),
        ("their trim codes only, after removing the model name, the emission norm and the seat count.", False),
        ("The Resolved Stock Variant column shows exactly which stock variant each enquiry was matched to - sanity-check it for an unfamiliar model.", False),
        ("", False),
        ("Assumptions", True),
        ('a. Free stock = stock that is not blocked / booked. "Physical" is in the yard or showroom, "In Transit" is invoiced but not yet received.', False),
        ("b. Colour comes from Exterior Color Name. If an enquiry has no colour, any colour of the variant counts.", False),
        ("c. Availability is a quantity - the same unit can serve more than one enquiry, so nothing is reserved here.", False),
        ("d. Enquiries with no variant captured cannot be matched and are listed as Variant Not Captured.", False),
        ("e. Test Drive (Y/N) is NOT part of the calculation: every live enquiry is matched, as in the Kia report.", False),
    ]
    for i, (t, bold) in enumerate(notes):
        c = ws.cell(row=r0 + i, column=1, value=t or None)
        c.font = Font(name=FONT, size=10, bold=bold)
    widths(ws, [46, 18, 72])
    return_buf = io.BytesIO()
    wb.save(return_buf)
    return return_buf.getvalue()
