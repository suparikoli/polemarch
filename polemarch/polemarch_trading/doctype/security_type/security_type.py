"""Security Type — lookup master for instrument classification.

Mirrors the Lead Source pattern: a tiny standalone doctype that backs a
Link field on the parent (Security). Operators can add new types from
the UI without a schema change.

`type_name` is both the displayed label and the primary key — autoname
goes through `field:type_name`, so re-saving with a new name renames
the document. That's by design — the field is user-editable.
"""

import frappe
from frappe.model.document import Document


class SecurityType(Document):
    def validate(self):
        if self.type_name:
            self.type_name = self.type_name.strip()
