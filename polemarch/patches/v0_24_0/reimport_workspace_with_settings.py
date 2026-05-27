"""v0_24_0 — re-import Polemarch workspace to surface the Settings card.

Adds:
  - "Settings" card-break section in the workspace sidebar (alongside
    Trading / Inventory / Reports / Audit).
  - "Polemarch Settings" link under that card (opens the Single
    DocType form).
  - "Polemarch Settings" shortcut (yellow chip in the top shortcut row).
  - "Settings" card block in the page layout.

Frappe doesn't auto-sync Workspace JSON on every migrate — only on
install or when the file's `modified` timestamp moves forward. This
patch performs the same drop-and-re-import the v0_11_0 patch does,
keeping the layout in lockstep with the source file.

Idempotent — safe to re-run; reads the JSON file each time.
"""

import json
import os

import frappe


def execute():
    json_path = frappe.get_app_path(
        "polemarch", "workspace", "polemarch", "polemarch.json"
    )
    if not os.path.exists(json_path):
        return

    with open(json_path, "r") as f:
        data = json.load(f)

    name = data["name"]

    # Drop the existing workspace + its children so the re-import lands
    # cleanly (Workspace Link / Workspace Shortcut / Workspace Number
    # Card all FK to the parent name).
    if frappe.db.exists("Workspace", name):
        frappe.delete_doc("Workspace", name, ignore_permissions=True, force=True)

    # Re-create via the standard import path. Workspace child tables get
    # restored from the JSON's `links` / `shortcuts` / `number_cards` arrays.
    workspace = frappe.get_doc(data)
    workspace.flags.ignore_permissions = True
    workspace.flags.ignore_validate = True
    workspace.insert(ignore_permissions=True)
    frappe.db.commit()
