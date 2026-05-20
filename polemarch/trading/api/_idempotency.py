"""Idempotency-key + audit-log helpers for whitelisted APIs.

Usage in an endpoint:

    @frappe.whitelist()
    def withdraw(customer, amount, idempotency_key=None, ...):
        with _idempotency.scope("polemarch.trading.api.wallet.withdraw",
                                 idempotency_key, locals()) as ctx:
            if ctx.replay:
                return ctx.replay   # cached prior response
            response = _do_withdraw(customer, amount)
            ctx.store(response)
            return response

The context manager handles three concerns:
  1. Replay detection: same key + same payload → return cached response.
  2. Conflict detection: same key + different payload → 409.
  3. Audit logging: every call lands in Polemarch API Log (best-effort).

Falls back gracefully if either doctype isn't migrated yet — endpoints stay
functional, they just don't dedupe or audit until the doctypes exist.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime


_IDEMPOTENCY_TTL_HOURS = 24


class IdempotencyConflict(frappe.ValidationError):
    """Same key, different payload — caller is replaying with new state."""

    http_status_code = 409


class _Context:
    def __init__(self, endpoint: str, key: Optional[str], payload: dict):
        self.endpoint = endpoint
        self.key = key
        self.payload = payload
        self.payload_hash = _hash_payload(payload) if key else None
        self.replay: Optional[Any] = None
        self.started_at = time.time()
        self._stored = False

    def store(self, response: Any) -> None:
        """Cache the response for future replays."""
        if not self.key or self._stored:
            return
        if not _idem_doctype_exists():
            return
        try:
            doc = frappe.get_doc(
                {
                    "doctype": "Polemarch API Idempotency Log",
                    "idempotency_key": self.key,
                    "endpoint": self.endpoint,
                    "user": frappe.session.user,
                    "request_hash": self.payload_hash,
                    "response_payload": _serialise(response),
                    "expires_at": add_to_date(now_datetime(), hours=_IDEMPOTENCY_TTL_HOURS),
                }
            )
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
            self._stored = True
        except frappe.DuplicateEntryError:
            # Another concurrent request beat us to the store; that's fine —
            # the existing row has the same response.
            pass


@contextmanager
def scope(endpoint: str, idempotency_key: Optional[str], payload: dict):
    """Context manager for idempotent endpoint execution + audit logging."""
    ctx = _Context(endpoint, idempotency_key, payload)

    if idempotency_key and _idem_doctype_exists():
        existing = frappe.db.get_value(
            "Polemarch API Idempotency Log",
            idempotency_key,
            ("request_hash", "response_payload"),
            as_dict=True,
        )
        if existing:
            if existing.request_hash != ctx.payload_hash:
                _audit_log_request(ctx, status_code=409, idempotency_hit=False,
                                   error=_("Idempotency replay with mismatched payload"))
                raise IdempotencyConflict(
                    _("Idempotency key reused with different payload.")
                )
            ctx.replay = _deserialise(existing.response_payload)

    error_message = ""
    status_code = 200
    response = None
    try:
        yield ctx
        if ctx.replay is None:
            # The caller stored its response in ctx via ctx.store(...).
            response = None
    except frappe.PermissionError:
        status_code, error_message = 403, "PermissionError"
        raise
    except IdempotencyConflict:
        # Already audited above.
        raise
    except frappe.ValidationError as exc:
        status_code, error_message = 400, str(exc)[:200]
        raise
    except Exception as exc:
        status_code, error_message = 500, str(exc)[:200]
        raise
    finally:
        _audit_log_request(
            ctx,
            status_code=status_code,
            idempotency_hit=bool(ctx.replay),
            response=response,
            error=error_message,
        )


# ── purge job ────────────────────────────────────────────────────────────


def purge_expired() -> int:
    """Daily scheduler job: drop idempotency-log rows past their TTL."""
    if not _idem_doctype_exists():
        return 0
    count_before = frappe.db.count("Polemarch API Idempotency Log")
    frappe.db.sql(
        "DELETE FROM `tabPolemarch API Idempotency Log` WHERE expires_at < %s",
        (now_datetime(),),
    )
    frappe.db.commit()
    return count_before - frappe.db.count("Polemarch API Idempotency Log")


# ── internals ────────────────────────────────────────────────────────────


def _hash_payload(payload: dict) -> str:
    canon = json.dumps(_strip_internals(payload), sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _strip_internals(d: dict) -> dict:
    # Drop self/idempotency_key, plus Frappe-injected request internals.
    bad_keys = {"self", "cls", "idempotency_key", "kwargs", "args"}
    return {k: v for k, v in d.items() if k not in bad_keys and not k.startswith("_")}


def _serialise(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return json.dumps(str(value))


def _deserialise(blob: Optional[str]) -> Any:
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return blob


def _idem_doctype_exists() -> bool:
    return bool(frappe.db.table_exists("Polemarch API Idempotency Log"))


def _audit_log_doctype_exists() -> bool:
    return bool(frappe.db.table_exists("Polemarch API Log"))


def _audit_log_request(
    ctx: _Context,
    status_code: int,
    idempotency_hit: bool,
    response: Any = None,
    error: str = "",
) -> None:
    if not _audit_log_doctype_exists():
        return
    try:
        duration_ms = int((time.time() - ctx.started_at) * 1000)
        doc = frappe.get_doc(
            {
                "doctype": "Polemarch API Log",
                "endpoint": ctx.endpoint,
                "user": frappe.session.user,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "logged_at": now_datetime(),
                "request_payload": _serialise(_strip_internals(ctx.payload))[:140000],
                "idempotency_key": ctx.key,
                "idempotency_hit": int(idempotency_hit),
                "response_payload": _serialise(response)[:140000] if response else None,
                "error_message": error or None,
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    except Exception:
        # Never let the audit log poison the user response.
        frappe.log_error(frappe.get_traceback(), "Polemarch API Audit Log")
