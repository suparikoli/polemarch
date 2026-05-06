"""Quick-create a Polemarch Sales Invoice for back-office trades booked
outside Medusa. The brand-aware validate hook on Sales Invoice already
takes care of tax-template + cost-center + Polemarch tagging — this
endpoint is just a thin wrapper that builds the doc from the dialog.
"""

import json
from datetime import date

import frappe
from frappe import _

from polemarch.install import POLEMARCH_BRAND, POLEMARCH_NAMING_SERIES


@frappe.whitelist()
def create_polemarch_trade(payload):
    frappe.only_for(["System Manager", "Sales Manager", "Sales User", "Accounts Manager", "Accounts User"])

    data = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    customer = (data.get("customer") or "").strip()
    if not customer or not frappe.db.exists("Customer", customer):
        frappe.throw(_("Valid Polemarch customer is required."))
    if not frappe.db.get_value("Customer", customer, "custom_is_polemarch_customer"):
        frappe.throw(_("{0} is not a Polemarch customer.").format(customer))

    items = data.get("items") or []
    if not items:
        frappe.throw(_("At least one trade line is required."))

    company = data.get("company") or frappe.defaults.get_global_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    if not company:
        frappe.throw(_("Default Company is not configured."))

    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.company = company
    si.posting_date = data.get("posting_date") or date.today()
    si.due_date = data.get("due_date") or si.posting_date
    si.naming_series = POLEMARCH_NAMING_SERIES
    if data.get("po_no"):
        si.po_no = data.get("po_no")
    if data.get("remarks"):
        si.remarks = data.get("remarks")

    for line in items:
        item_code = line.get("item_code")
        if not item_code or not frappe.db.exists("Item", item_code):
            frappe.throw(_("Item {0} not found.").format(item_code))
        if frappe.db.get_value("Item", item_code, "brand") != POLEMARCH_BRAND:
            frappe.throw(_("Item {0} is not a Polemarch item.").format(item_code))
        si.append("items", {
            "item_code": item_code,
            "qty": line.get("qty") or 1,
            "rate": line.get("rate") or 0,
        })

    si.flags.ignore_permissions = True
    si.insert(ignore_permissions=True)
    if data.get("submit"):
        si.submit()
    frappe.db.commit()

    return {"name": si.name, "route": f"/app/sales-invoice/{si.name}"}
