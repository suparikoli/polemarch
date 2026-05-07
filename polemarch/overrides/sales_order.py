import frappe
from frappe import _

from polemarch.install import (
    MITHTECH_SERVICES_BRAND,
    MITHTECH_SERVICES_TAX_TEMPLATE,
    POLEMARCH_BRAND,
    POLEMARCH_TAX_TEMPLATE,
)


def before_validate(doc, method=None):
    """See `polemarch.overrides.sales_invoice.before_validate` for the
    full rationale. Runs before the controller validate so IC's
    GST resolution sees the brand-specific template from the start."""
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        return
    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    is_mithtech = bool(brands) and brands == {MITHTECH_SERVICES_BRAND}
    _apply_brand_tax_template(doc, is_polemarch, is_mithtech)


def validate(doc, method=None):
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Order cannot mix {0} and {1} branded items.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_order = 1 if is_polemarch else 0

    if is_polemarch:
        for row in doc.get("items") or []:
            row.gst_treatment = "Non-GST"


def _line_brands(doc) -> set:
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)
    return brands


def _apply_brand_tax_template(doc, is_polemarch: bool, is_mithtech: bool):
    """See `polemarch.overrides.sales_invoice._apply_brand_tax_template`
    for the full rationale. Same logic — SO uses the same Sales Taxes
    and Charges Template doctype."""
    target_title = None
    if is_polemarch:
        target_title = POLEMARCH_TAX_TEMPLATE
    elif is_mithtech:
        target_title = MITHTECH_SERVICES_TAX_TEMPLATE
    if not target_title:
        return

    template_name = frappe.db.get_value(
        "Sales Taxes and Charges Template",
        {"title": target_title, "company": doc.company},
        "name",
    )
    if not template_name:
        return

    if doc.taxes_and_charges != template_name:
        doc.taxes_and_charges = template_name
        if hasattr(doc, "set_other_charges"):
            doc.set_other_charges()
        else:
            doc.set("taxes", [])
