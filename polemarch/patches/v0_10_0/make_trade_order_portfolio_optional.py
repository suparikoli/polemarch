"""Phase 10 — flip `Trade Order.portfolio` from required → optional.

Trade Order originally routed inventory by Portfolio (owner_kind × type
combinations: Proprietary-Trading, Proprietary-Investment, Customer-…).
Phase 8 replaced that mental model with classification (Stock in Trade /
Investment) + Phase 10 adds direct customer ownership on Investment
Holding. The Portfolio field is now a vestigial label, not a routing key.

Property Setter so the override survives `bench migrate` without editing
the JSON.

Idempotent.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    if not frappe.db.exists("DocType", "Trade Order"):
        return
    make_property_setter(
        doctype="Trade Order",
        fieldname="portfolio",
        property="reqd",
        value="0",
        property_type="Check",
        for_doctype=False,
        validate_fields_for_doctype=False,
    )
    frappe.db.commit()
    frappe.clear_cache(doctype="Trade Order")
