"""v0_24_0 — Phase 24 schema: child-table classification on Investment Holding.

Adds Custom Fields:
  Investment Holding
    - classifications              (Table → Investment Holding Classification)
    - qty_classified_sit           (Float, read-only, computed from child rows)
    - qty_classified_investment    (Float, read-only)
    - qty_unclassified             (Float, read-only)
    - qty_disposed_sit             (Float, read-only — incremented by FIFO sales of SiT-classified portion)
    - qty_disposed_investment      (Float, read-only)
    - reconciliation_journal_entry (Link → Journal Entry, set at day-5 reconciliation)
    - reconciliation_posted_on     (Datetime, set at day-5 reconciliation)
  Security
    - qty_unclassified             (Float, read-only — Σ across all open Holdings)
    - cost_unclassified            (Currency, read-only)

Doesn't migrate existing IH data — that's done in a separate patch
(`backfill_classification_child_rows`) so a re-run of just-this patch
is idempotent + cheap.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    fields = {
        "Investment Holding": [
            {
                "fieldname": "section_classifications",
                "label": "Classifications (child-table model — Phase 24)",
                "fieldtype": "Section Break",
                "insert_after": "remaining_cost",
                "collapsible": 0,
                "description": (
                    "Append-only classification rows. During the 5-business-day window from "
                    "acquisition, operators add rows here to assign Unclassified shares to "
                    "Stock in Trade or Investment. At day-5 any remaining Unclassified flips "
                    "to SiT and a reconciliation JE moves the cost from the Pending "
                    "Classification suspense account to the final inventory accounts."
                ),
            },
            {
                "fieldname": "classifications",
                "label": "Classifications",
                "fieldtype": "Table",
                "options": "Investment Holding Classification",
                "insert_after": "section_classifications",
            },
            {
                "fieldname": "section_classification_rollup",
                "label": "Classification Rollup",
                "fieldtype": "Section Break",
                "insert_after": "classifications",
            },
            {
                "fieldname": "qty_classified_sit",
                "label": "Qty Classified — Stock in Trade",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "insert_after": "section_classification_rollup",
                "description": "Σ qty from `classifications` rows where classification = 'Stock in Trade'.",
            },
            {
                "fieldname": "qty_classified_investment",
                "label": "Qty Classified — Investment",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "insert_after": "qty_classified_sit",
                "description": "Σ qty from `classifications` rows where classification = 'Investment'.",
            },
            {
                "fieldname": "column_break_classified",
                "fieldtype": "Column Break",
                "insert_after": "qty_classified_investment",
            },
            {
                "fieldname": "qty_unclassified",
                "label": "Qty Unclassified",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "in_list_view": 1,
                "insert_after": "column_break_classified",
                "description": (
                    "qty_remaining − qty_classified_sit − qty_classified_investment. The pool "
                    "of shares still waiting to be classified."
                ),
            },
            {
                "fieldname": "qty_disposed_sit",
                "label": "Qty Disposed — Stock in Trade",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "hidden": 1,
                "insert_after": "qty_unclassified",
                "description": "FIFO-consumed portion from the SiT-classified pool. Internal use.",
            },
            {
                "fieldname": "qty_disposed_investment",
                "label": "Qty Disposed — Investment",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "hidden": 1,
                "insert_after": "qty_disposed_sit",
                "description": "FIFO-consumed portion from the Investment-classified pool. Internal use.",
            },
            {
                "fieldname": "section_reconciliation",
                "label": "Day-5 Reconciliation",
                "fieldtype": "Section Break",
                "insert_after": "qty_disposed_investment",
                "collapsible": 1,
            },
            {
                "fieldname": "reconciliation_journal_entry",
                "label": "Reconciliation Journal Entry",
                "fieldtype": "Link",
                "options": "Journal Entry",
                "read_only": 1,
                "no_copy": 1,
                "insert_after": "section_reconciliation",
                "description": (
                    "Set when the day-5 scheduler posts the JE moving cost from Pending "
                    "Classification → Securities Inventory + Long-Term Investments based on "
                    "the final classification split."
                ),
            },
            {
                "fieldname": "reconciliation_posted_on",
                "label": "Reconciliation Posted On",
                "fieldtype": "Datetime",
                "read_only": 1,
                "no_copy": 1,
                "insert_after": "reconciliation_journal_entry",
            },
        ],
        "Security": [
            {
                "fieldname": "qty_unclassified",
                "label": "Unclassified",
                "fieldtype": "Float",
                "read_only": 1,
                "no_copy": 1,
                "precision": 0,
                "in_list_view": 1,
                "in_standard_filter": 1,
                "insert_after": "qty_investment",
                "description": "Σ qty_unclassified across all open Investment Holdings for this Security.",
            },
            {
                "fieldname": "cost_unclassified",
                "label": "Unclassified Cost",
                "fieldtype": "Currency",
                "options": "INR",
                "read_only": 1,
                "no_copy": 1,
                "insert_after": "qty_unclassified",
                "description": "Σ qty_unclassified × cost_basis_per_unit. Sits in the Pending Classification suspense account until day-5.",
            },
        ],
    }
    create_custom_fields(fields, ignore_validate=True, update=True)
    frappe.db.commit()
