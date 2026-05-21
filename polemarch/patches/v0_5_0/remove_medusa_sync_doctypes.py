"""Remove the 4 Medusa-sync doctypes from existing sites.

After the Medusa-owned-sync refactor, these doctypes no longer have JSON
files in the app source. Frappe's `bench migrate` schema-sync step would
flag them as orphan and remove them on its own — but the `tabSingles`
rows (for the two Single doctypes) and any data in the non-Single tables
need explicit cleanup so old values don't sit around as junk.

Idempotent. Safe to re-run.
"""

import frappe


_OBSOLETE_SINGLES = ["Medusa Settings", "Polemarch Sync Mapping"]
_OBSOLETE_TABLES = [
    "tabMedusa Sync Log",
    "tabPolemarch Field Map Row",
]
_OBSOLETE_DOCTYPES = [
    "Medusa Settings",
    "Medusa Sync Log",
    "Polemarch Sync Mapping",
    "Polemarch Field Map Row",
]


def execute():
    # 1. Drop tabSingles entries for the two Single doctypes.
    for single in _OBSOLETE_SINGLES:
        frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype = %s", (single,))

    # 2. Drop the physical tables for the non-Single doctypes.
    # Frappe's wrapper blocks DROP TABLE inside a patch (implicit-commit guard),
    # so commit pending changes first and use the lower-level cursor.
    frappe.db.commit()
    for table in _OBSOLETE_TABLES:
        try:
            frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `{table}`")
        except AttributeError:
            # Older Frappe versions lack sql_ddl — fall back to raw cursor.
            cursor = frappe.db._cursor
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
    frappe.db.commit()

    # 3. Clean up tabDocType registry rows + their DocField / DocPerm children.
    for dt in _OBSOLETE_DOCTYPES:
        if not frappe.db.exists("DocType", dt):
            continue
        frappe.db.sql("DELETE FROM `tabDocField` WHERE parent = %s", (dt,))
        frappe.db.sql("DELETE FROM `tabDocPerm`  WHERE parent = %s", (dt,))
        frappe.db.sql("DELETE FROM `tabDocType`  WHERE name   = %s", (dt,))

    # 4. Old Custom Fields that ONLY drove the Medusa flow (Customer.custom_vba_id,
    #    custom_aadhaar_hash, etc.) are kept — they have non-Medusa uses.
    #    The custom_medusa_*_id fields stay too: Medusa-plugin still stamps them
    #    on Frappe via REST, just no Frappe-side outbound logic uses them.

    # 5. Old patch-log entries from v0_0_1 that touched the sync mapping doctype
    #    are no longer needed but harmless if left.

    frappe.db.commit()
