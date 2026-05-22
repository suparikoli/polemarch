"""Phase 15 — drop the `customer` Custom Field + column on Investment Holding.

Investment Holding becomes proprietary-only. Customer ownership lives on
the separate Customer Holding doctype as a simple CRM snapshot.

Must run AFTER migrate_customer_holdings has captured the data into the
new doctype.

Idempotent.
"""

import frappe


def execute():
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Investment Holding", "fieldname": "customer"},
        "name",
    )
    if cf_name:
        try:
            frappe.delete_doc("Custom Field", cf_name, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "polemarch v0_15_0: delete CF Investment Holding.customer",
            )

    if frappe.db.table_exists("Investment Holding") and _column_exists(
        "tabInvestment Holding", "customer"
    ):
        stmt = "ALTER TABLE `tabInvestment Holding` DROP COLUMN `customer`"
        try:
            frappe.db.sql_ddl(stmt)
        except Exception:
            try:
                cursor = frappe.db._cursor  # type: ignore[attr-defined]
                cursor.execute(stmt)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "polemarch v0_15_0: drop column customer",
                )

    frappe.db.commit()


def _column_exists(table: str, column: str) -> bool:
    rows = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s",
        (column,),
    )
    return bool(rows)
