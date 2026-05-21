"""Phase 11 — import the Polemarch Workspace JSON into the DB.

Frappe doesn't auto-sync workspace JSON files the way it does doctype
JSON. We have to read the file and write it into tabWorkspace + the
two child tables (`Workspace Link` for cards/links, `Workspace
Shortcut` for the top tiles) ourselves.

Drop+insert pattern: if a Polemarch workspace already exists (from a
previous version of this file), delete it first so children clear,
then re-insert from the current JSON. This guarantees the rebuilt
layout overwrites any stale rows from the original Polemarch /
Polemarch Customizations workspace that referenced removed doctypes
(Polemarch Items, SI Polemarch flag, Medusa Reference doctypes).

Idempotent.
"""

import json
import os

import frappe


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

    with open(full_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    workspace_name = payload.get("name") or payload.get("label")
    if not workspace_name:
        return

    # Drop any pre-existing row + children. Cancelling a Workspace is a
    # no-op (it's not submittable); delete_doc handles the cascade.
    if frappe.db.exists("Workspace", workspace_name):
        try:
            frappe.delete_doc(
                "Workspace", workspace_name, force=True, ignore_permissions=True
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "polemarch v0_11_0: failed to drop existing Polemarch workspace",
            )

    # Strip non-storable / metadata keys before insert; everything else
    # maps 1:1 to Workspace doctype fields + child tables.
    payload.pop("doctype", None)
    payload["doctype"] = "Workspace"
    payload.setdefault("name", workspace_name)

    doc = frappe.get_doc(payload)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_links = True  # links may reference doctypes not yet synced
    doc.insert(ignore_permissions=True)

    frappe.db.commit()
    frappe.clear_cache()
