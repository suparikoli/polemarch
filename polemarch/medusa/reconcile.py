import frappe

from polemarch.install import POLEMARCH_BRAND
from polemarch.medusa.client import MedusaError, get_client
from polemarch.medusa.log import write_log
from polemarch.medusa.sync_customers import upsert_from_medusa
from polemarch.medusa.sync_items import push_item
from polemarch.medusa.sync_orders import handle_order_placed, handle_payment_captured


def run_hourly():
    client = get_client()
    if not client:
        return
    settings = client.settings
    since = settings.last_full_sync_at

    _reconcile_items(client, since)
    _reconcile_customers(client, since)
    _reconcile_orders(client, since)

    settings.last_full_sync_at = frappe.utils.now_datetime()
    settings.flags.ignore_permissions = True
    settings.save(ignore_permissions=True)
    frappe.db.commit()


@frappe.whitelist()
def full_resync():
    frappe.only_for("System Manager")
    settings = frappe.get_single("Medusa Settings")
    settings.last_full_sync_at = None
    settings.flags.ignore_permissions = True
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    run_hourly()
    return "ok"


def _reconcile_items(client, since):
    polemarch_items = frappe.get_all(
        "Item",
        filters={"brand": POLEMARCH_BRAND, "disabled": 0},
        pluck="name",
    )
    for name in polemarch_items:
        try:
            push_item(name)
        except MedusaError:
            continue


def _reconcile_customers(client, since):
    params = {"limit": 100, "offset": 0}
    while True:
        try:
            response = client.get("/admin/customers", params=params)
        except MedusaError as exc:
            write_log(direction="Medusa to ERPNext", entity_type="Customer", event="reconcile",
                      status="Failed", error=str(exc))
            return
        customers = response.get("customers") or []
        if not customers:
            return
        for medusa_customer in customers:
            try:
                upsert_from_medusa({"customer": medusa_customer}, event="reconcile.customer")
            except Exception:
                frappe.log_error(title="Medusa customer reconcile failed", message=frappe.get_traceback())
        if len(customers) < params["limit"]:
            return
        params["offset"] += params["limit"]


def _reconcile_orders(client, since):
    params = {"limit": 50, "offset": 0}
    if since:
        params["updated_at[gte]"] = str(since)
    while True:
        try:
            response = client.get("/admin/orders", params=params)
        except MedusaError as exc:
            write_log(direction="Medusa to ERPNext", entity_type="Order", event="reconcile",
                      status="Failed", error=str(exc))
            return
        orders = response.get("orders") or []
        if not orders:
            return
        for order in orders:
            try:
                if frappe.db.exists("Sales Invoice", {"custom_medusa_order_id": order.get("id")}):
                    continue
                if (order.get("payment_status") or "") == "captured":
                    handle_payment_captured({"order": order}, event_id=f"reconcile:{order.get('id')}")
                else:
                    handle_order_placed({"order": order}, event_id=f"reconcile:{order.get('id')}")
            except Exception:
                frappe.log_error(title="Medusa order reconcile failed", message=frappe.get_traceback())
        if len(orders) < params["limit"]:
            return
        params["offset"] += params["limit"]
