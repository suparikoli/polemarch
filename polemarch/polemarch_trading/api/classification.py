"""Whitelisted endpoint for classifying Investment Holdings.

Only one endpoint:
  POST /api/method/polemarch.polemarch_trading.api.classification.classify
       body: {"holding": "INV-HOLD-...", "classification": "Investment"}

(The "Unallocated" → "Stock in Trade" transition is handled by the daily
scheduler, not by this API.)
"""

import frappe
from frappe import _

from polemarch.polemarch_trading.api._ratelimit import rate_limit


_ALLOWED_ROLES = [
    "System Manager",
    "Accounts Manager",
    "Polemarch Compliance Officer",
    "Polemarch Trader",
]


@frappe.whitelist()
@rate_limit(per_min=30, scope="user")
def classify(holding: str, classification: str = "Investment"):
    """Classify a holding. Currently only `Investment` is supported as the
    target — `Stock in Trade` happens automatically via the scheduler if no
    one classifies in time."""
    frappe.only_for(_ALLOWED_ROLES, message=_("Not allowed to classify Investment Holdings."))

    if classification != "Investment":
        frappe.throw(
            _("Only `Investment` classification can be set via this endpoint. "
              "Stock in Trade is auto-applied after the deadline expires."),
            title=_("Invalid Classification"),
        )

    from polemarch.polemarch_trading import classification as eng
    return eng.classify_as_investment(holding)
