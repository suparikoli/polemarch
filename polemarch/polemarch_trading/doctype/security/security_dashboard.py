"""Sidebar dashboard for the Security doctype.

When you open a Security in Desk, this drives the "Connections" panel —
counts of linked docs and quick-jump filters. Frappe matches the
linked-doctype's `fieldname` against the Security.name automatically.

Investment Holding and Investment Disposal aren't listed here directly
because their `security` field is a Custom Field (added in v0_9_0) — the
dashboard machinery reads the standard `fields` list off the doctype JSON
and doesn't pick up Custom Fields by default. They show up via
`non_standard_fieldnames` below.
"""

from frappe import _


def get_data():
    return {
        "fieldname": "security",
        "non_standard_fieldnames": {
            # Custom Fields keyed back to Security
            "Investment Holding": "security",
            "Investment Disposal": "security",
        },
        "transactions": [
            {
                "label": _("Trading"),
                "items": [
                    "Security Purchase",
                    "Security Sale",
                ],
            },
            {
                "label": _("Inventory"),
                "items": [
                    "Investment Holding",
                ],
            },
            {
                "label": _("Disposals"),
                "items": [
                    "Investment Disposal",
                ],
            },
            {
                "label": _("Transfers"),
                "items": [
                    "Portfolio Transfer",
                ],
            },
        ],
    }
