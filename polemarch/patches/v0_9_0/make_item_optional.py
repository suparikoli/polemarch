"""Phase 9 — flip `item` from required → optional on Holding + Disposal.

New rows created by Security Purchase / Security Sale don't carry an Item
(Security is the trading identity now). The legacy `item` field stays so
backfilled rows keep rendering, but it stops being mandatory.

Uses `make_property_setter` so the override survives `bench migrate` even
though the underlying doctype JSON still has `"reqd": 1`. Idempotent.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


_TARGETS = [
    # (doctype, fieldname)
    ("Investment Holding", "item"),
    ("Investment Holding", "item_name"),
    ("Investment Disposal", "item"),
]


def execute():
    for doctype, fieldname in _TARGETS:
        if not frappe.db.exists("DocType", doctype):
            continue
        # property_type=Check forces a 0/1 value; pass "0" as string for compat.
        make_property_setter(
            doctype=doctype,
            fieldname=fieldname,
            property="reqd",
            value="0",
            property_type="Check",
            for_doctype=False,
            validate_fields_for_doctype=False,
        )
    frappe.db.commit()
    frappe.clear_cache(doctype="Investment Holding")
    frappe.clear_cache(doctype="Investment Disposal")
