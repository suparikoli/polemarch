import hashlib
import hmac
import json

import frappe
from werkzeug.wrappers import Response

from polemarch.medusa.log import write_log


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
    raw = frappe.request.get_data() or b""
    signature = frappe.get_request_header("x-medusa-signature") or ""
    settings = frappe.get_single("Medusa Settings")
    if not settings.enable_sync:
        return _resp(200, {"ok": True, "skipped": "sync disabled"})
    secret = settings.get_password("medusa_webhook_secret", raise_exception=False)
    if not _verify(raw, signature, secret):
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Other",
            status="Failed",
            error="HMAC verification failed",
            payload=raw.decode("utf-8", errors="replace"),
        )
        return _resp(401, {"ok": False, "error": "invalid signature"})

    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return _resp(400, {"ok": False, "error": "invalid json"})

    event = body.get("event") or ""
    data = body.get("data") or {}
    event_id = (
        frappe.get_request_header("x-medusa-event-id")
        or body.get("id")
        or _hash(raw)
    )

    if _already_handled(event_id):
        return _resp(200, {"ok": True, "deduped": True})

    handler = EVENT_DISPATCH.get(event)
    if not handler:
        write_log(
            direction="Medusa to ERPNext",
            entity_type="Other",
            event=event,
            event_id=event_id,
            status="Skipped",
            payload=body,
        )
        return _resp(200, {"ok": True, "skipped": event})

    try:
        result = handler(data, event_id=event_id)
    except Exception as exc:
        write_log(
            direction="Medusa to ERPNext",
            entity_type=_entity_for_event(event),
            event=event,
            event_id=event_id,
            status="Failed",
            payload=body,
            error=frappe.get_traceback(),
        )
        return _resp(500, {"ok": False, "error": str(exc)})

    return _resp(200, {"ok": True, "result": result})


def _verify(raw: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:32]


def _already_handled(event_id: str) -> bool:
    if not event_id:
        return False
    return bool(
        frappe.db.exists(
            "Medusa Sync Log",
            {"event_id": event_id, "status": "Success", "direction": "Medusa to ERPNext"},
        )
    )


def _entity_for_event(event: str) -> str:
    if event.startswith("customer."):
        return "Customer"
    if event.startswith("product."):
        return "Item"
    if event.startswith("order."):
        return "Order"
    return "Other"


def _resp(status: int, body: dict):
    frappe.response["http_status_code"] = status
    return body


def _handle_customer_created(data, event_id=None):
    from polemarch.medusa.sync_customers import upsert_from_medusa

    return upsert_from_medusa(data, event="customer.created", event_id=event_id)


def _handle_customer_updated(data, event_id=None):
    from polemarch.medusa.sync_customers import upsert_from_medusa

    return upsert_from_medusa(data, event="customer.updated", event_id=event_id)


def _handle_order_placed(data, event_id=None):
    from polemarch.medusa.sync_orders import handle_order_placed

    return handle_order_placed(data, event_id=event_id)


def _handle_payment_captured(data, event_id=None):
    from polemarch.medusa.sync_orders import handle_payment_captured

    return handle_payment_captured(data, event_id=event_id)


def _handle_fulfillment_created(data, event_id=None):
    from polemarch.medusa.sync_orders import handle_fulfillment_created

    return handle_fulfillment_created(data, event_id=event_id)


def _handle_order_canceled(data, event_id=None):
    from polemarch.medusa.sync_orders import handle_order_canceled

    return handle_order_canceled(data, event_id=event_id)


EVENT_DISPATCH = {
    "customer.created": _handle_customer_created,
    "customer.updated": _handle_customer_updated,
    "order.placed": _handle_order_placed,
    "order.payment_captured": _handle_payment_captured,
    "order.fulfillment_created": _handle_fulfillment_created,
    "order.canceled": _handle_order_canceled,
}
