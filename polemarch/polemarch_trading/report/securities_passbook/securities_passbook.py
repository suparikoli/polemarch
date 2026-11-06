"""Securities Passbook — script-type Frappe Query Report.

Every Security Purchase, Security Sale and Portfolio Transfer by transfer
date, across all securities, computed live by `polemarch_trading.passbook`.

Filters: security, party type / party, date range, direction (In / Out /
Reclass), and view:
  - Passbook:       one row per movement with the running qty and FIFO
                    cost of that security.
  - Counterparties: one row per (security, party) with totals; expand a
                    row to see that party's movements.
"""

from typing import Optional

from polemarch.polemarch_trading import passbook


def execute(filters: Optional[dict] = None):
    filters = filters or {}
    passbook.check_read_permission()
    entries = passbook.get_entries(
        security=filters.get("security"),
        party_type=filters.get("party_type"),
        party=filters.get("party"),
        from_date=filters.get("from_date"),
        to_date=filters.get("to_date"),
        direction=filters.get("direction"),
    )
    if filters.get("view") == "Counterparties":
        return _counterparty_columns(), _counterparty_rows(entries)
    return _passbook_columns(), [_strip(e) for e in entries]


def _strip(e):
    e = dict(e)
    e.pop("creation", None)
    e.pop("payments", None)
    e["indent"] = 0
    if e["direction"] == passbook.RECLASS:
        e["qty_reclass"] = e.pop("reclass_qty", 0)
    return e


def _passbook_columns():
    return [
        {"label": "Transfer Date", "fieldname": "date", "fieldtype": "Date", "width": 105},
        {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 125},
        {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 180},
        {"label": "Voucher Type", "fieldname": "voucher_type", "fieldtype": "Link", "options": "DocType", "width": 130},
        {"label": "Voucher", "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 160},
        {"label": "Party Type", "fieldname": "party_type", "fieldtype": "Data", "width": 90},
        {"label": "Party", "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 190},
        {"label": "Dir", "fieldname": "direction", "fieldtype": "Data", "width": 70},
        {"label": "Classification", "fieldname": "classification", "fieldtype": "Data", "width": 140},
        {"label": "Qty In", "fieldname": "qty_in", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Qty Out", "fieldname": "qty_out", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Reclass Qty", "fieldname": "qty_reclass", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Rate", "fieldname": "rate", "fieldtype": "Currency", "width": 95},
        {"label": "Amount", "fieldname": "amount", "fieldtype": "Currency", "width": 120},
        {"label": "Payment Type", "fieldname": "payment_type", "fieldtype": "Data", "width": 130},
        {"label": "Payment Details", "fieldname": "payment_details", "fieldtype": "Data", "width": 300},
        {"label": "FIFO Cost Out", "fieldname": "cost_out", "fieldtype": "Currency", "width": 120},
        {"label": "Balance Qty", "fieldname": "balance_qty", "fieldtype": "Float", "precision": 0, "width": 105},
        {"label": "Balance Cost", "fieldname": "balance_cost", "fieldtype": "Currency", "width": 125},
        {"label": "Posting Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
    ]


def _counterparty_columns():
    return [
        {"label": "Party / Movement", "fieldname": "particulars", "fieldtype": "Data", "width": 230},
        {"label": "Security", "fieldname": "security", "fieldtype": "Link", "options": "Security", "width": 125},
        {"label": "Name", "fieldname": "security_name", "fieldtype": "Data", "width": 170},
        {"label": "Party Type", "fieldname": "party_type", "fieldtype": "Data", "width": 90},
        {"label": "Party", "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 170},
        {"label": "Transfer Date", "fieldname": "date", "fieldtype": "Date", "width": 105},
        {"label": "Voucher Type", "fieldname": "voucher_type", "fieldtype": "Link", "options": "DocType", "width": 120},
        {"label": "Voucher", "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 155},
        {"label": "Qty In", "fieldname": "qty_in", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Qty Out", "fieldname": "qty_out", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Net Qty", "fieldname": "net_qty", "fieldtype": "Float", "precision": 0, "width": 95},
        {"label": "Rate", "fieldname": "rate", "fieldtype": "Currency", "width": 90},
        {"label": "Payment Type", "fieldname": "payment_type", "fieldtype": "Data", "width": 130},
        {"label": "Payment Details", "fieldname": "payment_details", "fieldtype": "Data", "width": 280},
        {"label": "Paid (bought)", "fieldname": "amount_paid", "fieldtype": "Currency", "width": 120},
        {"label": "Received (sold)", "fieldname": "amount_received", "fieldtype": "Currency", "width": 125},
        {"label": "Count", "fieldname": "count", "fieldtype": "Int", "width": 65},
    ]


def _counterparty_rows(entries):
    rows = []
    for i, g in enumerate(passbook.get_counterparties(entries)):
        parent_id = f"g{i}"
        rows.append({
            "row_id": parent_id, "parent_row": None, "indent": 0,
            "particulars": g["party"], "security": g["security"], "security_name": g["security_name"],
            "party_type": g["party_type"], "party": g["party"],
            "qty_in": g["qty_in"], "qty_out": g["qty_out"], "net_qty": g["net_qty"],
            "amount_paid": g["amount_paid"], "amount_received": g["amount_received"], "count": g["count"],
        })
        for j, e in enumerate(g["entries"]):
            paid = e["amount"] if e["direction"] == passbook.IN else 0
            received = e["amount"] if e["direction"] == passbook.OUT else 0
            rows.append({
                "row_id": f"{parent_id}-{j}", "parent_row": parent_id, "indent": 1,
                "particulars": e["voucher_no"], "security": e["security"], "security_name": e["security_name"],
                "party_type": e["party_type"], "party": e["party"], "date": e["date"],
                "voucher_type": e["voucher_type"], "voucher_no": e["voucher_no"],
                "qty_in": e["qty_in"], "qty_out": e["qty_out"], "net_qty": e["qty_in"] - e["qty_out"],
                "rate": e["rate"], "amount_paid": paid, "amount_received": received,
                "payment_type": e.get("payment_type"), "payment_details": e.get("payment_details"),
            })
    return rows
