# Polemarch Customizations

ERPNext customizations for the **Polemarch** business unit (securities trading: unlisted shares, pre-IPO, bonds, MFs, AIF, REIT, InvIT) operating under **Mithtech Innovative Solutions PVT LTD**, alongside the **Mithtech Services** business unit.

This app provides:
- Brand-aware data model separating Polemarch (no GST) from Mithtech Services (GST 18%)
- A Sale Transfer Order print format for Polemarch sales invoices
- Bidirectional sync with a Medusa v2 storefront (Items, Customers, Orders) — only Polemarch entities sync
- A Polemarch desk workspace with charts, number cards, and shortcuts

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site <your-site> install-app polemarch
bench build --app polemarch
bench --site <your-site> migrate
```

The `after_install` / `after_migrate` hooks idempotently create:
- Brands `Polemarch` and `Mithtech Services`
- Customer Group `Polemarch`
- Custom fields on Customer, Item, Sales Invoice, Sales Order
- Sales Taxes and Charges Templates per Company: `Polemarch - No GST` and `Mithtech Services - GST 18%`

## Brand-aware behaviour

- A Customer is auto-tagged `custom_is_polemarch_customer = 1` if they have a row in `custom_dp_details` or are in the `Polemarch` Customer Group.
- A Sales Invoice / Sales Order is auto-tagged `custom_is_polemarch_invoice` (or `_order`) if **all** line items have `brand = Polemarch`. Mixed-brand documents are rejected.
- The relevant tax template is auto-applied if the user hasn't picked one.
- The Sales Invoice print dialog defaults to **Polemarch Sale Transfer Order** for Polemarch invoices and **GST Tax Invoice** for Mithtech Services invoices.

## Configuring the Medusa Integration — ERPNext side

1. Open **Medusa Settings** (Single DocType: Desk → search "Medusa Settings").
2. Fill in:
   - `Medusa URL` — base URL of your Medusa v2 backend, e.g. `https://store.polemarch.in`
   - `Medusa Admin API Key` — created in the Medusa Admin UI under Settings → API Key Management → "Secret Keys"
   - `Medusa Publishable API Key` — same UI, "Publishable Keys" tab; required for storefront API
   - `Medusa Webhook Secret` — any random 32+ char string; must match the secret you set on the Medusa side (see below)
   - `Default Sales Channel ID`, `Default Region ID`, `Default Warehouse`, `Default Brand` (= `Polemarch`), `Default Polemarch Customer Group` (= `Polemarch`), `Default Price List`
   - Tick `Enable Sync`
3. Save. The controller validates that URL and admin API key are present.
4. Inbound webhook URL exposed by ERPNext:
   ```
   https://<your-erpnext-site>/api/method/polemarch.medusa.webhooks.receive
   ```
   This endpoint is `allow_guest=True`. Security is HMAC-SHA256 of the raw body using the `Medusa Webhook Secret`, sent in header `x-medusa-signature`. Requests without a valid signature are rejected with 401 and logged in `Medusa Sync Log`.
5. Manual full re-sync (for backfill or recovery):
   ```bash
   bench --site <site> execute polemarch.medusa.reconcile.full_resync
   ```
6. The hourly reconciler (`polemarch.medusa.reconcile.run_hourly`) is auto-scheduled via `scheduler_events.hourly`. It catches missed webhooks and re-pushes Polemarch items.

## Configuring the Medusa Integration — Medusa v2 side

1. In the Medusa Admin UI, create an **Admin API Key** and a **Publishable API Key**. Paste both into ERPNext's `Medusa Settings`.
2. Add a Subscriber that forwards Medusa events to ERPNext. Create `src/subscribers/erpnext-forward.ts`:
   ```ts
   import type { SubscriberConfig, SubscriberArgs } from "@medusajs/framework"
   import crypto from "crypto"

   const ERPNEXT_URL = process.env.ERPNEXT_URL!         // e.g. https://erp.polemarch.in
   const SECRET     = process.env.ERPNEXT_WEBHOOK_SECRET!

   export default async function forwardToErpnext({ event }: SubscriberArgs<any>) {
     const body = JSON.stringify({ event: event.name, data: event.data, id: event.id })
     const signature = crypto.createHmac("sha256", SECRET).update(body).digest("hex")
     await fetch(`${ERPNEXT_URL}/api/method/polemarch.medusa.webhooks.receive`, {
       method: "POST",
       headers: {
         "Content-Type": "application/json",
         "x-medusa-signature": signature,
         "x-medusa-event-id": event.id ?? "",
       },
       body,
     })
   }

   export const config: SubscriberConfig = {
     event: [
       "customer.created",
       "customer.updated",
       "order.placed",
       "order.payment_captured",
       "order.fulfillment_created",
       "order.canceled",
     ],
   }
   ```
3. In Medusa's `.env`:
   ```
   ERPNEXT_URL=https://erp.polemarch.in
   ERPNEXT_WEBHOOK_SECRET=<same value as Medusa Webhook Secret in ERPNext>
   ```
4. Restart the Medusa server. The subscriber registers automatically.

## Adding a new outbound API call (ERPNext → Medusa)

All outbound calls go through `polemarch.medusa.client.MedusaClient`:

```python
from polemarch.medusa.client import get_client

client = get_client()        # returns None if sync is disabled
if client:
    response = client.post("/admin/products", json_body={"title": "..."}, idempotency_key="…")
```

Rules:
- Wrap pushes triggered from doc events in `frappe.enqueue(..., queue="short", enqueue_after_commit=True)` so form saves stay snappy and a transient Medusa outage doesn't break the user's save.
- Always emit a `Medusa Sync Log` row via `polemarch.medusa.log.write_log(...)` — both on success and on failure.
- Pass an `idempotency_key` derived from the entity name + last-modified hash so retries don't dupe.

## Adding a new inbound webhook event

1. Add the event name to `EVENT_DISPATCH` in [polemarch/medusa/webhooks.py](polemarch/polemarch/medusa/webhooks.py) and write a small handler that delegates to the right module:
   ```python
   def _handle_my_new_event(data, event_id=None):
       from polemarch.medusa.sync_orders import handle_my_new_event
       return handle_my_new_event(data, event_id=event_id)

   EVENT_DISPATCH["order.something_new"] = _handle_my_new_event
   ```
2. Implement the handler in `polemarch/medusa/sync_orders.py` (or wherever it belongs) and call `write_log(...)` for both outcomes.
3. On the Medusa side, append the event name to the `event` array of `erpnext-forward.ts` and restart Medusa.
4. Test end-to-end with a captured payload:
   ```bash
   BODY='{"event":"order.something_new","data":{...}}'
   SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$ERPNEXT_WEBHOOK_SECRET" | awk '{print $2}')
   curl -X POST https://<site>/api/method/polemarch.medusa.webhooks.receive \
     -H "Content-Type: application/json" \
     -H "x-medusa-signature: $SIG" \
     -d "$BODY"
   ```

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Sync isn't happening | `Medusa Settings.enable_sync` is on? `Error Log` and `Medusa Sync Log` for failures. |
| Webhook returns 401 | Secret mismatch between `Medusa Webhook Secret` in ERPNext and `ERPNEXT_WEBHOOK_SECRET` in Medusa env. |
| Stale data | `bench --site <site> execute polemarch.medusa.reconcile.full_resync` |
| Wrong print format on Sales Invoice | Make sure all line items have `brand = Polemarch`. Mixed brands trigger a validation error and won't auto-tag. |

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/polemarch
pre-commit install
```

Tools: `ruff`, `eslint`, `prettier`, `pyupgrade`.

## License

MIT
