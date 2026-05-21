"""Drop the Phase 2/3 lot-tracking doctypes — they are no longer used.

The FIFO refactor (Phase 8) routes Sell-side Trade Orders directly to
Investment Holding / Investment Disposal. Three doctypes become dead:

    - Security Lot                  (replaced by Investment Holding rows)
    - Security Lot Ledger Entry     (replaced by Investment Disposal Lot)
    - Trade Order Lot Consumption   (was a child table on Trade Order; the
                                     parent now points to Disposal directly)

For each:
  1. Drop the `tab<DocType>` SQL table if it exists.
  2. Delete the row from `tabDocType` so Desk stops listing it.
  3. Delete every related Custom Field / Property Setter / Print Format /
     Workflow row keyed on that doctype.

Idempotent — every step uses `IF EXISTS` / `frappe.db.exists`.

CAUTION: this is destructive. Pre-Phase-8 sites that hadn't migrated to
classification will lose lot history. The companion patches
add_classification_fields_to_investment_holding +
backfill_classification_from_portfolio (which run earlier in the same
v0_8_0 batch) ensure the data needed by the new model is already
captured on Investment Holding before tables disappear.
"""

import frappe


_DEAD_DOCTYPES = [
    "Security Lot",
    "Security Lot Ledger Entry",
    "Trade Order Lot Consumption",
]


def execute():
    for doctype in _DEAD_DOCTYPES:
        _drop_doctype(doctype)
    frappe.db.commit()


def _drop_doctype(doctype: str) -> None:
    table = f"tab{doctype}"

    # 1. Drop the SQL table. Use sql_ddl so the DDL doesn't trip the
    # implicit-commit guard that wraps regular sql() calls inside patches.
    try:
        frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `{table}`")
    except Exception:
        # Some Frappe versions don't expose sql_ddl on the public API;
        # fall back to the raw cursor.
        try:
            cursor = frappe.db._cursor  # type: ignore[attr-defined]
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_8_0.drop_lot_doctypes: failed to drop {table}",
            )

    # 2. Clear DocType registry + related rows. Order matters — child
    # tables, links, fields, property setters first, then the parent row.
    for related_dt, fieldname in (
        ("DocField", "parent"),
        ("Custom Field", "dt"),
        ("Property Setter", "doc_type"),
        ("DocType Link", "parent"),
        ("DocType Action", "parent"),
        ("Print Format", "doc_type"),
        ("Report", "ref_doctype"),
        ("Workflow", "document_type"),
    ):
        try:
            frappe.db.delete(related_dt, {fieldname: doctype})
        except Exception:
            # Tolerate missing tables / missing doctypes — we're cleaning up.
            pass

    # 3. Finally, the DocType row itself.
    if frappe.db.exists("DocType", doctype):
        try:
            frappe.db.delete("DocType", {"name": doctype})
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_8_0.drop_lot_doctypes: failed to delete DocType row {doctype}",
            )
