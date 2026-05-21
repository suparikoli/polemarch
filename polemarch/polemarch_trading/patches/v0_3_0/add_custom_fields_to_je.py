"""Add `custom_source_doctype` + `custom_source_name` to Journal Entry.

These two fields let Polemarch tag every auto-posted JE with the source doc
(Investment Disposal, Settlement Instruction, Portfolio Transfer, etc.) and
look it up for idempotency without scanning user_remark.

Indexed as a composite via `search_index=1` on both fields, so the lookup
`{custom_source_doctype, custom_source_name}` is O(log n).

Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    fields = []
    if not frappe.db.exists(
        "Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_doctype"}
    ):
        fields.append(
            {
                "fieldname": "custom_source_doctype",
                "label": "Polemarch Source Doctype",
                "fieldtype": "Link",
                "options": "DocType",
                "insert_after": "user_remark",
                "no_copy": 1,
                "read_only": 1,
                "search_index": 1,
                "in_standard_filter": 1,
                "description": (
                    "Set by Polemarch's auto-JE flow to tag the source document "
                    "(Investment Disposal, Settlement Instruction, etc.) for idempotency."
                ),
            }
        )

    if not frappe.db.exists(
        "Custom Field", {"dt": "Journal Entry", "fieldname": "custom_source_name"}
    ):
        fields.append(
            {
                "fieldname": "custom_source_name",
                "label": "Polemarch Source Name",
                "fieldtype": "Data",
                "insert_after": "custom_source_doctype",
                "no_copy": 1,
                "read_only": 1,
                "search_index": 1,
                "in_standard_filter": 1,
            }
        )

    if not fields:
        return

    create_custom_fields({"Journal Entry": fields}, update=True)
