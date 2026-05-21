"""FIFO engine — walks Investment Holding rows oldest-first, keyed by Security.

No lot identity; just pure FIFO across Investment Holdings filtered by
classification (Stock in Trade or Investment) and `security`.

Ordering: by `creation` ASC (doc creation timestamp). The classification
deadline is also creation-anchored, so "oldest holding" is consistent
between FIFO selection and classification-expiry logic.

`consume()` is non-mutating — returns a plan that callers turn into
Investment Disposal Lot rows. Security Sale.on_submit and the legacy
matching/settlement paths both go through here.
"""

from dataclasses import dataclass
from datetime import date as _date
from typing import List, Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate


_LONG_TERM_THRESHOLD_DAYS = 730  # IT-Act unlisted-equity threshold


@dataclass
class ConsumedHolding:
    holding: str                # Investment Holding name
    qty: float                  # qty to consume from this holding
    cost_basis_per_unit: float
    acquisition_date: Optional[_date]
    holding_period_days: int
    is_long_term: bool


def consume(
    security: str,
    company: str,
    classification: str,
    qty_to_sell: float,
    sale_date: Optional[_date] = None,
    lock_timeout_seconds: int = 5,
    customer_filter: Optional[str] = None,
) -> List[ConsumedHolding]:
    """Plan a FIFO consumption against open Investment Holdings.

    Args:
        security: Security name (the standalone trading identity) to consume.
        company: Company scope.
        classification: "Stock in Trade" or "Investment" — filters the pool.
        qty_to_sell: how much to consume.
        sale_date: defaults to today; used for holding-period calc.
        lock_timeout_seconds: advisory lock wait.
        customer_filter: optional Customer name (for customer-owned holdings).
            Pass None for proprietary holdings.

    Returns: list of (holding, qty, cost_basis, acq_date, holding_days, is_long_term).
    Empty list if qty_to_sell <= 0 or no open holdings available.
    """
    qty_to_sell = flt(qty_to_sell)
    if qty_to_sell <= 0:
        return []

    sale_date = getdate(sale_date)

    lock_key = f"polemarch:fifo:{security}:{classification}:{customer_filter or '_prop'}"
    if not _acquire_advisory_lock(lock_key, lock_timeout_seconds):
        frappe.throw(
            _("Could not acquire FIFO lock for {0}/{1} within {2}s.").format(
                security, classification, lock_timeout_seconds
            ),
            title=_("FIFO Lock Timeout"),
        )

    # Walk Investment Holdings ordered by creation (FIFO).
    # The security Custom Field was added in v0_9_0; pre-v0_9_0 sites can't
    # consume here because there's no Holding.security column to query yet.
    has_security_col = frappe.db.has_column("Investment Holding", "security")
    if not has_security_col:
        frappe.throw(
            _(
                "Investment Holding.security column is missing. Run the v0_9_0 "
                "migration (`bench --site <site> migrate`) before consuming FIFO."
            ),
            title=_("Schema Out of Date"),
        )
    classification_check = (
        "classification = %s"
        if frappe.db.has_column("Investment Holding", "classification")
        else "1=1"
    )
    params = (
        (security, company, classification)
        if "classification" in classification_check
        else (security, company)
    )
    rows = frappe.db.sql(
        f"""
        SELECT name, qty_acquired, qty_disposed,
               COALESCE(qty_reserved, 0) AS qty_reserved,
               cost_basis_per_unit, acquisition_date, creation
          FROM `tabInvestment Holding`
         WHERE security = %s
           AND company  = %s
           AND status IN ('Open', 'Partially Disposed')
           AND {classification_check}
         ORDER BY creation ASC
         FOR UPDATE
        """,
        params,
        as_dict=True,
    )

    plan: List[ConsumedHolding] = []
    remaining = qty_to_sell
    for row in rows:
        if remaining <= 0:
            break
        available = flt(row.qty_acquired) - flt(row.qty_disposed) - flt(row.qty_reserved)
        if available <= 0:
            continue
        take = min(available, remaining)
        acq = getdate(row.acquisition_date) if row.acquisition_date else None
        days = (sale_date - acq).days if acq else 0
        plan.append(
            ConsumedHolding(
                holding=row.name,
                qty=take,
                cost_basis_per_unit=flt(row.cost_basis_per_unit),
                acquisition_date=acq,
                holding_period_days=days,
                is_long_term=days > _LONG_TERM_THRESHOLD_DAYS,
            )
        )
        remaining -= take

    return plan


def reserve(plan: List[ConsumedHolding]) -> None:
    """Bump qty_reserved on each Investment Holding in the plan."""
    for entry in plan:
        frappe.db.sql(
            "UPDATE `tabInvestment Holding` SET qty_reserved = COALESCE(qty_reserved, 0) + %s WHERE name = %s",
            (entry.qty, entry.holding),
        )
    frappe.db.commit()


def release_reservation(plan: List[ConsumedHolding]) -> None:
    """Decrement qty_reserved (reversal of reserve())."""
    for entry in plan:
        frappe.db.sql(
            "UPDATE `tabInvestment Holding` SET qty_reserved = GREATEST(COALESCE(qty_reserved, 0) - %s, 0) WHERE name = %s",
            (entry.qty, entry.holding),
        )
    frappe.db.commit()


# ── internals ───────────────────────────────────────────────────────────


def _acquire_advisory_lock(key: str, timeout: int) -> bool:
    result = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, timeout), as_list=True)
    return bool(result and result[0] and result[0][0])
