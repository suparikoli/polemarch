import frappe

from polemarch.install import POLEMARCH_CUSTOMER_GROUP


def validate(doc, method=None):
    has_dp = bool(doc.get("custom_dp_details"))
    in_polemarch_group = doc.customer_group == POLEMARCH_CUSTOMER_GROUP
    doc.custom_is_polemarch_customer = 1 if (has_dp or in_polemarch_group) else 0


def on_update(doc, method=None):
    if doc.custom_is_polemarch_customer:
        frappe.enqueue(
            "polemarch.medusa.sync_customers.push_customer",
            queue="short",
            customer_name=doc.name,
            enqueue_after_commit=True,
        )
