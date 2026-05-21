"""Settlement Instruction REST endpoints (Phase 2)."""

from typing import Optional

import frappe
from frappe import _


_OPS_ROLES = ["System Manager", "Polemarch Settlement Officer", "Accounts Manager"]


@frappe.whitelist()
def list_pending(customer: Optional[str] = None, before_date: Optional[str] = None):
    frappe.only_for(_OPS_ROLES, message=_("Not allowed to view settlement queue."))
    filters = {"settlement_state": "Pending", "docstatus": 1}
    if customer:
        filters["customer"] = customer
    if before_date:
        filters["expected_settlement_date"] = ["<=", before_date]

    return frappe.get_all(
        "Settlement Instruction",
        filters=filters,
        fields=[
            "name",
            "trade_order",
            "side",
            "security",
            "customer",
            "qty",
            "net_amount",
            "expected_settlement_date",
            "settlement_state",
        ],
        order_by="expected_settlement_date ASC, creation ASC",
        limit_page_length=200,
    )


@frappe.whitelist()
def fund(name: str):
    frappe.only_for(_OPS_ROLES, message=_("Not allowed to fund settlements."))
    from polemarch.polemarch_trading import settlement as engine

    engine.fund(name)
    return frappe.get_doc("Settlement Instruction", name).as_dict()


@frappe.whitelist()
def clear(name: str, dp_transfer_ref: Optional[str] = None, seller_bank_payout_ref: Optional[str] = None):
    frappe.only_for(_OPS_ROLES, message=_("Not allowed to clear settlements."))
    from polemarch.polemarch_trading import settlement as engine

    engine.clear(name, dp_transfer_ref=dp_transfer_ref, seller_bank_payout_ref=seller_bank_payout_ref)
    return frappe.get_doc("Settlement Instruction", name).as_dict()


@frappe.whitelist()
def fail(name: str, reason: str):
    frappe.only_for(_OPS_ROLES, message=_("Not allowed to fail settlements."))
    if not reason or not reason.strip():
        frappe.throw(_("A reason is required to fail a settlement."), title=_("Reason Required"))
    from polemarch.polemarch_trading import settlement as engine

    engine.fail(name, reason=reason)
    return frappe.get_doc("Settlement Instruction", name).as_dict()
