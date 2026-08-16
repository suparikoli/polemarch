"""v0_29_0 — drop the 5 dead Medusa reference columns from Polemarch doctypes.

After the v0_28_0 split, the Medusa wire layer moved to `medusync` and
the operator confirmed that the 5 Medusa reference columns should NOT
live on Polemarch doctypes at all. Polemarch stays a pure trading-
domain ERPNext customisation; medusync owns the Medusa back-references
on its side (e.g. via a future `Medusync External Id` doctype if a
generic store is needed; the per-doc idempotency on `event_id` covers
the common case for now).

Five columns to drop:
  Security.medusa_product_id
  Security Sale.medusa_order_id
  Security Sale.source
  Wallet Deposit.medusa_originated
  Wallet Withdrawal.medusa_originated

Live data check (2026-08-17): 0 non-null rows across all 5 columns.
Safe to drop without data loss.

The corresponding medusync handler code uses `frappe.db.has_column(…)`
guards before writing, so the handlers are no-op when the columns
are absent — they don't break, they just stop stamping back-references
on Polemarch doctypes. (The handlers are still invoked by the
`medusync.api.receive` dispatch; they just take the no-column branch
which is a graceful skip.)

Two-step drop:
  1. Delete the Custom Field rows in `tabCustom Field`.
  2. ALTER TABLE … DROP COLUMN for each (Frappe's Custom Field delete
     does NOT auto-drop the column in this bench version — explicit
     ALTER is required).

Idempotent. Safe to re-run.
"""

import frappe


# (doctype, fieldname) pairs
_DROPPABLE = (
	("Security",          "medusa_product_id"),
	("Security Sale",     "medusa_order_id"),
	("Security Sale",     "source"),
	("Wallet Deposit",    "medusa_originated"),
	("Wallet Withdrawal", "medusa_originated"),
)


def execute():
	deleted_any = False

	# Step 1: drop the underlying columns. Done FIRST so that even if
	# the Custom Field delete fails, the columns are still gone. The
	# `IF EXISTS` clause makes the statement idempotent — a re-run
	# after a successful first run is a no-op.
	for dt, fieldname in _DROPPABLE:
		if not frappe.db.has_column(dt, fieldname):
			continue
		try:
			frappe.db.sql_ddl(
				f"ALTER TABLE `tab{dt}` DROP COLUMN `{fieldname}`"
			)
			deleted_any = True
		except Exception as exc:
			frappe.log_error(
				title=f"v0_29_0: failed to ALTER TABLE tab{dt} DROP COLUMN {fieldname}",
				message=frappe.get_traceback() + f"\n\nOriginal error: {exc}",
			)
			# Don't raise — the Custom Field delete below is a
			# belt-and-suspenders for the case where the column drop
			# silently no-op'd.

	# Step 2: delete the Custom Field rows. Also belt-and-suspenders —
	# if a future site has a Custom Field but no column (rare), this
	# cleans it up.
	for dt, fieldname in _DROPPABLE:
		cf_name = frappe.db.get_value(
			"Custom Field",
			{"dt": dt, "fieldname": fieldname},
			"name",
		)
		if not cf_name:
			continue
		frappe.delete_doc(
			"Custom Field",
			cf_name,
			force=True,
			ignore_permissions=True,
		)
		deleted_any = True

	# Reload each affected doctype's meta cache so a freshly-restarted
	# worker doesn't see the deleted fields in
	# `frappe.get_meta(doctype).fields`.
	for dt, _ in _DROPPABLE:
		frappe.clear_cache(doctype=dt)

	frappe.db.commit()
	return {"deleted": deleted_any, "fields": list(_DROPPABLE)}
