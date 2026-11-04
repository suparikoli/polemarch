"""Whitelisted endpoints behind the Security form's Passbook and
Counterparties tabs. Read-only; see `polemarch_trading.passbook`."""

import frappe

from polemarch.polemarch_trading import passbook


@frappe.whitelist()
def get_security_passbook(security: str) -> dict:
    passbook.check_read_permission()
    return passbook.get_security_passbook(security)


@frappe.whitelist()
def get_security_counterparties(security: str) -> list:
    passbook.check_read_permission()
    return passbook.get_counterparties(passbook.get_entries(security=security))
