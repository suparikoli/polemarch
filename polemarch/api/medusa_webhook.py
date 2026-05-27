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


def _handle_customer_created(data: dict, event_id: Optional[str] = None) -> dict:
    """Storefront signup → create a Frappe Customer (if it doesn't
    already exist).

    Expected payload (from the Medusa erpnext-forward subscriber's
    full-customer fetchById output):
      id              <medusa customer id>
      email           <required>
      first_name, last_name, phone, company_name
      addresses[0]    <primary address — optional>
      metadata        <dict with optional kyc_pan, client_id>

    The Mithtech-only filter lives on the Medusa SIDE — Mithtech
    customers never reach this endpoint because operator policy
    creates them in ERPNext only. Everything that arrives here is a
    Polemarch customer.

    Idempotency: keyed on email. If a Customer with the same email is
    already linked (via a Contact's `email_id`), we update metadata
    rather than create a duplicate.

    Phase-18 hook (after_insert) auto-creates the placeholder Contact
    + sets customer_primary_contact + back-syncs customer_name from
    first/middle/last — so we just need to pass email + names and
    Frappe handles the rest."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        return {"status": "skipped", "reason": "no_email"}

    medusa_id = data.get("id") or event_id

    # Idempotency: find an existing Customer linked to this email via Contact
    existing_name = _customer_by_email(email)
    if existing_name:
        # Update metadata only — don't reshape an existing operator-managed customer
        updates: dict = {}
        if data.get("metadata", {}).get("client_id") and not frappe.db.get_value(
            "Customer", existing_name, "custom_client_id"
        ):
            updates["custom_client_id"] = data["metadata"]["client_id"]
        if updates:
            frappe.db.set_value("Customer", existing_name, updates, update_modified=False)
            frappe.db.commit()
        return {
            "status": "exists",
            "customer": existing_name,
            "medusa_id": medusa_id,
            "updates": list(updates.keys()),
        }

    # Create fresh Customer. The polemarch.overrides.customer.after_insert
    # hook will auto-create a placeholder Contact and link first_name/email/phone
    # via Dynamic Link. The validate hook will also auto-set
    # custom_is_polemarch_customer based on signals.
    customer_name = _compose_customer_name(data)
    cust_doc = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Polemarch"
            if frappe.db.exists("Customer Group", "Polemarch")
            else _default_customer_group(),
            "territory": _default_territory(),
        }
    )
    cust_doc.flags.ignore_permissions = True
    cust_doc.flags.ignore_mandatory = True
    cust_doc.insert(ignore_permissions=True)

    # Populate the auto-created Contact with email + phone now (the
    # placeholder Contact only has first_name = customer_name).
    _populate_contact(cust_doc.name, data)

    # Stamp the Medusa client id on the Frappe Customer for cross-system lookup
    metadata = data.get("metadata") or {}
    if metadata.get("client_id"):
        frappe.db.set_value(
            "Customer", cust_doc.name, "custom_client_id", metadata["client_id"],
            update_modified=False,
        )
    frappe.db.commit()
    return {
        "status": "created",
        "customer": cust_doc.name,
        "medusa_id": medusa_id,
    }


def _handle_customer_updated(data: dict, event_id: Optional[str] = None) -> dict:
    """Lightweight customer update. Currently syncs the metadata.client_id
    onto custom_client_id if present and previously unset. Heavier
    fields (KYC, addresses) are driven by Medusa→Frappe via the pull
    cron, not the push webhook — pull lets the operator preview and
    approve changes before they hit the Customer ledger."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        return {"status": "skipped", "reason": "no_email"}
    customer = _customer_by_email(email)
    if not customer:
        return {"status": "not_found", "email": email}
    metadata = data.get("metadata") or {}
    updates = {}
    if metadata.get("client_id") and not frappe.db.get_value(
        "Customer", customer, "custom_client_id"
    ):
        updates["custom_client_id"] = metadata["client_id"]
    if updates:
        frappe.db.set_value("Customer", customer, updates, update_modified=False)
        frappe.db.commit()
    return {
        "status": "synced",
        "customer": customer,
        "updates": list(updates.keys()),
    }


def _handle_customer_kyc_synced(data: dict, event_id: Optional[str] = None) -> dict:
    """Medusa-side KYC verification echoes the verified PAN / Aadhaar
    flags back to Frappe. Only updates flags + verified_on — the
    canonical KYC decision (verify / reject) is operator-driven from
    the Frappe side via polemarch.api.kyc. This handler just records
    that Medusa has the latest state."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        return {"status": "skipped", "reason": "no_email"}
    customer = _customer_by_email(email)
    if not customer:
        return {"status": "not_found", "email": email}
    updates = {}
    if data.get("pan_verified") is True:
        # Only stamp PAN onto custom_pan field, not the KYC status —
        # that requires the manual verify_kyc API call.
        pan = (data.get("pan") or "").upper().strip()
        if pan and not frappe.db.get_value("Customer", customer, "pan"):
            updates["pan"] = pan
    if updates:
        frappe.db.set_value("Customer", customer, updates, update_modified=False)
        frappe.db.commit()
    return {
        "status": "synced",
        "customer": customer,
        "updates": list(updates.keys()),
    }


def _handle_product_synced(data: dict, event_id: Optional[str] = None) -> dict:
    """Medusa-side Product → Frappe Security metadata sync.

    Securities are Frappe-managed (the operator decides what's tradable
    via the desk). Medusa derives its Product catalog FROM Frappe via
    the pull cron. So this handler is intentionally narrow — it only
    accepts a back-reference from Medusa once the Product has been
    created on the Medusa side, stamping `medusa_product_id` on the
    matching Security so cross-system lookups work.

    Expected payload:
      {
        isin: <ISIN, matches Security.name>,
        medusa_product_id: <Medusa product id>,
        medusa_variant_id?: <default variant id>,
      }

    Does NOT create Securities — those originate in Frappe. Returns
    'security_not_found' if the ISIN doesn't match an existing
    Security; operator should create it in Frappe first."""
    isin = (data.get("isin") or "").strip().upper()
    medusa_product_id = data.get("medusa_product_id") or ""
    if not isin:
        return {"status": "skipped", "reason": "no_isin"}
    if not frappe.db.exists("Security", isin):
        return {"status": "security_not_found", "isin": isin}
    if not medusa_product_id:
        return {"status": "skipped", "reason": "no_medusa_product_id"}

    # Only stamp the back-ref if Security has the Custom Field for it
    if frappe.db.has_column("Security", "medusa_product_id"):
        current = frappe.db.get_value("Security", isin, "medusa_product_id")
        if current == medusa_product_id:
            return {
                "status": "idempotent_skip",
                "security": isin,
                "medusa_product_id": medusa_product_id,
            }
        frappe.db.set_value(
            "Security", isin, "medusa_product_id", medusa_product_id,
            update_modified=False,
        )
        frappe.db.commit()
    return {
        "status": "synced",
        "security": isin,
        "medusa_product_id": medusa_product_id,
    }


def _handle_order_placed(data: dict, event_id: Optional[str] = None) -> dict:
    """Storefront-placed order → create a Security Sale with source =
    'Platform Purchase'.

    Expected payload shape (sent by the Medusa erpnext-plugin's order
    subscriber after pricing finalisation):
      {
        order_id: <medusa order id>,
        customer: <frappe customer name OR email>,
        security: <ISIN>,
        qty: <int>,
        rate: <currency, per unit>,
        payment_method: 'Customer Wallet' | 'Bank' | 'Cash' | 'Default Receivable',
        from_classification: 'Stock in Trade' | 'Investment',
        discount_amount?: <currency>   # promo-wallet utilisation —
                                        # recorded as a remarks line for
                                        # now; future hardening = post
                                        # a Discount JE alongside.
        posting_date?: <YYYY-MM-DD>
      }
    """
    # Resolve customer — accept either Frappe Customer.name or email
    customer = data.get("customer") or data.get("customer_email") or ""
    if "@" in customer:
        resolved = _customer_by_email(customer.lower())
        if not resolved:
            return {
                "status": "customer_not_found",
                "lookup_email": customer,
                "hint": "Create the Frappe Customer first (or rely on customer.created webhook)",
            }
        customer = resolved
    if not frappe.db.exists("Customer", customer):
        return {"status": "customer_not_found", "customer": customer}

    # Validate the security exists. Storefront should never reference an
    # unknown ISIN, but guard against payload tampering.
    security = data.get("security") or ""
    if not frappe.db.exists("Security", security):
        return {"status": "security_not_found", "security": security}

    medusa_order_id = data.get("order_id") or event_id

    # Idempotency via the medusa_order_id Custom Field
    if medusa_order_id and frappe.db.has_column("Security Sale", "medusa_order_id"):
        existing = frappe.db.get_value(
            "Security Sale",
            {"medusa_order_id": medusa_order_id},
            "name",
        )
        if existing:
            return {
                "status": "idempotent_skip",
                "sale": existing,
                "medusa_order_id": medusa_order_id,
            }

    qty = float(data.get("qty") or 0)
    rate = float(data.get("rate") or 0)
    discount = float(data.get("discount_amount") or 0)
    if qty <= 0 or rate <= 0:
        return {
            "status": "invalid_payload",
            "reason": "qty and rate must be > 0",
            "qty": qty,
            "rate": rate,
        }

    company = frappe.defaults.get_global_default("company")
    remarks_parts = [f"Medusa order {medusa_order_id}"]
    if discount > 0:
        remarks_parts.append(
            f"Promo-wallet discount applied: ₹{discount:,.2f} "
            f"(included in storefront-side pricing; sale.rate reflects net)"
        )

    sale = frappe.get_doc({
        "doctype": "Security Sale",
        "company": company,
        "posting_date": data.get("posting_date") or frappe.utils.today(),
        "security": security,
        "party_type": "Customer",
        "party": customer,
        "from_classification": data.get("from_classification", "Stock in Trade"),
        "qty": qty,
        "rate": rate,
        "payment_method": data.get("payment_method", "Customer Wallet"),
        "source": "Platform Purchase",
        "remarks": " | ".join(remarks_parts),
    })
    if frappe.db.has_column("Security Sale", "medusa_order_id") and medusa_order_id:
        sale.medusa_order_id = medusa_order_id
    sale.flags.ignore_permissions = True
    sale.insert(ignore_permissions=True)
    sale.submit()
    frappe.db.commit()
    return {
        "status": "created",
        "sale": sale.name,
        "medusa_order_id": medusa_order_id,
        "amount": sale.amount,
        "discount_applied": discount if discount > 0 else None,
    }


def _handle_order_canceled(data: dict, event_id: Optional[str] = None) -> dict:
    """Medusa-side order cancellation → cancel the linked Security Sale.

    Triggers the Frappe Security Sale's full cancel cascade (Gap 3 fix):
    wallet reversal → CH snapshot rollback → JEs cancelled → Disposal
    cancelled → qty_disposed_<class> restored. Idempotent: returns
    'already_cancelled' if the Sale is docstatus=2 already, and
    'not_found' if there's no Sale for the given Medusa order id (e.g.
    cancellation before the order.placed webhook completed)."""
    medusa_order_id = data.get("order_id") or event_id
    if not medusa_order_id:
        return {"status": "skipped", "reason": "no_order_id"}

    if not frappe.db.has_column("Security Sale", "medusa_order_id"):
        return {
            "status": "skipped",
            "reason": "medusa_order_id Custom Field not deployed yet",
        }

    sale_name = frappe.db.get_value(
        "Security Sale", {"medusa_order_id": medusa_order_id}, "name"
    )
    if not sale_name:
        return {
            "status": "not_found",
            "medusa_order_id": medusa_order_id,
            "hint": (
                "Sale for this Medusa order id doesn't exist on Frappe — "
                "either order.placed webhook never landed, or the Sale "
                "was already manually deleted."
            ),
        }

    sale = frappe.get_doc("Security Sale", sale_name)
    if sale.docstatus == 2:
        return {
            "status": "already_cancelled",
            "sale": sale_name,
            "medusa_order_id": medusa_order_id,
        }
    if sale.docstatus == 0:
        sale.flags.ignore_permissions = True
        sale.delete(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "draft_deleted",
            "sale": sale_name,
            "medusa_order_id": medusa_order_id,
        }

    sale.flags.ignore_permissions = True
    sale.cancel()
    frappe.db.commit()
    return {
        "status": "cancelled",
        "sale": sale_name,
        "medusa_order_id": medusa_order_id,
    }


def _customer_by_email(email: str) -> Optional[str]:
    """Find a Frappe Customer linked to this email via Contact.Dynamic Link.
    Returns the Customer name or None. Uses the standard ERPNext Contact
    pattern (Phase 16+) — email_id is on Contact, not Customer."""
    rows = frappe.db.sql(
        """
        SELECT dl.link_name
        FROM `tabContact` c
        JOIN `tabContact Email` ce ON ce.parent = c.name
        JOIN `tabDynamic Link` dl ON dl.parent = c.name
        WHERE LOWER(ce.email_id) = %s
          AND dl.link_doctype = 'Customer'
        ORDER BY c.creation ASC
        LIMIT 1
        """,
        (email,),
    )
    return rows[0][0] if rows else None


def _compose_customer_name(data: dict) -> str:
    """Compose the Customer's display name from Medusa first/last/email.
    Falls back to email-localpart, then to the Medusa id."""
    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    if first or last:
        return " ".join(p for p in (first, last) if p)
    email = data.get("email") or ""
    if "@" in email:
        return email.split("@", 1)[0]
    return data.get("id") or "Unnamed Customer"


def _populate_contact(customer_name: str, data: dict):
    """The polemarch.overrides.customer.after_insert hook creates a
    placeholder Contact with first_name = customer_name. Populate the
    real fields (first/last/email/phone) on that Contact so the
    Customer's Phase-16 sync hook can back-fill customer_name from
    full_name correctly."""
    primary_contact = frappe.db.get_value(
        "Customer", customer_name, "customer_primary_contact"
    )
    if not primary_contact or not frappe.db.exists("Contact", primary_contact):
        return
    doc = frappe.get_doc("Contact", primary_contact)
    if data.get("first_name"):
        doc.first_name = data["first_name"]
    if data.get("last_name"):
        doc.last_name = data["last_name"]
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    if email and not any(e.email_id == email for e in (doc.email_ids or [])):
        doc.append("email_ids", {"email_id": email, "is_primary": 1})
    if phone and not any(p.phone == phone for p in (doc.phone_nos or [])):
        doc.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)


def _default_customer_group() -> str:
    return (
        frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        or "All Customer Groups"
    )


def _default_territory() -> str:
    return (
        frappe.db.get_value("Territory", {"is_group": 0}, "name")
        or "All Territories"
    )


_DISPATCH: dict[str, Any] = {
    "ping": _handle_ping,
    "wallet.deposit.captured": _handle_wallet_deposit,
    "wallet.withdrawal.posted": _handle_wallet_withdrawal,
    # Backwards-compat: customer.synced now aliases customer.updated
    "customer.synced": _handle_customer_updated,
    "customer.created": _handle_customer_created,
    "customer.updated": _handle_customer_updated,
    "customer.kyc.synced": _handle_customer_kyc_synced,
    "product.synced": _handle_product_synced,
    "order.placed": _handle_order_placed,
    "order.canceled": _handle_order_canceled,
    "order.cancelled": _handle_order_canceled,  # British spelling, same handler
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
