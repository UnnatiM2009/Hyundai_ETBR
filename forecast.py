"""
forecast.py
-----------
Forecast (ML) page of the Hyundai ETBR dashboard: Enquiries -> Test drives -> Bookings -> Retails.

Everything is read from the same Enquiry sheet as the rest of the dashboard, and nothing here changes the numbers of
any other page.  Dates are the dashboard's own:

    Enquiries     Enquiry Date                                        (same as the Overview "Total enquiries")
    Test drives   Enquiry Date of enquiries with Test Drive = Y       (same as "Test drives completed")
    Retails       Retail date of status Retail                        (same as "Units retailed")
    Bookings      "Total bookings" exactly as the dashboard counts them:
                      status Booked on its Booking Date  +  status Retail on its Retail date.
                  For a month that is over this is  (bookings made in the month that are still undelivered at month
                  end)  +  (retails dated in the month).

How the forecast is made (kept simple and explainable - the Enquiry sheet only holds a few weeks of history)
  * Enquiries, Test drives and NEW bookings made (Booked + Retail + later-cancelled, on Booking Date) are daily counts.
    Three candidate models are scored by a rolling-origin back-test (train on the past, forecast the next 14 days,
    compare with what really happened) and the best one per series is used:
        flat28   flat average of the last 28 days (the "no model" baseline)
        flat14   flat average of the last 14 days (reacts faster)
        dow28    last-28-day level x day-of-week pattern
  * Retails - a booking-to-retail PIPELINE (a discrete-time hazard / survival model): "how many days after the booking
    does the car get retailed, or the booking get cancelled" is fitted on every past booking (bookings still open count as
    censored), then applied to (a) every booking that is open today and (b) the bookings the model expects to be made.
    It competes in the same back-test with the recent delivery pace and a 50/50 blend of the two.
  * Ranges: Monte-Carlo.  Daily counts are over-dispersed Poisson (negative binomial); the level drifts around its average
    by an amount calibrated from the back-test misses and widened for later months.  Low / High = the 10th / 90th percentile.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import data_processor as dp

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
MONTHS_AHEAD = 3            # months after the current one that can be forecast
SIMS = 400                  # Monte-Carlo paths
LOW_Q, HIGH_Q = 10, 90      # the "80% range"
BT_HORIZON = 14             # days forecast in each back-test fold
BT_MIN_TRAIN = 28           # fewest days of history a back-test fold may train on
BT_MAX_FOLDS = 8
LAG_BINS = [0, 1, 2, 3, 5, 8, 12, 18, 26, 40, 10 ** 6]       # booking age bins with one hazard each
MAX_LAG = 130
SERIES = ["enquiries", "test_drives", "bookings", "retails"]
SERIES_LABEL = {"enquiries": "Enquiries", "test_drives": "Test drives", "bookings": "Total bookings", "retails": "Retails"}
LEVEL_MODELS = ["flat28", "flat14", "dow28"]
MODEL_TEXT = {
    "flat28": "flat 28-day average", "flat14": "flat 14-day average", "dow28": "28-day level x weekday pattern",
    "pipeline": "booking-to-retail pipeline", "pace": "recent delivery pace", "blend": "pipeline + pace blend",
}

_cache: dict = {}


def _today() -> dt.date:
    return dt.date.today()


def _month_str(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m")


def _month_label(m: str) -> str:
    return pd.Timestamp(m + "-01").strftime("%B %Y")


def _month_end(d: pd.Timestamp) -> pd.Timestamp:
    return (pd.Timestamp(d).normalize() + pd.offsets.MonthEnd(0)).normalize()


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
class _Data:
    """Daily history + the booking rows the pipeline needs, built once per data load."""

    def __init__(self):
        e = dp.store.enquiry
        self.empty = e.empty
        if self.empty:
            return
        norm = lambda s: pd.to_datetime(s, errors="coerce").dt.normalize()
        self.enq_d = norm(e["Enquiry Date"])
        self.book_d = norm(e["Booking Date"])
        self.ret_d = norm(e["Retail date"])
        self.lost_d = norm(e["Lost Date"])
        self.evt_d = norm(e["Booking Event Date"])
        self.e = e

        first = self.enq_d.min()
        last = pd.concat([self.enq_d, self.book_d, self.ret_d]).max()
        if pd.isna(first) or pd.isna(last):
            self.empty = True
            return
        # the last day in the sheet may still be filling up when it is today
        if last.date() >= _today():
            last = last - pd.Timedelta(days=1)
        self.first, self.last = first.normalize(), last.normalize()
        self.idx = pd.date_range(self.first, self.last)
        cnt = lambda mask, dates: dates[mask].value_counts().reindex(self.idx, fill_value=0).to_numpy(dtype=float)
        gross = e["is_booked"] | e["is_retail"] | e["is_booking_cancel"]
        self.y = {
            "enquiries": cnt(self.enq_d.notna(), self.enq_d),
            "test_drives": cnt(e["Test Drive"].astype(str).str.upper().eq("Y"), self.enq_d),
            "gross": cnt(gross, self.book_d),                       # bookings MADE that day (before any retail / cancel)
            "retails": cnt(e["is_retail"], self.ret_d),
            "bookings": cnt(e["is_booking"], self.evt_d),           # dashboard "Total bookings" by its event date
        }
        self.n = len(self.idx)
        self.dow = self.idx.dayofweek.to_numpy()

        # booking rows for the pipeline (integer day numbers)
        bk = e[gross].copy()
        day = lambda s: (s - pd.Timestamp("1970-01-01")).dt.days
        self.bk_b = day(norm(bk["Booking Date"])).to_numpy(dtype=float)
        self.bk_r = np.where(bk["is_retail"], day(norm(bk["Retail date"])).fillna(np.nan), np.nan)
        self.bk_c = np.where(bk["is_booking_cancel"], day(norm(bk["Lost Date"])).fillna(np.nan), np.nan)
        self.bk_model = bk["Model"].astype(str).to_numpy()
        self.bk_month = norm(bk["Booking Date"]).dt.strftime("%Y-%m").to_numpy()
        ok = ~np.isnan(self.bk_b)
        self.bk_b, self.bk_r, self.bk_c = self.bk_b[ok], self.bk_r[ok], self.bk_c[ok]
        self.bk_model, self.bk_month = self.bk_model[ok], self.bk_month[ok]
        self.last_int = int((self.last - pd.Timestamp("1970-01-01")).days)
        self.first_int = int((self.first - pd.Timestamp("1970-01-01")).days)


def _data() -> _Data:
    key = (id(dp.store.enquiry), len(dp.store.enquiry), _today().toordinal())
    if _cache.get("key") != key:
        _cache.clear()
        _cache["key"] = key
        _cache["data"] = _Data()
    return _cache["data"]


# --------------------------------------------------------------------------- #
# Daily level models
# --------------------------------------------------------------------------- #
def _dow_factors(y: np.ndarray, dow: np.ndarray) -> np.ndarray:
    y, dow = y[-56:], dow[-56:]
    f = np.ones(7)
    mean = y.mean() if len(y) else 0
    if mean <= 0:
        return f
    for k in range(7):
        sel = y[dow == k]
        if len(sel):
            f[k] = sel.mean() / mean
    weeks = len(y) / 7.0
    w = min(1.0, weeks / 8.0) * 0.7            # little history -> trust the weekday pattern less
    f = 1 + w * (f - 1)
    return f / f.mean()


def _level_forecast(y: np.ndarray, dow: np.ndarray, fut_dow: np.ndarray, model: str) -> np.ndarray:
    if len(y) == 0:
        return np.zeros(len(fut_dow))
    if model == "flat14":
        return np.full(len(fut_dow), y[-14:].mean())
    lvl = y[-28:].mean()
    if model == "dow28":
        return lvl * _dow_factors(y, dow)[fut_dow]
    return np.full(len(fut_dow), lvl)


def _fold_origins(n: int) -> List[int]:
    """Day-indexes t such that [0, t) trains and [t, t+BT_HORIZON) is scored."""
    last = n - BT_HORIZON
    origins = list(range(last, BT_MIN_TRAIN - 1, -7))
    return sorted(origins[:BT_MAX_FOLDS])


def _score_level(d: _Data, key: str, model: str) -> Optional[dict]:
    y, dow = d.y[key], d.dow
    errs, abs_daily = [], []
    for t in _fold_origins(d.n):
        pred = _level_forecast(y[:t], dow[:t], dow[t:t + BT_HORIZON], model)
        act = y[t:t + BT_HORIZON]
        errs.append(abs(pred.sum() - act.sum()) / max(act.sum(), 1.0))
        abs_daily.append(np.abs(pred - act).mean())
    if not errs:
        return None
    w = np.linspace(1.0, 2.0, len(errs))       # recent folds count more
    return {"err": float(np.average(errs, weights=w)), "mae": float(np.mean(abs_daily)), "folds": len(errs)}


def _best_level(d: _Data, key: str) -> dict:
    scores = {m: _score_level(d, key, m) for m in LEVEL_MODELS}
    valid = {m: s for m, s in scores.items() if s}
    if not valid:
        return {"model": "flat28", "scores": {}, "err": 0.25, "folds": 0}
    best = min(valid, key=lambda m: (round(valid[m]["err"], 4), valid[m]["mae"]))
    return {"model": best, "scores": valid, "err": valid[best]["err"], "folds": valid[best]["folds"]}


def _overdispersion(d: _Data, key: str, model: str) -> float:
    """Negative-binomial alpha from the last 28 days of misses against the chosen level model."""
    y, dow = d.y[key], d.dow
    if d.n < 21:
        return 0.2
    t0 = max(14, d.n - 28)
    res, mus = [], []
    for t in range(t0, d.n):
        mu = _level_forecast(y[:t], dow[:t], dow[t:t + 1], model)[0]
        res.append(y[t] - mu)
        mus.append(mu)
    res, mus = np.array(res), np.array(mus)
    mean = max(mus.mean(), 1e-6)
    alpha = (np.mean(res ** 2) - mean) / (mean ** 2)
    return float(min(max(alpha, 0.0), 2.0))


# --------------------------------------------------------------------------- #
# Booking -> retail / cancel pipeline
# --------------------------------------------------------------------------- #
def _fit_hazards(d: _Data, asof: int):
    """Per-day hazards of 'retail' and 'cancel' by booking age, using only what was known at `asof` (day number)."""
    sel = d.bk_b <= asof
    b, r, c = d.bk_b[sel], d.bk_r[sel], d.bk_c[sel]
    got_r = ~np.isnan(r) & (r <= asof)
    got_c = ~np.isnan(c) & (c <= asof) & ~got_r
    life = np.where(got_r, r - b, np.where(got_c, c - b, asof - b)).clip(min=0)
    nb = len(LAG_BINS) - 1
    risk = np.zeros(nb)
    ev_r, ev_c = np.zeros(nb), np.zeros(nb)
    for k in range(nb):
        lo, hi = LAG_BINS[k], LAG_BINS[k + 1]
        risk[k] = np.clip(np.minimum(life, hi - 1) - lo + 1, 0, None).sum()
        inbin = (life >= lo) & (life < hi)
        ev_r[k] = (got_r & inbin).sum()
        ev_c[k] = (got_c & inbin).sum()
    h0r = ev_r.sum() / max(risk.sum(), 1.0)
    h0c = ev_c.sum() / max(risk.sum(), 1.0)
    a = 6.0                                                        # prior weight, in booking-days
    hr_bin = (ev_r + a * h0r) / (risk + a)
    hc_bin = (ev_c + a * h0c) / (risk + a)
    lags = np.arange(MAX_LAG + 1)
    bin_of = np.searchsorted(LAG_BINS, lags, side="right") - 1
    hr = np.clip(hr_bin[bin_of], 0, 0.85)
    hc = np.clip(hc_bin[bin_of], 0, 0.5)
    over = hr + hc > 0.95
    if over.any():
        s = 0.95 / (hr + hc)[over]
        hr[over] *= s
        hc[over] *= s
    return hr, hc, int(sel.sum())


def _outcome_probs(hr: np.ndarray, hc: np.ndarray, age: int, steps: int, pace: float = 1.0):
    """For a booking that is still open after day `age`: P(retail) and P(cancel) on each of the next `steps` days."""
    hr = np.clip(hr * pace, 0, 0.95)
    alive = 1.0                                      # it is open today, so it starts "alive"
    pr, pc = np.zeros(steps), np.zeros(steps)
    for j in range(steps):
        k = min(age + 1 + j, MAX_LAG)
        pr[j] = alive * hr[k]
        pc[j] = alive * hc[k]
        alive *= (1 - hr[k] - hc[k])
    return pr, pc


def _fresh_probs(hr: np.ndarray, hc: np.ndarray, steps: int, pace: float = 1.0):
    """Same for a booking made today: lag 0 is the day it is made."""
    hr = np.clip(hr * pace, 0, 0.95)
    pr, pc = np.zeros(steps), np.zeros(steps)
    alive = 1.0
    for k in range(steps):
        kk = min(k, MAX_LAG)
        pr[k] = alive * hr[kk]
        pc[k] = alive * hc[kk]
        alive *= (1 - hr[kk] - hc[kk])
    return pr, pc


def _open_bookings(d: _Data, asof: int):
    alive = (d.bk_b <= asof) & ~(~np.isnan(d.bk_r) & (d.bk_r <= asof)) & ~(~np.isnan(d.bk_c) & (d.bk_c <= asof))
    # a booking that retails / cancels AFTER asof is open at asof
    return np.where(alive)[0]


def _pipeline_expected(d: _Data, asof: int, g_future: np.ndarray, pace: float = 1.0) -> np.ndarray:
    """Expected retails on each of the next len(g_future) days, as known at `asof`."""
    H = len(g_future)
    hr, hc, _ = _fit_hazards(d, asof)
    out = np.zeros(H)
    open_idx = _open_bookings(d, asof)
    ages = (asof - d.bk_b[open_idx]).astype(int).clip(min=0)
    for a in np.unique(ages):
        n = int((ages == a).sum())
        pr, _ = _outcome_probs(hr, hc, int(a), H, pace)
        out += n * pr
    pr_f, _ = _fresh_probs(hr, hc, H, pace)
    out += np.convolve(g_future, pr_f)[:H]
    return out


def _backtest_retail(d: _Data) -> dict:
    y = d.y["retails"]
    gl = _best_level(d, "gross")
    scores = {"pipeline": [], "pace": [], "blend": []}
    maes = {"pipeline": [], "pace": [], "blend": []}
    for t in _fold_origins(d.n):
        asof = d.first_int + t - 1
        g_fut = _level_forecast(d.y["gross"][:t], d.dow[:t], d.dow[t:t + BT_HORIZON], gl["model"])
        pipe = _pipeline_expected(d, asof, g_fut)
        pace = _level_forecast(y[:t], d.dow[:t], d.dow[t:t + BT_HORIZON], "flat28")
        act = y[t:t + BT_HORIZON]
        for name, pred in (("pipeline", pipe), ("pace", pace), ("blend", (pipe + pace) / 2)):
            scores[name].append(abs(pred.sum() - act.sum()) / max(act.sum(), 1.0))
            maes[name].append(np.abs(pred - act).mean())
    valid = {}
    for name, errs in scores.items():
        if errs:
            w = np.linspace(1.0, 2.0, len(errs))
            valid[name] = {"err": float(np.average(errs, weights=w)), "mae": float(np.mean(maes[name])), "folds": len(errs)}
    if not valid:
        return {"model": "pace", "scores": {}, "err": 0.3, "folds": 0, "gross_model": gl["model"]}
    best = min(valid, key=lambda m: (round(valid[m]["err"], 4), valid[m]["mae"]))
    return {"model": best, "scores": valid, "err": valid[best]["err"], "folds": valid[best]["folds"], "gross_model": gl["model"]}


# --------------------------------------------------------------------------- #
# The forecast
# --------------------------------------------------------------------------- #
def _sample_nb(rng, mu: np.ndarray, alpha: float) -> np.ndarray:
    mu = np.clip(mu, 1e-9, None)
    if alpha < 0.02:
        return rng.poisson(mu).astype(float)
    shape = 1.0 / alpha
    return rng.poisson(rng.gamma(shape, mu / shape)).astype(float)


def _pct(a: np.ndarray, q: float, axis=0):
    return np.percentile(a, q, axis=axis)


def _core() -> dict:
    d = _data()
    if "core" in _cache:
        return _cache["core"]
    out = _build_core(d)
    _cache["core"] = out
    return out


def _build_core(d: _Data) -> dict:
    if d.empty or d.n < 14:
        return {"empty": True, "message": "The Enquiry sheet needs at least two weeks of dates before a forecast can be made."}

    rng = np.random.default_rng(20261031)
    last = d.last
    cur_month = _month_str(last)
    months = [cur_month]
    m = pd.Timestamp(cur_month + "-01")
    for _ in range(MONTHS_AHEAD):
        m = m + pd.offsets.MonthBegin(1)
        months.append(m.strftime("%Y-%m"))
    end = _month_end(pd.Timestamp(months[-1] + "-01"))
    fut = pd.date_range(last + pd.Timedelta(days=1), end)
    H = len(fut)
    if H == 0:
        return {"empty": True, "message": "No days left to forecast."}
    fdow = fut.dayofweek.to_numpy()
    fmonth = np.array([_month_str(x) for x in fut])
    month_idx = {mm: i for i, mm in enumerate(months)}
    mi_of_day = np.array([month_idx[x] for x in fmonth])
    # days until the end of the month a day belongs to (for "still undelivered at month end")
    to_month_end = np.array([(_month_end(x) - x).days for x in fut])

    # ---- level models for enquiries, test drives and new bookings made ----
    picks, mus, alphas, sig = {}, {}, {}, {}
    for key in ("enquiries", "test_drives", "gross"):
        best = _best_level(d, key)
        picks[key] = best
        mus[key] = _level_forecast(d.y[key], d.dow, fdow, best["model"])
        alphas[key] = _overdispersion(d, key, best["model"])
        sig[key] = float(np.clip(best["err"], 0.06, 0.55))
    rb = _backtest_retail(d)
    picks["retails"] = rb
    sig["retails"] = float(np.clip(rb["err"], 0.08, 0.6))

    # ---- retail model: pipeline with a delivery-pace multiplier ----
    asof = d.last_int
    pipe_mean = _pipeline_expected(d, asof, mus["gross"])
    pace_mean = _level_forecast(d.y["retails"], d.dow, fdow, "flat28")
    first14 = slice(0, min(14, H))
    pace_scale = 1.0
    if rb["model"] in ("pace", "blend"):
        target = pace_mean[first14].sum() if rb["model"] == "pace" else (pipe_mean[first14].sum() + pace_mean[first14].sum()) / 2
        pace_scale = float(np.clip(target / max(pipe_mean[first14].sum(), 1e-6), 0.25, 1.8))
    hr, hc, n_fit = _fit_hazards(d, asof)

    # ---- simulation ----
    S = SIMS
    zdrift = {k: rng.standard_normal(S) for k in ("enquiries", "test_drives", "gross", "retails")}
    growth = np.array([0.6 + 0.35 * i for i in mi_of_day])               # later months are less certain

    def drift(key):
        s = np.minimum(sig[key] * growth, 0.7)[None, :]
        return np.exp(s * zdrift[key][:, None] - s ** 2 / 2)

    enq_sim = _sample_nb(rng, mus["enquiries"][None, :] * drift("enquiries"), alphas["enquiries"])
    td_sim = _sample_nb(rng, mus["test_drives"][None, :] * drift("test_drives"), alphas["test_drives"])
    g_sim = _sample_nb(rng, mus["gross"][None, :] * drift("gross"), alphas["gross"])
    pace_mult = np.exp(0.18 * zdrift["retails"] - 0.18 ** 2 / 2)         # delivery pace is uncertain too

    R = np.zeros((S, H))             # all retails on each day
    R_open_cur = np.zeros((S, H))    # ... that come from bookings already open this month (net zero inside the month)
    C_open_cur = np.zeros((S, H))    # cancels of bookings already open this month
    alive_end = np.zeros((S, H))     # bookings made on the day that are still undelivered at the end of their month
    open_idx = _open_bookings(d, asof)
    ages = (asof - d.bk_b[open_idx]).astype(int).clip(min=0)
    cur_mask = d.bk_month[open_idx] == cur_month
    open_models = d.bk_model[open_idx]

    for a in np.unique(ages):
        sel = ages == a
        for flag in (True, False):
            n = int((sel & (cur_mask == flag)).sum())
            if not n:
                continue
            pr, pc = _outcome_probs(hr, hc, int(a), H, pace_scale)
            p = np.concatenate([pr, pc, [max(1e-12, 1 - pr.sum() - pc.sum())]])
            p = p / p.sum()
            draw = rng.multinomial(n, p, size=S)
            rr, cc = draw[:, :H].astype(float), draw[:, H:2 * H].astype(float)
            R += rr
            if flag:
                R_open_cur += rr
                C_open_cur += cc

    for f in range(H):
        steps = H - f
        pr, pc = _fresh_probs(hr, hc, steps, pace_scale)
        p = np.concatenate([pr, pc, [max(1e-12, 1 - pr.sum() - pc.sum())]])
        p = p / p.sum()
        n = g_sim[:, f].astype(np.int64)
        draw = rng.multinomial(n, p)
        rr, cc = draw[:, :steps].astype(float), draw[:, steps:2 * steps].astype(float)
        R[:, f:] += rr
        L = int(min(to_month_end[f], steps - 1))
        done = rr[:, :L + 1].sum(axis=1) + cc[:, :L + 1].sum(axis=1)
        alive_end[:, f] = n - done
    R *= pace_mult[:, None]
    R_open_cur *= pace_mult[:, None]

    in_cur = (mi_of_day == 0)
    dB = alive_end + R - np.where(in_cur[None, :], R_open_cur + C_open_cur, 0.0)

    sims = {"enquiries": enq_sim, "test_drives": td_sim, "bookings": dB, "retails": R}

    # ---- actual month-to-date values (the dashboard's own numbers) ----
    st = dp.store
    def actual(month):
        return {
            "enquiries": int(len(st.view("enquiry", month))),
            "test_drives": int((st.view("enquiry", month)["Test Drive"] == "Y").sum()),
            "bookings": int(len(st.view("booked", month))),
            "retails": int(len(st.view("retail", month))),
        }

    cur_actual = actual(cur_month)
    month_tot = {}
    for key in SERIES:
        arr = sims[key]
        per_month = []
        for i, mm in enumerate(months):
            tot = arr[:, mi_of_day == i].sum(axis=1)
            if i == 0:
                tot = tot + cur_actual[key]
            per_month.append(tot)
        month_tot[key] = per_month        # list over months of (S,) arrays

    return {
        "empty": False, "d": d, "months": months, "fut": fut, "H": H, "mi_of_day": mi_of_day, "fdow": fdow,
        "sims": sims, "picks": picks, "mus": mus, "pipe_mean": pipe_mean, "pace_mean": pace_mean,
        "pace_scale": pace_scale, "month_tot": month_tot, "cur_actual": cur_actual, "cur_month": cur_month,
        "hr": hr, "hc": hc, "n_fit": n_fit, "open_idx": open_idx, "open_ages": ages, "open_models": open_models,
        "sig": sig, "alphas": alphas, "rb": rb,
    }


# --------------------------------------------------------------------------- #
# Open bookings -> expected retails
# --------------------------------------------------------------------------- #
def _open_section(c: dict) -> dict:
    d: _Data = c["d"]
    hr, hc = c["hr"], c["hc"]
    ages, models = c["open_ages"], c["open_models"]
    pace = c["pace_scale"]
    n = len(ages)

    def expect(horizon: int):
        pr_tot, pc_tot = 0.0, 0.0
        per_model: Dict[str, float] = {}
        for i, a in enumerate(ages):
            pr, pc = _outcome_probs(hr, hc, int(a), horizon, pace)
            pr_tot += pr.sum()
            pc_tot += pc.sum()
            per_model[models[i]] = per_model.get(models[i], 0.0) + pr.sum()
        return pr_tot, pc_tot, per_model

    e14, c14, _ = expect(14)
    e30, c30, pm30 = expect(30)
    kpis = {
        "open": int(n),
        "avg_age": round(float(ages.mean()), 1) if n else 0.0,
        "over_30": int((ages > 30).sum()),
        "expected_14": round(e14, 1),
        "expected_30": round(e30, 1),
        "cancel_30": round(c30, 1),
    }
    rows = []
    for mdl in sorted(set(models)):
        sel = models == mdl
        rows.append({
            "model": mdl, "open": int(sel.sum()), "avg_age": round(float(ages[sel].mean()), 1),
            "oldest": int(ages[sel].max()), "expected_30": round(pm30.get(mdl, 0.0), 1),
            "share": round(pm30.get(mdl, 0.0) / max(int(sel.sum()), 1) * 100, 1),
        })
    rows.sort(key=lambda r: -r["open"])
    return {"kpis": kpis, "rows": rows}


# --------------------------------------------------------------------------- #
# Model-wise projection
# --------------------------------------------------------------------------- #
def _model_section(c: dict, month_i: int) -> dict:
    d: _Data = c["d"]
    e = d.e
    last = d.last
    start = last - pd.Timedelta(days=27)
    win = lambda dates: (dates >= start) & (dates <= last)
    shares = {}
    models = sorted({str(x) for x in e["Model"].dropna().astype(str).str.strip() if x and x.lower() not in ("nan", "none")})
    def count_by(mask, dates):
        sub = e.loc[mask & win(dates), "Model"].astype(str).str.strip().value_counts()
        tot = float(sub.sum())
        return {k: (v / tot if tot else 0.0) for k, v in sub.items()}
    shares["enquiries"] = count_by(d.enq_d.notna(), d.enq_d)
    shares["test_drives"] = count_by(e["Test Drive"].astype(str).str.upper().eq("Y"), d.enq_d)
    shares["bookings"] = count_by(e["is_booking"], d.evt_d)
    shares["retails"] = count_by(e["is_retail"], d.ret_d)
    mid = {k: float(c["month_tot"][k][month_i].mean()) for k in SERIES}
    lo = {k: float(_pct(c["month_tot"][k][month_i], LOW_Q)) for k in SERIES}
    hi = {k: float(_pct(c["month_tot"][k][month_i], HIGH_Q)) for k in SERIES}
    rows = []
    for mdl in models:
        row = {"model": mdl}
        keep = False
        for k in SERIES:
            s = shares[k].get(mdl, 0.0)
            row[k] = round(mid[k] * s, 1)
            row[k + "_low"] = round(lo[k] * s, 1)
            row[k + "_high"] = round(hi[k] * s, 1)
            keep = keep or s > 0
        if keep:
            rows.append(row)
    rows.sort(key=lambda r: -r["enquiries"])
    return {"rows": rows}


# --------------------------------------------------------------------------- #
# Public: the Forecast page payload
# --------------------------------------------------------------------------- #
def _stats(arr: np.ndarray) -> dict:
    return {"mid": round(float(arr.mean()), 1), "low": round(float(_pct(arr, LOW_Q)), 1), "high": round(float(_pct(arr, HIGH_Q)), 1)}


def _ratio(num: np.ndarray, den: np.ndarray) -> dict:
    den = np.where(den > 0, den, np.nan)
    r = np.nan_to_num(num / den * 100.0)
    return {"mid": round(float(np.nanmean(r)), 1), "low": round(float(_pct(r, LOW_Q)), 1), "high": round(float(_pct(r, HIGH_Q)), 1)}


def month_ratios(c: dict, month_i: int) -> dict:
    mt = c["month_tot"]
    e, t, b, r = (mt[k][month_i] for k in ("enquiries", "test_drives", "bookings", "retails"))
    return {"E2T": _ratio(t, e), "E2B": _ratio(b, e), "E2R": _ratio(r, e), "B2R": _ratio(r, b)}


def compute_forecast(ahead: int = MONTHS_AHEAD) -> dict:
    c = _core()
    if c.get("empty"):
        return {"empty": True, "message": c.get("message", "")}
    d: _Data = c["d"]
    ahead = int(min(max(ahead, 1), MONTHS_AHEAD))
    months = c["months"][:ahead + 1]
    mi = c["mi_of_day"]
    fut: pd.DatetimeIndex = c["fut"]

    # ---- month table ----
    month_rows = []
    for i, mm in enumerate(months):
        row = {"month": mm, "label": _month_label(mm), "status": "current" if i == 0 else "future", "series": {}}
        for k in SERIES:
            arr = c["month_tot"][k][i]
            s = _stats(arr)
            if i == 0:
                s["actual"] = c["cur_actual"][k]
                s["forecast_rest"] = round(s["mid"] - c["cur_actual"][k], 1)
            row["series"][k] = s
        row["ratios"] = month_ratios(c, i)
        month_rows.append(row)

    # ---- history for charts ----
    hist_days = 70
    h_idx = d.idx[-hist_days:]
    hist = {
        "dates": [x.strftime("%Y-%m-%d") for x in h_idx],
        "actual": {k: [int(v) for v in d.y[k][-hist_days:]] for k in ("enquiries", "test_drives", "bookings", "retails")},
    }

    # ---- daily forecast (mid / low / high) ----
    fut_days = int(np.sum(mi <= ahead))
    daily = {"dates": [x.strftime("%Y-%m-%d") for x in fut[:fut_days]], "series": {}}
    for k in SERIES:
        arr = c["sims"][k][:, :fut_days]
        daily["series"][k] = {"mid": [round(float(v), 2) for v in arr.mean(axis=0)]}

    # ---- current-month running total (actual, then forecast with range) ----
    cur_start = pd.Timestamp(c["cur_month"] + "-01")
    cum = {}
    cur_days = pd.date_range(cur_start, _month_end(cur_start))
    n_act = (d.last - cur_start).days + 1
    for k in SERIES:
        act = np.cumsum(d.y[k][d.idx >= cur_start][:n_act])
        rest = c["sims"][k][:, mi == 0]
        add = np.cumsum(rest, axis=1)
        base_actual = c["cur_actual"][k]          # the dashboard's own month-to-date number
        mid = (add.mean(axis=0) + base_actual)
        lo = _pct(add, LOW_Q, axis=0) + base_actual
        hi = _pct(add, HIGH_Q, axis=0) + base_actual
        cum[k] = {
            "dates": [x.strftime("%Y-%m-%d") for x in cur_days],
            "actual": [int(v) for v in act],
            "mid": [round(float(v), 1) for v in mid], "low": [round(float(v), 1) for v in lo], "high": [round(float(v), 1) for v in hi],
        }

    # ---- week by week (Mon-Sun): last 8 weeks actual, next weeks forecast ----
    def weeks():
        all_idx = d.idx.append(fut[:fut_days])
        df = pd.DataFrame(index=all_idx)
        out = {}
        for k in SERIES:
            a = np.concatenate([d.y[k], np.full(fut_days, np.nan)])
            f = np.concatenate([np.full(d.n, np.nan), c["sims"][k][:, :fut_days].mean(axis=0)])
            df[k + "_a"], df[k + "_f"] = a, f
        wk = (df.index - pd.to_timedelta(df.index.dayofweek, unit="D")).normalize()
        g = df.groupby(wk)
        res = {"weeks": [], "series": {k: {"actual": [], "forecast": []} for k in SERIES}}
        for w, sub in g:
            res["weeks"].append(w.strftime("%d %b"))
            n_days = len(sub)
            for k in SERIES:
                a_part = sub[k + "_a"].dropna()
                f_part = sub[k + "_f"].dropna()
                if len(a_part) == n_days:
                    res["series"][k]["actual"].append(int(a_part.sum()))
                    res["series"][k]["forecast"].append(None)
                elif len(a_part) == 0:
                    res["series"][k]["actual"].append(None)
                    res["series"][k]["forecast"].append(round(float(f_part.sum()), 1))
                else:                                           # the week that contains today
                    res["series"][k]["actual"].append(int(a_part.sum()))
                    res["series"][k]["forecast"].append(round(float(a_part.sum() + f_part.sum()), 1))
        return res
    wk = weeks()
    keep = slice(-(8 + 2 + ahead * 4), None)
    wk["weeks"] = wk["weeks"][keep]
    for k in SERIES:
        wk["series"][k]["actual"] = wk["series"][k]["actual"][keep]
        wk["series"][k]["forecast"] = wk["series"][k]["forecast"][keep]

    # ---- actual months for the monthly chart ----
    st = dp.store
    past = []
    for mm in st.available_periods():
        if mm < c["cur_month"]:
            past.append({
                "month": mm, "label": _month_label(mm),
                "enquiries": int(len(st.view("enquiry", mm))),
                "test_drives": int((st.view("enquiry", mm)["Test Drive"] == "Y").sum()),
                "bookings": int(len(st.view("booked", mm))),
                "retails": int(len(st.view("retail", mm))),
            })

    # ---- reliability ----
    back = []
    for k, key in (("enquiries", "enquiries"), ("test_drives", "test_drives"), ("bookings", "gross")):
        p = c["picks"][key]
        base = p["scores"].get("flat28")
        back.append({
            "series": SERIES_LABEL[k] if k != "bookings" else "New bookings made",
            "model": MODEL_TEXT[p["model"]], "miss": round(p["err"] * 100, 1),
            "baseline_miss": round(base["err"] * 100, 1) if base else None, "folds": p["folds"],
        })
    rp = c["rb"]
    base = rp["scores"].get("pace")
    back.append({"series": "Retails", "model": MODEL_TEXT[rp["model"]], "miss": round(rp["err"] * 100, 1),
                 "baseline_miss": round(base["err"] * 100, 1) if base else None, "folds": rp["folds"]})

    days = d.n
    if days < 60:
        badge = {"tone": "amber", "text": f"Limited history · {days} days"}
    elif days < 120:
        badge = {"tone": "blue", "text": f"Moderate history · {days} days"}
    else:
        badge = {"tone": "green", "text": f"Good history · {days} days"}

    notes = []
    if days < 90:
        notes.append(f"The Enquiry sheet holds {days} days ({d.first:%d %b} to {d.last:%d %b %Y}), so the model has little to learn "
                     "from: the ranges are wide and later months are indicative only. They tighten automatically as more months are uploaded.")
    m0 = months[0]
    cur = month_rows[0]["series"]
    notes.append(f"{_month_label(m0)} so far: {cur['enquiries']['actual']} enquiries, {cur['test_drives']['actual']} test drives, "
                 f"{cur['bookings']['actual']} bookings and {cur['retails']['actual']} retails; the forecast adds the rest of the month "
                 f"({(_month_end(pd.Timestamp(m0 + '-01')) - d.last).days} day(s) left).")
    if c["pace_scale"] < 0.8:
        notes.append("Recent deliveries are running slower than the booking-to-retail history suggests (stock or dispatch is the likely limit), "
                     "so the retail forecast is scaled down to the recent pace.")
    elif c["pace_scale"] > 1.25:
        notes.append("Recent deliveries are running faster than the booking-to-retail history suggests, so the retail forecast is scaled up to the recent pace.")

    return {
        "empty": False, "as_of": d.last.strftime("%Y-%m-%d"), "first_date": d.first.strftime("%Y-%m-%d"), "history_days": days,
        "ahead": ahead, "badge": badge, "notes": notes, "months": month_rows, "past_months": past,
        "history": hist, "daily": daily, "cumulative": cum, "weekly": wk,
        "models": {mm: _model_section(c, i)["rows"] for i, mm in enumerate(months)},
        "open": _open_section(c), "backtest": back,
        "method": [
            "Enquiries, test drives and bookings made are daily counts; three simple models are scored on a rolling back-test and the best is used for each.",
            f"Retails come from a booking-to-retail pipeline fitted on {c['n_fit']} past bookings (retail or cancel, by booking age); "
            "it is applied to every booking open today and to the bookings expected to be made.",
            "Total bookings = bookings made in the month that are still undelivered at month end + retails dated in the month, exactly as the Bookings page counts them.",
            "Low / High is an 80% range from 400 simulated futures; it is wider for later months.",
        ],
        "scale": {"retail_pace": round(c["pace_scale"], 2)},
    }


# --------------------------------------------------------------------------- #
# Helpers shared with insights.py (the Management Insights page)
# --------------------------------------------------------------------------- #
def core() -> dict:
    return _core()


def predict_window(d: _Data, c: dict, t: int, horizon: int) -> Dict[str, np.ndarray]:
    """What the forecast engine would have predicted, day by day, for days [t, t+horizon) knowing only days [0, t).
    The model TYPE per series is the one the live forecast uses; every parameter is re-fitted on the earlier days only."""
    fdow = d.dow[t:t + horizon]
    out = {}
    for key in ("enquiries", "test_drives", "gross"):
        out[key] = _level_forecast(d.y[key][:t], d.dow[:t], fdow, c["picks"][key]["model"])
    asof = d.first_int + t - 1
    pipe = _pipeline_expected(d, asof, out["gross"])
    pace = _level_forecast(d.y["retails"][:t], d.dow[:t], fdow, "flat28")
    kind = c["rb"]["model"]
    out["retails"] = pipe if kind == "pipeline" else pace if kind == "pace" else (pipe + pace) / 2
    return out
