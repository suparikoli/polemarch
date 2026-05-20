"""Lightweight per-minute rate-limit decorator backed by Frappe's Redis cache.

Frappe doesn't natively rate-limit whitelisted endpoints; this fills the gap
without pulling in a heavier framework. Storage is an incrementing counter
keyed by (endpoint, scope-value, current-minute-bucket) with a 90s TTL —
naturally rolls over each minute, no cleanup needed.

Scopes:
  - "user"     : rate per `frappe.session.user`
  - "customer" : rate per resolved customer (positional arg or kwargs)
  - "ip"       : rate per request IP

Failure mode: if the Redis cache is unavailable, the decorator becomes a
no-op rather than denying traffic — availability > policy enforcement.
"""

from __future__ import annotations

import functools
import time
from typing import Callable

import frappe
from frappe import _


def rate_limit(per_min: int, scope: str = "user"):
    if scope not in ("user", "customer", "ip"):
        raise ValueError(f"Unsupported rate-limit scope: {scope}")

    def decorator(func: Callable) -> Callable:
        endpoint = f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            scope_value = _resolve_scope(scope, kwargs)
            bucket = int(time.time() // 60)
            key = f"polemarch:rl:{endpoint}:{scope_value}:{bucket}"

            try:
                cache = frappe.cache()
                count = int(cache.get(key) or 0) + 1
                cache.set(key, count, expires_in_sec=90)
            except Exception:
                # Cache unavailable — fail open.
                return func(*args, **kwargs)

            if count > per_min:
                frappe.local.response.http_status_code = 429
                frappe.throw(
                    _("Rate limit exceeded: {0} calls/minute per {1}.").format(per_min, scope),
                    title=_("Too Many Requests"),
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def _resolve_scope(scope: str, kwargs: dict) -> str:
    if scope == "user":
        return frappe.session.user or "anonymous"
    if scope == "customer":
        candidate = kwargs.get("customer")
        if candidate:
            return str(candidate)
        # Fall back to the session-user → customer mapping if available.
        return frappe.db.get_value("Customer", {"email_id": frappe.session.user}, "name") or frappe.session.user
    if scope == "ip":
        request = getattr(frappe.local, "request", None)
        if request and getattr(request, "remote_addr", None):
            return request.remote_addr
        return "unknown-ip"
    return "unknown-scope"
