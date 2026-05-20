"""Seed Polemarch-specific Roles.

These roles are referenced by:
  - Portfolio Transfer (Polemarch Compliance Officer is the approver role)
  - Trading API endpoints (Polemarch Settlement Officer, Polemarch Trader)
  - Permission-query conditions (Phase 5)

Each role is created if missing. Existing role records are left untouched —
this patch does NOT bulk-assign roles to users; that's an admin action.

Idempotent.
"""

import frappe


_ROLES = [
    {
        "role_name": "Polemarch Settlement Officer",
        "desk_access": 1,
        "description": (
            "Drives settlement state transitions (fund / clear / fail) and back-office "
            "trade matching. Required for the Settlement Instruction state machine."
        ),
    },
    {
        "role_name": "Polemarch Compliance Officer",
        "desk_access": 1,
        "description": (
            "Approves / rejects Portfolio Transfers. Cannot self-approve — the doctype "
            "controller blocks transitions where actor == requested_by."
        ),
    },
    {
        "role_name": "Polemarch Trader",
        "desk_access": 1,
        "description": (
            "Creates and cancels Trade Orders, initiates Portfolio Transfers. "
            "Read-only on Wallet / Position / Settlement for assigned customers."
        ),
    },
]


def execute():
    for spec in _ROLES:
        if frappe.db.exists("Role", spec["role_name"]):
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Role",
                "role_name": spec["role_name"],
                "desk_access": spec["desk_access"],
                "is_custom": 1,
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
