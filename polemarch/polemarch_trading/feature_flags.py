"""Phase rollout flags for the Polemarch Trading subsystem.

These are intentionally module-level constants (not DB-backed Singles) so that
the runtime cost is zero and the flag state is part of the deployment artifact
— flipping a flag is a code-review-able change, not a Desk edit.

Operators who need an emergency kill-switch can override any flag at runtime
via `frappe.local.conf` (site_config.json) — see `_get(...)` below.
"""

import frappe


_DEFAULTS = {
    # Phase 1: mirror-write SLLE rows from Investment Disposal alongside the
    # existing qty_disposed mutation. Enable for 4–6 weeks; disable only if
    # the daily `verify_holding_lot_ledger_mirror` job is flagging issues
    # that need investigation without polluting more rows.
    "MIRROR_WRITE_SLLE": True,
    # Phase 1: when a Customer's custom_is_polemarch_customer flips to 1,
    # auto-create a Wallet via the Customer on_update hook.
    "AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG": True,
    # Phase 2: Medusa order.placed webhook ALSO creates a Trade Order
    # alongside the existing Sales Order flow. Default OFF; flip after smoke.
    "CREATE_TRADE_ORDER_FROM_MEDUSA": False,
    # Phase 3: Investment Disposal.on_submit auto-posts a cost-recognition JE.
    "AUTO_POST_CAPITAL_GAINS_JE": False,
    # Phase 5: customer-role permission_query_conditions are wired in hooks.py.
    "CUSTOMER_ROLE_SCOPING": False,
}


def is_enabled(flag_name: str) -> bool:
    """Read a flag, allowing site_config.json to override the default.

    site_config.json shape:
        { "polemarch_flags": { "MIRROR_WRITE_SLLE": false } }
    """
    overrides = (frappe.local.conf or {}).get("polemarch_flags") or {}
    if flag_name in overrides:
        return bool(overrides[flag_name])
    return bool(_DEFAULTS.get(flag_name, False))
