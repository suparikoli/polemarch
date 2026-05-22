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


_OPEN_STATUS_FILTER = (
    '[["Investment Holding","status","in",["Open","Partially Disposed"]],'
    '["Investment Holding","qty_remaining",">",0]'
)

# Sum(Investment Holding.remaining_cost) is what gives us "book value of
# currently-held qty". Frappe v16's workspace renderer happily draws
# Document Type cards but not Custom-method cards — so we use this path
# even though we ALSO have a polemarch.api.holdings_summary endpoint for
# the same values (still used by the LCM table + Security form panel).
CARDS = [
    {
        "label": "Polemarch - Total Holdings Value",
        "document_type": "Investment Holding",
        "function": "Sum",
        "aggregate_function_based_on": "remaining_cost",
        "filters_json": _OPEN_STATUS_FILTER + "]",
        "color": "#4F46E5",
    },
    {
        "label": "Polemarch - Stock in Trade Value",
        "document_type": "Investment Holding",
        "function": "Sum",
        "aggregate_function_based_on": "remaining_cost",
        "filters_json": _OPEN_STATUS_FILTER + ',["Investment Holding","classification","=","Stock in Trade"]]',
        "color": "#0EA5E9",
    },
    {
        "label": "Polemarch - Investment Value",
        "document_type": "Investment Holding",
        "function": "Sum",
        "aggregate_function_based_on": "remaining_cost",
        "filters_json": _OPEN_STATUS_FILTER + ',["Investment Holding","classification","=","Investment"]]',
        "color": "#10B981",
    },
    {
        "label": "Polemarch - Unclassified Value",
        "document_type": "Investment Holding",
        "function": "Sum",
        "aggregate_function_based_on": "remaining_cost",
        "filters_json": _OPEN_STATUS_FILTER + ',["Investment Holding","classification","=","Unallocated"]]',
        "color": "#9CA3AF",
    },
    {
        "label": "Polemarch - Open Lots",
        "document_type": "Investment Holding",
        "function": "Count",
        "filters_json": _OPEN_STATUS_FILTER + "]",
        "color": "#F59E0B",
    },
]


# Orphans from older iterations of this patch:
#   - First iteration set label to the short version → Frappe appended -1
#     to dodge collisions with stock ERPNext "Total Assets" / "Annual Sales"
#     style cards
#   - Second iteration used Unicode em-dash + type=Custom; those cards
#     didn't render on the v16 workspace, replaced by ASCII-dash +
#     type=Document Type variants below.
_ORPHAN_NAMES = [
    "Total Holdings Value-1",
    "Stock in Trade Value-1",
    "Investment Value-1",
    "Unrealised Gain-1",
    "Polemarch — Total Holdings Value",
    "Polemarch — Stock in Trade Value",
    "Polemarch — Investment Value",
    "Polemarch — Unclassified Value",
    "Polemarch — Unrealised Gain",
]


def execute():
    # Cleanup: kill orphans from the first iteration.
    for orphan in _ORPHAN_NAMES:
        if frappe.db.exists("Number Card", orphan):
            frappe.delete_doc("Number Card", orphan, force=1, ignore_permissions=True)

    for spec in CARDS:
        # Build the doc payload from the spec, treating Document Type as
        # canonical (Frappe v16's workspace renderer draws these).
        payload = {
            "doctype": "Number Card",
            "label": spec["label"],
            "type": "Document Type",
            "document_type": spec["document_type"],
            "function": spec["function"],
            "filters_json": spec["filters_json"],
            "is_public": 1,
            "show_percentage_stats": 0,
            "color": spec["color"],
        }
        if spec.get("aggregate_function_based_on"):
            payload["aggregate_function_based_on"] = spec["aggregate_function_based_on"]

        if frappe.db.exists("Number Card", spec["label"]):
            nc = frappe.get_doc("Number Card", spec["label"])
            for k, v in payload.items():
                if k != "doctype":
                    setattr(nc, k, v)
            nc.flags.ignore_permissions = True
            nc.save(ignore_permissions=True)
        else:
            nc = frappe.get_doc(payload)
            nc.flags.ignore_permissions = True
            nc.insert(ignore_permissions=True)

    frappe.db.commit()
