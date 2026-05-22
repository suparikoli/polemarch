"""v0_24_0 — hide form-redundant legacy Custom Fields on Investment Holding.

These four fields are still readable by FIFO / list views / reports, so
we don't drop them — just hide them from the form because the new
child-table classification model + the breakdown panel render the same
information more usefully:

  - classification (Select Stock in Trade / Investment / Unallocated):
    derived from `classifications` child rows via
    _compute_classification_rollup. Single-value back-compat shim.
    Phase 24C will drop the field entirely.

  - classified_on (Datetime): set on first save with the legacy single
    classification. Stale the moment you classify any partial qty —
    each child row carries its own `classified_on` instead.

  - classified_by (Link User): same — stale, per-row in the child table.

  - qty_reserved (Float): legacy Trade Order field. Pre-Phase-13 Sell
    orders bumped it; Phase 13 dropped Trade Order entirely. Always 0
    in practice; the breakdown panel makes it irrelevant.

Idempotent: skips when the Custom Field is already hidden.
"""

import frappe


HIDE = ["classification", "classified_on", "classified_by", "qty_reserved"]


def execute():
    for fn in HIDE:
        cf = frappe.db.get_value(
            "Custom Field",
            {"dt": "Investment Holding", "fieldname": fn},
            "name",
        )
        if not cf:
            continue
        if not frappe.db.get_value("Custom Field", cf, "hidden"):
            frappe.db.set_value(
                "Custom Field", cf, "hidden", 1, update_modified=False
            )
    frappe.db.commit()
    frappe.clear_cache(doctype="Investment Holding")
