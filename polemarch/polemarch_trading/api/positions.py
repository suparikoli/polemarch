"""Positions REST endpoints (Phase 5 — customer-facing).

Read-only. All endpoints resolve the customer from the session user when
called under the Customer role; back-office roles may pass `customer=...`.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate

from polemarch.polemarch_trading.api._ratelimit import rate_limit


_DEFAULT_FY_START_MONTH_DAY = "-04-01"  # India FY starts 1 April.


@frappe.whitelist()
@rate_limit(per_min=60, scope="user")
def get_positions(customer: Optional[str] = None, portfolio: Optional[str] = None):
    """Aggregated positions per (portfolio, security) for the resolved customer."""
    customer = _resolve_customer(customer)
    filters = {"customer": customer}
    if portfolio:
        filters["portfolio"] = portfolio

    return frappe.get_all(
        "Security Position",
        filters=filters,
        fields=[
            "name",
            "portfolio",
            "security",
            "isin",
            "qty_held",
            "qty_reserved",
            "qty_available",
            "total_cost",
            "avg_cost",
            "last_marked_price",
            "market_value",
            "unrealized_pnl",
            "realized_pnl_ytd",
            "last_updated_on",
        ],
        order_by="security ASC",
        limit_page_length=500,
    )


@frappe.whitelist()
@rate_limit(per_min=60, scope="user")
def get_lots(
    customer: Optional[str] = None,
    security: Optional[str] = None,
    portfolio: Optional[str] = None,
):
    """Lot-level view for drill-down from a position. Restricted to open
    or partially-disposed Investment Holdings only — fully disposed Holdings
    are hidden by default (they're audit-only).

    Filters by Item linked to the Security (Security → Item is 1:1)."""
    _resolve_customer(customer)
    filters = {
        "status": ["in", ["Open", "Partially Disposed"]],
    }
    if security:
        item = frappe.db.get_value("Security", security, "item")
        if item:
            filters["item"] = item
    if portfolio:
        filters["custom_portfolio"] = portfolio

    return frappe.get_all(
        "Investment Holding",
        filters=filters,
        fields=[
            "name",
            "item",
            "item_name",
            "custom_portfolio",
            "classification",
            "acquisition_date",
            "qty_acquired",
            "qty_remaining",
            "qty_reserved",
            "cost_basis_per_unit",
            "remaining_cost",
            "status",
        ],
        order_by="creation ASC",
        limit_page_length=500,
    )


@frappe.whitelist()
@rate_limit(per_min=30, scope="user")
def get_realized_pnl(
    customer: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """Realized P&L summary aggregated from Investment Disposal Lots.

    Returns aggregated LTCG / STCG buckets per Item."""
    _resolve_customer(customer)
    from_date = from_date or _fy_start_for_today().isoformat()
    to_date = to_date or "2099-12-31"

    rows = frappe.db.sql(
        """
        SELECT idl.parent                                              AS disposal,
               disposal.item                                           AS item,
               idl.is_long_term,
               COALESCE(SUM(idl.cost_basis_amount), 0)                 AS cost_basis,
               COALESCE(SUM(idl.sale_amount), 0)                       AS proceeds,
               COALESCE(SUM(idl.realized_gain), 0)                     AS realized_gain
          FROM `tabInvestment Disposal Lot` idl
          JOIN `tabInvestment Disposal`     disposal ON disposal.name = idl.parent
         WHERE disposal.docstatus = 1
           AND DATE(disposal.disposal_date) BETWEEN %(from_date)s AND %(to_date)s
         GROUP BY disposal.item, idl.is_long_term
         ORDER BY disposal.item
        """,
        {"from_date": from_date, "to_date": to_date},
        as_dict=True,
    )

    ltcg = sum(flt(r.realized_gain) for r in rows if r.is_long_term)
    stcg = sum(flt(r.realized_gain) for r in rows if not r.is_long_term)

    return {
        "customer": customer,
        "from_date": from_date,
        "to_date": to_date,
        "ltcg_total": ltcg,
        "stcg_total": stcg,
        "total_realized": ltcg + stcg,
        "by_security": rows,
    }


@frappe.whitelist()
@rate_limit(per_min=30, scope="user")
def get_unrealized_pnl(customer: Optional[str] = None, as_of_date: Optional[str] = None):
    """Mark-to-market unrealised P&L using current Security.last_traded_price.

    The daily mark-to-market job is Phase 6; until then, LTP is whatever
    the price-scraper has stamped on the Security master."""
    customer = _resolve_customer(customer)

    rows = frappe.db.sql(
        """
        SELECT sp.security,
               sp.portfolio,
               sp.qty_held,
               sp.total_cost,
               sp.avg_cost,
               COALESCE(sec.last_traded_price, 0) AS ltp,
               sec.price_updated_on
          FROM `tabSecurity Position` sp
          JOIN `tabSecurity` sec ON sec.name = sp.security
         WHERE sp.customer = %s
           AND sp.qty_held > 0
        """,
        (customer,),
        as_dict=True,
    )

    enriched = []
    total_cost = 0.0
    total_market = 0.0
    for r in rows:
        market_value = flt(r.qty_held) * flt(r.ltp)
        unrealized = market_value - flt(r.total_cost)
        total_cost += flt(r.total_cost)
        total_market += market_value
        enriched.append(
            {
                "security": r.security,
                "portfolio": r.portfolio,
                "qty_held": flt(r.qty_held),
                "total_cost": flt(r.total_cost),
                "avg_cost": flt(r.avg_cost),
                "last_traded_price": flt(r.ltp),
                "price_updated_on": r.price_updated_on,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
            }
        )

    return {
        "customer": customer,
        "as_of_date": as_of_date or frappe.utils.today(),
        "total_cost": total_cost,
        "total_market_value": total_market,
        "total_unrealized_pnl": total_market - total_cost,
        "by_security": enriched,
    }


# ── helpers ──────────────────────────────────────────────────────────────


def _resolve_customer(customer: Optional[str]) -> str:
    user_roles = set(frappe.get_roles(frappe.session.user))
    if customer:
        if "Customer" in user_roles and not (
            user_roles & {"System Manager", "Accounts Manager", "Polemarch Settlement Officer", "Polemarch Trader"}
        ):
            mapped = frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name")
            if mapped != customer:
                frappe.throw(_("Customer mismatch."), title=_("Forbidden"))
        return customer

    mapped = frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name")
    if not mapped:
        frappe.throw(
            _("No Customer mapped to user {0}.").format(frappe.session.user),
            title=_("Customer Mapping Missing"),
        )
    return mapped


def _fy_start_for_today():
    """India FY starts 1 April. Returns the most recent 1-April relative to today."""
    today = getdate()
    fy_start = getdate(f"{today.year}{_DEFAULT_FY_START_MONTH_DAY}")
    if today < fy_start:
        fy_start = getdate(f"{today.year - 1}{_DEFAULT_FY_START_MONTH_DAY}")
    return fy_start
