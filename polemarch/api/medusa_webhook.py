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

    # HMAC already authenticated the caller — switch the session user
    # to Administrator for the duration of the dispatch so downstream
    # doc-event hooks (Contact sync on Customer save, etc.) can read /
    # write linked doctypes without tripping on Guest-role checks. The
    # `frappe.db.set_value` + `.save(ignore_permissions=True)` calls
    # in the handlers themselves stay best-practice — this is the
    # belt-and-braces for hooks that don't honour the flag.
    prev_user = frappe.session.user
    frappe.set_user("Administrator")
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
    finally:
        frappe.set_user(prev_user)


@frappe.whitelist(allow_guest=True)
def receive_mapped() -> dict:
    """Counterpart of `receive()` for the canonical-mapping push path
    (Medusa erpnext-plugin → `pushViaMapping`). The two endpoints exist
    side-by-side: `receive` handles the legacy full-payload envelope
    used by `forwardEvent`, while `receive_mapped` handles the per-
    mapping per-doctype envelope produced by `applyMapping`.

    Body shape:
      {
        "event":        "customer.created" | "customer.updated" | ...,
        "id":           <unique event id — `<medusa-event-id>:<mapping-id>`>,
        "mapping_id":   <erpnext_mapping.id on the Medusa side>,
        "mapping_name": <"Customer ↔ Customer", etc.>,
        "doctype":      "Customer",
        "key_field":    "email_id",      // erpnext-side identity column
        "key_value":    "user@x.com",    // value to look up
        "payload":      { <erpnext_field>: <transformed value>, ... }
      }

    HMAC verification, sync-disabled gate, and JSON parsing match
    `receive()` exactly. The dispatch differs: instead of a fixed
    `event → handler` table, the doctype + key_field decide the
    upsert path. Customer gets the after_insert Contact wiring,
    Security gets `medusa_originated`-style stamping, every other
    doctype gets the generic `frappe.db.exists` + `set_value` /
    `frappe.new_doc.insert` path.

    Idempotency: re-posting the same `id` is harmless because (a) the
    upsert by key is naturally idempotent (same payload → no diff),
    and (b) ERPNext's modified-timestamp doesn't change on set_value
    when the values are unchanged. Re-creates are prevented by the
    `if existing_name` branch.

    Returns:
      {ok, doctype, name, status: "created" | "updated" | "skipped",
       skipped_fields?: [...], mapping_name}
    """
    if not is_medusa_sync_enabled():
        return {"ok": True, "skipped": True, "reason": "sync_disabled"}

    raw_body = frappe.request.get_data(as_text=False) or b""
    signature_header = frappe.get_request_header(_SIGNATURE_HEADER) or ""
    secret = get_medusa_webhook_secret()
    if not secret:
        frappe.local.response.http_status_code = 500
        return {
            "ok": False,
            "error": "Polemarch Settings.medusa_webhook_secret is not configured.",
        }
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature_header, computed):
        frappe.local.response.http_status_code = 401
        return {"ok": False, "error": "Invalid signature."}

    try:
        envelope: dict[str, Any] = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        frappe.local.response.http_status_code = 400
        return {"ok": False, "error": "Body is not valid JSON."}

    event = envelope.get("event") or ""
    event_id = envelope.get("id") or ""
    doctype = (envelope.get("doctype") or "").strip()
    key_field = (envelope.get("key_field") or "").strip()
    key_value = envelope.get("key_value")
    payload = envelope.get("payload") or {}
    mapping_name = envelope.get("mapping_name") or "(unnamed)"

    if not doctype:
        frappe.local.response.http_status_code = 400
        return {"ok": False, "error": "Missing `doctype`."}
    if not key_field or key_value in (None, ""):
        # Frappe upserts need an identity. Missing key = the Medusa
        # source row didn't have the field the mapping points at —
        # that's a mapping config issue, not a transient error.
        return {
            "ok": True,
            "skipped": True,
            "reason": "missing_key_value",
            "doctype": doctype,
            "key_field": key_field,
        }

    # Same Administrator-switch pattern as `receive()` — HMAC is the
    # auth boundary; once it passes, hooks that read/write linked
    # doctypes (e.g. Contact sync on Customer save) need full
    # permissions.
    prev_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        result = _upsert_via_mapping(
            doctype=doctype,
            key_field=key_field,
            key_value=key_value,
            payload=payload,
            event=event,
            event_id=event_id,
        )
        frappe.db.commit()
        result["ok"] = True
        result["mapping_name"] = mapping_name
        return result
    except Exception as e:
        frappe.log_error(
            title=f"Medusa receive_mapped failed: {mapping_name} ({event})",
            message=(
                f"event_id={event_id}\nenvelope={envelope}\n"
                f"error={e}\n{frappe.get_traceback()}"
            ),
        )
        frappe.local.response.http_status_code = 500
        return {"ok": False, "event": event, "error": str(e)}
    finally:
        frappe.set_user(prev_user)


def _upsert_via_mapping(
    doctype: str,
    key_field: str,
    key_value: Any,
    payload: dict,
    event: str,
    event_id: str,
) -> dict:
    """Doctype-aware upsert for `receive_mapped`. Each branch handles
    the per-doctype quirks (Customer's Contact wiring, etc.) and then
    delegates back to a shared scalar-only `_set_doctype_fields` helper
    so the field-write logic stays in one place."""
    if doctype == "Customer":
        return _upsert_mapped_customer(
            key_field=key_field,
            key_value=str(key_value),
            payload=payload,
        )
    # Generic fallback — applies to Security, Wallet Deposit, etc. that
    # don't need custom child-doc wiring.
    existing_name = frappe.db.get_value(
        doctype, {key_field: key_value}, "name"
    )
    if existing_name:
        _set_doctype_fields(doctype, existing_name, payload)
        return {
            "doctype": doctype,
            "name": existing_name,
            "status": "updated",
        }
    new_doc = frappe.get_doc({"doctype": doctype, **payload})
    new_doc.flags.ignore_permissions = True
    new_doc.flags.ignore_mandatory = True
    new_doc.insert(ignore_permissions=True)
    return {
        "doctype": doctype,
        "name": new_doc.name,
        "status": "created",
    }


def _upsert_mapped_customer(
    key_field: str, key_value: str, payload: dict
) -> dict:
    """Customer-specific upsert. Identity is by Contact email (Phase
    16+), so when `key_field == "email_id"` we route through the
    `_customer_by_email` helper instead of querying the Customer
    table directly. Existing customers get a column-level set_value;
    new customers go through the after_insert hook chain (Contact
    placeholder + customer_name back-sync) and then have their
    payload-driven Customer columns set in a second pass."""
    email_lower = key_value.strip().lower()
    existing_name: Optional[str] = None
    if key_field == "email_id":
        existing_name = _customer_by_email(email_lower)
    else:
        existing_name = frappe.db.get_value("Customer", {key_field: key_value}, "name")

    # Scalar columns that exist on Customer go through set_value.
    # `email_id` and `mobile_no` are also Customer columns (ERPNext
    # syncs them from the primary Contact, but the column is writeable
    # too), so they live in the same set as the custom_* fields.
    if existing_name:
        _set_doctype_fields("Customer", existing_name, payload)
        return {
            "doctype": "Customer",
            "name": existing_name,
            "status": "updated",
        }

    # New Customer — synthesise the mandatory fields the after_insert
    # hook needs. customer_name might already be in the payload (from
    # `metadata.pan_registered_name` → customer_name in the canonical
    # mapping); fall back to email-localpart if it isn't.
    customer_name = (
        payload.get("customer_name")
        or (email_lower.split("@", 1)[0] if "@" in email_lower else email_lower)
    )
    cust_doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": (
            "Polemarch"
            if frappe.db.exists("Customer Group", "Polemarch")
            else _default_customer_group()
        ),
        "territory": _default_territory(),
    })
    cust_doc.flags.ignore_permissions = True
    cust_doc.flags.ignore_mandatory = True
    cust_doc.insert(ignore_permissions=True)

    # Wire up the placeholder Contact created by polemarch.overrides.
    # customer.after_insert with the email/phone from the payload so
    # `_customer_by_email` finds this customer on the next push.
    _populate_contact(
        cust_doc.name,
        {
            "first_name": customer_name.split(" ", 1)[0],
            "last_name": (
                customer_name.split(" ", 1)[1]
                if " " in customer_name
                else ""
            ),
            "email": payload.get("email_id") or email_lower,
            "phone": payload.get("mobile_no"),
        },
    )

    # Set any remaining payload columns on the new Customer row.
    # `customer_name` is already on the doc; everything else (email_id,
    # mobile_no, pan, custom_*) gets a single set_value pass.
    _set_doctype_fields("Customer", cust_doc.name, payload)
    return {
        "doctype": "Customer",
        "name": cust_doc.name,
        "status": "created",
    }


def _set_doctype_fields(doctype: str, name: str, payload: dict) -> None:
    """Write the payload's scalar fields to an existing doc via
    `frappe.db.set_value`. Filters out non-existent columns so a stale
    canonical mapping (referencing a field the operator hasn't seeded
    yet) doesn't blow up the whole push — those fields simply skip.
    `customer_name` is always preserved on Customer (the field IS
    writeable, but blocking accidental overwrites of operator-edited
    legal names is more important — we only set it on first insert).

    Datetime values arrive from Medusa as ISO 8601 ("2026-05-28T07:21:
    04.555Z"); MariaDB's datetime columns want "YYYY-MM-DD HH:MM:SS"
    and reject the ISO form with error 1292. We use Frappe's standard
    `get_datetime` helper to coerce — it accepts ISO, Python datetime,
    epoch, and the MariaDB form, then returns a datetime object that
    set_value writes correctly.
    """
    if not payload:
        return
    meta = frappe.get_meta(doctype)
    datetime_fields: set[str] = {
        f.fieldname
        for f in meta.fields
        if (f.fieldtype or "") in ("Date", "Datetime")
    }
    updates: dict = {}
    for fieldname, value in payload.items():
        if not frappe.db.has_column(doctype, fieldname):
            continue
        # Customer.customer_name is set at insert time and intentionally
        # NOT clobbered on updates — operators may have edited it to
        # match a court-corrected PAN name etc.
        if doctype == "Customer" and fieldname == "customer_name":
            continue
        # Datetime coercion — ISO 8601 strings (what Medusa emits) need
        # to become Python datetime / Frappe's expected format. Pass
        # None through untouched so `clear_value` semantics work.
        if (
            fieldname in datetime_fields
            and value
            and isinstance(value, str)
        ):
            try:
                value = frappe.utils.get_datetime(value)
            except Exception:
                # If parsing fails, leave the original — Frappe will
                # surface a clear error rather than silently dropping.
                pass
        updates[fieldname] = value
    if updates:
        frappe.db.set_value(doctype, name, updates, update_modified=True)


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
    """Customer update — top-level metadata sync PLUS bank/demat child
    table upsert when the payload includes those arrays.

    The bank/demat sync was wired in Phase 189 (Bank + BOID sync,
    Medusa→Frappe). The Medusa-side erpnext-forward subscriber listens
    to bank_account.verified / demat_account.verified events and
    rewrites them to customer.updated on the wire, with the enriched
    customer payload carrying:

      bank_accounts: [
        {bank_name, ifsc, account_number_last4, account_holder_name,
         is_primary, verification_status, ...}
      ]
      demat_accounts: [
        {depository, dp_id, client_id, boid, dp_name,
         account_holder_name, is_primary, verification_status, ...}
      ]

    Each Medusa row is upserted into the Customer's `custom_bank_
    details` / `custom_dp_details` child tables, keyed by:
      - Bank: (ifsc, account_number_last4)
      - Demat: (bo_id)

    Unverified Medusa rows are skipped (only `verification_status ==
    'verified'` rows land on Frappe — the operator workspace shouldn't
    show pending rows).
    """
    email = (data.get("email") or "").strip().lower()
    if not email:
        return {"status": "skipped", "reason": "no_email"}
    customer = _customer_by_email(email)
    if not customer:
        return {"status": "not_found", "email": email}

    metadata = data.get("metadata") or {}
    top_level_updates = {}
    if metadata.get("client_id") and not frappe.db.get_value(
        "Customer", customer, "custom_client_id"
    ):
        top_level_updates["custom_client_id"] = metadata["client_id"]
    if top_level_updates:
        frappe.db.set_value(
            "Customer", customer, top_level_updates, update_modified=False
        )

    # ── Bank + Demat child-row upserts ──
    bank_synced = _sync_bank_accounts(
        customer, data.get("bank_accounts") or []
    )
    demat_synced = _sync_demat_accounts(
        customer, data.get("demat_accounts") or []
    )

    frappe.db.commit()
    return {
        "status": "synced",
        "customer": customer,
        "updates": list(top_level_updates.keys()),
        "bank_accounts": bank_synced,
        "demat_accounts": demat_synced,
    }


def _sync_bank_accounts(customer_name: str, rows: list) -> dict:
    """Upsert Medusa-verified banks into the Customer's
    custom_bank_details child table.

    Match key is (IFSC, last4). The Medusa side now decrypts the
    `account_number_encrypted` column server-side (via the wallet
    module's `listBankAccountsForSync` helper) and passes the FULL
    account number on `row.account_number`; we use that for
    `ac_number` so Frappe operators see the real number rather than
    just "1485". The `last4` field is kept around purely as the
    stable match key — full account numbers can be re-issued or
    masked differently between systems, but (IFSC, last4) is enough
    to identify a row without false positives.

    For backward compatibility (older payloads that only carry
    last4), we fall back to last4 in `ac_number` if `account_number`
    isn't present.
    """
    if not rows:
        return {"upserted": 0, "skipped": 0, "removed_unverified": 0}
    customer_doc = frappe.get_doc("Customer", customer_name)
    existing_by_key = {
        (b.bank_code or "", (b.ac_number or "")[-4:]): b
        for b in (customer_doc.get("custom_bank_details") or [])
    }
    upserted = 0
    skipped = 0
    for row in rows:
        if (row.get("verification_status") or "") != "verified":
            skipped += 1
            continue
        ifsc = (row.get("ifsc") or "").upper()
        last4 = row.get("account_number_last4") or ""
        # Prefer the full decrypted account number — fall back to
        # last4 only if the wallet helper couldn't decrypt (key
        # rotation skew, etc.) or the payload predates the helper.
        full_number = row.get("account_number") or last4
        key = (ifsc, last4)
        existing = existing_by_key.get(key)
        payload = {
            "bank_name": row.get("bank_name") or "",
            "bank_code": ifsc,
            "ac_number": full_number,
            "account_holder": row.get("account_holder_name") or "",
            "is_primary": 1 if row.get("is_primary") else 0,
            "cheque_image": row.get("bank_proof_file_url") or "",
        }
        if existing:
            for k, v in payload.items():
                existing.set(k, v)
        else:
            customer_doc.append("custom_bank_details", payload)
        upserted += 1
    if upserted or skipped:
        customer_doc.flags.ignore_permissions = True
        customer_doc.save()
    return {"upserted": upserted, "skipped": skipped}


def _sync_demat_accounts(customer_name: str, rows: list) -> dict:
    """Upsert Medusa-verified demats into the Customer's
    custom_dp_details child table. Key = bo_id (BOID is unique per
    depository — for CDSL it's the 16-digit number; for NSDL it's
    dp_id + client_id concatenated)."""
    if not rows:
        return {"upserted": 0, "skipped": 0}
    customer_doc = frappe.get_doc("Customer", customer_name)
    existing_by_key = {
        (b.bo_id or ""): b
        for b in (customer_doc.get("custom_dp_details") or [])
    }
    upserted = 0
    skipped = 0
    for row in rows:
        if (row.get("verification_status") or "") != "verified":
            skipped += 1
            continue
        depository = (row.get("depository") or "").upper()
        # CDSL: bo_id is the 16-digit BOID. The Medusa side typically
        # stores it monolithically in `boid` with `dp_id` + `client_id`
        # empty — but Frappe's DP Details child requires both fields.
        # Convention: first 8 digits = DP ID, last 8 digits = Client
        # ID. Split here so the child row passes mandatory validation.
        # NSDL: dp_id + client_id arrive populated; combine for bo_id.
        if depository == "CDSL":
            bo_id = row.get("boid") or ""
            dp_id = row.get("dp_id") or ""
            client_id = row.get("client_id") or ""
            if bo_id and len(bo_id) == 16 and not (dp_id and client_id):
                dp_id = bo_id[:8]
                client_id = bo_id[8:]
        else:
            dp_id = row.get("dp_id") or ""
            client_id = row.get("client_id") or ""
            bo_id = f"{dp_id}{client_id}"
        if not bo_id:
            skipped += 1
            continue
        existing = existing_by_key.get(bo_id)
        payload = {
            "dp_id": dp_id,
            "client_id": client_id,
            "bo_id": bo_id,
            "depository": depository,
            "dp_name": row.get("dp_name") or "",
            "primary_bo_name": row.get("account_holder_name") or "",
            "is_primary": 1 if row.get("is_primary") else 0,
            "cmr_copy": row.get("cmr_file_url") or "",
        }
        if existing:
            for k, v in payload.items():
                existing.set(k, v)
        else:
            customer_doc.append("custom_dp_details", payload)
        upserted += 1
    if upserted or skipped:
        customer_doc.flags.ignore_permissions = True
        customer_doc.save()
    return {"upserted": upserted, "skipped": skipped}


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
