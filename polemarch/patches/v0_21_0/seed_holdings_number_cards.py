"""v0_21_0 — seed 4 Number Cards backing the Polemarch workspace.

Each card calls a whitelisted method in
`polemarch.api.holdings_summary` that returns
{value, fieldtype: "Currency"} so the card renders with ₹ prefix.

Idempotent: looks up by the namespaced label, only inserts when missing.
Frappe's Number Card autoname derives `name` from `label`, so we use the
full "Polemarch — X" string as the label — that becomes the name and
avoids colliding with stock ERPNext default cards (e.g. "Total Assets").
A best-effort cleanup pass kills any orphaned `<short label>-1` cards
left behind by the first iteration of this patch.

Re-running on a site that already has the cards is a no-op.
"""

import frappe


CARDS = [
    {
        "label": "Polemarch — Total Holdings Value",
        "method": "polemarch.api.holdings_summary.card_total_holdings_value",
        "color": "#4F46E5",
    },
    {
        "label": "Polemarch — Stock in Trade Value",
        "method": "polemarch.api.holdings_summary.card_sit_value",
        "color": "#0EA5E9",
    },
    {
        "label": "Polemarch — Investment Value",
        "method": "polemarch.api.holdings_summary.card_investment_value",
        "color": "#10B981",
    },
    {
        "label": "Polemarch — Unrealised Gain",
        "method": "polemarch.api.holdings_summary.card_unrealised_gain",
        "color": "#F59E0B",
    },
]


# Orphans from the first iteration of this patch (which set label to the
# short version and let Frappe append -1 to avoid colliding with ERPNext
# default cards of the same short label).
_ORPHAN_NAMES = [
    "Total Holdings Value-1",
    "Stock in Trade Value-1",
    "Investment Value-1",
    "Unrealised Gain-1",
]


def execute():
    # Cleanup: kill orphans from the first iteration.
    for orphan in _ORPHAN_NAMES:
        if frappe.db.exists("Number Card", orphan):
            frappe.delete_doc("Number Card", orphan, force=1, ignore_permissions=True)

    for spec in CARDS:
        if frappe.db.exists("Number Card", spec["label"]):
            # Already seeded; refresh method/color in case we tweak the spec.
            nc = frappe.get_doc("Number Card", spec["label"])
            nc.type = "Custom"
            nc.method = spec["method"]
            nc.color = spec["color"]
            nc.is_public = 1
            nc.show_percentage_stats = 0
            nc.flags.ignore_permissions = True
            nc.save(ignore_permissions=True)
            continue

        nc = frappe.get_doc({
            "doctype": "Number Card",
            "label": spec["label"],
            "type": "Custom",
            "method": spec["method"],
            "is_public": 1,
            "show_percentage_stats": 0,
            "color": spec["color"],
        })
        nc.flags.ignore_permissions = True
        nc.insert(ignore_permissions=True)

    frappe.db.commit()
