"""Remove the 7 Medusa-related Custom Fields from existing sites.

Drops:
  - Customer.custom_medusa_customer_id
  - Item.custom_medusa_product_id
  - Sales Invoice.custom_medusa_order_id
  - Sales Invoice.custom_platform_fee
  - Sales Invoice.custom_low_order_fee
  - Sales Invoice.custom_stamp_duty
  - Sales Order.custom_medusa_order_id

Also drops the underlying columns from the parent tables.

Idempotent. Safe to re-run.
"""

import frappe


_FIELDS_TO_DROP = [
    ("Customer",       "custom_medusa_customer_id"),
    ("Item",           "custom_medusa_product_id"),
    ("Sales Invoice",  "custom_medusa_order_id"),
    ("Sales Invoice",  "custom_platform_fee"),
    ("Sales Invoice",  "custom_low_order_fee"),
    ("Sales Invoice",  "custom_stamp_duty"),
    ("Sales Order",    "custom_medusa_order_id"),
]

_PARENT_TABLES = {
    "Customer": "tabCustomer",
    "Item": "tabItem",
    "Sales Invoice": "tabSales Invoice",
    "Sales Order": "tabSales Order",
}


def execute():
    # 1. Delete the Custom Field definitions.
    for dt, fieldname in _FIELDS_TO_DROP:
        frappe.db.sql(
            "DELETE FROM `tabCustom Field` WHERE dt = %s AND fieldname = %s",
            (dt, fieldname),
        )
    frappe.db.commit()

    # 2. Drop the underlying columns from the parent tables.
    # ALTER TABLE is a DDL statement; frappe.db.sql blocks implicit commits in
    # patches, so use sql_ddl (raw cursor fallback for older Frappe).
    for dt, fieldname in _FIELDS_TO_DROP:
        table = _PARENT_TABLES[dt]
        stmt = f"ALTER TABLE `{table}` DROP COLUMN IF EXISTS `{fieldname}`"
        try:
            frappe.db.sql_ddl(stmt)
        except AttributeError:
            cursor = frappe.db._cursor
            cursor.execute(stmt)
        except Exception:
            # Column may not exist — ignore.
            pass

    frappe.db.commit()
    frappe.clear_cache()
