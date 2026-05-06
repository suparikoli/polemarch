import frappe

from polemarch.install import POLEMARCH_BRAND


def on_update(doc, method=None):
    if doc.brand == POLEMARCH_BRAND:
        frappe.enqueue(
            "polemarch.medusa.sync_items.push_item",
            queue="short",
            item_name=doc.name,
            enqueue_after_commit=True,
        )


def on_trash(doc, method=None):
    if doc.brand == POLEMARCH_BRAND and doc.get("custom_medusa_product_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_items.delete_item",
            queue="short",
            medusa_product_id=doc.custom_medusa_product_id,
            enqueue_after_commit=True,
        )
