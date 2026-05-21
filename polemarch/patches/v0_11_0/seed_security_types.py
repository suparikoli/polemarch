"""Phase 11 — seed initial Security Type records.

Before Phase 11, Security.security_type was a Select with hard-coded
options (Equity, Preference, Bond, Debenture, SGB, Warrant). The new
schema makes it a Link to the standalone `Security Type` doctype so
operators can add new types from the UI without a schema change.

Seed all the legacy Select options as Security Type records (so existing
Security rows keep resolving cleanly) plus the new default `Unlisted
Shares` — Polemarch's primary instrument class.

Idempotent.
"""

import frappe


_SEED_TYPES = [
    # (type_name, description)
    ("Unlisted Shares",
     "Equity in a private/pre-IPO company; not traded on a public exchange."),
    ("Listed Equity",
     "Equity in a publicly listed company."),
    ("Equity",
     "Generic equity instrument (legacy bucket)."),
    ("Preference",
     "Preference share — priority dividend, typically non-voting."),
    ("Bond",
     "Fixed-income instrument issued by a company or government."),
    ("Debenture",
     "Unsecured debt instrument."),
    ("SGB",
     "Sovereign Gold Bond — government-issued gold-linked bond."),
    ("Warrant",
     "Option to buy/sell underlying security at a future date."),
]


def execute():
    if not frappe.db.exists("DocType", "Security Type"):
        # Pre-Phase-11 sites — the doctype hasn't synced yet, so we can't
        # insert records. Bail; the post-sync run of this patch on the
        # next migrate will pick it up.
        return

    for type_name, description in _SEED_TYPES:
        if frappe.db.exists("Security Type", type_name):
            continue
        frappe.get_doc({
            "doctype": "Security Type",
            "type_name": type_name,
            "description": description,
        }).insert(ignore_permissions=True)

    frappe.db.commit()
