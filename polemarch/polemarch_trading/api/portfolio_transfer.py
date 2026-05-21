"""Portfolio Transfer REST endpoints (Phase 4).

Workflow:
  initiate → approve → post           (happy path)
  initiate → reject                    (denied; resubmittable as Draft)

All endpoints role-gated. `approve` requires `Polemarch Compliance Officer`
AND a user different from `requested_by` (no self-approval).
"""

from typing import Optional

import frappe
from frappe import _


_INITIATOR_ROLES = ["System Manager", "Accounts Manager", "Polemarch Trader"]
_APPROVER_ROLES = ["System Manager", "Polemarch Compliance Officer"]


@frappe.whitelist()
def initiate(
    from_portfolio: str,
    to_portfolio: str,
    security: str,
    qty: float,
    fmv_per_unit: float,
    reason: str,
    fmv_source: str = "Last Traded Price",
    valuation_reference: Optional[str] = None,
    transfer_date: Optional[str] = None,
    reversal_of: Optional[str] = None,
):
    frappe.only_for(_INITIATOR_ROLES, message=_("Not allowed to initiate Portfolio Transfers."))

    doc = frappe.get_doc(
        {
            "doctype": "Portfolio Transfer",
            "from_portfolio": from_portfolio,
            "to_portfolio": to_portfolio,
            "security": security,
            "qty": qty,
            "fmv_per_unit": fmv_per_unit,
            "fmv_source": fmv_source,
            "valuation_reference": valuation_reference,
            "transfer_date": transfer_date,
            "reversal_of": reversal_of,
            "reason": reason,
            "requested_by": frappe.session.user,
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()  # moves Draft → Pending Approval
    return doc.as_dict()


@frappe.whitelist()
def approve(name: str):
    frappe.only_for(_APPROVER_ROLES, message=_("Not allowed to approve Portfolio Transfers."))
    pt = frappe.get_doc("Portfolio Transfer", name)
    pt.transition_to("Approved", actor=frappe.session.user)
    return pt.as_dict()


@frappe.whitelist()
def reject(name: str, reason: str):
    frappe.only_for(_APPROVER_ROLES, message=_("Not allowed to reject Portfolio Transfers."))
    if not reason or not reason.strip():
        frappe.throw(_("A reason is required to reject a Portfolio Transfer."), title=_("Reason Required"))
    pt = frappe.get_doc("Portfolio Transfer", name)
    pt.transition_to("Rejected", actor=frappe.session.user, reason=reason)
    return pt.as_dict()


@frappe.whitelist()
def post(name: str):
    """Drive Approved → Posted via the engine. Writes SLLEs, creates new lots,
    posts JE atomically (advisory locks on both portfolios)."""
    frappe.only_for(
        ["System Manager", "Accounts Manager", "Polemarch Compliance Officer"],
        message=_("Not allowed to post Portfolio Transfers."),
    )
    from polemarch.polemarch_trading import portfolio_transfer as pt_engine

    pt_engine.post_transfer(name)
    return frappe.get_doc("Portfolio Transfer", name).as_dict()


@frappe.whitelist()
def list_pending():
    frappe.only_for(
        ["System Manager", "Accounts Manager", "Polemarch Compliance Officer"],
        message=_("Not allowed to view pending Portfolio Transfers."),
    )
    return frappe.get_all(
        "Portfolio Transfer",
        filters={
            "transfer_state": ["in", ["Pending Approval", "Approved"]],
            "docstatus": ["!=", 2],
        },
        fields=[
            "name",
            "transfer_date",
            "from_portfolio",
            "to_portfolio",
            "security",
            "qty",
            "fmv_per_unit",
            "total_fmv",
            "transfer_state",
            "requested_by",
            "approved_by",
            "reason",
        ],
        order_by="modified DESC",
        limit_page_length=200,
    )
