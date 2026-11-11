"""Carry the Mithtech-only tick along the CRM funnel.

Lead -> Opportunity / Prospect is copied by ERPNext's mappers (the CRM
fields are not no_copy). These hooks cover the paths the mappers miss:
Customer's tick is no_copy, and an Opportunity or Prospect can be made
by hand for an existing party.
"""

import frappe

FLAG = "custom_is_mithtech_only"
CUSTOMER_SOURCES = (("lead_name", "Lead"), ("opportunity_name", "Opportunity"), ("prospect_name", "Prospect"))
OPPORTUNITY_PARTIES = ("Lead", "Customer", "Prospect")


def _flag(doctype, name):
    return bool(name) and bool(frappe.db.get_value(doctype, name, FLAG))


def customer_before_insert(doc, method=None):
    if not doc.get(FLAG) and any(_flag(dt, doc.get(f)) for f, dt in CUSTOMER_SOURCES):
        doc.set(FLAG, 1)


def opportunity_before_insert(doc, method=None):
    if not doc.get(FLAG) and doc.opportunity_from in OPPORTUNITY_PARTIES and _flag(doc.opportunity_from, doc.party_name):
        doc.set(FLAG, 1)


def prospect_before_insert(doc, method=None):
    if not doc.get(FLAG) and any(_flag("Lead", row.lead) for row in doc.get("leads") or []):
        doc.set(FLAG, 1)
