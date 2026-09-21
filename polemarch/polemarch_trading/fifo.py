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
    classification: str = ""    # bucket this lot actually came from -- set by
                                # consume_cascade() so a sale that spills from
                                # Stock in Trade into Investment can stamp each
                                # Disposal lot with its true source bucket.


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

    Investment Holding is proprietary-only as of Phase 15 — customer
    holdings are CRM data on the separate `Customer Holding` doctype,
    and FIFO never walks them.

    Args:
        security: Security name (the standalone trading identity) to consume.
        company: Company scope.
        classification: "Stock in Trade" or "Investment" — filters the pool.
        qty_to_sell: how much to consume.
        sale_date: defaults to today; used for holding-period calc.
        lock_timeout_seconds: advisory lock wait.
        customer_filter: deprecated since Phase 15 — ignored. Kept in the
            signature so any external caller doesn't break.

    Returns: list of (holding, qty, cost_basis, acq_date, holding_days, is_long_term).
    Empty list if qty_to_sell <= 0 or no open holdings available.
    """
    qty_to_sell = flt(qty_to_sell)
    if qty_to_sell <= 0:
        return []

    sale_date = getdate(sale_date)

    lock_key = f"polemarch:fifo:{security}:{classification}:_prop"
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

    # A lot can be SPLIT across classes (e.g. 4,000 Stock in Trade + 15,000
    # Investment in one 19,000 lot). Filtering on the legacy single
    # `classification` Select and then treating the whole remaining qty as
    # available would let a Stock-in-Trade sale consume Investment shares --
    # the legacy label on a mixed lot is whichever class was set first.
    # So: select the per-class columns, keep any lot that either carries
    # quantity in the requested class OR is an unclassified legacy lot whose
    # label matches, and cap availability per class below.
    has_split = frappe.db.has_column("Investment Holding", "qty_classified_sit")
    has_legacy = frappe.db.has_column("Investment Holding", "classification")

    if has_split:
        class_col = ("qty_classified_sit" if classification == "Stock in Trade"
                     else "qty_classified_investment" if classification == "Investment"
                     else None)
        split_sum = ("(COALESCE(qty_classified_sit,0) + COALESCE(qty_classified_investment,0)"
                     " + COALESCE(qty_unclassified,0))")
        if class_col:
            where_class = (f"(({split_sum} > 0 AND COALESCE({class_col},0) > 0)"
                           f" OR ({split_sum} = 0 AND classification = %s))")
            select_class = f"COALESCE({class_col}, 0) AS qty_in_class, {split_sum} AS split_total"
        else:                                    # Unallocated
            where_class = (f"(({split_sum} > 0 AND COALESCE(qty_unclassified,0) > 0)"
                           f" OR ({split_sum} = 0 AND classification = %s))")
            select_class = f"COALESCE(qty_unclassified, 0) AS qty_in_class, {split_sum} AS split_total"
        params = [security, company, classification]
    else:
        where_class = "classification = %s" if has_legacy else "1=1"
        select_class = "NULL AS qty_in_class, 0 AS split_total"
        params = [security, company] + ([classification] if has_legacy else [])

    rows = frappe.db.sql(
        f"""
        SELECT name, qty_acquired, qty_disposed,
               COALESCE(qty_reserved, 0) AS qty_reserved,
               cost_basis_per_unit, acquisition_date, creation,
               {select_class}
          FROM `tabInvestment Holding`
         WHERE security = %s
           AND company  = %s
           AND status IN ('Open', 'Partially Disposed')
           AND {where_class}
         ORDER BY creation ASC
         FOR UPDATE
        """,
        tuple(params),
        as_dict=True,
    )

    plan: List[ConsumedHolding] = []
    remaining = qty_to_sell
    for row in rows:
        if remaining <= 0:
            break
        available = flt(row.qty_acquired) - flt(row.qty_disposed) - flt(row.qty_reserved)
        # On a split lot, only the portion sitting in THIS class is sellable.
        if flt(row.get("split_total") or 0) > 0:
            available = min(available, flt(row.get("qty_in_class") or 0))
        if available <= 0:
            continue
        take = min(available, remaining)
        acq = getdate(row.acquisition_date) if row.acquisition_date else None
        # Clamp at 0: a backdated sale (sale_date < acquisition_date) would
        # otherwise yield a negative holding period, which is meaningless and
        # silently lands on the STCG side of the LTCG threshold comparison.
        days = max(0, (sale_date - acq).days) if acq else 0
        plan.append(
            ConsumedHolding(
                holding=row.name,
                qty=take,
                cost_basis_per_unit=flt(row.cost_basis_per_unit),
                acquisition_date=acq,
                holding_period_days=days,
                classification=classification,
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


# Default consumption order: sell the trading book down first and only then
# break into the long-term investment block (operator ruling 2026-09-19).
DEFAULT_CONSUMPTION_ORDER = ("Stock in Trade", "Investment")


def consume_cascade(
    security: str,
    company: str,
    qty_to_sell: float,
    sale_date: Optional[_date] = None,
    order: tuple = DEFAULT_CONSUMPTION_ORDER,
    lock_timeout_seconds: int = 5,
) -> List[ConsumedHolding]:
    """FIFO-consume across classifications in `order`, spilling as each empties.

    consume() is single-bucket: it plans against one classification and stops.
    This walks the buckets in order, taking whatever each can cover before
    moving to the next, so a sale larger than the trading book eats into the
    investment block instead of failing.

    Every returned lot carries `.classification`, which the Security Sale
    controller stamps onto its Investment Disposal lots -- that is what lets
    the COGS JE credit Securities Inventory for the Stock-in-Trade portion and
    Long-Term Investments for the Investment portion of one disposal.

    Returns a plan that may be SHORT of qty_to_sell; the caller checks coverage.
    """
    qty_to_sell = flt(qty_to_sell)
    if qty_to_sell <= 0:
        return []

    plan: List[ConsumedHolding] = []
    remaining = qty_to_sell
    for classification in order:
        if remaining <= 0.0001:
            break
        part = consume(
            security=security,
            company=company,
            classification=classification,
            qty_to_sell=remaining,
            sale_date=sale_date,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        plan.extend(part)
        remaining -= sum(flt(p.qty) for p in part)
    return plan
