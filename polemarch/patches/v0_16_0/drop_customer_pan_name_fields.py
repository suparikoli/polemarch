"""Phase 16 — drop custom_first_name / custom_middle_name / custom_last_name
on Customer.

These were created for the (since-removed) Medusa sync mapper which wrote
PAN-card name parts separately. Polemarch now uses the standard ERPNext
Contact linked to each Customer for storing first / middle / last name,
email, and phone. The custom fields are dead weight.

Drops the Custom Field rows AND the underlying columns. Any data in
those columns is lost — but no production code reads them.

Idempotent.
"""

import frappe


_DEAD_FIELDS = ["custom_first_name", "custom_middle_name", "custom_last_name"]


def execute():
    for fieldname in _DEAD_FIELDS:
        cf_name = frappe.db.get_value(
            "Custom Field",
            {"dt": "Customer", "fieldname": fieldname},
            "name",
        )
        if cf_name:
            try:
                frappe.delete_doc(
                    "Custom Field", cf_name, force=True, ignore_permissions=True
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"polemarch v0_16_0: delete CF Customer.{fieldname}",
                )

        # Drop the underlying column. Frappe doesn't auto-drop columns when
        # a Custom Field row goes — leaving them around is dead schema.
        if frappe.db.table_exists("Customer") and _column_exists("tabCustomer", fieldname):
            stmt = f"ALTER TABLE `tabCustomer` DROP COLUMN `{fieldname}`"
            try:
                frappe.db.sql_ddl(stmt)
            except Exception:
                try:
                    cursor = frappe.db._cursor  # type: ignore[attr-defined]
                    cursor.execute(stmt)
                except Exception:
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"polemarch v0_16_0: drop column Customer.{fieldname}",
                    )

    frappe.db.commit()
    frappe.clear_cache(doctype="Customer")


def _column_exists(table: str, column: str) -> bool:
    rows = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s",
        (column,),
    )
    return bool(rows)
