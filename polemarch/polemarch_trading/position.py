"""Security Position engine — atomic position-row mutations under row-level lock.

Position rows are denormalized rollups of Investment Holding quantities for
fast UI queries. `apply_delta(...)` holds a `FOR UPDATE` lock on the row
(creating it if missing) while applying the qty/cost delta in a single UPDATE.

`recompute_from_lots(portfolio, security)` is the authoritative reconciliation
path — used by the nightly audit job and the admin "Recompute" button. Despite
the function name, it rolls up Investment Holdings (the legacy `lots` name is
preserved so external callers don't break).
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, now_datetime


def apply_delta(
    portfolio: str,
    security: str,
    qty_delta: float = 0,
    cost_delta: float = 0,
    reserved_delta: float = 0,
    realized_pnl_delta: float = 0,
) -> str:
    """Atomically apply deltas to a Security Position row.

    Creates the row if it doesn't exist. Returns the position name.
    """
    name = _position_name(portfolio, security)

    locked = frappe.db.sql(
        """
        SELECT name, qty_held, qty_reserved, total_cost, realized_pnl_ytd, version
          FROM `tabSecurity Position`
         WHERE name = %s
           FOR UPDATE
        """,
        (name,),
        as_dict=True,
    )

    if not locked:
        # Bootstrap row; relies on validate() guaranteeing structural invariants.
        try:
            doc = frappe.get_doc(
                {
                    "doctype": "Security Position",
                    "portfolio": portfolio,
                    "security": security,
                    "qty_held": 0,
                    "qty_reserved": 0,
                    "total_cost": 0,
                    "realized_pnl_ytd": 0,
                    "last_updated_on": now_datetime(),
                }
            )
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            # Concurrent creator won the race; fall through to lock below.
            pass
        locked = frappe.db.sql(
            """
            SELECT name, qty_held, qty_reserved, total_cost, realized_pnl_ytd, version
              FROM `tabSecurity Position`
             WHERE name = %s
               FOR UPDATE
            """,
            (name,),
            as_dict=True,
        )
        if not locked:
            frappe.throw(
                _("Failed to bootstrap Security Position for {0}/{1}.").format(portfolio, security),
                title=_("Position Bootstrap Failed"),
            )

    state = locked[0]
    new_held = flt(state.qty_held) + flt(qty_delta)
    new_reserved = flt(state.qty_reserved) + flt(reserved_delta)
    new_cost = flt(state.total_cost) + flt(cost_delta)
    new_realized = flt(state.realized_pnl_ytd) + flt(realized_pnl_delta)
    new_available = new_held - new_reserved

    if new_held < -0.0001 or new_reserved < -0.0001:
        frappe.throw(
            _("Security Position {0}: delta would drive qty negative (held={1}, reserved={2}).").format(
                name, new_held, new_reserved
            ),
            title=_("Negative Position"),
        )
    if new_reserved > new_held + 0.0001:
        frappe.throw(
            _("Security Position {0}: reserved ({1}) cannot exceed held ({2}).").format(
                name, new_reserved, new_held
            ),
            title=_("Reservation Exceeds Holdings"),
        )

    avg_cost = (new_cost / new_held) if new_held > 0 else 0
    market_value = new_held * 0  # market price refresh is a separate Phase 6 job.
    unrealized_pnl = market_value - new_cost

    frappe.db.sql(
        """
        UPDATE `tabSecurity Position`
           SET qty_held         = %s,
               qty_reserved     = %s,
               qty_available    = %s,
               total_cost       = %s,
               avg_cost         = %s,
               market_value     = %s,
               unrealized_pnl   = %s,
               realized_pnl_ytd = %s,
               last_updated_on  = %s,
               version          = version + 1,
               modified         = %s
         WHERE name = %s
        """,
        (
            new_held,
            new_reserved,
            new_available,
            new_cost,
            avg_cost,
            market_value,
            unrealized_pnl,
            new_realized,
            now_datetime(),
            now_datetime(),
            name,
        ),
    )

    return name


def recompute_from_lots(portfolio: str, security: str) -> str:
    """Rebuild the Security Position row from Investment Holding ground truth.

    Used by the nightly reconciliation audit job and as an admin escape hatch.

    Filters by `security` directly (Phase 9 — no Item lookup). Portfolio is
    matched via the legacy custom_portfolio Custom Field on Investment
    Holding (set by Phase-0 backfill). Holdings that pre-date Phase 9 keep
    portfolio; Holdings minted by Security Purchase live in classifications
    instead and don't carry portfolio — those don't contribute to the
    portfolio-keyed Security Position rollup.
    """
    name = _position_name(portfolio, security)
    if not frappe.db.has_column("Investment Holding", "security"):
        return name

    rollup = frappe.db.sql(
        """
        SELECT COALESCE(SUM(qty_remaining), 0)                              AS qty_held,
               COALESCE(SUM(COALESCE(qty_reserved, 0)), 0)                  AS qty_reserved,
               COALESCE(SUM(qty_remaining * cost_basis_per_unit), 0)        AS total_cost
          FROM `tabInvestment Holding`
         WHERE security = %s
           AND custom_portfolio = %s
           AND status IN ('Open', 'Partially Disposed')
        """,
        (security, portfolio),
        as_dict=True,
    )[0]

    qty_held = flt(rollup.qty_held)
    qty_reserved = flt(rollup.qty_reserved)
    total_cost = flt(rollup.total_cost)
    avg_cost = (total_cost / qty_held) if qty_held > 0 else 0

    if not frappe.db.exists("Security Position", name):
        if qty_held == 0:
            return name
        doc = frappe.get_doc(
            {
                "doctype": "Security Position",
                "portfolio": portfolio,
                "security": security,
                "qty_held": qty_held,
                "qty_reserved": qty_reserved,
                "total_cost": total_cost,
                "avg_cost": avg_cost,
                "last_updated_on": now_datetime(),
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name

    frappe.db.sql(
        """
        UPDATE `tabSecurity Position`
           SET qty_held         = %s,
               qty_reserved     = %s,
               qty_available    = %s,
               total_cost       = %s,
               avg_cost         = %s,
               last_updated_on  = %s,
               version          = version + 1,
               modified         = %s
         WHERE name = %s
        """,
        (qty_held, qty_reserved, qty_held - qty_reserved, total_cost, avg_cost,
         now_datetime(), now_datetime(), name),
    )

    return name


def _position_name(portfolio: str, security: str) -> str:
    # Mirrors the doctype autoname `format:POS-{portfolio}-{security}`.
    return f"POS-{portfolio}-{security}"
