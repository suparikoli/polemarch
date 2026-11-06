"""Securities passbook — every movement of a security, computed live.

Reads submitted Security Purchases (qty in), Security Sales (qty out) and
Portfolio Transfers (SiT <-> Investment reclass, no qty change) and orders
them by transfer date. Nothing is stored: the Security form tabs and the
`Securities Passbook` report both call `get_entries` / `get_counterparties`.

Running balances are per security and always run over the full history,
so a row shown under a party or date filter still carries the real book
balance after that movement.

Cost: a purchase adds its amount; a sale removes the FIFO cost of the lots
its Investment Disposal consumed (`total_cost_basis`). Both closing figures
must equal the Security's cached `qty_total` / `cost_total`.
"""

from collections import OrderedDict
from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt, getdate

from polemarch.polemarch_trading.payment_lines import summarise

IN, OUT, RECLASS = "In", "Out", "Reclass"
# Same-day order: stock arrives before it leaves.
_DAY_ORDER = {IN: 0, RECLASS: 1, OUT: 2}
_READ_DOCTYPES = ("Security", "Security Purchase", "Security Sale", "Portfolio Transfer")


def check_read_permission():
    for dt in _READ_DOCTYPES:
        if not frappe.has_permission(dt, "read"):
            frappe.throw(_("Not permitted to read {0}").format(dt), frappe.PermissionError)


def _purchases(securities):
    filters = {"docstatus": 1}
    if securities:
        filters["security"] = ["in", securities]
    rows = frappe.get_all(
        "Security Purchase",
        filters=filters,
        fields=["name", "security", "transfer_date", "posting_date", "party_type", "party",
                "intended_classification", "qty", "rate", "amount", "creation"],
    )
    return [
        {
            "voucher_type": "Security Purchase", "voucher_no": r.name, "security": r.security,
            "date": r.transfer_date or r.posting_date, "posting_date": r.posting_date,
            "party_type": r.party_type, "party": r.party, "direction": IN,
            "classification": r.intended_classification,
            "qty_in": flt(r.qty), "qty_out": 0.0, "rate": flt(r.rate), "amount": flt(r.amount),
            "cost_in": flt(r.amount), "cost_out": 0.0, "creation": r.creation,
        }
        for r in rows
    ]


def _sales(securities):
    filters = {"docstatus": 1}
    if securities:
        filters["security"] = ["in", securities]
    rows = frappe.get_all(
        "Security Sale",
        filters=filters,
        fields=["name", "security", "transfer_date", "posting_date", "party_type", "party",
                "from_classification", "qty", "rate", "amount", "investment_disposal_ref", "creation"],
    )
    disposals = [r.investment_disposal_ref for r in rows if r.investment_disposal_ref]
    cost = dict(
        frappe.get_all(
            "Investment Disposal",
            filters={"name": ["in", disposals]},
            fields=["name", "total_cost_basis"],
            as_list=True,
        )
    ) if disposals else {}
    return [
        {
            "voucher_type": "Security Sale", "voucher_no": r.name, "security": r.security,
            "date": r.transfer_date or r.posting_date, "posting_date": r.posting_date,
            "party_type": r.party_type, "party": r.party, "direction": OUT,
            "classification": r.from_classification,
            "qty_in": 0.0, "qty_out": flt(r.qty), "rate": flt(r.rate), "amount": flt(r.amount),
            "cost_in": 0.0, "cost_out": flt(cost.get(r.investment_disposal_ref)), "creation": r.creation,
        }
        for r in rows
    ]


def _transfers(securities):
    filters = {"docstatus": 1}
    if securities:
        filters["security"] = ["in", securities]
    rows = frappe.get_all(
        "Portfolio Transfer",
        filters=filters,
        fields=["name", "security", "transfer_date", "from_classification", "to_classification",
                "qty", "fmv_per_unit", "total_fmv", "reversal_of", "creation"],
    )
    return [
        {
            "voucher_type": "Portfolio Transfer", "voucher_no": r.name, "security": r.security,
            "date": r.transfer_date, "posting_date": r.transfer_date,
            "party_type": None, "party": None, "direction": RECLASS,
            "classification": f"{r.from_classification} → {r.to_classification}",
            "qty_in": 0.0, "qty_out": 0.0, "reclass_qty": flt(r.qty),
            "rate": flt(r.fmv_per_unit), "amount": flt(r.total_fmv),
            "cost_in": 0.0, "cost_out": 0.0, "reversal_of": r.reversal_of, "creation": r.creation,
        }
        for r in rows
    ]


def _attach_payments(entries):
    """payment_type and a one-line summary of each document's payment rows."""
    for doctype in ("Security Purchase", "Security Sale"):
        names = [e["voucher_no"] for e in entries if e["voucher_type"] == doctype]
        if not names:
            continue
        rows = frappe.get_all(
            "Security Payment",
            filters={"parent": ["in", names], "parenttype": doctype},
            fields=["parent", "payment_type", "payment_date", "amount", "paid_through", "reference",
                    "journal_entry", "wallet_transaction"],
            order_by="parent, idx",
        )
        summary = summarise(rows)
        by_parent = {}
        for r in rows:
            by_parent.setdefault(r.parent, []).append(r)
        types = dict(frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", "payment_type"], as_list=True))
        for e in entries:
            if e["voucher_type"] == doctype:
                e["payment_type"] = types.get(e["voucher_no"])
                e["payment_details"] = summary.get(e["voucher_no"])
                e["payments"] = by_parent.get(e["voucher_no"], [])


def _sort_key(e):
    return (getdate(e["date"]), _DAY_ORDER[e["direction"]], getdate(e["posting_date"]), e["creation"], e["voucher_no"])


def get_entries(
    security: Optional[str] = None,
    party: Optional[str] = None,
    party_type: Optional[str] = None,
    from_date=None,
    to_date=None,
    direction: Optional[str] = None,
) -> list:
    """All movements, oldest first, with running qty/cost per security."""
    securities = [security] if security else None
    entries = _purchases(securities) + _sales(securities) + _transfers(securities)
    entries.sort(key=_sort_key)
    _attach_payments(entries)

    names = frappe.get_all("Security", fields=["name", "security_name"], as_list=True)
    sec_name = dict(names)
    bal_qty, bal_cost = {}, {}
    for e in entries:
        s = e["security"]
        bal_qty[s] = bal_qty.get(s, 0.0) + e["qty_in"] - e["qty_out"]
        bal_cost[s] = bal_cost.get(s, 0.0) + e["cost_in"] - e["cost_out"]
        e["balance_qty"] = flt(bal_qty[s], 6)
        e["balance_cost"] = flt(bal_cost[s], 2)
        e["security_name"] = sec_name.get(s)

    fd, td = (getdate(from_date) if from_date else None), (getdate(to_date) if to_date else None)

    def keep(e):
        if party and e["party"] != party:
            return False
        if party_type and e["party_type"] != party_type:
            return False
        if direction and e["direction"] != direction:
            return False
        d = getdate(e["date"])
        return not ((fd and d < fd) or (td and d > td))

    return [e for e in entries if keep(e)]


def get_counterparties(entries: list) -> list:
    """Group movements by (security, party): totals plus the rows themselves."""
    groups = OrderedDict()
    for e in entries:
        if not e["party"]:
            continue
        key = (e["security"], e["party_type"], e["party"])
        g = groups.get(key)
        if not g:
            g = groups[key] = {
                "security": e["security"], "security_name": e["security_name"],
                "party_type": e["party_type"], "party": e["party"],
                "qty_in": 0.0, "qty_out": 0.0, "amount_paid": 0.0, "amount_received": 0.0,
                "count": 0, "first_date": e["date"], "last_date": e["date"], "entries": [],
            }
        g["qty_in"] += e["qty_in"]
        g["qty_out"] += e["qty_out"]
        if e["direction"] == IN:
            g["amount_paid"] += e["amount"]
        elif e["direction"] == OUT:
            g["amount_received"] += e["amount"]
        g["count"] += 1
        g["last_date"] = e["date"]
        g["entries"].append(e)
    out = list(groups.values())
    for g in out:
        g["net_qty"] = g["qty_in"] - g["qty_out"]
    out.sort(key=lambda g: (g["security"], -(g["qty_in"] + g["qty_out"]), g["party"]))
    return out


def get_security_passbook(security: str) -> dict:
    """Passbook for one security, with the check against the cached totals."""
    entries = get_entries(security=security)
    sec = frappe.db.get_value("Security", security, ["qty_total", "cost_total"], as_dict=True) or {}
    closing_qty = entries[-1]["balance_qty"] if entries else 0.0
    closing_cost = entries[-1]["balance_cost"] if entries else 0.0
    return {
        "entries": entries,
        "closing_qty": closing_qty,
        "closing_cost": closing_cost,
        "qty_total": flt(sec.get("qty_total")),
        "cost_total": flt(sec.get("cost_total")),
        "qty_ok": abs(closing_qty - flt(sec.get("qty_total"))) < 1e-6,
        "cost_ok": abs(closing_cost - flt(sec.get("cost_total"))) < 0.01,
    }
