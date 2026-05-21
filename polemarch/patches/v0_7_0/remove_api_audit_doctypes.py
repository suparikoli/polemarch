"""Remove Polemarch API Log + Polemarch API Idempotency Log doctypes.

With the Medusa-sync layer fully delegated to the Medusa-side plugin, the
Frappe-side trading APIs no longer need:
  - external-caller idempotency dedup (the wallet engine has its own
    per-row idempotency_key check on Wallet Transaction; that's enough)
  - dedicated API audit logging (Frappe's native Activity Log + Error Log
    cover what we need)

Drops both doctypes and their underlying tables.

Idempotent. Safe to re-run.
"""

import frappe


_OBSOLETE_DOCTYPES = [
    "Polemarch API Idempotency Log",
    "Polemarch API Log",
]
_OBSOLETE_TABLES = [
    "tabPolemarch API Idempotency Log",
    "tabPolemarch API Log",
]


def execute():
    # 1. Clean tabDocType + child registry rows.
    for dt in _OBSOLETE_DOCTYPES:
        if not frappe.db.exists("DocType", dt):
            continue
        frappe.db.sql("DELETE FROM `tabDocField` WHERE parent = %s", (dt,))
        frappe.db.sql("DELETE FROM `tabDocPerm`  WHERE parent = %s", (dt,))
        frappe.db.sql("DELETE FROM `tabDocType`  WHERE name   = %s", (dt,))

    frappe.db.commit()

    # 2. Drop the underlying tables.
    for table in _OBSOLETE_TABLES:
        stmt = f"DROP TABLE IF EXISTS `{table}`"
        try:
            frappe.db.sql_ddl(stmt)
        except AttributeError:
            cursor = frappe.db._cursor
            cursor.execute(stmt)
        except Exception:
            pass

    frappe.db.commit()
    frappe.clear_cache()
