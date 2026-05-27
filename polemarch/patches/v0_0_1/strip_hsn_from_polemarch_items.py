"""Backfill: clear `gst_hsn_code` and force `item_group =
"Polemarch Securities"` on every existing brand=Polemarch Item.

Earlier versions of the polemarch app (and the test seeder) wrote
HSN 998311 onto share Items to satisfy India Compliance's mandatory-
field check on Item creation. Once
`install._make_hsn_optional_on_item()` drops the reqd flag on the
HSN Custom Field, those stale HSN values stay on the rows and are
visible on Sales Invoice line items — wrong both legally (securities
are out of GST scope) and in any GSTR-1 report that reads HSN from
the SI item.

This patch sweeps the table. Idempotent — safe to re-run."""

import frappe

from polemarch.install import POLEMARCH_BRAND, POLEMARCH_ITEM_GROUP


def execute():
    items = frappe.get_all("Item", filters={"brand": POLEMARCH_BRAND}, pluck="name")
    if not items:
        return

    cleared_hsn = 0
    fixed_group = 0
    for name in items:
        current_hsn = frappe.db.get_value("Item", name, "gst_hsn_code")
        current_group = frappe.db.get_value("Item", name, "item_group")
        if current_hsn:
            # update_modified=False to avoid bumping `modified` and
            # triggering downstream doc-event hooks for every row.
            frappe.db.set_value("Item", name, "gst_hsn_code", None, update_modified=False)
            cleared_hsn += 1
        if current_group != POLEMARCH_ITEM_GROUP and frappe.db.exists("Item Group", POLEMARCH_ITEM_GROUP):
            frappe.db.set_value("Item", name, "item_group", POLEMARCH_ITEM_GROUP, update_modified=False)
            fixed_group += 1

    if cleared_hsn or fixed_group:
        frappe.db.commit()
        frappe.logger().info(
            f"polemarch: cleared HSN on {cleared_hsn} item(s); "
            f"fixed item_group on {fixed_group} item(s)."
        )
