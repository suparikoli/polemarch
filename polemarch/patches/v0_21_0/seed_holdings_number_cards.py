"""v0_21_0 — seed 4 Number Cards backing the Polemarch workspace.

Each card calls a whitelisted method in
`polemarch.api.holdings_summary` that returns
{value, fieldtype: "Currency"} so the card renders with ₹ prefix.

Idempotent: looks up by name, only inserts when missing. Re-running the
patch on a site that already has the cards is a no-op.
"""

import frappe


CARDS = [
    {
        "name": "Polemarch — Total Holdings Value",
        "label": "Total Holdings Value",
        "method": "polemarch.api.holdings_summary.card_total_holdings_value",
        "color": "#4F46E5",
    },
    {
        "name": "Polemarch — Stock in Trade Value",
        "label": "Stock in Trade Value",
        "method": "polemarch.api.holdings_summary.card_sit_value",
        "color": "#0EA5E9",
    },
    {
        "name": "Polemarch — Investment Value",
        "label": "Investment Value",
        "method": "polemarch.api.holdings_summary.card_investment_value",
        "color": "#10B981",
    },
    {
        "name": "Polemarch — Unrealised Gain",
        "label": "Unrealised Gain",
        "method": "polemarch.api.holdings_summary.card_unrealised_gain",
        "color": "#F59E0B",
    },
]


def execute():
    for spec in CARDS:
        if frappe.db.exists("Number Card", spec["name"]):
            # Already seeded; refresh the method/colour in case we tweak the spec.
            nc = frappe.get_doc("Number Card", spec["name"])
            nc.type = "Custom"
            nc.method = spec["method"]
            nc.label = spec["label"]
            nc.color = spec["color"]
            nc.is_public = 1
            nc.show_percentage_stats = 0
            nc.flags.ignore_permissions = True
            nc.save(ignore_permissions=True)
            continue

        nc = frappe.get_doc({
            "doctype": "Number Card",
            "name": spec["name"],
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
