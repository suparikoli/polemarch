"""Backfill the `Polemarch - Non-GST` Item Tax Template link on every
existing brand=Polemarch Item.

Going forward, `polemarch.overrides.item.validate` ensures the link
on every save. This patch handles items that already exist (created
before the validate hook was added). Idempotent — appends only if
the row isn't already present for that company.
"""

import frappe

from polemarch.install import POLEMARCH_BRAND, POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE


def execute():
    items = frappe.get_all("Item", filters={"brand": POLEMARCH_BRAND}, pluck="name")
    if not items:
        return

    itts = frappe.get_all(
        "Item Tax Template",
        filters={"title": POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE},
        pluck="name",
    )
    if not itts:
        # The ITT is created by `install.setup()` (run via after_migrate);
        # if absent here, this patch ran in the wrong order — fall
        # through quietly and let the next migrate retry.
        return

    linked = 0
    for item_name in items:
        item = frappe.get_doc("Item", item_name)
        existing_links = {row.item_tax_template for row in item.get("taxes") or []}
        added = False
        for itt in itts:
            if itt in existing_links:
                continue
            item.append("taxes", {"item_tax_template": itt})
            added = True
        if added:
            item.flags.ignore_permissions = True
            item.flags.ignore_mandatory = True
            item.save(ignore_permissions=True)
            linked += 1
    if linked:
        frappe.db.commit()
        frappe.logger().info(
            f"polemarch: linked {POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE} ITT to "
            f"{linked} existing Polemarch item(s)"
        )
