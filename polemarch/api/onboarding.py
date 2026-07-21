"""Polemarch customer onboarding — single-call create that lays down
Customer + first DP Details + first Bank Details rows in one transaction.

Used by the "+ Polemarch Customer" wizard surfaced on the Customer list
view and the Polemarch workspace.
"""

import json

import frappe
from frappe import _

from polemarch.install import POLEMARCH_CUSTOMER_GROUP


@frappe.whitelist()
def create_polemarch_customer(payload):
    frappe.only_for(["System Manager", "Sales User", "Sales Manager"])

    data = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    customer_name = (data.get("customer_name") or "").strip()
    if not customer_name:
        frappe.throw(_("Customer name is required."))
    email = (data.get("email_id") or "").strip()
    if not email:
        frappe.throw(_("Email is required."))

    if frappe.db.exists("Customer", {"email_id": email}):
        frappe.throw(_("A customer with email {0} already exists.").format(email))

    dp = data.get("dp") or {}
    bank = data.get("bank") or {}
    _validate_dp(dp)
    _validate_bank(bank)

    customer = frappe.new_doc("Customer")
    customer.customer_name = customer_name
    customer.customer_type = data.get("customer_type") or "Individual"
    customer.customer_group = POLEMARCH_CUSTOMER_GROUP
    customer.territory = data.get("territory") or "India"
    customer.email_id = email
    customer.mobile_no = data.get("mobile_no")
    if data.get("pan"):
        customer.pan = data.get("pan")
    if data.get("gstin"):
        customer.gstin = data.get("gstin")
        customer.gst_category = data.get("gst_category") or "Unregistered"

    customer.append("custom_dp_details", {
        "dp_id": dp.get("dp_id"),
        "client_id": dp.get("client_id"),
        "bo_id": dp.get("bo_id"),
        "depository": dp.get("depository"),
        "dp_name": dp.get("dp_name"),
        "broker_name": dp.get("broker_name"),
        "primary_bo_name": dp.get("primary_bo_name") or customer_name,
        "primary_bo_pan": dp.get("primary_bo_pan") or data.get("pan"),
        "cmr_copy": dp.get("cmr_copy"),
        "is_primary": 1,
    })
    customer.append("custom_bank_details", {
        "bank_name": bank.get("bank_name"),
        "bank_branch": bank.get("bank_branch"),
        "bank_code": bank.get("bank_code"),
        "ac_number": bank.get("ac_number"),
        "account_holder": bank.get("account_holder") or customer_name,
        "cheque_image": bank.get("cheque_image"),
        "micr": bank.get("micr"),
        "is_primary": 1,
    })

    customer.flags.ignore_permissions = True
    customer.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"name": customer.name, "route": f"/app/customer/{customer.name}"}


def _validate_dp(dp: dict):
    for field in ("dp_id", "client_id", "bo_id", "dp_name", "cmr_copy"):
        if not dp.get(field):
            frappe.throw(_("DP Details: {0} is required.").format(field))


def _validate_bank(bank: dict):
    for field in ("bank_name", "bank_code", "ac_number", "cheque_image"):
        if not bank.get(field):
            frappe.throw(_("Bank Details: {0} is required.").format(field))

# NOTE: `create_polemarch_item` (create a brand=Polemarch ERPNext Item in
# the `Polemarch Securities` group) was removed — shares are modelled by
# the `Security` doctype now, not ERPNext Items. Its list-view button
# (public/js/item_list.js) and the Item form script (public/js/item.js)
# were removed alongside it.
