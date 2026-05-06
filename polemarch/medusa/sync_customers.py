import frappe

from polemarch.install import POLEMARCH_CUSTOMER_GROUP
from polemarch.medusa.client import MedusaError, get_client
from polemarch.medusa.log import write_log


def push_customer(customer_name: str):
    client = get_client()
    if not client:
        return
    customer = frappe.get_doc("Customer", customer_name)
    if not customer.custom_is_polemarch_customer:
        return

    payload = _customer_to_payload(customer)
    medusa_id = customer.get("custom_medusa_customer_id")

    try:
        if medusa_id:
            response = client.post(f"/admin/customers/{medusa_id}", json_body=payload,
                                    idempotency_key=f"customer-update:{customer.name}:{customer.modified}")
        else:
            existing = _find_medusa_customer_by_email(client, payload.get("email"))
            if existing:
                medusa_id = existing.get("id")
                response = client.post(f"/admin/customers/{medusa_id}", json_body=payload,
                                        idempotency_key=f"customer-update:{customer.name}:{customer.modified}")
            else:
                response = client.post("/admin/customers", json_body=payload,
                                        idempotency_key=f"customer-create:{customer.name}")
                medusa_id = (response.get("customer") or {}).get("id")
            if medusa_id:
                frappe.db.set_value("Customer", customer.name, "custom_medusa_customer_id", medusa_id, update_modified=False)
    except MedusaError as exc:
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Customer",
            event="customer.update" if medusa_id else "customer.create",
            status="Failed",
            erpnext_doctype="Customer",
            erpnext_ref=customer.name,
            payload=payload,
            error=str(exc),
        )
        raise

    write_log(
        direction="ERPNext to Medusa",
        entity_type="Customer",
        event="customer.update" if medusa_id else "customer.create",
        status="Success",
        erpnext_doctype="Customer",
        erpnext_ref=customer.name,
        medusa_id=medusa_id,
        payload=payload,
        response=response,
    )


def upsert_from_medusa(data: dict, *, event: str, event_id: str = None):
    customer_data = data.get("customer") or data
    medusa_id = customer_data.get("id")
    email = customer_data.get("email")
    if not email:
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Customer",
            event=event,
            event_id=event_id,
            status="Skipped",
            payload=data,
            error="No email on Medusa customer payload",
        )
        return None

    existing = frappe.db.get_value("Customer", {"custom_medusa_customer_id": medusa_id}, "name") if medusa_id else None
    if not existing and email:
        existing = frappe.db.get_value("Customer", {"email_id": email}, "name")

    full_name = " ".join(filter(None, [customer_data.get("first_name"), customer_data.get("last_name")])) or email

    if existing:
        doc = frappe.get_doc("Customer", existing)
        doc.customer_name = full_name
        doc.email_id = email
        if customer_data.get("phone"):
            doc.mobile_no = customer_data.get("phone")
        if not doc.custom_medusa_customer_id and medusa_id:
            doc.custom_medusa_customer_id = medusa_id
        if doc.customer_group != POLEMARCH_CUSTOMER_GROUP:
            doc.customer_group = POLEMARCH_CUSTOMER_GROUP
        doc.flags.ignore_permissions = True
        doc.flags.from_medusa_sync = True
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.new_doc("Customer")
        doc.customer_name = full_name
        doc.customer_type = "Individual"
        doc.customer_group = POLEMARCH_CUSTOMER_GROUP
        doc.territory = "India"
        doc.email_id = email
        doc.mobile_no = customer_data.get("phone")
        doc.custom_medusa_customer_id = medusa_id
        doc.flags.ignore_permissions = True
        doc.flags.from_medusa_sync = True
        doc.insert(ignore_permissions=True)

    frappe.db.commit()

    write_log(
        direction="Medusa to ERPNext",
        entity_type="Customer",
        event=event,
        event_id=event_id,
        status="Success",
        erpnext_doctype="Customer",
        erpnext_ref=doc.name,
        medusa_id=medusa_id,
        payload=data,
    )
    return doc.name


def _customer_to_payload(customer) -> dict:
    full_name = customer.customer_name or ""
    parts = full_name.split(" ", 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    primary_dp = next(iter(customer.get("custom_dp_details") or []), None)
    metadata = {
        "erpnext_customer_name": customer.name,
        "pan": customer.get("pan"),
    }
    kyc_map = {"Verified": "verified", "Rejected": "rejected", "In Review": "submitted", "Not Started": "pending"}
    erp_kyc = customer.get("custom_kyc_status") or "Not Started"
    metadata["kyc_status"] = kyc_map.get(erp_kyc, "pending")
    if erp_kyc == "Rejected" and customer.get("custom_kyc_status_reason"):
        metadata["kyc_rejection_reason"] = customer.custom_kyc_status_reason
    if primary_dp:
        metadata.update({"dp_id": primary_dp.dp_id, "client_id": primary_dp.client_id, "bo_id": primary_dp.bo_id})
    metadata = {k: v for k, v in metadata.items() if v is not None}
    return {
        "first_name": first,
        "last_name": last,
        "email": customer.email_id,
        "phone": customer.mobile_no,
        "metadata": metadata,
    }


def _find_medusa_customer_by_email(client, email):
    if not email:
        return None
    try:
        resp = client.get("/admin/customers", params={"q": email, "limit": 1})
    except MedusaError:
        return None
    customers = resp.get("customers") or []
    return customers[0] if customers else None
