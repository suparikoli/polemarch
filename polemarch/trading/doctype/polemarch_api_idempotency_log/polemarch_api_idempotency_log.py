from frappe.model.document import Document


class PolemarchAPIIdempotencyLog(Document):
    """Idempotency cache for whitelisted non-GET endpoints.

    Rows are inserted by `polemarch.trading.api._idempotency.replay_or_store`
    and purged daily by `polemarch.trading.api._idempotency.purge_expired`.
    Never edited by hand.
    """

    pass
