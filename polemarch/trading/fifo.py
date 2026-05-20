"""Canonical FIFO consumption engine for Security Lots.

`consume(security, portfolio, qty_to_sell)` returns a non-mutating plan of
which Security Lots to draw from, oldest-first. The caller is responsible
for inserting Consume SLLE rows (typically `Trade Order.transition_to('Matched')`
or the Phase-1 mirror-write hook in Investment Disposal).

Concurrency: a MySQL advisory lock keyed on (security, portfolio) serializes
concurrent FIFO scans, preventing two callers from over-consuming the same
oldest lot. The lock is released when the calling transaction commits or
the connection drops.
"""

from dataclasses import dataclass
from datetime import date as _date
from typing import List, Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate


LONG_TERM_THRESHOLD_DAYS = 730


@dataclass
class ConsumedLot:
    security_lot: str
    qty: float
    cost_basis_per_unit: float
    acquisition_date: _date
    holding_period_days: int
    is_long_term: bool

    def as_dict(self) -> dict:
        return {
            "security_lot": self.security_lot,
            "qty": self.qty,
            "cost_basis_per_unit": self.cost_basis_per_unit,
            "acquisition_date": self.acquisition_date.isoformat() if self.acquisition_date else None,
            "holding_period_days": self.holding_period_days,
            "is_long_term": int(self.is_long_term),
        }


def consume(
    security: str,
    portfolio: str,
    qty_to_sell: float,
    sale_date: Optional[_date] = None,
    lock_timeout_seconds: int = 5,
) -> List[ConsumedLot]:
    """Plan a FIFO consumption against open Security Lots.

    Non-mutating — does NOT write SLLE rows. Caller inserts them.

    Raises if the advisory lock cannot be acquired within `lock_timeout_seconds`.
    Returns a partial plan (qty < qty_to_sell) if open lots are insufficient;
    callers decide whether that's acceptable (facilitator-only trades may
    legitimately have empty plans).
    """
    qty_to_sell = flt(qty_to_sell)
    if qty_to_sell <= 0:
        return []

    if sale_date is None:
        sale_date = getdate()

    lock_key = f"polemarch:fifo:{security}:{portfolio}"
    if not _acquire_advisory_lock(lock_key, lock_timeout_seconds):
        frappe.throw(
            _("Could not acquire FIFO lock for {0}/{1} within {2}s.").format(
                security, portfolio, lock_timeout_seconds
            ),
            title=_("FIFO Lock Timeout"),
        )

    # Scan rows with row-lock so concurrent callers (after we release the advisory
    # lock by commit) see consistent state. ORDER BY acquisition_date ASC enforces
    # FIFO; secondary `creation ASC` deterministically breaks ties.
    rows = frappe.db.sql(
        """
        SELECT name,
               qty_acquired,
               qty_disposed,
               cost_basis_per_unit,
               acquisition_date
          FROM `tabSecurity Lot`
         WHERE security  = %s
           AND portfolio = %s
           AND status   IN ('Open', 'Partially Disposed')
           AND lot_state IN ('Open', 'Reserved')
         ORDER BY acquisition_date ASC, creation ASC
         FOR UPDATE
        """,
        (security, portfolio),
        as_dict=True,
    )

    plan: List[ConsumedLot] = []
    remaining = qty_to_sell
    for row in rows:
        if remaining <= 0:
            break
        available = flt(row.qty_acquired) - flt(row.qty_disposed)
        if available <= 0:
            continue
        take = min(available, remaining)
        acq = getdate(row.acquisition_date)
        days = (sale_date - acq).days if acq else 0
        plan.append(
            ConsumedLot(
                security_lot=row.name,
                qty=take,
                cost_basis_per_unit=flt(row.cost_basis_per_unit),
                acquisition_date=acq,
                holding_period_days=days,
                is_long_term=days > LONG_TERM_THRESHOLD_DAYS,
            )
        )
        remaining -= take

    return plan


def write_consume_entries(
    plan: List[ConsumedLot],
    reference_doctype: str,
    reference_name: str,
    sale_price_per_unit: float = 0,
) -> List[str]:
    """Insert one Consume SLLE per planned lot.

    Returns the list of created SLLE names. Idempotency is the caller's
    responsibility (typically by checking for existing rows with the same
    (reference_doctype, reference_name)).
    """
    sale_price_per_unit = flt(sale_price_per_unit)
    names: List[str] = []
    for entry in plan:
        slle = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": entry.security_lot,
                "entry_type": "Consume",
                "qty": entry.qty,
                "cost_basis_per_unit": entry.cost_basis_per_unit,
                "sale_price_per_unit": sale_price_per_unit,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "holding_period_days": entry.holding_period_days,
                "is_long_term": int(entry.is_long_term),
                "realized_gain": (sale_price_per_unit - entry.cost_basis_per_unit) * entry.qty,
            }
        )
        slle.flags.ignore_permissions = True
        slle.insert(ignore_permissions=True)
        slle.submit()
        names.append(slle.name)
    return names


def reverse_consume_entries(reference_doctype: str, reference_name: str) -> List[str]:
    """Post Reversal SLLE rows for every Consume row tied to a reference doc.

    Idempotent: skips already-cancelled rows.
    """
    consume_rows = frappe.get_all(
        "Security Lot Ledger Entry",
        filters={
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "entry_type": "Consume",
            "is_cancelled": 0,
            "docstatus": 1,
        },
        fields=["name", "security_lot", "qty", "cost_basis_per_unit"],
    )

    reversal_names: List[str] = []
    for row in consume_rows:
        reversal = frappe.get_doc(
            {
                "doctype": "Security Lot Ledger Entry",
                "security_lot": row.security_lot,
                "entry_type": "Reversal",
                "qty": row.qty,
                "cost_basis_per_unit": row.cost_basis_per_unit,
                "reverses": row.name,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
            }
        )
        reversal.flags.ignore_permissions = True
        reversal.insert(ignore_permissions=True)
        reversal.submit()

        # Mark the original as cancelled via the reversal path.
        original = frappe.get_doc("Security Lot Ledger Entry", row.name)
        original.flags.from_reversal = True
        original.is_cancelled = 1
        original.reversed_by = reversal.name
        original.db_update()

        reversal_names.append(reversal.name)
    return reversal_names


def _acquire_advisory_lock(key: str, timeout_seconds: int) -> bool:
    result = frappe.db.sql(
        "SELECT GET_LOCK(%s, %s)", (key, timeout_seconds), as_list=True
    )
    if not result or not result[0]:
        return False
    return bool(result[0][0])
