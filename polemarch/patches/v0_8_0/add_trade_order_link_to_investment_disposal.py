"""Add `polemarch_trade_order` Custom Field on Investment Disposal.

Phase 8 — the FIFO refactor routes Sell-side Trade Orders directly to
Investment Disposal (no Security Lot intermediary). settlement.py needs
a back-link from the Disposal it creates back to the Trade Order that
spawned it, so audits + UI can navigate Order ↔ Disposal both ways.

Idempotent. Safe to re-run.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if frappe.db.exists(
        "Custom Field",
        {"dt": "Investment Disposal", "fieldname": "polemarch_trade_order"},
    ):
        return

    create_custom_fields(
        {
            "Investment Disposal": [
                {
                    "fieldname": "polemarch_trade_order",
                    "label": "Trade Order",
                    "fieldtype": "Link",
                    "options": "Trade Order",
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "insert_after": "naming_series",
                    "description": (
                        "Back-link to the Trade Order whose settlement created this "
                        "Disposal. Populated by polemarch.polemarch_trading.settlement "
                        "when emitting the Disposal from a settled Sell order."
                    ),
                }
            ]
        },
        update=True,
    )
