"""Whitelisted endpoints for form-button-triggered Medusa actions."""

import frappe
from frappe import _


@frappe.whitelist()
def resync_item(item_code: str):
    frappe.only_for("System Manager")
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} not found.").format(item_code))
    from polemarch.medusa.sync_items import push_item

    push_item(item_code)
    return {"ok": True, "message": _("Pushed {0} to Medusa.").format(item_code)}


@frappe.whitelist()
def resync_customer(customer: str):
    frappe.only_for("System Manager")
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} not found.").format(customer))
    from polemarch.medusa.sync_customers import push_customer

    push_customer(customer)
    return {"ok": True, "message": _("Pushed {0} to Medusa.").format(customer)}


@frappe.whitelist()
def resync_invoice_status(invoice: str, status: str = "paid"):
    frappe.only_for("System Manager")
    if not frappe.db.exists("Sales Invoice", invoice):
        frappe.throw(_("Sales Invoice {0} not found.").format(invoice))
    from polemarch.medusa.sync_orders import mirror_status

    mirror_status(invoice, status)
    return {"ok": True, "message": _("Mirrored status {0} to Medusa for {1}.").format(status, invoice)}
