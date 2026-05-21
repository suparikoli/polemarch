"""Phase 11 — import the Polemarch Workspace JSON into the DB.

Frappe doesn't auto-sync workspace JSON files the way it does doctype
JSON. The workspace file lives under
`polemarch/workspace/polemarch/polemarch.json` but never lands in
`tabWorkspace` unless we explicitly import it.

This patch uses `frappe.modules.import_file.import_file_by_path` to
load the file. Idempotent — Frappe's importer respects the JSON's
`name` and updates the existing row instead of inserting a duplicate
on re-runs.
"""

import os

import frappe
from frappe.modules.import_file import import_file_by_path


_WORKSPACE_PATH = "polemarch/workspace/polemarch/polemarch.json"


def execute():
    app_path = frappe.get_app_path("polemarch")
    full_path = os.path.join(app_path, _WORKSPACE_PATH)

    if not os.path.exists(full_path):
        frappe.log_error(
            f"Polemarch workspace file missing at {full_path}",
            "polemarch v0_11_0.import_polemarch_workspace",
        )
        return

    # force=True overwrites any existing Workspace row with the same name —
    # critical because the previous version of this file referenced
    # removed doctypes (Polemarch Items / Polemarch Invoice flag) and we
    # want operators to land on the rebuilt layout.
    import_file_by_path(full_path, force=True, reset_permissions=True)

    frappe.db.commit()
    frappe.clear_cache()
