"""Polemarch Holdings by Security — script-type Frappe Query Report.

One row per Security. Aggregates open Investment Holding lots into
Stock-in-Trade vs Investment buckets, with cost basis and (optionally)
market value using Security.last_traded_price.

Filters:
  - company (default: user's default Company)
  - security_type (Link to Security Type, optional)

Backed by `polemarch.api.holdings_summary.get_summary`.
"""

from typing import Optional

import frappe

from polemarch.api.holdings_summary import _attach_market_value, _rows


def execute(filters: Optional[dict] = None):
    filters = filters or {}
    company = filters.get("company") or frappe.defaults.get_user_default("Company")

    rows = [_attach_market_value(r) for r in _rows(company=company)]

    # Filter by security_type post-hoc so the aggregator stays generic.
    sec_type = filters.get("security_type")
    if sec_type:
        rows = [r for r in rows if r.get("security_type") == sec_type]

    columns = [
        {"label": "Security",         "fieldname": "security",         "fieldtype": "Link",      "options": "Security", "width": 130},
        {"label": "Name",             "fieldname": "security_name",    "fieldtype": "Data",      "width": 200},
        {"label": "Type",             "fieldname": "security_type",    "fieldtype": "Link",      "options": "Security Type", "width": 110},
        {"label": "SiT Units",        "fieldname": "sit_units",        "fieldtype": "Float",     "width": 110, "precision": 0},
        {"label": "Investment Units", "fieldname": "inv_units",        "fieldtype": "Float",     "width": 130, "precision": 0},
        {"label": "Total Units",      "fieldname": "total_units",      "fieldtype": "Float",     "width": 110, "precision": 0},
        {"label": "SiT Cost",         "fieldname": "sit_cost",         "fieldtype": "Currency",  "width": 130},
        {"label": "Investment Cost",  "fieldname": "inv_cost",         "fieldtype": "Currency",  "width": 140},
        {"label": "Total Cost",       "fieldname": "total_cost",       "fieldtype": "Currency",  "width": 130},
        {"label": "Latest Price",     "fieldname": "last_traded_price","fieldtype": "Currency",  "width": 110},
        {"label": "Market Value",     "fieldname": "market_value",     "fieldtype": "Currency",  "width": 140},
        {"label": "Unrealised Gain",  "fieldname": "unrealised_gain",  "fieldtype": "Currency",  "width": 140},
        {"label": "Gain %",           "fieldname": "unrealised_gain_pct", "fieldtype": "Percent", "width": 90},
        {"label": "Lots",             "fieldname": "lots",             "fieldtype": "Int",       "width": 70},
    ]

    # Build a synthetic totals row to render at the bottom. Frappe doesn't
    # render report totals natively for script reports — emit a row with a
    # bold/blank security and let the operator scroll to it.
    if rows:
        total = {
            "security": "TOTAL",
            "security_name": "",
            "security_type": "",
            "sit_units":   sum(r["sit_units"]   for r in rows),
            "inv_units":   sum(r["inv_units"]   for r in rows),
            "total_units": sum(r["total_units"] for r in rows),
            "sit_cost":    sum(r["sit_cost"]    for r in rows),
            "inv_cost":    sum(r["inv_cost"]    for r in rows),
            "total_cost":  sum(r["total_cost"]  for r in rows),
            "last_traded_price": None,
            "lots":        sum(int(r["lots"])  for r in rows),
        }
        priced = [r for r in rows if r.get("market_value") is not None]
        if priced:
            priced_cost = sum(r["total_cost"] for r in priced)
            priced_market = sum(r["market_value"] for r in priced)
            total["market_value"] = priced_market
            total["unrealised_gain"] = priced_market - priced_cost
            total["unrealised_gain_pct"] = (
                (priced_market - priced_cost) / priced_cost * 100 if priced_cost else 0
            )
        else:
            total["market_value"] = None
            total["unrealised_gain"] = None
            total["unrealised_gain_pct"] = None
        rows.append(total)

    return columns, rows
