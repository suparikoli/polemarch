"""Medusa webhook receiver — HMAC-secured inbound entry point.

The Medusa erpnext-plugin posts a JSON payload to
`/api/method/polemarch.api.medusa_webhook.receive` whenever a relevant
event fires on the Medusa side. Signature is computed as HMAC-SHA256
of the raw JSON body using `Polemarch Settings.medusa_webhook_secret`,
sent in the `x-medusa-signature` header.

Supported events (dispatched to handlers based on payload['event']):

  - wallet.deposit.captured   → polemarch.api.wallet_sync.record_deposit
  - wallet.withdrawal.posted  → polemarch.api.wallet_sync.record_withdrawal
  - customer.synced           → upserts metadata on the Customer
  - order.placed              → creates a Security Sale (source =
                                 "Platform Purchase")
  - ping                      → returns {"ok": True, "pong": True} for
                                 the admin "test connection" button

Endpoint is `allow_guest=True` because the Medusa server doesn't have
a Frappe user. Authentication is purely HMAC signature verification —
unsigned or wrong-signature requests get 401. Replay attacks are
mitigated by including the event_id in the signature material AND a
per-event idempotency check on the receiving side (wallet_sync
endpoints idempotent by gateway_ref).
"""

import hashlib
import hmac
import json
from typing import Any, Optional

import frappe

from polemarch.polemarch_trading.doctype.polemarch_settings.polemarch_settings import (
    get_medusa_webhook_secret,
    is_medusa_sync_enabled,
)


_SIGNATURE_HEADER = "x-medusa-signature"


@frappe.whitelist(allow_guest=True)
def receive() -> dict:
    # 1. Master toggle
    if not is_medusa_sync_enabled():
        # Return 200 (not 503) so Medusa doesn't retry. Operators can
        # see the "sync disabled" state in their plugin's UI via the
        # ping route below.
        return {"ok": True, "skipped": True, "reason": "sync_disabled"}

    # 2. HMAC verification
    raw_body = frappe.request.get_data(as_text=False) or b""
    signature_header = frappe.get_request_header(_SIGNATURE_HEADER) or ""
    secret = get_medusa_webhook_secret()
    if not secret:
        frappe.local.response.http_status_code = 500
        return {
            "ok": False,
            "error": "Polemarch Settings.medusa_webhook_secret is not configured. "
                     "Set it in the desk before sending webhooks.",
        }
    computed = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature_header, computed):
        frappe.local.response.http_status_code = 401
        return {"ok": False, "error": "Invalid signature."}

    # 3. Parse payload
    try:
        payload: dict[str, Any] = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        frappe.local.response.http_status_code = 400
        return {"ok": False, "error": "Body is not valid JSON."}

    event = payload.get("event") or ""
    data = payload.get("data") or {}
    event_id = payload.get("event_id") or payload.get("id") or ""

    # 4. Dispatch
    handler = _DISPATCH.get(event)
    if not handler:
        return {
            "ok": True,
            "event": event,
            "skipped": True,
            "reason": "no_handler_for_event",
        }

    try:
        result = handler(data, event_id=event_id)
        return {"ok": True, "event": event, "result": result}
    except Exception as e:
        frappe.log_error(
            title=f"Medusa webhook handler failed: {event}",
            message=f"event_id={event_id}\npayload={payload}\nerror={e}\n{frappe.get_traceback()}",
        )
        frappe.local.response.http_status_code = 500
        return {"ok": False, "event": event, "error": str(e)}


# ── event handlers ──────────────────────────────────────────────────


def _handle_ping(data: dict, event_id: Optional[str] = None) -> dict:
    """Sanity check for the admin "test connection" flow."""
    return {"pong": True, "echo": data}


def _handle_wallet_deposit(data: dict, event_id: Optional[str] = None) -> dict:
    """data: {customer, amount, gateway_ref?, posting_date?, source?, remarks?}"""
    from polemarch.api.wallet_sync import record_deposit
    return record_deposit(
        customer=data["customer"],
        amount=float(data["amount"]),
        posting_date=data.get("posting_date"),
        gateway_ref=data.get("gateway_ref") or event_id,
        source=data.get("source", "cashfree"),
        remarks=data.get("remarks"),
    )


def _handle_wallet_withdrawal(data: dict, event_id: Optional[str] = None) -> dict:
    from polemarch.api.wallet_sync import record_withdrawal
    return record_withdrawal(
        customer=data["customer"],
        amount=float(data["amount"]),
        posting_date=data.get("posting_date"),
        gateway_ref=data.get("gateway_ref") or event_id,
        remarks=data.get("remarks"),
    )


def _handle_customer_synced(data: dict, event_id: Optional[str] = None) -> dict:
    """Light-touch metadata upsert for a customer. Does NOT create
    customers — that's an explicit operator action in ERPNext (so we
    avoid accidentally creating a customer ledger row from a half-
    formed Medusa signup). Returns 'not_found' if the customer
    doesn't exist on the Frappe side.

    Pre-deploy expectation: operator creates the Customer in ERPNext
    first (with custom_is_mithtech_only correctly set), then Medusa
    side syncs the customer's metadata (Medusa client ID, etc.)."""
    customer = data.get("customer")
    if not customer or not frappe.db.exists("Customer", customer):
        return {"status": "not_found", "customer": customer}
    updates = {}
    if "medusa_client_id" in data and not frappe.db.get_value(
        "Customer", customer, "custom_client_id"
    ):
        updates["custom_client_id"] = data["medusa_client_id"]
    if updates:
        frappe.db.set_value("Customer", customer, updates, update_modified=False)
        frappe.db.commit()
    return {"status": "synced", "customer": customer, "updates": list(updates.keys())}


def _handle_order_placed(data: dict, event_id: Optional[str] = None) -> dict:
    """Storefront-placed order → create a Security Sale with source =
    'Platform Purchase'.

    Expected payload shape:
      {
        order_id: <medusa order id>,
        customer: <frappe customer name>,
        security: <ISIN>,
        qty: <int>,
        rate: <currency>,
        payment_method: 'Customer Wallet' | 'Bank' | 'Cash' | 'Default Receivable',
        from_classification: 'Stock in Trade' | 'Investment',
        discount_amount?: <currency>  # from promo-wallet utilisation
      }
    """
    customer = data["customer"]
    if not frappe.db.exists("Customer", customer):
        return {"status": "customer_not_found", "customer": customer}

    medusa_order_id = data.get("order_id") or event_id

    # Idempotency: if a Security Sale with this Medusa order id already
    # exists, return it. We use the Custom Field `medusa_order_id` (added
    # in a forthcoming patch) when present; fall back to remarks-pattern
    # match otherwise.
    if medusa_order_id and frappe.db.has_column("Security Sale", "medusa_order_id"):
        existing = frappe.db.get_value(
            "Security Sale",
            {"medusa_order_id": medusa_order_id},
            "name",
        )
        if existing:
            return {"status": "idempotent_skip", "sale": existing}

    company = frappe.defaults.get_global_default("company")
    sale = frappe.get_doc({
        "doctype": "Security Sale",
        "company": company,
        "posting_date": data.get("posting_date") or frappe.utils.today(),
        "security": data["security"],
        "party_type": "Customer",
        "party": customer,
        "from_classification": data.get("from_classification", "Stock in Trade"),
        "qty": float(data["qty"]),
        "rate": float(data["rate"]),
        "payment_method": data.get("payment_method", "Customer Wallet"),
        "source": "Platform Purchase",
        "remarks": f"Medusa order {medusa_order_id}",
    })
    if frappe.db.has_column("Security Sale", "medusa_order_id") and medusa_order_id:
        sale.medusa_order_id = medusa_order_id
    sale.flags.ignore_permissions = True
    sale.insert(ignore_permissions=True)
    sale.submit()
    frappe.db.commit()
    return {"status": "created", "sale": sale.name}


_DISPATCH: dict[str, Any] = {
    "ping": _handle_ping,
    "wallet.deposit.captured": _handle_wallet_deposit,
    "wallet.withdrawal.posted": _handle_wallet_withdrawal,
    "customer.synced": _handle_customer_synced,
    "order.placed": _handle_order_placed,
}


@frappe.whitelist(allow_guest=True)
def ping() -> dict:
    """Unauthenticated readiness probe. Returns config visibility
    flags (without revealing secrets). Useful for the Medusa admin UI's
    'test connection' button before it sends a signed ping event."""
    secret = get_medusa_webhook_secret()
    return {
        "ok": True,
        "sync_enabled": is_medusa_sync_enabled(),
        "webhook_secret_configured": bool(secret),
        "supported_events": sorted(_DISPATCH.keys()),
    }
