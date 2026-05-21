from frappe.model.document import Document


class PolemarchAPIIdempotencyLog(Document):
    """Idempotency cache for whitelisted non-GET endpoints.

    Rows are inserted by `polemarch.polemarch_trading.api._idempotency.replay_or_store`
    and purged daily by `polemarch.polemarch_trading.api._idempotency.purge_expired`.
    Never edited by hand.
    """

    pass
