import frappe
from frappe import _

from polemarch.install import MITHTECH_SERVICES_BRAND, POLEMARCH_BRAND


def validate(doc, method=None):
    """Block mixing Polemarch and Mithtech Services items on one order.
    Polemarch share orders go through Security Purchase / Sale, so the
    old `custom_is_polemarch_order` flag was retired in v0_30_0.
    """
    brands = _line_brands(doc)
    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Order cannot mix {0} and {1} branded items.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )


def _line_brands(doc) -> set:
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)
    return brands
