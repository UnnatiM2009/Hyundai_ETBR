"""
insights.py
-----------
Management Insights page of the Hyundai ETBR dashboard.  It reuses the Forecast engine in forecast.py; nothing here changes the
numbers of any other page - it only reads the same Enquiry-sheet history.

What it answers
  1. Next month's enquiries, bookings and retails            -> taken from forecast.py (same model, same ranges), shown as an outlook.
  2. Who is declining (sales consultants, models, sources)   -> the last 28 complete days against the 28 days before, per stage,
                                                                with an exact test.
  3. Predicted conversion rates                              -> E2T, E2B, E2R, B2R for next month, overall and per consultant / model.
  4. Sales consultant productivity                           -> output per 28 active days, conversion against the rest of the team, an index.
  5. Automatic management observations                       -> plain-language findings written from the numbers above (rules, not guesses).
  6. Actual against the machine-learning forecast            -> a walk-forward track record: what the model would have predicted at
                                                                the time vs what happened.

Statistics, kept deliberately simple and explainable (the history is only a few weeks, so a bigger model would only memorise noise)
  * Decline test.  A count in the last 28 days (X1) against the 28 days before (X2): given X1+X2, X1 is Binomial(X1+X2, 1/2) when the
    rate has not changed.  The exact two-sided p-value says how likely a swing this big is by chance alone.  A consultant with a few
    bookings therefore needs a big swing before being called "declining"; a smaller one is only "watch".
  * Momentum.  A weighted average of the log-changes of retails (35%), bookings (30%), enquiries (25%) and test drives (10%).
  * Conversion forecast.  Flow-based, like the Comparison page (a month's bookings / the same month's enquiries).  For a consultant or
    model the next-month rate is a recency-weighted rate (last 28 days at full weight, the 28 days before at half weight) shrunk
    towards the overall rate when the base is small (empirical-Bayes).
  * Retail data gap.  When retails of the last 14 days are far below the 14 days before, the Enquiry sheet may be out of date.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np
import pandas as pd

import data_processor as dp
import forecast as fc

WINDOW = 28
WEIGHTS = {"retails": 0.35, "bookings": 0.30, "enquiries": 0.25, "test_drives": 0.10}
STAGE_LABEL = {"enquiries": "Enquiries", "test_drives": "Test drives", "bookings": "Bookings", "retails": "Retails"}
SHRINK_K = 25.0
LOW_VOLUME = 30
NOT_PEOPLE = {"unassigned", "not captured"}      # buckets, not sales consultants: left out of every consultant ranking
MOM_CAP = 300.0                                  # a swing above +/-300% is shown as 300%


def _binom_p(x1: int, x2: int) -> float:
    """Exact two-sided p-value of X1 ~ Binomial(X1 + X2, 1/2)."""
    n = x1 + x2
    if n == 0:
        return 1.0
    m = min(x1, x2)
    tail = sum(math.comb(n, k) for k in range(m + 1))
    return min(1.0, 2.0 * tail / (2 ** n))


def _pct_change(new: float, old: float):
    if old == 0:
        return None if new == 0 else None
    return round((new - old) / old * 100, 1)


# --------------------------------------------------------------------------- #
# Event table: one row per event, with the group columns
# --------------------------------------------------------------------------- #
def _events(d: "fc._Data") -> pd.DataFrame:
    e = d.e
    base = pd.DataFrame({
        "consultant": e["Consultant Name"].astype(str).str.strip(),
        "model": e["Model"].astype(str).str.strip(),
        "source": e["Source"].astype(str).str.strip(),
    })
    frames = []
    def add(stage, mask, dates):
        sub = base[mask & dates.notna()].copy()
        sub["stage"] = stage
        sub["date"] = dates[mask & dates.notna()].values
        frames.append(sub)
    add("enquiries", e["Enquiry Date"].notna(), d.enq_d)
    add("test_drives", e["Test Drive"].astype(str).str.upper().eq("Y"), d.enq_d)
    add("bookings", e["is_booked"] | e["is_retail"], d.book_d)          # new bookings made (before they retail)
    add("retails", e["is_retail"], d.ret_d)
    ev = pd.concat(frames, ignore_index=True)
    for col in ("consultant", "model", "source"):
        ev.loc[ev[col].str.lower().isin(["", "nan", "none"]), col] = "Not captured"
    return ev


def _surge_note(d: "fc._Data") -> str:
    """Enquiries are compared 28 days against 28 days; a one-off surge in the earlier window makes everyone look lower."""
    w0, w1 = _windows(d)
    y = pd.Series(d.y["enquiries"], index=d.idx)
    prior, last = y[w0[0]:w0[1]], y[w1[0]:w1[1]]
    if prior.empty or last.empty:
        return ""
    med = float(y.median())
    spikes = prior[prior > 2.5 * max(med, 1.0)]
    if len(spikes) >= 1 and prior.sum() > 1.15 * last.sum():
        days = ", ".join(f"{i:%d %b} ({int(v)})" for i, v in spikes.sort_values(ascending=False).head(3).items())
        return (f" The earlier 28 days include unusually busy enquiry days ({days}; a normal day is about {med:.0f}), "
                "so part of every enquiry decline below is that surge, not a change in performance.")
    return ""


def _windows(d: "fc._Data"):
    last = d.last
    w1 = (last - pd.Timedelta(days=WINDOW - 1), last)
    w0 = (last - pd.Timedelta(days=2 * WINDOW - 1), last - pd.Timedelta(days=WINDOW))
    return w0, w1


# --------------------------------------------------------------------------- #
# 2. Who is declining
# --------------------------------------------------------------------------- #
def _decline(d: "fc._Data", ev: pd.DataFrame) -> dict:
    w0, w1 = _windows(d)
    have_prior = w0[0] >= d.first - pd.Timedelta(days=3)
    out = {"window": {"last_from": w1[0].strftime("%Y-%m-%d"), "last_to": w1[1].strftime("%Y-%m-%d"),
                      "prior_from": w0[0].strftime("%Y-%m-%d"), "prior_to": w0[1].strftime("%Y-%m-%d")},
           "available": bool(have_prior), "tabs": {}}
    if not have_prior:
        return out
    in1 = (ev["date"] >= w1[0]) & (ev["date"] <= w1[1])
    in0 = (ev["date"] >= w0[0]) & (ev["date"] <= w0[1])
    for dim in ("consultant", "model", "source"):
        rows = []
        grp1 = ev[in1].groupby([dim, "stage"]).size()
        grp0 = ev[in0].groupby([dim, "stage"]).size()
        names = sorted(set(ev.loc[in1 | in0, dim]))
        if dim == "consultant":
            names = [n for n in names if n.lower() not in NOT_PEOPLE]
        for name in names:
            stages, mom, wsum = {}, 0.0, 0.0
            worst_p, worst_stage = 1.0, None
            for st in STAGE_LABEL:
                x1, x0 = int(grp1.get((name, st), 0)), int(grp0.get((name, st), 0))
                p = _binom_p(x1, x0)
                stages[st] = {"last": x1, "prior": x0, "change": _pct_change(x1, x0), "p": round(p, 3)}
                if x1 + x0 > 0:
                    mom += WEIGHTS[st] * math.log((x1 + 1.0) / (x0 + 1.0))
                    wsum += WEIGHTS[st]
                    if x1 < x0 and p < worst_p:
                        worst_p, worst_stage = p, st
            mom = mom / wsum if wsum else 0.0
            volume = stages["enquiries"]["last"] + stages["enquiries"]["prior"]
            sig_drop = worst_p < 0.05
            enq1, enq0 = stages["enquiries"]["last"], stages["enquiries"]["prior"]
            bk_ret_drop = any(stages[k]["last"] < stages[k]["prior"] and stages[k]["p"] < 0.10 for k in ("bookings", "retails"))
            if enq1 == 0 and enq0 >= 10:
                status = "inactive"             # nothing in the last 28 days: left, transferred, or not recording
            elif enq0 == 0 and enq1 > 0:
                status = "new"                  # nothing before: started or began recording in the last 28 days
            elif mom <= -0.25 and sig_drop:
                status = "declining"
            elif mom <= -0.15 or bk_ret_drop:
                status = "watch"
            elif mom >= 0.2:
                status = "improving"
            else:
                status = "stable"
            if volume < LOW_VOLUME and status == "declining":
                status = "watch"
            rows.append({
                "label": name, "stages": stages,
                "momentum": round(max(-MOM_CAP, min(MOM_CAP, (math.exp(mom) - 1) * 100)), 1), "status": status,
                "worst_stage": STAGE_LABEL.get(worst_stage) if worst_stage else None, "worst_p": round(worst_p, 3),
                "low_volume": bool(volume < LOW_VOLUME),
            })
        order = {"declining": 0, "inactive": 1, "watch": 2, "stable": 3, "improving": 4, "new": 5}
        rows.sort(key=lambda r: (order[r["status"]], -r["momentum"] if r["status"] in ("improving", "new") else r["momentum"]))
        out["tabs"][dim] = rows
    return out


# --------------------------------------------------------------------------- #
# 3. Conversion forecast
# --------------------------------------------------------------------------- #
def _rate_row(label, enq1, enq0, td1, td0, bk1, bk0, rt1, rt0, overall):
    """Shrunk, recency-weighted next-month rates for one group."""
    def rate(num1, num0, den1, den0, prior):
        den = den1 + 0.5 * den0
        num = num1 + 0.5 * num0
        p = (num + SHRINK_K * prior / 100.0) / (den + SHRINK_K) if (den + SHRINK_K) > 0 else 0.0
        n_eff = den + SHRINK_K
        half = 1.28 * math.sqrt(max(p * (1 - p), 1e-6) / max(n_eff, 1)) * 1.4
        return {"mid": round(p * 100, 1), "low": round(max(p - half, 0) * 100, 1), "high": round(min(p + half, 1) * 100, 1)}
    return {
        "label": label, "enquiries": int(enq1 + enq0),
        "E2T": rate(td1, td0, enq1, enq0, overall["E2T"]),
        "E2B": rate(bk1, bk0, enq1, enq0, overall["E2B"]),
        "E2R": rate(rt1, rt0, enq1, enq0, overall["E2R"]),
        "B2R": rate(rt1, rt0, bk1 + rt1, bk0 + rt0, overall["B2R"]),
        "small": bool(enq1 + enq0 < LOW_VOLUME),
    }


def _conversion(d: "fc._Data", c: dict, ev: pd.DataFrame) -> dict:
    ratios = fc.month_ratios(c, 1 if len(c["months"]) > 1 else 0)
    month_i = 1 if len(c["months"]) > 1 else 0
    w0, w1 = _windows(d)
    in1 = (ev["date"] >= w1[0]) & (ev["date"] <= w1[1])
    in0 = (ev["date"] >= w0[0]) & (ev["date"] <= w0[1])
    overall_prior = {k: v["mid"] for k, v in ratios.items()}

    def counts(mask, dim, name):
        sel = ev[mask & (ev[dim] == name)] if dim else ev[mask]
        vc = sel["stage"].value_counts()
        return [int(vc.get(s, 0)) for s in ("enquiries", "test_drives", "bookings", "retails")]

    tabs = {}
    for dim in ("consultant", "model"):
        rows = []
        for name in sorted(set(ev[dim])):
            if dim == "consultant" and name.lower() in NOT_PEOPLE:
                continue
            e1, t1, b1, r1 = counts(in1, dim, name)
            e0, t0, b0, r0 = counts(in0, dim, name)
            if e1 + e0 + b1 + b0 + r1 + r0 == 0:
                continue
            rows.append(_rate_row(name, e1, e0, t1, t0, b1, b0, r1, r0, overall_prior))
        rows.sort(key=lambda r: -r["enquiries"])
        tabs[dim] = rows

    # "now" = the last 28 days, flow-based, for comparison with the forecast
    e1, t1, b1, r1 = counts(in1, None, None)
    now = {
        "E2T": round(t1 / e1 * 100, 1) if e1 else 0.0, "E2B": round(b1 / e1 * 100, 1) if e1 else 0.0,
        "E2R": round(r1 / e1 * 100, 1) if e1 else 0.0, "B2R": round(r1 / (b1 + r1) * 100, 1) if (b1 + r1) else 0.0,
    }
    return {
        "month": c["months"][month_i], "month_label": fc._month_label(c["months"][month_i]),
        "overall": ratios, "now": now, "tabs": tabs,
        "labels": {"E2T": "Enquiry → Test drive", "E2B": "Enquiry → Booking", "E2R": "Enquiry → Retail", "B2R": "Booking → Retail"},
    }


# --------------------------------------------------------------------------- #
# 4. Consultant productivity
# --------------------------------------------------------------------------- #
def _productivity(d: "fc._Data", ev: pd.DataFrame) -> dict:
    e = d.e
    first_seen = ev.groupby("consultant")["date"].min()
    last_seen = ev.groupby("consultant")["date"].max()
    tot = ev.groupby(["consultant", "stage"]).size().unstack(fill_value=0)
    for s in STAGE_LABEL:
        if s not in tot.columns:
            tot[s] = 0
    hist_days = d.n
    rows = []
    for name in tot.index:
        span = (d.last - first_seen[name]).days + 1          # to the END of the data, so someone who stopped is not inflated
        span = int(min(max(span, 14), hist_days))
        scale = 28.0 / span
        r = {"label": name, "active_days": span, "volume": int(tot.loc[name, "enquiries"])}
        for s in STAGE_LABEL:
            r["total_" + s] = int(tot.loc[name, s])
            r["per28_" + s] = round(float(tot.loc[name, s]) * scale, 1)
        r["e2t"] = round(tot.loc[name, "test_drives"] / tot.loc[name, "enquiries"] * 100, 1) if tot.loc[name, "enquiries"] else None
        r["e2b"] = round(tot.loc[name, "bookings"] / tot.loc[name, "enquiries"] * 100, 1) if tot.loc[name, "enquiries"] else None
        r["e2r"] = round(tot.loc[name, "retails"] / tot.loc[name, "enquiries"] * 100, 1) if tot.loc[name, "enquiries"] else None
        rows.append(r)
    rows = [r for r in rows if r["label"].lower() not in NOT_PEOPLE]
    if not rows:
        return {"rows": [], "team": {}}
    team = {s: float(np.mean([r["per28_" + s] for r in rows])) for s in STAGE_LABEL}
    tot_enq = sum(r["total_enquiries"] for r in rows)
    team_conv = {
        "e2t": sum(r["total_test_drives"] for r in rows) / tot_enq * 100 if tot_enq else 0,
        "e2b": sum(r["total_bookings"] for r in rows) / tot_enq * 100 if tot_enq else 0,
        "e2r": sum(r["total_retails"] for r in rows) / tot_enq * 100 if tot_enq else 0,
    }
    for r in rows:
        idx = 0.0
        for s, w in WEIGHTS.items():
            idx += w * (r["per28_" + s] / team[s] if team[s] else 0)
        r["index"] = round(idx * 100, 0)
        r["low_volume"] = bool(r["volume"] < LOW_VOLUME)
        r["vs_team_e2b"] = round(r["e2b"] - team_conv["e2b"], 1) if r["e2b"] is not None else None
        if r["low_volume"]:
            r["band"] = "low volume"
        elif r["index"] >= 115:
            r["band"] = "above team"
        elif r["index"] <= 85:
            r["band"] = "below team"
        else:
            r["band"] = "in line"
    rows.sort(key=lambda r: -r["index"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "team": {k: round(v, 1) for k, v in team.items()},
            "team_conv": {k: round(v, 1) for k, v in team_conv.items()}}


# --------------------------------------------------------------------------- #
# 6. Track record (walk-forward)
# --------------------------------------------------------------------------- #
def _track(d: "fc._Data", c: dict) -> dict:
    keys = [("enquiries", "enquiries"), ("test_drives", "test_drives"), ("bookings", "gross"), ("retails", "retails")]
    labels = {"enquiries": "Enquiries", "test_drives": "Test drives", "bookings": "New bookings made", "retails": "Retails"}
    out = {"weeks": [], "months": [], "summary": [], "labels": labels}
    n = d.n
    weekly = {k: {"actual": [], "forecast": []} for k, _ in keys}
    week_labels = []
    for j in range(3, -1, -1):
        t = n - 7 * (j + 1)
        if t < fc.BT_MIN_TRAIN:
            continue
        pred = fc.predict_window(d, c, t, 7)
        week_labels.append(f"{d.idx[t]:%d %b} – {d.idx[t + 6]:%d %b}")
        for k, yk in keys:
            weekly[k]["actual"].append(int(d.y[yk][t:t + 7].sum()))
            weekly[k]["forecast"].append(round(float(pred[yk].sum()), 1))
    out["weeks"] = {"labels": week_labels, "series": weekly}
    for k, _ in keys:
        a = np.array(weekly[k]["actual"], dtype=float)
        f = np.array(weekly[k]["forecast"], dtype=float)
        if len(a):
            wape = float(np.abs(f - a).sum() / max(a.sum(), 1.0))      # weekly misses relative to total volume
            out["summary"].append({
                "key": k, "label": labels[k], "weeks": int(len(a)),
                "accuracy": round(max(0.0, 100 - wape * 100), 1),
                "bias": round(float((f.sum() - a.sum()) / max(a.sum(), 1.0) * 100), 1),
                "actual": int(a.sum()), "forecast": round(float(f.sum()), 1),
            })
    # the forecast made on the 1st of the current month, against what happened since
    cur_start = pd.Timestamp(c["cur_month"] + "-01")
    t = int((cur_start - d.idx[0]).days)
    if t >= 21 and n - t >= 7:
        pred = fc.predict_window(d, c, t, n - t)
        rows = []
        for k, yk in keys:
            a = float(d.y[yk][t:].sum())
            f = float(pred[yk].sum())
            rows.append({"key": k, "label": labels[k], "actual": int(a), "forecast": round(f, 1),
                         "error": round((f - a) / max(a, 1.0) * 100, 1)})
        out["months"] = [{"label": f"{fc._month_label(c['cur_month'])} (1st – {d.last:%d %b})", "days": int(n - t), "rows": rows,
                          "train_days": t}]
    return out


# --------------------------------------------------------------------------- #
# 5. Observations (rules written as plain language)
# --------------------------------------------------------------------------- #
def _observations(d, c, fcast, decline, conv, prod, track) -> List[dict]:
    obs: List[dict] = []
    add = lambda kind, area, title, evidence, action: obs.append(
        {"kind": kind, "area": area, "title": title, "evidence": evidence, "action": action})

    months = fcast["months"]
    if len(months) > 1:
        cur, nxt = months[0]["series"], months[1]["series"]
        nm = months[1]["label"]
        for k, lab in (("enquiries", "enquiries"), ("bookings", "bookings"), ("retails", "retails")):
            a, b = cur[k]["mid"], nxt[k]
            if a <= 0:
                continue
            change = (b["mid"] - a) / a * 100
            if abs(change) < 8:
                continue
            below = b["high"] < a
            above = b["low"] > a
            if change > 0 and not above:
                continue                                    # a rise inside the range is not worth a headline
            if change < 0:
                add("risk" if below else "watch", "Outlook", f"{nm}: {lab} are expected to fall {abs(change):.0f}%",
                    f"{b['mid']:.0f} expected in {nm} (range {b['low']:.0f}–{b['high']:.0f}) against about {a:.0f} in {months[0]['label']}.",
                    "Check lead generation and consultant capacity now; the range is wide while history is short.")
            else:
                add("good" if above else "watch", "Outlook", f"{nm}: {lab} are expected to rise {change:.0f}%",
                    f"{b['mid']:.0f} expected in {nm} (range {b['low']:.0f}–{b['high']:.0f}) against about {a:.0f} in {months[0]['label']}.",
                    "Make sure stock, test-drive cars and staffing can handle the volume." if k != "retails" else
                    "Check that stock and allotments can cover the deliveries.")
    # declining people / models
    pfmt = lambda p: "under 1%" if p < 0.01 else f"{p * 100:.0f}%"
    improving_seen = {"consultant": 0, "model": 0, "source": 0}
    for dim, noun in (("consultant", "Consultant"), ("model", "Model"), ("source", "Source")):
        for r in decline.get("tabs", {}).get(dim, []):
            s = r["stages"]
            if r["status"] == "inactive":
                add("watch", noun, f"{r['label']} has had no enquiries in the last 28 days",
                    f"{s['enquiries']['prior']} enquiries, {s['bookings']['prior']} bookings and {s['retails']['prior']} retails in the 28 days before.",
                    "Confirm whether they have left, moved team, or their enquiries are not being recorded; reassign any open follow-ups.")
            elif r["status"] == "declining":
                worst = r["worst_stage"] or "volume"
                st = s[{v: k for k, v in STAGE_LABEL.items()}.get(worst, "enquiries")]
                add("risk", noun, f"{r['label']} is declining",
                    f"Last 28 days vs the 28 before: enquiries {s['enquiries']['prior']}→{s['enquiries']['last']}, bookings "
                    f"{s['bookings']['prior']}→{s['bookings']['last']}, retails {s['retails']['prior']}→{s['retails']['last']} "
                    f"(overall {r['momentum']:+.0f}%). Biggest fall: {worst.lower()} ({st['prior']}→{st['last']}; chance of a swing this big by luck: {pfmt(r['worst_p'])}).",
                    "Review the pipeline one by one with them: follow-ups pending, lost reasons, test-drive coverage.")
            elif r["status"] == "improving" and not r["low_volume"] and dim != "source" and improving_seen[dim] < (3 if dim == "consultant" else 2):
                improving_seen[dim] += 1
                add("good", noun, f"{r['label']} is improving",
                    f"Last 28 days vs the 28 before: enquiries {s['enquiries']['prior']}→{s['enquiries']['last']}, bookings "
                    f"{s['bookings']['prior']}→{s['bookings']['last']}, retails {s['retails']['prior']}→{s['retails']['last']} ({r['momentum']:+.0f}%).",
                    "Find what is working and share it with the team.")
    watch = [r["label"] for r in decline.get("tabs", {}).get("consultant", []) if r["status"] == "watch"]
    if watch:
        add("watch", "Consultant", f"{len(watch)} consultant(s) to watch",
            ", ".join(watch[:8]) + (" …" if len(watch) > 8 else "") + " - softer than the 28 days before, but not yet clearly below normal.",
            "Look at them again after the next data upload.")
    # productivity
    low = [r for r in prod["rows"] if r["band"] == "below team" and not r["low_volume"]]
    for r in low[:4]:
        add("watch", "Consultant", f"{r['label']}: output below the team",
            f"Productivity index {r['index']:.0f} (team = 100). Per 28 days: {r['per28_enquiries']} enquiries, {r['per28_bookings']} bookings, "
            f"{r['per28_retails']} retails. Enquiry→booking {r['e2b']}% against {prod['team_conv']['e2b']}% for the team.",
            "Compare their lead mix and follow-up discipline with a top performer.")
    # conversion
    nowc, ov = conv["now"], conv["overall"]
    if nowc["E2T"] < 20:
        add("watch", "Conversion", "Test-drive coverage is low",
            f"Only {nowc['E2T']}% of enquiries in the last 28 days had a test drive.", "Push test drives: they are the strongest step before booking.")
    if ov["B2R"]["mid"] < 50:
        add("watch", "Conversion", "Less than half of bookings turn into retails in a month",
            f"Booking → retail is forecast at {ov['B2R']['mid']}% for {conv['month_label']} (range {ov['B2R']['low']}–{ov['B2R']['high']}%).",
            "Check stock availability for open bookings and follow up on delivery dates.")
    # open bookings
    op = fcast["open"]["kpis"]
    if op["over_30"]:
        add("risk", "Pipeline", f"{op['over_30']} booking(s) have been waiting more than 30 days for delivery",
            f"{op['open']} bookings are open, average age {op['avg_age']} days; about {op['cancel_30']:.0f} of them are expected to cancel within 30 days.",
            "Call these customers this week and confirm delivery or reallocate stock.")
    # cancellations
    e = d.e
    can = e[e["is_booking_cancel"]]
    w0, w1 = _windows(d)
    c1 = int(((can["Lost Date"] >= w1[0]) & (can["Lost Date"] <= w1[1])).sum())
    c0 = int(((can["Lost Date"] >= w0[0]) & (can["Lost Date"] <= w0[1])).sum())
    if c1 > c0 and c1 >= 5:
        add("risk", "Cancellations", f"Booking cancellations rose from {c0} to {c1}",
            "Last 28 days against the 28 days before (by cancel date).", "Open the Booking Cancellations page and review the reasons and consultants.")
    elif c1 >= 5 and c1 < c0:
        add("good", "Cancellations", f"Booking cancellations fell from {c0} to {c1}", "Last 28 days against the 28 days before.", "Keep the follow-up routine that is working.")
    # data gap
    r14 = float(d.y["retails"][-14:].sum())
    r14p = float(d.y["retails"][-28:-14].sum())
    if r14p >= 8 and r14 < 0.4 * r14p:
        add("data", "Data", "Retails of the last 14 days look low",
            f"{int(r14)} retails in the last 14 days against {int(r14p)} in the 14 before - the Enquiry sheet may be out of date.",
            "Upload the latest Enquiry export before relying on the retail numbers.")
    # track record
    acc = [s for s in track["summary"] if s["weeks"]]
    if acc:
        worst = min(acc, key=lambda s: s["accuracy"])
        if worst["accuracy"] < 70:
            add("data", "Forecast", f"The {worst['label'].lower()} forecast has been rough",
                f"Weekly accuracy {worst['accuracy']}% over the last {worst['weeks']} weeks (bias {worst['bias']:+.0f}%).",
                "Treat that line of the forecast as indicative until more history is loaded.")
    order = {"risk": 0, "watch": 1, "data": 2, "good": 3}
    obs.sort(key=lambda o: order[o["kind"]])
    return obs


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #
def compute_insights() -> dict:
    c = fc.core()
    if c.get("empty"):
        return {"empty": True, "message": c.get("message", "")}
    d = c["d"]
    fcast = fc.compute_forecast(fc.MONTHS_AHEAD)
    ev = _events(d)
    decline = _decline(d, ev)
    conv = _conversion(d, c, ev)
    prod = _productivity(d, ev)
    track = _track(d, c)
    obs = _observations(d, c, fcast, decline, conv, prod, track)

    months = fcast["months"]
    nxt = months[1] if len(months) > 1 else months[0]
    cur = months[0]
    dec_n = sum(1 for r in decline.get("tabs", {}).get("consultant", []) if r["status"] == "declining")
    inactive_n = sum(1 for r in decline.get("tabs", {}).get("consultant", []) if r["status"] == "inactive")
    watch_n = sum(1 for r in decline.get("tabs", {}).get("consultant", []) if r["status"] == "watch")
    kpis = [
        {"label": f"{nxt['label']} enquiries", "value": nxt["series"]["enquiries"]["mid"], "low": nxt["series"]["enquiries"]["low"],
         "high": nxt["series"]["enquiries"]["high"], "ref": cur["series"]["enquiries"]["mid"], "ref_label": cur["label"], "tone": "blue"},
        {"label": f"{nxt['label']} bookings", "value": nxt["series"]["bookings"]["mid"], "low": nxt["series"]["bookings"]["low"],
         "high": nxt["series"]["bookings"]["high"], "ref": cur["series"]["bookings"]["mid"], "ref_label": cur["label"], "tone": "green"},
        {"label": f"{nxt['label']} retails", "value": nxt["series"]["retails"]["mid"], "low": nxt["series"]["retails"]["low"],
         "high": nxt["series"]["retails"]["high"], "ref": cur["series"]["retails"]["mid"], "ref_label": cur["label"], "tone": "purple"},
        {"label": "Enquiry → booking (forecast)", "value": conv["overall"]["E2B"]["mid"], "low": conv["overall"]["E2B"]["low"],
         "high": conv["overall"]["E2B"]["high"], "ref": conv["now"]["E2B"], "ref_label": "last 28 days", "tone": "amber", "pct": True},
    ]
    return {
        "empty": False, "as_of": fcast["as_of"], "history_days": fcast["history_days"], "badge": fcast["badge"],
        "intro": (f"Built from {fcast['history_days']} days of the Enquiry sheet ({d.first:%d %b} to {d.last:%d %b %Y}). "
                  "Each finding below shows the numbers behind it; none of it changes the other pages."),
        "notes": fcast["notes"][:1], "kpis": kpis,
        "counts": {"declining": dec_n, "watch": watch_n, "inactive": inactive_n,
                   "risk": sum(1 for o in obs if o["kind"] == "risk"), "open_over_30": fcast["open"]["kpis"]["over_30"]},
        "observations": obs, "decline": decline, "conversion": conv, "productivity": prod, "track": track,
        "decline_note": ("A swing is called 'declining' only when it is big and not explainable by chance; a consultant with few cases "
                         "needs a bigger swing. 'Inactive' = no enquiries at all in the last 28 days; 'New' = nothing in the 28 days before. "
                         "Bookings here are new bookings made (Booked + Retail, on Booking Date)." + _surge_note(d)),
    }
