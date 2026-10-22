"""
team_scope.py
-------------
Team Leader data scoping.

A Limited user whose Designation is "Team Leader" must only see the enquiries handled by their own
team. The rule is enforced on the SERVER for every request, so it cannot be bypassed from the
browser (changing the Consultant dropdown, editing a URL, etc.).

How it works
~~~~~~~~~~~~
* auth.py works out, per request, which consultants the signed-in Team Leader may see and stores
  that set in the SCOPE context variable (None = unrestricted, which is every Full user).
* install() gives the shared data store (data_processor.store) a "scoped" view of its enquiry
  table: while SCOPE is set, `store.enquiry` returns only the rows whose Consultant Name is in the
  set. Every existing calculation (KPIs, breakdowns, follow-ups, comparison, filters, meta, the
  new Daily report ...) reads `store.enquiry`, so they are all limited automatically and none of
  their logic had to change. data_processor.py itself is NOT modified.
* An empty scope (a Team Leader with no consultants linked) returns an empty table - the safe
  default is "see nothing", never "see everything".

Which consultants belong to a Team Leader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Enquiry export has no Team Leader column, so the team is listed in the account workbook
(data/Data.xlsx, sheet Users) in a "Consultants" column: the consultant names exactly as they
appear in the Enquiry sheet, separated by semicolons, commas or new lines. The Team Leader's own
name is always included as well (in case they handle enquiries themselves).
"""
from __future__ import annotations

import contextvars
import re
import threading

# None  -> unrestricted (Full users, and Limited users who are not Team Leaders)
# frozenset({...normalised consultant names...}) -> only these consultants are visible
SCOPE: contextvars.ContextVar = contextvars.ContextVar("team_scope", default=None)

CONSULTANT_COLUMN = "Consultant Name"

_lock = threading.Lock()
_cache = {"full": None, "frames": {}}
_installed = False


def norm(name) -> str:
    """Case / spacing-insensitive form of a person's name, used for matching."""
    return " ".join(str(name or "").lower().split())


def split_names(text) -> list:
    """'A; B, C' or one name per line -> ['A', 'B', 'C']."""
    return [p.strip() for p in re.split(r"[;,|\r\n]+", str(text or "")) if p.strip()]


def is_team_leader(user: dict) -> bool:
    return "team leader" in norm(user.get("designation", ""))


def scope_for(user: dict):
    """The set of consultants this user may see, or None when they may see everyone."""
    if user.get("access") != "limited" or not is_team_leader(user):
        return None
    names = set(split_names(user.get("consultants", "")))
    names.add(user.get("name") or "")
    return frozenset(n for n in (norm(x) for x in names) if n)


def describe(user: dict, full_frame=None) -> dict:
    """What the page shows a Team Leader about their own scope (names only - nothing sensitive)."""
    scope = scope_for(user)
    if scope is None:
        return {"restricted": False}
    listed = split_names(user.get("consultants", ""))
    present = {}
    if full_frame is not None and CONSULTANT_COLUMN in getattr(full_frame, "columns", []):
        for name in full_frame[CONSULTANT_COLUMN].dropna().astype(str).str.strip().unique():
            present[norm(name)] = name
    matched = sorted({present[norm(n)] for n in listed if norm(n) in present})
    own = norm(user.get("name"))
    if own in present and present[own] not in matched:
        matched.append(present[own])
    unmatched = [n for n in listed if norm(n) not in present]
    return {"restricted": True, "consultants": sorted(matched), "unmatched": unmatched}


def _scoped(full):
    scope = SCOPE.get()
    if scope is None or full is None or CONSULTANT_COLUMN not in getattr(full, "columns", []):
        return full
    with _lock:
        if _cache["full"] is not full:                      # data was reloaded: forget old views
            _cache["full"], _cache["frames"] = full, {}
        frame = _cache["frames"].get(scope)
        if frame is None:
            frame = full[full[CONSULTANT_COLUMN].map(norm).isin(scope)].copy()
            _cache["frames"][scope] = frame
        return frame


def full_enquiry(store):
    """The unrestricted enquiry table (for the few places that must see every row)."""
    return store.__dict__.get("enquiry")


def install(store_cls) -> None:
    """Give DashboardData a scope-aware `enquiry` attribute. Safe to call more than once."""
    global _installed
    if _installed:
        return

    def _get(self):
        return _scoped(self.__dict__.get("enquiry"))

    def _set(self, value):
        self.__dict__["enquiry"] = value

    store_cls.enquiry = property(_get, _set)
    _installed = True
