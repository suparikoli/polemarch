import frappe
from frappe import _

from polemarch.install import (
    MITHTECH_SERVICES_BRAND,
    MITHTECH_SERVICES_TAX_TEMPLATE,
    POLEMARCH_BRAND,
    POLEMARCH_TAX_TEMPLATE,
)


def validate(doc, method=None):
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Invoice cannot mix {0} and {1} branded items. Split into two invoices.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_invoice = 1 if is_polemarch else 0

    if not doc.taxes_and_charges:
        suffix = POLEMARCH_TAX_TEMPLATE if is_polemarch else MITHTECH_SERVICES_TAX_TEMPLATE
        template = frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"title": suffix, "company": doc.company},
            "name",
        )
        if template:
            doc.taxes_and_charges = template

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
