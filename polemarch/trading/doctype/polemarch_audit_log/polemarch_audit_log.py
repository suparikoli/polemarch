from frappe.model.document import Document


class PolemarchAuditLog(Document):
    """Append-only output of daily reconciliation jobs.

    Each row records one job-run: which job, when, how many rows it checked,
    how many were mismatched, and a JSON snapshot of the first 50 mismatches
    for forensic review. The audit jobs in `polemarch.trading.audit` write
    here when the doctype is present and fall back to `frappe.log_error`
    otherwise.
    """

    pass
