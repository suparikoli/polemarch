"""Runtime consumer of the `Polemarch Sync Mapping` Single doctype.

The mapping doctype (configured via the Mappings tab in the Medusa
admin) defines, per entity, a list of (medusa_path → erpnext_field,
transform, direction) rows. This module walks those rows at sync time
and produces dicts ready to feed into `frappe.get_doc(...).update()`.

Public surface:
  - `apply_inbound(payload, section)` — Medusa→ERPNext direction.
    Returns dict of ERPNext field → value, or None if the mapper is
    globally disabled (caller falls back to its hardcoded behaviour).
  - `apply_inbound_each(payload, section)` — same idea but for
    multi-row sections (Bank Accounts, Demat Accounts, Order Items)
    where the payload carries a list and we emit one dict per element.
  - `is_enabled()` — quick global check.

Design notes:
  - `is_required` causes a `ValidationError` (so the upstream sync
    job logs it on the Medusa Sync Log row); non-required missing
    fields are silently skipped.
  - Unknown transforms are logged and passed through unchanged
    rather than crashing the sync.
  - Direction `"ERPNext to Medusa"` rows are skipped on inbound (and
    will be honoured by future ERPNext→Medusa code, not yet wired).
  - The settings doctype is fetched via `get_cached_doc`, so multiple
    calls in one request hit the in-process cache. The cache is
    invalidated automatically when the doctype is saved via the
    Frappe admin form.
"""

import re
from datetime import date, datetime
from typing import Any, Iterable

import frappe

SETTINGS_NAME = "Polemarch Sync Mapping"

# Multi-row sections enumerate Medusa-side arrays. Format:
#   (section_field, list_path_relative_to_payload)
# When `apply_inbound_each(payload, section)` is called, the caller
# is expected to know the section field; the list path stored here
# tells the mapper where the array lives on the Medusa payload.
ARRAY_PATHS = {
    "bank_account_mappings": "metadata.bank_accounts",
    "demat_account_mappings": "metadata.demat_accounts",
    "order_item_mappings": "items",
}


def is_enabled() -> bool:
    try:
        return bool(frappe.get_cached_doc(SETTINGS_NAME).enabled)
    except Exception:
        # Doctype not migrated yet, or DB unavailable. Treat as
        # disabled so the caller takes its legacy path.
        return False


def apply_inbound(payload: dict, section: str) -> dict | None:
    """Apply the mapping rows in `section` to a single payload.

    Returns:
        - dict of erpnext_field → value when the mapper is enabled
        - None when the mapper is disabled globally (caller should
          fall back to its hardcoded Medusa→ERPNext behaviour)
    """
    if not is_enabled():
        return None
    rows = _enabled_rows(section)
    return _apply_rows(payload, rows, section_for_error=section)


def apply_inbound_each(payload: dict, section: str) -> list[dict] | None:
    """For multi-row sections (bank/demat/order_item). Walks the array
    at the section's known list path on the payload, returns one dict
    per element. Returns None when the mapper is disabled (caller
    falls back to legacy behaviour)."""
    if not is_enabled():
        return None
    list_path = ARRAY_PATHS.get(section)
    if not list_path:
        # Caller used a section name without an array path mapped.
        # Treat as single-row apply.
        return [apply_inbound(payload, section) or {}]
    array = _walk_path(payload, list_path)
    if not isinstance(array, list):
        return []
    rows = _enabled_rows(section)
    return [_apply_rows(el, rows, section_for_error=section) for el in array]


# ─────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────


def _enabled_rows(section: str) -> list:
    settings = frappe.get_cached_doc(SETTINGS_NAME)
    rows = settings.get(section) or []
    return [
        r
        for r in rows
        if r.is_enabled and (r.direction or "Bidirectional") != "ERPNext to Medusa"
    ]


def _apply_rows(payload: Any, rows: Iterable, section_for_error: str) -> dict:
    result: dict = {}
    for row in rows:
        value = _walk_path(payload, row.medusa_path)
        if value is None or value == "":
            if row.is_required:
                frappe.throw(
                    "[{0}] Required Medusa field '{1}' is missing or empty in payload.".format(
                        section_for_error, row.medusa_path
                    )
                )
            continue
        try:
            value = _apply_transform(value, row.transform, payload)
        except Exception as exc:
            frappe.log_error(
                title="Polemarch Sync Mapping: transform failed",
                message=(
                    f"section={section_for_error} path={row.medusa_path} "
                    f"transform={row.transform!r} value={value!r}\n{exc}"
                ),
            )
            # Pass through untransformed rather than crashing the
            # whole sync.
            pass
        result[row.erpnext_field] = value
    return result


# Path syntax:
#   `first_name`                   → dict["first_name"]
#   `metadata.aadhaar_last4`       → dict["metadata"]["aadhaar_last4"]
#   `items[].variant.sku`          → first non-null match in array
#   `items[0].variant.sku`         → array element 0 specifically
#
# `[]` (empty index) inside `_walk_path` returns the FIRST non-null
# nested match. For per-element iteration use `apply_inbound_each`.

_INDEX_RE = re.compile(r"\[(\d*)\]")


def _walk_path(data: Any, path: str | None) -> Any:
    if path is None or data is None:
        return None
    # Tokenise the path. Each token is either a dict key or a list
    # access. List access tokens carry the optional integer index.
    tokens: list[tuple[str, str | int | None]] = []
    for chunk in path.split("."):
        m = _INDEX_RE.search(chunk)
        if m:
            key = chunk[: m.start()]
            idx_s = m.group(1)
            tokens.append(("key", key))
            tokens.append(("idx", int(idx_s) if idx_s else None))
        else:
            tokens.append(("key", chunk))
    cursor: Any = data
    for kind, token in tokens:
        if cursor is None:
            return None
        if kind == "key":
            if token == "":
                # `[]` produced an empty preceding key, skip.
                continue
            if isinstance(cursor, dict):
                cursor = cursor.get(token)
            else:
                return None
        elif kind == "idx":
            if not isinstance(cursor, list):
                return None
            if token is None:
                # `[]` — first non-null. Return first element; caller
                # using single-value mapping is asking for "any one".
                cursor = cursor[0] if cursor else None
            else:
                cursor = cursor[token] if 0 <= token < len(cursor) else None
    return cursor


# ─────────────────────────────────────────────────────────────────
# Transforms
# ─────────────────────────────────────────────────────────────────


def _apply_transform(value: Any, transform: str | None, payload: Any = None) -> Any:
    if not transform:
        return value
    fn = TRANSFORMS.get(transform)
    if not fn:
        # Unknown transform — return value as-is. Logged by caller.
        return value
    return fn(value, payload)


def _t_upper(v, _payload):
    return str(v).upper() if v is not None else v


def _t_lower(v, _payload):
    return str(v).lower() if v is not None else v


def _t_trim(v, _payload):
    return str(v).strip() if v is not None else v


def _t_mask_aadhaar(v, _payload):
    """Always reduce to last 4 digits. The Medusa side should already
    send just the last 4, but this is a belt-and-braces safeguard."""
    if v is None:
        return None
    s = "".join(c for c in str(v) if c.isdigit())
    return s[-4:] if s else None


def _t_mask_pan(v, _payload):
    """PAN format: AAAAA9999A. Return first 5 + 'XXXXX' suffix for
    masked display fields (NOT for stored canonical PAN — only the
    masked-display field uses this)."""
    if v is None:
        return None
    s = str(v).upper().strip()
    if len(s) >= 10:
        return s[:5] + "XXXXX"
    return s


def _t_iso_date(v, _payload):
    """Pass through ISO-8601 strings (YYYY-MM-DD) — Frappe's Date
    field parses these natively. Also handle datetime objects."""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)[:10] if v else None


def _t_date_dd_mm_yyyy(v, _payload):
    """Convert 'dd/mm/yyyy' → 'yyyy-mm-dd' for Frappe Date fields."""
    if v is None:
        return None
    s = str(v).strip()
    parts = s.split("/")
    if len(parts) != 3:
        return s
    d, m, y = parts
    return f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"


def _t_paise_to_rupees(v, _payload):
    """Medusa stores INR as paise (integer). Frappe stores as rupees
    (float). Divide by 100."""
    if v is None:
        return None
    try:
        return float(v) / 100
    except (TypeError, ValueError):
        return v


def _t_concat_name(_v, payload):
    """Build customer_name from first/middle/last on the same payload.
    Ignores the input value — composes from sibling fields.

    Falls back to whatever pieces are present; an all-blank composition
    returns None so the caller can skip the field rather than write an
    empty string."""
    if not isinstance(payload, dict):
        return None
    parts = [
        payload.get("first_name") or "",
        (payload.get("metadata") or {}).get("middle_name") or "",
        payload.get("last_name") or "",
    ]
    joined = " ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


TRANSFORMS = {
    "Upper": _t_upper,
    "Lower": _t_lower,
    "Trim": _t_trim,
    "Mask Aadhaar": _t_mask_aadhaar,
    "Mask PAN": _t_mask_pan,
    "ISO Date": _t_iso_date,
    "Date dd/mm/yyyy": _t_date_dd_mm_yyyy,
    "Paise to Rupees": _t_paise_to_rupees,
    "Concatenate First+Middle+Last": _t_concat_name,
}
