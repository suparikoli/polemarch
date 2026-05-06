import frappe
from frappe import _

from polemarch.install import MITHTECH_SERVICES_BRAND, POLEMARCH_BRAND


def validate(doc, method=None):
    brands = set()
    for row in doc.get("items") or []:
        brand = row.get("brand") or frappe.db.get_value("Item", row.item_code, "brand")
        if brand:
            brands.add(brand)

    if POLEMARCH_BRAND in brands and MITHTECH_SERVICES_BRAND in brands:
        frappe.throw(
            _("A single Sales Order cannot mix {0} and {1} branded items.").format(
                POLEMARCH_BRAND, MITHTECH_SERVICES_BRAND
            )
        )

    doc.custom_is_polemarch_order = 1 if brands == {POLEMARCH_BRAND} else 0
