"""Phase 9 — drop the `item` column from Security.

Security is now standalone. The 1:1 Security ↔ Item back-reference is
gone — Security Purchase / Sale don't need it, and the FIFO engine
queries Holdings directly by `security`. Item.custom_security stays
(it's the only thing keeping the v0_9_0 backfill working), but Security
itself no longer points back.

Migrate handles the DocType JSON change (the field is removed from
field_order + fields), but the underlying `item` column in `tabSecurity`
doesn't get dropped automatically — drop it here so audits don't see
stale phantom data.

Idempotent.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Security"):
        return
    if not _column_exists("tabSecurity", "item"):
        return

    stmt = "ALTER TABLE `tabSecurity` DROP COLUMN `item`"
    try:
        frappe.db.sql_ddl(stmt)
    except AttributeError:
        # Older Frappe versions
        cursor = frappe.db._cursor  # type: ignore[attr-defined]
        cursor.execute(stmt)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "polemarch v0_9_0.drop_security_item_link",
        )

    frappe.db.commit()


def _column_exists(table: str, column: str) -> bool:
    rows = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s",
        (column,),
    )
    return bool(rows)
