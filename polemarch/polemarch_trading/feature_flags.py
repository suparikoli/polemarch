"""Phase rollout flags for the Polemarch Trading subsystem.

These are intentionally module-level constants (not DB-backed Singles) so that
the runtime cost is zero and the flag state is part of the deployment artifact
— flipping a flag is a code-review-able change, not a Desk edit.

Operators who need an emergency kill-switch can override any flag at runtime
via `frappe.local.conf` (site_config.json) — see `_get(...)` below.
"""

import frappe


_DEFAULTS = {
    # When a Customer's custom_is_polemarch_customer flips to 1, auto-create
    # a Wallet via the Customer on_update hook.
    "AUTO_CREATE_WALLET_ON_POLEMARCH_FLAG": True,
    # Investment Disposal.on_submit auto-posts a cost-recognition JE.
    "AUTO_POST_CAPITAL_GAINS_JE": False,
    # Customer-role permission_query_conditions are wired in hooks.py.
    "CUSTOMER_ROLE_SCOPING": False,
}


def is_enabled(flag_name: str) -> bool:
    """Read a flag, allowing site_config.json to override the default.

    site_config.json shape:
        { "polemarch_flags": { "AUTO_POST_CAPITAL_GAINS_JE": true } }
    """
    overrides = (frappe.local.conf or {}).get("polemarch_flags") or {}
    if flag_name in overrides:
        return bool(overrides[flag_name])
    return bool(_DEFAULTS.get(flag_name, False))
