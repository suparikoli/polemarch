"""Per-customer Polemarch dashboard data — orders count, total invested,
recent trades, KYC completeness checklist. Rendered into the Polemarch
tab on the Customer form by public/js/customer.js.
"""

import frappe
from frappe import _


@frappe.whitelist()
def get_dashboard(customer: str) -> dict:
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} not found.").format(customer))

    inv_filters = {
        "customer": customer,
        "custom_is_polemarch_invoice": 1,
        "docstatus": 1,
    }
    total_orders = frappe.db.count("Sales Invoice", filters=inv_filters)
    total_invested = (
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(grand_total), 0)
            FROM `tabSales Invoice`
            WHERE customer = %s AND custom_is_polemarch_invoice = 1 AND docstatus = 1
            """,
            (customer,),
        )[0][0]
        or 0
    )
    currency = frappe.db.get_value("Customer", customer, "default_currency") or frappe.defaults.get_global_default("currency") or "INR"

    recent_trades = frappe.get_all(
        "Sales Invoice",
        filters=inv_filters,
        fields=["name", "posting_date", "grand_total", "status"],
        order_by="posting_date desc, creation desc",
        limit=10,
    )

    pending_orders = frappe.db.count(
        "Sales Order",
        filters={
            "customer": customer,
            "custom_is_polemarch_order": 1,
            "docstatus": 1,
            "status": ["in", ["To Bill", "To Deliver and Bill"]],
        },
    )

    kyc = _kyc_status(customer)

    return {
        "total_orders": total_orders,
        "total_invested": float(total_invested),
        "currency": currency,
        "recent_trades": [
            {
                "name": r.name,
                "posting_date": str(r.posting_date) if r.posting_date else None,
                "grand_total": float(r.grand_total or 0),
                "status": r.status,
            }
            for r in recent_trades
        ],
        "pending_orders": pending_orders,
        "kyc": kyc,
        # Phase 5 — surface wallet / open orders / positions when the
        # trading subsystem has migrated. Each section is best-effort and
        # returns None if the doctypes / data aren't there yet.
        "wallet": _wallet_section(customer),
        "open_trade_orders": _open_trade_orders_section(customer),
        "positions": _positions_section(customer),
    }


def _wallet_section(customer: str) -> dict | None:
    if not frappe.db.table_exists("Wallet"):
        return None
    wallet = f"WAL-{customer}"
    if not frappe.db.exists("Wallet", wallet):
        return None
    row = frappe.db.get_value(
        "Wallet",
        wallet,
        (
            "currency",
            "balance_total",
            "balance_available",
            "balance_reserved",
            "balance_pending",
            "status",
        ),
        as_dict=True,
    )
    if not row:
        return None
    last_txn = frappe.db.get_value(
        "Wallet Transaction",
        {"wallet": wallet, "docstatus": 1},
        ("posting_datetime", "txn_type", "direction", "amount"),
        as_dict=True,
        order_by="posting_datetime DESC",
    )
    return {
        "wallet": wallet,
        "status": row.status,
        "currency": row.currency,
        "balance_total": float(row.balance_total or 0),
        "balance_available": float(row.balance_available or 0),
        "balance_reserved": float(row.balance_reserved or 0),
        "balance_pending": float(row.balance_pending or 0),
        "last_transaction": (
            {
                "posting_datetime": str(last_txn.posting_datetime) if last_txn else None,
                "txn_type": last_txn.txn_type if last_txn else None,
                "direction": last_txn.direction if last_txn else None,
                "amount": float(last_txn.amount or 0) if last_txn else 0,
            }
            if last_txn
            else None
        ),
    }


def _open_trade_orders_section(customer: str) -> list:
    if not frappe.db.table_exists("Trade Order"):
        return []
    rows = frappe.get_all(
        "Trade Order",
        filters={
            "customer": customer,
            "order_state": ["in", ["Submitted", "Matched", "Settling"]],
            "docstatus": ["!=", 2],
        },
        fields=[
            "name",
            "side",
            "security",
            "security_name",
            "qty",
            "price",
            "net_amount",
            "order_state",
            "settlement_status",
            "expected_settlement_date",
        ],
        order_by="modified DESC",
        limit_page_length=10,
    )
    return [
        {
            "name": r.name,
            "side": r.side,
            "security": r.security,
            "security_name": r.security_name,
            "qty": float(r.qty or 0),
            "price": float(r.price or 0),
            "net_amount": float(r.net_amount or 0),
            "order_state": r.order_state,
            "settlement_status": r.settlement_status,
            "expected_settlement_date": str(r.expected_settlement_date) if r.expected_settlement_date else None,
        }
        for r in rows
    ]


def _positions_section(customer: str) -> dict | None:
    if not frappe.db.table_exists("Security Position"):
        return None
    rows = frappe.get_all(
        "Security Position",
        filters={"customer": customer, "qty_held": [">", 0]},
        fields=[
            "name",
            "security",
            "isin",
            "portfolio",
            "qty_held",
            "qty_available",
            "avg_cost",
            "total_cost",
            "market_value",
            "unrealized_pnl",
        ],
        order_by="security ASC",
        limit_page_length=50,
    )
    return {
        "count": len(rows),
        "total_cost": float(sum(r.total_cost or 0 for r in rows)),
        "total_market_value": float(sum(r.market_value or 0 for r in rows)),
        "total_unrealized_pnl": float(sum(r.unrealized_pnl or 0 for r in rows)),
        "rows": [
            {
                "security": r.security,
                "isin": r.isin,
                "portfolio": r.portfolio,
                "qty_held": float(r.qty_held or 0),
                "qty_available": float(r.qty_available or 0),
                "avg_cost": float(r.avg_cost or 0),
                "total_cost": float(r.total_cost or 0),
                "market_value": float(r.market_value or 0),
                "unrealized_pnl": float(r.unrealized_pnl or 0),
            }
            for r in rows
        ],
    }


def _kyc_status(customer: str) -> dict:
    doc = frappe.get_doc("Customer", customer)
    checks = [
        ("pan_number", bool((doc.get("pan") or "").strip()), _("PAN number on record")),
        ("pan_card", bool(doc.get("custom_pan_card")), _("PAN card uploaded")),
        ("aadhaar_card", bool(doc.get("custom_aadhaar_card")), _("Aadhaar card uploaded")),
        ("dp_details", bool(doc.get("custom_dp_details")), _("Demat (DP Details) on file")),
        ("bank_details", bool(doc.get("custom_bank_details")), _("Bank account on file")),
    ]
    items = [{"key": k, "ok": ok, "label": label} for k, ok, label in checks]
    completeness = round(100 * sum(1 for i in items if i["ok"]) / len(items)) if items else 0
    return {"items": items, "completeness": completeness}
