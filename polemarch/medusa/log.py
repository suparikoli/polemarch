import json

import frappe


def write_log(*, direction, entity_type, status, event=None, erpnext_doctype=None, erpnext_ref=None,
              medusa_id=None, event_id=None, payload=None, response=None, error=None):
    try:
        doc = frappe.new_doc("Medusa Sync Log")
        doc.direction = direction
        doc.entity_type = entity_type
        doc.event = event
        doc.status = status
        doc.erpnext_doctype = erpnext_doctype
        doc.erpnext_ref = erpnext_ref
        doc.medusa_id = medusa_id
        doc.event_id = event_id
        doc.payload = _safe_dump(payload)
        doc.response = _safe_dump(response)
        doc.error = error if isinstance(error, str) else _safe_dump(error)
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(title="Medusa sync log write failed", message=frappe.get_traceback())


def _safe_dump(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value[:140000]
    try:
        return json.dumps(value, default=str)[:140000]
    except Exception:
        return str(value)[:140000]
