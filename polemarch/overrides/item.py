import frappe

from polemarch.install import (
    POLEMARCH_BRAND,
    POLEMARCH_ITEM_GROUP,
    POLEMARCH_NON_GST_ITEM_TAX_TEMPLATE,
)


def validate(doc, method=None):
    """Polemarch-side Item rules.

    For every brand=Polemarch Item:
      1. Force `item_group = "Polemarch Securities"` (creates the
         group if it doesn't exist yet — install hook normally seeds
         this, but new sites and per-customer overrides may skip it).
      2. Clear `gst_hsn_code` — Polemarch shares are securities under
         CGST Act Schedule III and explicitly carry no HSN/SAC.
      3. Auto-link the `Polemarch - Non-GST` Item Tax Template so
         India Compliance stamps `gst_treatment = "Non-GST"` on
         every Sales Invoice / Sales Order line that references the
         item, zeroing GST computation regardless of the document-
         level tax template.

    Mithtech-branded Items (processing fee, low-order fee) keep their
    HSN — the fee services ARE taxable supplies (SAC 997152, GST 18%)
    and their HSN drives the SI's GST breakup table.

    Idempotent — re-saving an already-correct item is a no-op.
    """
    if doc.brand != POLEMARCH_BRAND:
        return
    # Force the security item_group and clear HSN
    if doc.item_group != POLEMARCH_ITEM_GROUP and frappe.db.exists("Item Group", POLEMARCH_ITEM_GROUP):
        doc.item_group = POLEMARCH_ITEM_GROUP
    if doc.get("gst_hsn_code"):
        doc.gst_hsn_code = None
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
