"""v0_24_0 — re-import Polemarch workspace JSON.

Phase 24A added a new Number Card ('Polemarch — Unclassified Value') to
the workspace JSON, but Frappe doesn't auto-sync Workspace JSON files
on migrate. v0_11_0 only ran once at install. Re-importing here lifts
the new number_cards array entry + content block onto live sites.

Same drop+insert pattern as v0_11_0 — idempotent. Safe to re-run.
"""

import json
import os

import frappe


_WORKSPACE_PATH = "workspace/polemarch/polemarch.json"


def execute():
    app_path = frappe.get_app_path("polemarch")
    full_path = os.path.join(app_path, _WORKSPACE_PATH)
    if not os.path.exists(full_path):
        return

    with open(full_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    workspace_name = payload.get("name") or payload.get("label")
    if not workspace_name:
        return

    if frappe.db.exists("Workspace", workspace_name):
        try:
            frappe.delete_doc(
                "Workspace", workspace_name, force=True, ignore_permissions=True
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "polemarch v0_24_0: failed to drop existing Polemarch workspace",
            )

    payload.pop("doctype", None)
    payload["doctype"] = "Workspace"
    payload.setdefault("name", workspace_name)

    doc = frappe.get_doc(payload)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_links = True
    doc.insert(ignore_permissions=True)

    frappe.db.commit()
    frappe.clear_cache()
