"""Classification engine for Investment Holdings.

Workflow:
  1. New Investment Holding → classification = "Unallocated".
  2. On insert, `compute_deadline()` stamps classification_deadline = creation
     + 2 working days (Mon-Fri minus the company's Holiday List).
  3. Within the window, an authorized user can call
     `classify_as_investment(holding_name)` to mark it `Investment`.
  4. The daily scheduler `auto_classify_expired_unallocated()` walks all
     Unallocated Holdings whose deadline has passed and locks them as
     `Stock in Trade`.
  5. After classification, Portfolio Transfer is the only way to move
     between Stock in Trade ↔ Investment (FMV-based, approval controlled).
"""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime, getdate

# Skip Saturday + Sunday by default. Holiday List (per-company) takes
# precedence when present and adds India public holidays on top.
_WORKING_DAYS = 2
_WEEKEND = {5, 6}  # Saturday=5, Sunday=6 in Python's date.weekday()


# ── public API ──────────────────────────────────────────────────────────


def compute_deadline(start_dt, company=None):
    """Compute classification deadline = start_dt + 2 working days.

    Uses ERPNext's Holiday List (resolved via Company.default_holiday_list)
    to skip holidays. Falls back to weekend-only skipping if no Holiday List
    is configured.

    Returns a datetime.
    """
    if not start_dt:
        start_dt = now_datetime()
    start = get_datetime(start_dt)

    holidays = _resolve_holidays(company)
    current = start
    days_added = 0
    while days_added < _WORKING_DAYS:
        current = add_days(current, 1)
        if _is_working_day(current, holidays):
            days_added += 1
    return current


def auto_classify_expired_unallocated() -> dict:
    """Daily scheduler: walk Unallocated Holdings past their deadline and
    set classification = Stock in Trade. Returns a count summary.
    """
    if not frappe.db.has_column("Investment Holding", "classification"):
        # Patch hasn't run yet; nothing to do.
        return {"processed": 0, "reason": "classification field absent"}

    expired = frappe.get_all(
        "Investment Holding",
        filters={
            "classification": "Unallocated",
            "classification_deadline": ["<=", now_datetime()],
        },
        pluck="name",
    )

    processed = 0
    for name in expired:
        try:
            frappe.db.set_value(
                "Investment Holding", name,
                {
                    "classification": "Stock in Trade",
                    "classified_on": now_datetime(),
                    "classified_by": "Administrator",
                },
                update_modified=False,
            )
            processed += 1
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Polemarch Auto-Classify ({name})",
            )

    frappe.db.commit()
    return {"processed": processed, "considered": len(expired)}


def classify_as_investment(holding_name: str, actor: str | None = None) -> dict:
    """Mark a holding as Investment. Only valid if currently Unallocated and
    we're still within the deadline window.

    Returns the updated state.
    """
    actor = actor or frappe.session.user
    holding = frappe.get_doc("Investment Holding", holding_name)

    if holding.classification != "Unallocated":
        frappe.throw(
            _(
                "Investment Holding {0} is already classified as {1}. "
                "Use Portfolio Transfer to move between Stock in Trade and Investment."
            ).format(holding_name, holding.classification),
            title=_("Already Classified"),
        )

    if holding.classification_deadline and get_datetime(holding.classification_deadline) < now_datetime():
        frappe.throw(
            _(
                "Investment Holding {0}: classification window expired on {1}. "
                "It will be auto-classified as Stock in Trade on the next scheduler run; "
                "after that, use Portfolio Transfer to move to Investment."
            ).format(holding_name, holding.classification_deadline),
            title=_("Window Expired"),
        )

    frappe.db.set_value(
        "Investment Holding", holding_name,
        {
            "classification": "Investment",
            "classified_on": now_datetime(),
            "classified_by": actor,
        },
        update_modified=True,
    )
    frappe.db.commit()
    return {
        "holding": holding_name,
        "classification": "Investment",
        "classified_by": actor,
        "classified_on": str(now_datetime()),
    }


# ── helpers ─────────────────────────────────────────────────────────────


def _resolve_holidays(company):
    """Return a set of date objects for the given company's Holiday List."""
    holiday_list = None
    if company:
        holiday_list = frappe.db.get_value("Company", company, "default_holiday_list")
    if not holiday_list:
        # Try the global default.
        holiday_list = frappe.defaults.get_global_default("holiday_list")
    if not holiday_list or not frappe.db.exists("Holiday List", holiday_list):
        return set()

    rows = frappe.get_all(
        "Holiday",
        filters={"parent": holiday_list},
        fields=["holiday_date"],
    )
    return {getdate(r.holiday_date) for r in rows}


def _is_working_day(dt, holidays: set) -> bool:
    """Mon-Fri + not in Holiday List."""
    d = getdate(dt)
    if d.weekday() in _WEEKEND:
        return False
    if d in holidays:
        return False
    return True
