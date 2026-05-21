import re

import frappe
from frappe import _
from frappe.model.document import Document


ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class Security(Document):
    """Master record for an unlisted security.

    ISIN is the natural primary key (regulator-recognized, globally unique).
    The Item link is a 1:1 back-reference into ERPNext's GST/India-Compliance
    plumbing; Security itself stays decoupled from inventory taxonomy.
    """

    def validate(self):
        self._normalise_isin()
        self._validate_isin_format()
        self._sync_to_item()

    def before_insert(self):
        self._auto_link_item_by_medusa_product_id()

    def on_update(self):
        # Invalidate cached LTP / lookup queries.
        frappe.cache().hdel("polemarch_security_by_isin", self.isin)

    def _normalise_isin(self):
        if self.isin:
            self.isin = self.isin.strip().upper()

    def _validate_isin_format(self):
        if not self.isin or not ISIN_RE.match(self.isin):
            frappe.throw(
                _("ISIN must be 12 characters: 2 letters + 9 alphanumeric + 1 digit (e.g. INE002A01018)."),
                title=_("Invalid ISIN"),
            )

    def _sync_to_item(self):
        if not self.item or not self.security_name:
            return
        existing_item_name = frappe.db.get_value("Item", self.item, "item_name")
        if existing_item_name and existing_item_name != self.security_name:
            frappe.db.set_value("Item", self.item, "item_name", self.security_name)

    def _auto_link_item_by_medusa_product_id(self):
        if self.item or not self.medusa_product_id:
            return
        item = frappe.db.get_value(
            "Item",
            {"custom_medusa_product_id": self.medusa_product_id},
            "name",
        )
        if item:
            self.item = item
