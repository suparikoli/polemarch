"""Phase 14 — drop Portfolio + Security Position + custom_portfolio CF.

Portfolio was the Phase-0 routing master (Trading vs Investment book,
Proprietary vs per-Customer ownership). Phase 8 and Phase 10 collapsed
its job into Investment Holding.classification + Investment Holding.
customer respectively. Nothing reads or writes Portfolio after Phase 13.

Security Position was a `(portfolio, security)` denormalised qty rollup
fed from Holdings. With Portfolio gone, the rollup makes no sense; the
same data is one SQL GROUP BY away from Investment Holding.

This patch drops:
  - tabPortfolio + tabDocType row
  - tabSecurity Position + tabDocType row
  - Custom Field `Investment Holding.custom_portfolio` + underlying column

Idempotent. Destructive — Portfolio + Security Position rows are deleted.
The customer_dashboard._positions_section now computes from Investment
Holding live.
"""

import frappe


_DEAD_DOCTYPES = [
    "Security Position",
    "Portfolio",
]


def execute():
    for doctype in _DEAD_DOCTYPES:
        _drop_doctype(doctype)

    _drop_custom_portfolio_field()

    frappe.db.commit()
    frappe.clear_cache()


def _drop_doctype(doctype: str) -> None:
    table = f"tab{doctype}"

    try:
        frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `{table}`")
    except Exception:
        try:
            cursor = frappe.db._cursor  # type: ignore[attr-defined]
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"polemarch v0_14_0: drop {table}",
            )

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
                f"polemarch v0_14_0: delete tabDocType row {doctype}",
            )


def _drop_custom_portfolio_field() -> None:
    """Drop Investment Holding.custom_portfolio Custom Field + column."""
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Investment Holding", "fieldname": "custom_portfolio"},
        "name",
    )
    if cf_name:
        try:
            frappe.delete_doc("Custom Field", cf_name, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "polemarch v0_14_0: delete CF Investment Holding.custom_portfolio",
            )

    if frappe.db.table_exists("Investment Holding") and _column_exists(
        "tabInvestment Holding", "custom_portfolio"
    ):
        stmt = "ALTER TABLE `tabInvestment Holding` DROP COLUMN `custom_portfolio`"
        try:
            frappe.db.sql_ddl(stmt)
        except Exception:
            try:
                cursor = frappe.db._cursor  # type: ignore[attr-defined]
                cursor.execute(stmt)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "polemarch v0_14_0: drop column custom_portfolio",
                )


def _column_exists(table: str, column: str) -> bool:
    rows = frappe.db.sql(
        f"SHOW COLUMNS FROM `{table}` LIKE %s",
        (column,),
    )
    return bool(rows)
