import frappe

from polemarch.install import POLEMARCH_BRAND
from polemarch.medusa.client import MedusaError, get_client
from polemarch.medusa.log import write_log


def push_item(item_name: str):
    client = get_client()
    if not client:
        return
    item = frappe.get_doc("Item", item_name)
    if item.brand != POLEMARCH_BRAND:
        return
    settings = client.settings
    payload = _item_to_product(item, settings)

    medusa_id = item.get("custom_medusa_product_id")
    try:
        if medusa_id:
            response = client.post(f"/admin/products/{medusa_id}", json_body=payload,
                                    idempotency_key=_idempotency_key("item-update", item))
        else:
            response = client.post("/admin/products", json_body=payload,
                                    idempotency_key=_idempotency_key("item-create", item))
            new_id = (response.get("product") or {}).get("id")
            if new_id:
                frappe.db.set_value("Item", item.name, "custom_medusa_product_id", new_id, update_modified=False)
                medusa_id = new_id
    except MedusaError as exc:
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Item",
            event="item.update" if medusa_id else "item.create",
            status="Failed",
            erpnext_doctype="Item",
            erpnext_ref=item.name,
            payload=payload,
            error=str(exc),
        )
        raise

    write_log(
        direction="ERPNext to Medusa",
        entity_type="Item",
        event="item.update" if medusa_id else "item.create",
        status="Success",
        erpnext_doctype="Item",
        erpnext_ref=item.name,
        medusa_id=medusa_id,
        payload=payload,
        response=response,
    )


def delete_item(medusa_product_id: str):
    client = get_client()
    if not client:
        return
    try:
        response = client.delete(f"/admin/products/{medusa_product_id}")
    except MedusaError as exc:
        write_log(
            direction="ERPNext to Medusa",
            entity_type="Item",
            event="item.delete",
            status="Failed",
            medusa_id=medusa_product_id,
            error=str(exc),
        )
        return
    write_log(
        direction="ERPNext to Medusa",
        entity_type="Item",
        event="item.delete",
        status="Success",
        medusa_id=medusa_product_id,
        response=response,
    )


def _item_to_product(item, settings) -> dict:
    # Securities (Polemarch items) are out of GST scope under Schedule III
    # and carry no HSN — only the processing fee (HSN 997152) does, and
    # that's calculated by a Medusa custom module, not stored on Items.
    metadata = {
        "erpnext_item_code": item.item_code,
        "isin": item.get("custom_isin"),
        "rta": item.get("custom_rta"),
        "last_traded_price": item.get("custom_last_traded_price"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    payload = {
        "title": item.item_name or item.item_code,
        "handle": frappe.scrub(item.item_code).replace("_", "-"),
        "description": item.description or "",
        "status": "published" if not item.disabled else "draft",
        "metadata": metadata,
    }
    if item.image:
        payload["thumbnail"] = item.image

    price = _resolve_price(item, settings)
    payload["variants"] = [
        {
            "title": "Default",
            "sku": item.item_code,
            "manage_inventory": False,
            "prices": [{"amount": int(round(price * 100)), "currency_code": (settings.default_currency or "INR").lower()}],
        }
    ]
    if settings.default_sales_channel_id:
        payload["sales_channels"] = [{"id": settings.default_sales_channel_id}]
    return payload


def _resolve_price(item, settings) -> float:
    if item.get("custom_last_traded_price"):
        return float(item.custom_last_traded_price)
    if settings.default_price_list:
        rate = frappe.db.get_value(
            "Item Price",
            {"item_code": item.item_code, "price_list": settings.default_price_list},
            "price_list_rate",
        )
        if rate:
            return float(rate)
    return float(item.standard_rate or 0)


def _idempotency_key(prefix: str, item) -> str:
    return f"{prefix}:{item.name}:{item.modified}"
