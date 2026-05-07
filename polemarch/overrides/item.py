import frappe

from polemarch.install import POLEMARCH_BRAND, POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE


def validate(doc, method=None):
    """For every Polemarch-branded Item, ensure the
    `Polemarch - Non-GST` Item Tax Template is linked in `Item.taxes`.

    India Compliance reads the effective ITT for each Sales Invoice /
    Sales Order line via `update_gst_treatment_map`, then stamps
    `gst_treatment = "Non-GST"` on the row — which zeroes tax
    computation automatically, regardless of the document-level
    Sales Taxes and Charges Template. This is the idiomatic GST-free
    setup IC was designed for; no invoice-level template swap needed.

    Idempotent — appends only if not already present for this
    company. Multi-company sites end up with one row per company.
    """
    if doc.brand != POLEMARCH_BRAND:
        return
    _ensure_non_gst_item_tax_template(doc)


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


def _ensure_non_gst_item_tax_template(doc):
    """Append a row to `Item.taxes` for every company that has a
    `Polemarch - Non-GST` ITT created (`install._create_polemarch_item_tax_template`
    creates one per company on `after_migrate`)."""
    existing = {row.item_tax_template for row in doc.get("taxes") or []}
    itts = frappe.get_all(
        "Item Tax Template",
        filters={"title": POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE},
        fields=["name"],
        pluck="name",
    )
    for itt in itts:
        if itt in existing:
            continue
        doc.append("taxes", {"item_tax_template": itt})
