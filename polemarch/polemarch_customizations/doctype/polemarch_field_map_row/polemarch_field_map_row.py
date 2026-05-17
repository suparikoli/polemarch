"""Child table — one row per (Medusa path → ERPNext field) mapping
inside `Polemarch Sync Mapping`. Plain child, no controller logic
beyond Frappe's default Document behaviour. The transform + direction
logic is interpreted by the sync code, not here."""

from frappe.model.document import Document


class PolemarchFieldMapRow(Document):
    pass
