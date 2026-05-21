"""Phase 9 — clear `account_type="Stock"` on Securities Inventory accounts.

Phase 2's CoA seed marked `Securities Inventory - Trading` as account_type
"Stock" because at the time the lot doctypes were ERPNext-style inventory.
Post-Phase-8/9, Investment Holding IS the inventory ledger — there are no
Stock Entries / Bins / Warehouses involved — and account_type="Stock"
trips ERPNext's StockAccountInvalidTransaction guard whenever a plain
Journal Entry tries to DR/CR it.

This patch flips every Polemarch-seeded Securities Inventory account from
"Stock" to "" (Current Asset). Same treatment for `Long-Term Investments`
which was account_type="Investments" — same lock-down, same fix.

Idempotent.
"""

import frappe


_TARGET_NAMES = (
    "Securities Inventory - Trading",
    "Long-Term Investments",
)


def execute():
    rows = frappe.db.sql(
        """
        SELECT name, account_type
          FROM `tabAccount`
         WHERE account_name IN %(names)s
           AND account_type IN ('Stock', 'Investments')
        """,
        {"names": _TARGET_NAMES},
        as_dict=True,
    )
    for row in rows:
        frappe.db.set_value(
            "Account", row.name, "account_type", "", update_modified=False
        )
    frappe.db.commit()
