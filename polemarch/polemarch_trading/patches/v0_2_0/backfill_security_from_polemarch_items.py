"""Backfill Security rows from existing brand=Polemarch Items.

For each Item with brand=Polemarch:
  1. Resolve an ISIN from one of (in priority order):
       - Item.custom_isin (if the Custom Field exists and is non-empty)
       - Item.item_code (if it matches ISIN regex)
       - Item.name (if it matches ISIN regex)
  2. Create a Security row keyed by that ISIN (skip if Security exists).
  3. Set Item.custom_security → Security.name on the originating Item.

Items without a resolvable ISIN are logged and skipped — they need manual
remediation by Item Manager before they can participate in Trade Order /
SLLE flows. Existing GST/India-Compliance Sales Invoice flows for these
items remain unaffected.

Idempotent. Safe to re-run.
"""

import re

import frappe

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
POLEMARCH_BRAND = "Polemarch"


def execute():
    items = frappe.get_all(
        "Item",
        filters={"brand": POLEMARCH_BRAND, "disabled": 0},
        fields=["name", "item_code", "item_name"],
    )

    has_custom_isin = bool(
        frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_isin"})
    )
    has_custom_rta = bool(
        frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_rta"})
    )
    has_custom_ltp = bool(
        frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_last_traded_price"})
    )
    has_custom_security = bool(
        frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_security"})
    )

    skipped = []

    for item in items:
        isin, extras = _resolve_isin_and_extras(
            item, has_custom_isin, has_custom_rta, has_custom_ltp
        )
        if not isin:
            skipped.append({"item": item.name, "reason": "no resolvable ISIN"})
            continue

        security_name = _ensure_security(
            isin=isin,
            security_name=item.item_name or isin,
            item=item.name,
            extras=extras,
        )

        if has_custom_security and security_name:
            current = frappe.db.get_value("Item", item.name, "custom_security")
            if current != security_name:
                frappe.db.set_value(
                    "Item", item.name, "custom_security", security_name, update_modified=False
                )

    frappe.db.commit()

    if skipped:
        # Skipped Items are an admin-actionable list, not a failure. Log so an
        # operator can run `frappe.log_error` views and reconcile manually.
        frappe.log_error(
            f"backfill_security_from_polemarch_items: {len(skipped)} Item(s) skipped — no ISIN. "
            f"First 20: {skipped[:20]}",
            "Polemarch Security Backfill",
        )


def _resolve_isin_and_extras(item, has_custom_isin, has_custom_rta, has_custom_ltp):
    extras = {}

    if has_custom_rta:
        rta = frappe.db.get_value("Item", item.name, "custom_rta")
        if rta:
            extras["rta"] = rta

    if has_custom_ltp:
        ltp = frappe.db.get_value("Item", item.name, "custom_last_traded_price")
        if ltp:
            extras["last_traded_price"] = ltp

    # Priority 1: explicit custom_isin field.
    if has_custom_isin:
        candidate = frappe.db.get_value("Item", item.name, "custom_isin")
        if candidate and ISIN_RE.match(candidate.strip().upper()):
            return candidate.strip().upper(), extras

    # Priority 2: item_code matches ISIN.
    if item.item_code and ISIN_RE.match(item.item_code.strip().upper()):
        return item.item_code.strip().upper(), extras

    # Priority 3: name matches ISIN.
    if item.name and ISIN_RE.match(item.name.strip().upper()):
        return item.name.strip().upper(), extras

    return None, extras


def _ensure_security(isin, security_name, item, extras):
    if frappe.db.exists("Security", isin):
        # Backfill missing item on existing row.
        sec = frappe.get_doc("Security", isin)
        dirty = False
        if not sec.item and item:
            sec.item = item
            dirty = True
        if not sec.rta and extras.get("rta"):
            sec.rta = extras["rta"]
            dirty = True
        if not sec.last_traded_price and extras.get("last_traded_price"):
            sec.last_traded_price = extras["last_traded_price"]
            dirty = True
        if dirty:
            sec.save(ignore_permissions=True)
        return sec.name

    try:
        doc = frappe.get_doc({
            "doctype": "Security",
            "isin": isin,
            "security_name": security_name,
            "security_type": "Equity",  # Default; admin can edit afterwards.
            "tradable": 1,
            "active": 1,
            "item": item,
            **extras,
        })
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception as exc:
        frappe.log_error(
            f"backfill_security_from_polemarch_items: failed to create Security for ISIN={isin}, "
            f"item={item}: {exc}",
            "Polemarch Security Backfill",
        )
        return None
