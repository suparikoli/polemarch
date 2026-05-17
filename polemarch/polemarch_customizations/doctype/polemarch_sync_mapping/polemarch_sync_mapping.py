"""Polemarch Sync Mapping — Single doctype that holds Medusa → ERPNext
field-mapping configuration for the Polemarch app.

Each entity (Customer / Bank Account / Demat Account / Item / Order /
Order Item) has:
  - a target DocType (Link → DocType)
  - a child table of `Polemarch Field Map Row` mappings

The sync code reads these mappings at runtime via
`get_active_mapping(entity)` and falls back to the hardcoded defaults
in `polemarch.medusa.sync_*` when a mapping isn't configured.

Exposes a whitelisted endpoint `get_target_doctype_fields(doctype)`
used by the form script to populate the autocomplete dropdown on the
`erpnext_field` column.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PolemarchSyncMapping(Document):
    def validate(self):
        self._validate_no_duplicate_paths()

    def _validate_no_duplicate_paths(self):
        """Each (section, medusa_path) pair must be unique — otherwise
        the second row silently overwrites the first during sync."""
        sections = [
            ("customer_mappings", "Customer"),
            ("bank_account_mappings", "Bank Account"),
            ("demat_account_mappings", "Demat Account"),
            ("item_mappings", "Item"),
            ("order_mappings", "Order"),
            ("order_item_mappings", "Order Item"),
        ]
        for fieldname, label in sections:
            seen = {}
            for row in self.get(fieldname) or []:
                if not row.medusa_path:
                    continue
                if row.medusa_path in seen:
                    frappe.throw(
                        _(
                            "{0} mappings: duplicate Medusa path "
                            "<strong>{1}</strong> on rows {2} and {3}."
                        ).format(label, row.medusa_path, seen[row.medusa_path], row.idx)
                    )
                seen[row.medusa_path] = row.idx


@frappe.whitelist()
def get_target_doctype_fields(doctype: str) -> list:
    """Return the list of fieldnames + labels + types for the given
    DocType. Used by the form script to populate the autocomplete on
    the `erpnext_field` column of the mapping rows.

    Returns rows like:
        [
            {"value": "customer_name", "description": "Customer Name (Data)"},
            {"value": "email_id",      "description": "Email Id (Data)"},
            ...
        ]
    """
    if not doctype:
        return []
    if not frappe.db.exists("DocType", doctype):
        return []

    fields = []
    meta = frappe.get_meta(doctype)
    for df in meta.fields:
        if df.fieldtype in (
            "Section Break", "Column Break", "Tab Break",
            "HTML", "Heading", "Button",
        ):
            continue
        if not df.fieldname:
            continue
        fields.append({
            "value": df.fieldname,
            "description": f"{df.label or df.fieldname} ({df.fieldtype})",
        })
    # Always include the canonical metadata fields the user might want
    # to write to even if they're not in the schema's field list.
    for sysname, syslabel in (
        ("name", "name (Auto / ID)"),
        ("modified", "Last Modified (datetime)"),
    ):
        if not any(f["value"] == sysname for f in fields):
            fields.append({"value": sysname, "description": syslabel})
    return sorted(fields, key=lambda r: r["value"])
