import frappe
from frappe import _

from polemarch.install import MITHTECH_SERVICES_BRAND, POLEMARCH_BRAND


def validate(doc, method=None):
    """See `polemarch.overrides.sales_invoice.validate` — same shape.
    Tax treatment for Polemarch securities is handled at the Item
    level via the `Polemarch - Non-GST` Item Tax Template, not here.
    """
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Order cannot mix {0} and {1} branded items.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    is_polemarch = bool(brands) and brands == {POLEMARCH_BRAND}
    doc.custom_is_polemarch_order = 1 if is_polemarch else 0


def _line_brands(doc) -> set:
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)
    return brands
