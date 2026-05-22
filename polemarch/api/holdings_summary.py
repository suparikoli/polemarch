"""Holdings summary API — aggregates Investment Holding rows by (security ×
classification) for the company.

Backs three UIs:
  - Frappe Query Report "Polemarch Holdings by Security" (script type)
  - Section panel on the Security form (per-security rollup)
  - Three workspace Number Cards (totals)

Valuation:
  - cost_value      = qty_remaining × cost_basis_per_unit  (always available)
  - market_value    = qty_remaining × Security.last_traded_price  (NULL if price stale/missing)
  - unrealised_gain = market_value − cost_value  (NULL when market_value is NULL)

`Security.last_traded_price` is a manually-maintained field on the Security
master; rows where it's blank fall through cleanly with market_value = None
so the report still works on day 1 before prices are imported.

Scoping:
  - Open + Partially Disposed only (Fully Disposed is just historical).
  - qty_remaining > 0 (defensive: skip rows where the maths zeroed out).
  - Per-company. Single-company sites get filtered automatically.
"""

from typing import Optional

import frappe
from frappe.utils import flt


# ─── core aggregator ────────────────────────────────────────────────────────


def _rows(company: Optional[str] = None, security: Optional[str] = None) -> list:
    """Return one row per Security with SiT / Investment / total rollups."""
    where = ["ih.status IN ('Open', 'Partially Disposed')", "ih.qty_remaining > 0"]
    params: list = []
    if company:
        where.append("ih.company = %s")
        params.append(company)
    if security:
        where.append("s.name = %s")
        params.append(security)
    where_sql = " AND ".join(where)

    return frappe.db.sql(
        f"""
        SELECT
            s.name                                              AS security,
            s.security_name                                     AS security_name,
            s.isin                                              AS isin,
            s.security_type                                     AS security_type,
            s.last_traded_price                                 AS last_traded_price,

            SUM(IF(ih.classification='Stock in Trade', ih.qty_remaining, 0))                       AS sit_units,
            SUM(IF(ih.classification='Stock in Trade', ih.qty_remaining * ih.cost_basis_per_unit, 0)) AS sit_cost,
            SUM(IF(ih.classification='Investment',     ih.qty_remaining, 0))                       AS inv_units,
            SUM(IF(ih.classification='Investment',     ih.qty_remaining * ih.cost_basis_per_unit, 0)) AS inv_cost,
            SUM(IF(ih.classification='Unallocated',    ih.qty_remaining, 0))                       AS unalloc_units,
            SUM(IF(ih.classification='Unallocated',    ih.qty_remaining * ih.cost_basis_per_unit, 0)) AS unalloc_cost,

            SUM(ih.qty_remaining)                                AS total_units,
            SUM(ih.qty_remaining * ih.cost_basis_per_unit)       AS total_cost,
            COUNT(ih.name)                                       AS lots
        FROM `tabSecurity` s
        JOIN `tabInvestment Holding` ih ON ih.security = s.name
        WHERE {where_sql}
        GROUP BY s.name
        HAVING SUM(ih.qty_remaining) > 0
        ORDER BY total_cost DESC, s.name ASC
        """,
        tuple(params),
        as_dict=True,
    )


def _attach_market_value(row: dict) -> dict:
    """Compute market_value, unrealised_gain, and per-classification LCM
    on a single row in-place.

    LCM = Lower of Cost or Market (conservative accounting): for each
    classification bucket we value at min(cost_value, fair_value). When
    last_traded_price is blank/0, fair_value is unknown — LCM falls back
    to cost so the row still has a defensible valuation.

    Adds these keys to the row:
        sit_market         total_market
        inv_market
        sit_lcm            inv_lcm        unalloc_lcm        total_lcm
        market_value       (alias of total_market for back-compat)
        unrealised_gain    unrealised_gain_pct
    """
    ltp = flt(row.get("last_traded_price"))

    def _pair(units, cost):
        units = flt(units)
        cost = flt(cost)
        if ltp > 0 and units > 0:
            fair = units * ltp
            return fair, min(fair, cost)
        # No price → fair unknown, LCM defaults to cost.
        return None, cost

    row["sit_market"], row["sit_lcm"]         = _pair(row.get("sit_units"),     row.get("sit_cost"))
    row["inv_market"], row["inv_lcm"]         = _pair(row.get("inv_units"),     row.get("inv_cost"))
    row["unalloc_market"], row["unalloc_lcm"] = _pair(row.get("unalloc_units"), row.get("unalloc_cost"))

    total_units = flt(row.get("total_units"))
    total_cost = flt(row.get("total_cost"))
    if ltp > 0 and total_units > 0:
        total_market = total_units * ltp
        row["total_market"] = total_market
        row["market_value"] = total_market
        row["unrealised_gain"] = total_market - total_cost
        row["unrealised_gain_pct"] = (total_market - total_cost) / total_cost * 100 if total_cost else 0
    else:
        row["total_market"] = None
        row["market_value"] = None
        row["unrealised_gain"] = None
        row["unrealised_gain_pct"] = None

    # Total LCM = sum of per-classification LCMs (each already individually
    # min'd against its own cost). When LTP is missing, this collapses to
    # total cost as expected.
    row["total_lcm"] = flt(row["sit_lcm"]) + flt(row["inv_lcm"]) + flt(row["unalloc_lcm"])
    return row


# ─── whitelisted API surface ────────────────────────────────────────────────


@frappe.whitelist()
def get_summary(company: Optional[str] = None) -> dict:
    """Full rollup: per-security rows + grand totals.

    Returns:
        {
            "company": str,
            "rows": [{security, security_name, ..., market_value, unrealised_gain}, ...],
            "totals": {
                "sit_units", "sit_cost",
                "inv_units", "inv_cost",
                "total_units", "total_cost",
                "market_value", "unrealised_gain", "unrealised_gain_pct",
                "securities", "lots",
            },
        }
    """
    company = company or frappe.defaults.get_user_default("Company")
    rows = [_attach_market_value(r) for r in _rows(company=company)]

    totals = {
        "sit_units": sum(flt(r["sit_units"]) for r in rows),
        "sit_cost": sum(flt(r["sit_cost"]) for r in rows),
        "inv_units": sum(flt(r["inv_units"]) for r in rows),
        "inv_cost": sum(flt(r["inv_cost"]) for r in rows),
        "total_units": sum(flt(r["total_units"]) for r in rows),
        "total_cost": sum(flt(r["total_cost"]) for r in rows),
        "market_value": sum(flt(r["market_value"]) for r in rows if r["market_value"] is not None) or None,
        "securities": len(rows),
        "lots": sum(int(r["lots"]) for r in rows),
    }
    # If at least one security has market data, compute totals net of NULLs.
    priced = [r for r in rows if r["market_value"] is not None]
    if priced and totals["total_cost"]:
        # Note: market_value sum only counts priced rows; gain is computed
        # against the matching cost of THOSE rows so the % is honest.
        priced_cost = sum(flt(r["total_cost"]) for r in priced)
        priced_market = sum(flt(r["market_value"]) for r in priced)
        totals["unrealised_gain"] = priced_market - priced_cost
        totals["unrealised_gain_pct"] = (
            (priced_market - priced_cost) / priced_cost * 100 if priced_cost else 0
        )
        totals["priced_securities"] = len(priced)
        totals["unpriced_securities"] = len(rows) - len(priced)
    else:
        totals["unrealised_gain"] = None
        totals["unrealised_gain_pct"] = None
        totals["priced_securities"] = 0
        totals["unpriced_securities"] = len(rows)

    return {"company": company, "rows": rows, "totals": totals}


@frappe.whitelist()
def get_security_rollup(security: str, company: Optional[str] = None) -> dict:
    """Single-security rollup — for the Security form panel."""
    company = company or frappe.defaults.get_user_default("Company")
    rows = _rows(company=company, security=security)
    if not rows:
        return {
            "security": security,
            "company": company,
            "has_holdings": False,
        }
    r = _attach_market_value(rows[0])
    r["company"] = company
    r["has_holdings"] = True

    # Earliest classification deadline across Unclassified Holdings — drives
    # the "auto-classifies in 3d 4h" countdown rendered in the Unclassified
    # row of the LCM table.
    deadline_row = frappe.db.sql(
        """
        SELECT MIN(classification_deadline) AS deadline
        FROM `tabInvestment Holding`
        WHERE security = %s
          AND company = %s
          AND status IN ('Open', 'Partially Disposed')
          AND qty_remaining > 0
          AND classification = 'Unallocated'
          AND classification_deadline IS NOT NULL
        """,
        (security, company),
        as_dict=True,
    )
    r["earliest_classification_deadline"] = (
        deadline_row[0]["deadline"] if deadline_row and deadline_row[0]["deadline"] else None
    )
    return r


# ─── Number Card endpoints ──────────────────────────────────────────────────
#
# Number Cards with type=Custom call a single function and expect either a
# number return OR an dict with {value, fieldtype}. We return Currency-shaped
# dicts so the cards render with ₹ prefix and locale-aware formatting.


def _currency_card(value, label: Optional[str] = None) -> dict:
    return {
        "value": flt(value),
        "fieldtype": "Currency",
        "route_options": {},
    }


@frappe.whitelist()
def card_total_holdings_value(company: Optional[str] = None) -> dict:
    """Number Card: total cost basis of all open Holdings."""
    s = get_summary(company)
    return _currency_card(s["totals"]["total_cost"])


@frappe.whitelist()
def card_sit_value(company: Optional[str] = None) -> dict:
    """Number Card: Stock-in-Trade book value."""
    s = get_summary(company)
    return _currency_card(s["totals"]["sit_cost"])


@frappe.whitelist()
def card_investment_value(company: Optional[str] = None) -> dict:
    """Number Card: Investment-bucket book value."""
    s = get_summary(company)
    return _currency_card(s["totals"]["inv_cost"])


@frappe.whitelist()
def card_unclassified_value(company: Optional[str] = None) -> dict:
    """Number Card: book value of Unclassified shares (within the 5-business-
    day classification window — sits in the Pending Classification suspense
    account until day-5 reconciliation)."""
    where = ["status IN ('Open', 'Partially Disposed')", "qty_remaining > 0"]
    params: list = []
    if company:
        where.append("company = %s")
        params.append(company)
    where.append("classification = 'Unallocated'")
    where_sql = " AND ".join(where)
    row = frappe.db.sql(
        f"SELECT COALESCE(SUM(qty_remaining * cost_basis_per_unit), 0) "
        f"FROM `tabInvestment Holding` WHERE {where_sql}",
        tuple(params),
    )
    return _currency_card(flt(row[0][0]) if row else 0)


@frappe.whitelist()
def card_unrealised_gain(company: Optional[str] = None) -> dict:
    """Number Card: unrealised gain across priced securities (NULL-safe)."""
    s = get_summary(company)
    return _currency_card(s["totals"]["unrealised_gain"] or 0)
