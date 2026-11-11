"""Mithtech-only tick on Lead / Opportunity / Prospect / Customer.

bench --site polemarch-test.localhost run-tests --app polemarch \
    --module polemarch.tests.test_mithtech_marking
"""

import frappe
from erpnext.crm.doctype.lead.lead import make_customer, make_opportunity
from frappe.tests import IntegrationTestCase

FLAG = "custom_is_mithtech_only"


def _lead(name, mithtech):
    return frappe.get_doc(
        {"doctype": "Lead", "first_name": name, "company_name": f"{name} Pvt Ltd", FLAG: 1 if mithtech else 0}
    ).insert()


class TestMithtechMarking(IntegrationTestCase):
    def test_fields_exist(self):
        for dt in ("Lead", "Opportunity", "Prospect", "Customer"):
            self.assertTrue(frappe.get_meta(dt).has_field(FLAG), dt)

    def test_lead_to_customer_keeps_tick(self):
        lead = _lead("TEST Mith Cust", True)
        cust = make_customer(lead.name)
        cust.insert()
        self.assertEqual(cust.lead_name, lead.name)
        self.assertEqual(cust.get(FLAG), 1)

    def test_lead_to_opportunity_keeps_tick(self):
        for mithtech in (True, False):
            lead = _lead(f"TEST Mith Opp {int(mithtech)}", mithtech)
            opp = make_opportunity(lead.name)
            opp.insert()
            self.assertEqual(bool(opp.get(FLAG)), mithtech)

    def test_manual_opportunity_takes_party_tick(self):
        lead = _lead("TEST Mith Manual", True)
        opp = frappe.get_doc(
            {"doctype": "Opportunity", "opportunity_from": "Lead", "party_name": lead.name}
        ).insert()
        self.assertEqual(opp.get(FLAG), 1)

    def test_lead_to_prospect_keeps_tick(self):
        # ERPNext's Lead.create_prospect builds the Prospect by hand (no
        # mapper), so the tick comes from prospect_before_insert.
        lead = _lead("TEST Mith Prospect", True)
        company = f"TEST Mith Prospect Co {frappe.generate_hash(length=6)}"
        lead.create_prospect(company)
        self.assertEqual(frappe.db.get_value("Prospect", company, FLAG), 1)
