import re

import frappe
from frappe import _
from frappe.model.document import Document


ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class Security(Document):
    """Master record for an unlisted security.

    ISIN is the natural primary key (regulator-recognized, globally unique).
    Post-Phase-9 the doctype is fully standalone — no Item back-link, no
    sync to ERPNext inventory taxonomy. Security Purchase / Security Sale
    drive trades directly against the Security and the Investment Holding
    rows it backs.
    """

    def validate(self):
        self._normalise_isin()
        self._validate_isin_format()

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
