"""v0_24_0 — backfill `custom_is_polemarch_customer=1` on existing
customers who count as Polemarch customers but never got the flag set.

The original validate-time logic in polemarch.overrides.customer.validate
only flipped the flag when:
  - the customer had a DP Details row, OR
  - they were in the 'Polemarch' Customer Group

Customers who only got involved with Polemarch via the trading
subsystem (Wallet topped up, Security Sale to them, Customer Holding
minted, Polemarch invoice issued) never matched either criterion, so
their Polemarch tab + dashboard stayed hidden.

This patch broadens the criteria (matching the new
`_is_polemarch_customer` helper) and flips the flag for all matching
existing customers. Idempotent — re-running is a no-op.
"""

import frappe


def execute():
    candidates = set()

    # Direct signals — anyone in the Polemarch group or with DP details.
    if frappe.db.exists("Customer Group", "Polemarch"):
        candidates.update(
            frappe.db.sql_list(
                "SELECT name FROM `tabCustomer` WHERE customer_group = %s",
                ("Polemarch",),
            )
        )
    candidates.update(
        frappe.db.sql_list(
            """
            SELECT DISTINCT c.name
            FROM `tabCustomer` c
            JOIN `tabDP Details` dp ON dp.parent = c.name
            """,
        )
        if frappe.db.table_exists("DP Details")
        else []
    )

    # Trading-subsystem signals.
    if frappe.db.table_exists("Wallet"):
        candidates.update(
            frappe.db.sql_list("SELECT DISTINCT customer FROM `tabWallet` WHERE customer IS NOT NULL")
        )
    if frappe.db.table_exists("Customer Holding"):
        candidates.update(
            frappe.db.sql_list(
                "SELECT DISTINCT customer FROM `tabCustomer Holding` WHERE customer IS NOT NULL"
            )
        )
    if frappe.db.has_column("Sales Invoice", "custom_is_polemarch_invoice"):
        candidates.update(
            frappe.db.sql_list(
                """
                SELECT DISTINCT customer FROM `tabSales Invoice`
                WHERE custom_is_polemarch_invoice = 1 AND docstatus = 1
                """,
            )
        )
    for dt in ("Security Sale", "Security Purchase"):
        if frappe.db.table_exists(dt):
            candidates.update(
                frappe.db.sql_list(
                    f"""
                    SELECT DISTINCT party FROM `tab{dt}`
                    WHERE party_type = 'Customer' AND docstatus = 1
                    """,
                )
            )

    candidates.discard(None)
    candidates.discard("")
    if not candidates:
        return

    flipped = 0
    for name in candidates:
        if not frappe.db.exists("Customer", name):
            continue
        current = frappe.db.get_value("Customer", name, "custom_is_polemarch_customer")
        if current:
            continue
        frappe.db.set_value(
            "Customer", name, "custom_is_polemarch_customer", 1, update_modified=False
        )
        flipped += 1

    frappe.db.commit()
    if flipped:
        print(f"Polemarch customer flag backfill: flipped {flipped} of {len(candidates)} candidates")
