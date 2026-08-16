"""v0_28_0 — drop the 2 Medusa fields from Polemarch Settings.

After the medusync split, the master toggle (`enable_medusa_sync`) and
the HMAC webhook secret (`medusa_webhook_secret`) live in `Medusync
Settings`, not `Polemarch Settings`. This patch removes the two
DocField rows (the section_sync Section Break that contained them is
also dropped — orphaned section breaks look broken in the form), and
clears any tabSingles row entries so future reads of the field fall
through to `KeyError` / `AttributeError` as expected.

The corresponding helper functions
(`is_medusa_sync_enabled`, `get_medusa_webhook_secret`) are also
removed from `polemarch_settings.py` in the same commit; any caller
that still imports them will fail loudly rather than silently
returning False/None.

Idempotent. Safe to re-run.
"""

import frappe


_FIELDS_TO_DROP = ("enable_medusa_sync", "medusa_webhook_secret")
_SECTION_TO_DROP = "section_sync"


def execute():
	# 1. Clear any tabSingles row entries for these fields. tabSingles
	# is where Single doctype values live; the values are key→json.
	# Reading the doc and re-saving without the keys writes the new
	# state.
	doc = frappe.get_single("Polemarch Settings")
	changed = False
	for fieldname in _FIELDS_TO_DROP:
		if doc.get(fieldname) is not None:
			doc.set(fieldname, None)
			changed = True
	if changed:
		doc.flags.ignore_permissions = True
		doc.save()

	# 2. Drop the two DocField rows + the orphaned Section Break.
	# Polemarch Settings' fields are defined in the doctype JSON, not
	# as Custom Fields, so this is a `tabDocField` delete, not
	# `tabCustom Field`.
	for fieldname in (*_FIELDS_TO_DROP, _SECTION_TO_DROP):
		df_name = frappe.db.get_value(
			"DocField",
			{"parent": "Polemarch Settings", "fieldname": fieldname},
			"name",
		)
		if df_name:
			frappe.db.delete("DocField", {"name": df_name})

	# 3. Reload Polemarch Settings DocType so the new JSON is picked
	# up by the meta cache. Without this, a freshly-restarted worker
	# might still see the old fields in `frappe.get_meta("Polemarch
	# Settings").fields`.
	frappe.clear_cache(doctype="Polemarch Settings")
	frappe.db.commit()

