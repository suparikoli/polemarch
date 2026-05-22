"""Phase 13 — drop Trade Order + Settlement Instruction + Settlement Run.

Trade Order's customer-mediated lifecycle (Submitted → Matched → Settling
→ Settled) is gone. Settlement Instruction wrapped Trade Order — also
gone. Settlement Run + Settlement Run Item batched Settlement Instructions
— also gone. Polemarch's customer trades now flow through Security Sale +
Security Purchase with `payment_method=Customer Wallet`, instant.

This patch drops:
  - tab<DocType> SQL tables for all four doctypes
  - tabDocType registry rows for each
  - Related Custom Field / Property Setter rows
  - The polemarch_trade_order Custom Field on Investment Disposal (the
    back-link no longer has a target)

Idempotent.

CAUTION: destructive. Any existing Trade Order / Settlement Instruction
rows are deleted. The settlement.fund / settlement.clear codepath is
gone — there's no rollback once this lands.
"""

import frappe


_DEAD_DOCTYPES = [
    "Settlement Run Item",       # child of Settlement Run
    "Settlement Run",
    "Settlement Instruction",
    "Trade Order",
]


def execute():
    for doctype in _DEAD_DOCTYPES:
        _drop_doctype(doctype)

    _drop_trade_order_custom_field()

    frappe.db.commit()
    frappe.clear_cache()


def _drop_doctype(doctype: str) -> None:
    table = f"tab{doctype}"

    # 1. Drop the SQL table (DDL — use sql_ddl to avoid the implicit-commit
    # guard that wraps regular sql() calls inside patches).
    try:
        frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `{table}`")
    except Exception:
        try:
            cursor = frappe.db._cursor  # type: ignore[attr-defined]
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_13_0.drop_trade_order_doctypes: drop {table}",
            )

    # 2. Clear DocType registry + related rows. Child tables / fields /
    # property setters first, then the parent row.
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
            pass

    if frappe.db.exists("DocType", doctype):
        try:
            frappe.db.delete("DocType", {"name": doctype})
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_13_0.drop_trade_order_doctypes: delete tabDocType row {doctype}",
            )


def _drop_trade_order_custom_field() -> None:
    """Investment Disposal.polemarch_trade_order back-linked to the now-gone
    Trade Order. Drop the Custom Field row + the underlying column."""
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Investment Disposal", "fieldname": "polemarch_trade_order"},
        "name",
    )
    if cf_name:
        try:
            frappe.delete_doc("Custom Field", cf_name, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "polemarch v0_13_0: delete Custom Field Investment Disposal.polemarch_trade_order",
            )

    # Drop the underlying column too — Frappe doesn't auto-drop columns when
    # the Custom Field row goes; the column lingers as dead schema otherwise.
    if frappe.db.table_exists("Investment Disposal") and _column_exists(
        "tabInvestment Disposal", "polemarch_trade_order"
    ):
        stmt = "ALTER TABLE `tabInvestment Disposal` DROP COLUMN `polemarch_trade_order`"
        try:
            frappe.db.sql_ddl(stmt)
        except Exception:
            try:
                cursor = frappe.db._cursor  # type: ignore[attr-defined]
                cursor.execute(stmt)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "polemarch v0_13_0: drop column polemarch_trade_order",
                )


def _column_exists(table: str, column: str) -> bool:
    rows = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s",
        (column,),
    )
    return bool(rows)
