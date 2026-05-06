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
