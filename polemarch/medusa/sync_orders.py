from datetime import date, timedelta

import frappe

from polemarch.install import POLEMARCH_BRAND
from polemarch.medusa.client import MedusaError, get_client
from polemarch.medusa.log import write_log
from polemarch.medusa.sync_customers import upsert_from_medusa


def handle_order_placed(data: dict, event_id: str = None):
    order = data.get("order") or data
    medusa_order_id = order.get("id")
    customer_name = _ensure_customer(order)
    if not customer_name:
        return None

    existing = frappe.db.get_value("Sales Order", {"custom_medusa_order_id": medusa_order_id}, "name")
    if existing:
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Order",
            event="order.placed",
            event_id=event_id,
            status="Skipped",
            erpnext_doctype="Sales Order",
            erpnext_ref=existing,
            medusa_id=medusa_order_id,
            payload=data,
            error="Sales Order already exists for this Medusa order",
        )
        return existing

    items = _build_so_items(order.get("items") or [])
    if not items:
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Order",
            event="order.placed",
            event_id=event_id,
            status="Failed",
            medusa_id=medusa_order_id,
            payload=data,
            error="No matching ERPNext Items for line items",
        )
        return None

    company = _default_company()
    so = frappe.new_doc("Sales Order")
    so.customer = customer_name
    so.company = company
    so.transaction_date = date.today()
    so.delivery_date = date.today() + timedelta(days=2)
    so.po_no = order.get("display_id") or medusa_order_id
    so.custom_medusa_order_id = medusa_order_id
    for line in items:
        so.append("items", line)
    so.flags.ignore_permissions = True
    so.flags.from_medusa_sync = True
    so.insert(ignore_permissions=True)
    if (order.get("payment_status") or "") == "captured":
        so.submit()
    frappe.db.commit()

    write_log(
        direction="Medusa to ERPNext",
        entity_type="Order",
        event="order.placed",
        event_id=event_id,
        status="Success",
        erpnext_doctype="Sales Order",
        erpnext_ref=so.name,
        medusa_id=medusa_order_id,
        payload=data,
    )
    return so.name


def handle_payment_captured(data: dict, event_id: str = None):
    order = data.get("order") or data
    medusa_order_id = order.get("id")
    so_name = frappe.db.get_value("Sales Order", {"custom_medusa_order_id": medusa_order_id}, "name")
    if not so_name:
        so_name = handle_order_placed(data, event_id=event_id)
        if not so_name:
            return None

    so = frappe.get_doc("Sales Order", so_name)
    if so.docstatus == 0:
        so.submit()

    existing_invoice = frappe.db.get_value("Sales Invoice", {"custom_medusa_order_id": medusa_order_id}, "name")
    if existing_invoice:
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Order",
            event="order.payment_captured",
            event_id=event_id,
            status="Skipped",
            erpnext_doctype="Sales Invoice",
            erpnext_ref=existing_invoice,
            medusa_id=medusa_order_id,
            payload=data,
            error="Invoice already exists",
        )
        return existing_invoice

    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    invoice = make_sales_invoice(so.name)
    invoice.custom_medusa_order_id = medusa_order_id
    invoice.flags.ignore_permissions = True
    invoice.flags.from_medusa_sync = True
    invoice.insert(ignore_permissions=True)
    invoice.submit()

    fee_invoice_name = _maybe_book_processing_fee(order, so, medusa_order_id, event_id)

    _create_payment_entry(invoice, order)
    frappe.db.commit()

    write_log(
        direction="Medusa to ERPNext",
        entity_type="Order",
        event="order.payment_captured",
        event_id=event_id,
        status="Success",
        erpnext_doctype="Sales Invoice",
        erpnext_ref=invoice.name,
        medusa_id=medusa_order_id,
        payload={"primary": invoice.name, "fee": fee_invoice_name},
    )
    return invoice.name


def _maybe_book_processing_fee(order: dict, so, medusa_order_id: str, event_id: str = None):
    """Detect a processing fee on the Medusa order and book it as a
    SECOND, GST-bearing Sales Invoice under Mithtech Services. Returns
    the new invoice name or None."""
    settings = frappe.get_cached_doc("Medusa Settings")
    metadata_key = (settings.get("processing_fee_metadata_key") or "").strip()
    fee_item_code = settings.get("processing_fee_item_code")
    if not metadata_key or not fee_item_code:
        return None
    if not frappe.db.exists("Item", fee_item_code):
        return None

    fee_paise = (order.get("metadata") or {}).get(metadata_key)
    try:
        fee_paise = float(fee_paise or 0)
    except (TypeError, ValueError):
        return None
    if fee_paise <= 0:
        return None
    fee_amount = fee_paise / 100

    if frappe.db.exists("Sales Invoice", {
        "custom_medusa_order_id": medusa_order_id,
        "is_pos": 0,
        "items.item_code": fee_item_code,
    }):
        return None

    fee_si = frappe.new_doc("Sales Invoice")
    fee_si.customer = so.customer
    fee_si.company = so.company
    fee_si.posting_date = so.transaction_date
    fee_si.due_date = so.transaction_date
    fee_si.po_no = so.po_no
    fee_si.custom_medusa_order_id = medusa_order_id
    fee_si.append("items", {"item_code": fee_item_code, "qty": 1, "rate": fee_amount})

    # No explicit template needed — the processing-fee item carries
    # HSN 997152 + brand=Mithtech Services. ERPNext + India Compliance
    # apply the company's standard Output GST template based on the
    # customer's place_of_supply (intra/inter-state) and compute 18%
    # automatically. Earlier versions forced a polemarch-app-defined
    # `Mithtech Services - GST 18%` template here; that was removed in
    # favour of the Item-Tax-Template approach (see
    # `polemarch.install._create_polemarch_non_gst_item_tax_template`).
    fee_si.flags.ignore_permissions = True
    fee_si.flags.from_medusa_sync = True
    fee_si.insert(ignore_permissions=True)
    fee_si.submit()

    write_log(
        direction="Medusa to ERPNext",
        entity_type="Order",
        event="order.processing_fee",
        event_id=f"{event_id}:fee" if event_id else None,
        status="Success",
        erpnext_doctype="Sales Invoice",
        erpnext_ref=fee_si.name,
        medusa_id=medusa_order_id,
        payload={"fee_amount": fee_amount, "metadata_key": metadata_key},
    )
    _ = company_abbr
    return fee_si.name


def handle_fulfillment_created(data: dict, event_id: str = None):
    order = data.get("order") or data
    medusa_order_id = order.get("id")
    write_log(
        direction="Medusa to ERPNext",
        entity_type="Order",
        event="order.fulfillment_created",
        event_id=event_id,
        status="Success",
        medusa_id=medusa_order_id,
        payload=data,
    )
    return medusa_order_id


def handle_order_canceled(data: dict, event_id: str = None):
    order = data.get("order") or data
    medusa_order_id = order.get("id")
    invoice_name = frappe.db.get_value("Sales Invoice", {"custom_medusa_order_id": medusa_order_id}, "name")
    so_name = frappe.db.get_value("Sales Order", {"custom_medusa_order_id": medusa_order_id}, "name")
    for doctype, name in (("Sales Invoice", invoice_name), ("Sales Order", so_name)):
        if not name:
            continue
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.flags.ignore_permissions = True
            doc.cancel()
    frappe.db.commit()
    write_log(
        direction="Medusa to ERPNext",
        entity_type="Order",
        event="order.canceled",
        event_id=event_id,
        status="Success",
        medusa_id=medusa_order_id,
        payload=data,
    )
    return medusa_order_id


def mirror_status(invoice_name: str, new_status: str):
    client = get_client()
    if not client:
        return
    medusa_order_id = frappe.db.get_value("Sales Invoice", invoice_name, "custom_medusa_order_id")
    if not medusa_order_id:
        return
    payload = {"metadata": {"erpnext_status": new_status}}
    try:
        response = client.post(f"/admin/orders/{medusa_order_id}", json_body=payload,
                                idempotency_key=f"order-status:{invoice_name}:{new_status}")
    except MedusaError as exc:
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Order",
            event=f"order.status:{new_status}",
            status="Failed",
            erpnext_doctype="Sales Invoice",
            erpnext_ref=invoice_name,
            medusa_id=medusa_order_id,
            error=str(exc),
        )
        return
    write_log(
        direction="ERPNext to Medusa",
        entity_type="Order",
        event=f"order.status:{new_status}",
        status="Success",
        erpnext_doctype="Sales Invoice",
        erpnext_ref=invoice_name,
        medusa_id=medusa_order_id,
        response=response,
    )


def _ensure_customer(order: dict) -> str:
    customer_payload = order.get("customer") or {}
    medusa_customer_id = customer_payload.get("id") or order.get("customer_id")
    if medusa_customer_id:
        existing = frappe.db.get_value("Customer", {"custom_medusa_customer_id": medusa_customer_id}, "name")
        if existing:
            return existing
    if customer_payload:
        return upsert_from_medusa({"customer": customer_payload}, event="customer.created")
    if order.get("email"):
        return upsert_from_medusa({"customer": {"email": order.get("email")}}, event="customer.created")
    return None


def _build_so_items(lines: list) -> list:
    rows = []
    for line in lines:
        item_code = _resolve_item_code(line)
        if not item_code:
            continue
        if frappe.db.get_value("Item", item_code, "brand") != POLEMARCH_BRAND:
            continue
        unit_amount = (line.get("unit_price") or 0) / 100
        rows.append({
            "item_code": item_code,
            "qty": line.get("quantity") or 1,
            "rate": unit_amount,
        })
    return rows


def _resolve_item_code(line: dict):
    variant = line.get("variant") or {}
    sku = variant.get("sku") or line.get("variant_sku")
    if sku and frappe.db.exists("Item", sku):
        return sku
    product = variant.get("product") or {}
    handle = product.get("handle")
    if handle:
        item = frappe.db.get_value("Item", {"item_code": handle}, "name")
        if item:
            return item
    medusa_product_id = product.get("id")
    if medusa_product_id:
        return frappe.db.get_value("Item", {"custom_medusa_product_id": medusa_product_id}, "name")
    return None


def _create_payment_entry(invoice, order: dict):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry("Sales Invoice", invoice.name)
    pe.reference_no = order.get("display_id") or order.get("id") or invoice.name
    pe.reference_date = date.today()
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)
    pe.submit()


def _default_company() -> str:
    return frappe.defaults.get_global_default("company") or frappe.db.get_single_value("Global Defaults", "default_company")
