"""Add classification fields to Investment Holding.

The classification model replaces the previous lot/portfolio concept:

  - All new Holdings start as `Unallocated`.
  - Within 2 working days of creation (Mon-Fri minus India Holiday List),
    the holding must be manually classified as `Investment`.
  - If not, a daily scheduler auto-classifies it as `Stock in Trade`.

Idempotent. Safe to re-run.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    fields_to_add = []

    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "classification"}
    ):
        fields_to_add.append({
            "fieldname": "classification",
            "label": "Classification",
            "fieldtype": "Select",
            "options": "Unallocated\nStock in Trade\nInvestment",
            "default": "Unallocated",
            "reqd": 1,
            "in_list_view": 1,
            "in_standard_filter": 1,
            "insert_after": "status",
            "description": (
                "Auto-set to Stock in Trade after 2 working days if not manually "
                "classified as Investment by an authorized user."
            ),
        })

    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "classification_deadline"}
    ):
        fields_to_add.append({
            "fieldname": "classification_deadline",
            "label": "Classification Deadline",
            "fieldtype": "Datetime",
            "read_only": 1,
            "no_copy": 1,
            "in_standard_filter": 1,
            "insert_after": "classification",
            "description": (
                "Computed as creation + 2 working days (Mon-Fri minus the company's "
                "Holiday List). After this point, the daily auto-classifier locks the "
                "holding as Stock in Trade if it's still Unallocated."
            ),
        })

    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "classified_by"}
    ):
        fields_to_add.append({
            "fieldname": "classified_by",
            "label": "Classified By",
            "fieldtype": "Link",
            "options": "User",
            "read_only": 1,
            "no_copy": 1,
            "insert_after": "classification_deadline",
        })

    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "classified_on"}
    ):
        fields_to_add.append({
            "fieldname": "classified_on",
            "label": "Classified On",
            "fieldtype": "Datetime",
            "read_only": 1,
            "no_copy": 1,
            "insert_after": "classified_by",
        })

    if not frappe.db.exists(
        "Custom Field", {"dt": "Investment Holding", "fieldname": "qty_reserved"}
    ):
        fields_to_add.append({
            "fieldname": "qty_reserved",
            "label": "Qty Reserved",
            "fieldtype": "Float",
            "default": "0",
            "read_only": 1,
            "no_copy": 1,
            "insert_after": "qty_disposed",
            "description": (
                "Reserved by open Trade Orders (Sell-side) pending settlement. "
                "qty_remaining - qty_reserved = qty available for new orders."
            ),
        })

    if fields_to_add:
        create_custom_fields(
            {"Investment Holding": fields_to_add},
            update=True,
        )
