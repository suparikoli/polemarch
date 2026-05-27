"""Catalog sync API — Frappe Security ↔ Medusa Product.

Securities are Frappe-managed: the operator decides which ISINs are
listed for trading via the desk (Security DocType). The Medusa side
mirrors the catalog by polling this endpoint on a cron — each
Security with `active=1, tradable=1` becomes a Medusa Product. When
the operator un-ticks tradable or active, the next pull marks the
Product as draft / archived (not deleted, to preserve order
history).

Endpoint:

  list_securities_for_medusa(since=None, limit=100, include_inactive=False)

Returns the active/tradable Securities modified since the given
timestamp, decorated with the fields the Medusa Product mirror
needs:

  - isin              → Product.handle (lowercased) AND Product.metadata.isin
  - security_name     → Product.title
  - security_type     → Product.metadata.security_type
  - face_value        → Product.metadata.face_value
  - last_traded_price → Product.variants[0].prices[0].amount (paise)
  - tradable, active  → Product.status (published if both true, else draft)
  - polemarch_page_url, calcula_page_url, sector, rta, company_name
    → Product.metadata.{...}

Idempotent on the Medusa side: the pull cron upserts by ISIN. New
runs find the existing Product and update only the fields that
changed.

Filter on Medusa back-ref: Securities already mirrored have
`medusa_product_id` set by the webhook handler; we expose it in the
response so the Medusa cron knows to UPDATE rather than CREATE.
"""

from typing import Optional

import frappe
from frappe.utils import flt

from polemarch.polemarch_trading.doctype.polemarch_settings.polemarch_settings import (
    is_medusa_sync_enabled,
)


@frappe.whitelist()
def list_customers_for_medusa(
    since: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Return Polemarch customers (NOT Mithtech-only) modified since
    the given timestamp, with KYC fields. Used by the Medusa customer
    pull cron to mirror operator-side KYC changes into Medusa's
    customer.metadata so the storefront can reflect verified status.

    Args:
      since:  ISO datetime string; defaults to 1 day ago.
      limit:  Max rows (default 100, max 500).

    Returns:
      {
        customers: [{
          name, email, customer_name, customer_type, customer_group,
          custom_is_polemarch_customer, custom_is_mithtech_only,
          custom_kyc_status, custom_kyc_verified_on,
          pan, custom_client_id, modified
        }, ...],
        now: <ISO datetime — caller stores this as next cursor>,
      }

    Filters out:
      - custom_is_mithtech_only=1 (operator policy: never sync these
        to Medusa)
      - Customers without an email_id on their primary Contact
        (Medusa keys customers by email; we can't mirror without one)
    """
    if not is_medusa_sync_enabled():
        return {
            "customers": [],
            "now": frappe.utils.now_datetime().isoformat(),
        }

    limit = max(1, min(int(limit or 100), 500))
    since_dt = (
        frappe.utils.get_datetime(since)
        if since
        else frappe.utils.add_to_date(None, days=-1)
    )
    since_str = frappe.utils.get_datetime_str(since_dt)

    # Pull Customers modified since cursor, joined with their primary
    # Contact's primary email. Filter mithtech-only out at SQL level.
    rows = frappe.db.sql(
        """
        SELECT
            c.name                                              AS name,
            c.customer_name                                     AS customer_name,
            c.customer_type                                     AS customer_type,
            c.customer_group                                    AS customer_group,
            c.custom_is_polemarch_customer                      AS custom_is_polemarch_customer,
            c.custom_is_mithtech_only                           AS custom_is_mithtech_only,
            c.custom_kyc_status                                 AS custom_kyc_status,
            c.custom_kyc_verified_on                            AS custom_kyc_verified_on,
            c.pan                                               AS pan,
            c.custom_client_id                                  AS custom_client_id,
            c.modified                                          AS modified,
            (
                SELECT LOWER(ce.email_id)
                FROM `tabContact Email` ce
                JOIN `tabContact` ct ON ct.name = ce.parent
                JOIN `tabDynamic Link` dl ON dl.parent = ct.name
                WHERE dl.link_doctype = 'Customer'
                  AND dl.link_name = c.name
                ORDER BY ce.is_primary DESC, ce.idx ASC
                LIMIT 1
            )                                                   AS email
        FROM `tabCustomer` c
        WHERE c.modified >= %s
          AND COALESCE(c.custom_is_mithtech_only, 0) = 0
        ORDER BY c.modified ASC
        LIMIT %s
        """,
        (since_str, limit),
        as_dict=True,
    )

    # Filter out rows with no email — Medusa keys by email, can't sync
    return {
        "customers": [r for r in rows if r.get("email")],
        "now": frappe.utils.now_datetime().isoformat(),
    }


@frappe.whitelist()
def list_securities_for_medusa(
    since: Optional[str] = None,
    limit: int = 100,
    include_inactive: bool = False,
) -> dict:
    """Return active+tradable Securities modified since `since` for the
    Medusa Product mirror.

    Args:
      since:           ISO datetime string; defaults to 1 day ago.
      limit:           Max rows (default 100, max 500).
      include_inactive: When True, also returns Securities with
                       active=0 or tradable=0 — used by the Medusa
                       cron to ALSO de-publish their Products. Default
                       False (only active+tradable).

    Returns:
      {
        securities: [{isin, security_name, security_type, face_value,
                      last_traded_price, tradable, active,
                      polemarch_page_url, calcula_page_url, company_name,
                      sector, rta, medusa_product_id, modified}, ...],
        now: <ISO datetime — caller stores this as next cursor>,
      }
    """
    if not is_medusa_sync_enabled():
        return {
            "securities": [],
            "now": frappe.utils.now_datetime().isoformat(),
        }

    limit = max(1, min(int(limit or 100), 500))
    since_dt = (
        frappe.utils.get_datetime(since)
        if since
        else frappe.utils.add_to_date(None, days=-1)
    )
    since_str = frappe.utils.get_datetime_str(since_dt)

    filters: dict = {"modified": [">=", since_str]}
    if not include_inactive:
        filters["active"] = 1
        filters["tradable"] = 1

    # Determine which fields exist (some are Custom Fields added by
    # later patches; safer to enumerate via meta).
    fields = [
        "name AS isin",
        "security_name",
        "security_type",
        "face_value",
        "last_traded_price",
        "tradable",
        "active",
        "polemarch_page_url",
        "calcula_page_url",
        "company_name",
        "sector",
        "rta",
        "modified",
    ]
    if frappe.db.has_column("Security", "medusa_product_id"):
        fields.append("medusa_product_id")

    rows = frappe.get_all(
        "Security",
        filters=filters,
        fields=fields,
        order_by="modified ASC",
        limit=limit,
    )

    # Coerce numerics for the Medusa side
    for r in rows:
        r["face_value"] = flt(r.get("face_value"))
        r["last_traded_price"] = flt(r.get("last_traded_price"))
        # Medusa stores prices in paise (minor units); we send rupees
        # and the cron converts. Keep as rupees here for clarity.

    return {
        "securities": rows,
        "now": frappe.utils.now_datetime().isoformat(),
    }
