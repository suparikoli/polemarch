import frappe
from frappe import _
from frappe.model.document import Document


class MedusaSettings(Document):
    def validate(self):
        if not self.enable_sync:
            return
        for field in ("medusa_url", "medusa_admin_api_key"):
            if not self.get(field):
                frappe.throw(_("{0} is required when sync is enabled.").format(self.meta.get_label(field)))


@frappe.whitelist()
def test_connection():
    frappe.only_for("System Manager")
    from polemarch.medusa.client import MedusaClient, MedusaError

    settings = frappe.get_single("Medusa Settings")
    if not settings.medusa_url:
        return {"ok": False, "message": _("Medusa URL is not set.")}
    try:
        client = MedusaClient(settings)
        response = client.get("/admin/users/me")
        user = (response or {}).get("user") or {}
        email = user.get("email") or _("(unknown)")
        return {"ok": True, "message": _("Connected. Authenticated as {0}.").format(email)}
    except MedusaError as exc:
        return {"ok": False, "message": str(exc)[:500]}


@frappe.whitelist()
def rotate_webhook_secret():
    """Generate a new webhook secret and store it. Returns the plaintext
    value ONCE so the operator can copy it into Medusa's env. After this
    call, any inbound webhook signed with the old secret is rejected, so
    the operator must redeploy Medusa with the new value immediately."""
    import secrets as _secrets

    frappe.only_for("System Manager")
    new_secret = _secrets.token_urlsafe(32)
    settings = frappe.get_single("Medusa Settings")
    settings.medusa_webhook_secret = new_secret
    settings.flags.ignore_permissions = True
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    return {
        "secret": new_secret,
        "message": _(
            "New secret generated. Copy it now — it won't be shown again. "
            "Update ERPNEXT_WEBHOOK_SECRET on the Medusa side and redeploy."
        ),
    }
