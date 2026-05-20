from frappe.model.document import Document


class PolemarchAPILog(Document):
    """Audit trail for whitelisted non-GET endpoints.

    Populated by `polemarch.trading.api._idempotency.audit_log_request`.
    Read-only for non-Sys-Admin roles.
    """

    pass
