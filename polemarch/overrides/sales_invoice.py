import frappe
from frappe import _

from polemarch.install import MITHTECH_SERVICES_BRAND, POLEMARCH_BRAND


def validate(doc, method=None):
    """Polemarch-side Sales Invoice rules — purely classification +
    cost-center concerns. The 0% GST treatment for share-transfer
    invoices is handled at the **Item** level via the
    `Polemarch - Non-GST` Item Tax Template (auto-linked to every
    `brand=Polemarch` item by `polemarch.overrides.item.validate`).
    India Compliance reads each line's effective ITT and stamps
    `gst_treatment = "Non-GST"` on the row, which zeroes computed tax
    automatically — no invoice-level template juggling needed.

    Earlier versions of this hook tried to swap the Sales Taxes and
    Charges Template per-brand (Polemarch / Mithtech Services); that
    fought IC's intra/inter-state suppression and produced 36% tax on
    intra-state Mithtech invoices. The Item-Tax-Template approach is
    IC's intended mechanism for non-GST goods/services.
    """
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Invoice cannot mix {0} and {1} branded items. Split into two invoices.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_invoice = 1 if is_polemarch else 0
    _apply_cost_center(doc, is_polemarch)


def on_submit(doc, method=None):
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="paid",
            enqueue_after_commit=True,
        )


def on_cancel(doc, method=None):
    if doc.get("custom_medusa_order_id"):
        frappe.enqueue(
            "polemarch.medusa.sync_orders.mirror_status",
            queue="short",
            invoice_name=doc.name,
            new_status="canceled",
            enqueue_after_commit=True,
        )


def _line_brands(doc) -> set:
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)
    return brands


def _apply_cost_center(doc, is_polemarch: bool):
    target_brand = POLEMARCH_BRAND if is_polemarch else MITHTECH_SERVICES_BRAND
    cc = frappe.db.get_value(
        "Cost Center",
        {"cost_center_name": target_brand, "company": doc.company, "is_group": 0},
        "name",
    )
    if not cc:
        return
    for row in doc.get("items") or []:
        if not row.cost_center:
            row.cost_center = cc
