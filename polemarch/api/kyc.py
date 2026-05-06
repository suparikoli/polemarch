"""KYC verification actions on Polemarch customers.

Verify / reject the KYC submission, write an audit comment, optionally
push status back to Medusa metadata so the storefront UI reflects it.
The Medusa storefront's KYC tab reads `customer.metadata.kyc_status`.
"""

import frappe
from frappe import _


VALID_STATES = {"Verified", "Rejected", "In Review", "Not Started"}


@frappe.whitelist()
def verify_kyc(customer: str):
    return _set_status(customer, "Verified")


@frappe.whitelist()
def reject_kyc(customer: str, reason: str = ""):
    if not (reason or "").strip():
        frappe.throw(_("Please provide a reason for rejection."))
    return _set_status(customer, "Rejected", reason=reason)


@frappe.whitelist()
def mark_in_review(customer: str):
    return _set_status(customer, "In Review")


def _set_status(customer: str, status: str, reason: str = ""):
    frappe.only_for(["System Manager", "Polemarch KYC Approver", "Sales Manager"])
    if status not in VALID_STATES:
        frappe.throw(_("Unknown status {0}.").format(status))
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} not found.").format(customer))

    doc = frappe.get_doc("Customer", customer)
    previous = doc.get("custom_kyc_status") or "Not Started"
    doc.custom_kyc_status = status
    doc.custom_kyc_status_reason = reason if status == "Rejected" else ""
    doc.custom_kyc_verified_on = frappe.utils.now_datetime() if status == "Verified" else None
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)

    doc.add_comment(
        "Workflow",
        _("KYC status: {0} → {1}{2}").format(
            previous, status, f" ({reason})" if reason else ""
        ),
    )
    frappe.db.commit()

    _send_email(doc, status, reason)
    _push_to_medusa(doc, status, reason)

    return {"ok": True, "status": status}


def _send_email(customer_doc, status: str, reason: str):
    email = customer_doc.get("email_id")
    if not email:
        return
    if status == "Verified":
        subject = _("Your Polemarch KYC has been verified")
        body = _(
            "Hi {0},<br><br>Your KYC has been verified. You can now place trades on Polemarch.<br><br>— Polemarch"
        ).format(customer_doc.customer_name)
    elif status == "Rejected":
        subject = _("Your Polemarch KYC needs attention")
        body = _(
            "Hi {0},<br><br>Your KYC submission was returned with the note:<br><blockquote>{1}</blockquote>"
            "Please update your details and resubmit.<br><br>— Polemarch"
        ).format(customer_doc.customer_name, reason)
    else:
        return
    try:
        frappe.sendmail(recipients=[email], subject=subject, message=body, now=False)
    except Exception:
        frappe.log_error(title="Polemarch KYC email failed", message=frappe.get_traceback())


def _push_to_medusa(customer_doc, status: str, reason: str):
    if not customer_doc.get("custom_medusa_customer_id"):
        return
    settings = frappe.get_cached_doc("Medusa Settings")
    if not settings.enable_sync:
        return

    from polemarch.medusa.client import MedusaError, get_client
    from polemarch.medusa.log import write_log

    medusa_status = {"Verified": "verified", "Rejected": "rejected", "In Review": "submitted"}.get(status)
    if not medusa_status:
        return

    payload = {"metadata": {"kyc_status": medusa_status}}
    if status == "Rejected" and reason:
        payload["metadata"]["kyc_rejection_reason"] = reason

    client = get_client()
    if not client:
        return
    try:
        response = client.post(
            f"/admin/customers/{customer_doc.custom_medusa_customer_id}",
            json_body=payload,
            idempotency_key=f"kyc-status:{customer_doc.name}:{status}:{customer_doc.modified}",
        )
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Customer",
            event=f"customer.kyc:{medusa_status}",
            status="Success",
            erpnext_doctype="Customer",
            erpnext_ref=customer_doc.name,
            medusa_id=customer_doc.custom_medusa_customer_id,
            payload=payload,
            response=response,
        )
    except MedusaError as exc:
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Customer",
            event=f"customer.kyc:{medusa_status}",
            status="Failed",
            erpnext_doctype="Customer",
            erpnext_ref=customer_doc.name,
            medusa_id=customer_doc.custom_medusa_customer_id,
            payload=payload,
            error=str(exc),
        )
