"""v0_30_0 — retire Sales Order.`custom_is_polemarch_order`.

Polemarch share orders are booked as Security Purchase / Security
Sale, never as Sales Orders, so the auto-derived flag no longer marks
anything. Deletes the Custom Field (which drops the column), any
Property Setters keyed on it, and the column itself if it survived.

Idempotent — re-running after the field is gone is a no-op.
"""

import frappe

FIELD = "custom_is_polemarch_order"


def execute():
    cf_name = frappe.db.get_value(
        "Custom Field", {"dt": "Sales Order", "fieldname": FIELD}, "name"
    )
    if cf_name:
        frappe.delete_doc("Custom Field", cf_name, force=True, ignore_permissions=True)

    for ps in frappe.get_all(
        "Property Setter",
        filters={"doc_type": "Sales Order", "field_name": FIELD},
        pluck="name",
    ):
        frappe.delete_doc("Property Setter", ps, force=True, ignore_permissions=True)

    # ALTER TABLE commits implicitly; flush first (see v0_26_0).
    if frappe.db.has_column("Sales Order", FIELD):
        frappe.db.commit()
        frappe.db.sql(f"ALTER TABLE `tabSales Order` DROP COLUMN `{FIELD}`")

    frappe.clear_cache(doctype="Sales Order")
    frappe.db.commit()
