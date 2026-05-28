"""Phase 18 — backfill primary Contact for existing Polemarch customers.

Before Phase 18, Customers created programmatically (smoke tests,
imports, API consumers) skipped Contact creation entirely. The Phase 16
sync hook then had nothing to sync from, and the Phase 18 validate
enforcement would now refuse to save these stale rows.

For each Polemarch customer (custom_is_polemarch_customer=1) without
a primary Contact:
  - Reuse the most recent Contact already linked via Dynamic Link, if any
  - Otherwise create a placeholder Contact (first_name = customer_name)
    + Dynamic Link the Contact → Customer
  - Set customer_primary_contact

Idempotent: skips customers that already have customer_primary_contact
populated or any Contact linked.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Customer"):
        return

    # Polemarch customers missing a primary Contact. Filter by
    # `custom_is_mithtech_only=0` (the surviving sync-gate flag after
    # v0_26_0 retired `custom_is_polemarch_customer`). Pre-v0_26_0 sites
    # may still have the old column — `frappe.db.has_column` keeps the
    # patch backwards-safe.
    if frappe.db.has_column("Customer", "custom_is_mithtech_only"):
        candidates = frappe.db.sql(
            """
            SELECT name, customer_name
              FROM `tabCustomer`
             WHERE COALESCE(custom_is_mithtech_only, 0) = 0
               AND (customer_primary_contact IS NULL OR customer_primary_contact = '')
            """,
            as_dict=True,
        )
    elif frappe.db.has_column("Customer", "custom_is_polemarch_customer"):
        candidates = frappe.db.sql(
            """
            SELECT name, customer_name
              FROM `tabCustomer`
             WHERE custom_is_polemarch_customer = 1
               AND (customer_primary_contact IS NULL OR customer_primary_contact = '')
            """,
            as_dict=True,
        )
    else:
        # Fresh install without either field — no candidates to fix.
        candidates = []

    if not candidates:
        return

    from polemarch.overrides.customer import _ensure_primary_contact

    for row in candidates:
        try:
            _ensure_primary_contact(row.name, row.customer_name)
        except Exception:
            # Don't let one bad row block the whole backfill — log and continue.
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_18_0: backfill Contact for Customer {row.name}",
            )

    frappe.db.commit()
