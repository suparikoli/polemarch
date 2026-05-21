# Medusa ↔ ERPNext (Polemarch) Sync — Build Specification

**Status:** Frappe side complete + decoupled. Medusa side not started.
**Build target:** Medusa v2 plugin (TypeScript).
**Build location:** `backrow23.polemarch.in` server, inside the Medusa codebase (SSH access pending).

---

## 1. Context

The Polemarch Frappe app is now **100% Medusa-agnostic**. The Frappe side previously contained:
- Webhook receiver + HMAC verification
- Field-mapping engine + runtime-configurable Polemarch Sync Mapping doctype
- Outbound push (Customer, Item, Sales Invoice mirror)
- Hourly reconcile job polling Medusa
- 4 sync doctypes (Medusa Settings, Medusa Sync Log, Polemarch Sync Mapping, Polemarch Field Map Row)
- 7 Custom Fields (`custom_medusa_*_id` audit refs + `custom_platform_fee`/`low_order_fee`/`stamp_duty`)
- Workspace cards, number cards, dashboard indicators, JS buttons

All of that is gone. Frappe is a passive ERPNext customization that exposes its standard REST API. The Medusa-side plugin owns the entire integration.

---

## 2. Build requirements (user-specified)

1. **Manual sync buttons** — in the Medusa admin, two buttons:
   - "Push to ERPNext" — push selected doctype's data from Medusa → Frappe
   - "Pull from ERPNext" — pull selected doctype's data from Frappe → Medusa
2. **Automated cron sync** — scheduled job runs on a configurable interval (per-doctype interval is a nice-to-have; global interval is the minimum).
3. **Doctype + field selection in Medusa admin** — admin picks which Frappe doctypes to sync and which fields to map.
4. **Field mapping config in Medusa admin** — drag-and-drop or form-based mapping of Medusa entity fields → Frappe doctype fields.

---

## 3. Architecture

```
              ┌──────────────────────────────┐
              │      MEDUSA (v2)             │
              │                              │
              │  Admin Plugin (TypeScript)   │
              │  ┌──────────────────────┐    │
              │  │  Sync Settings UI    │    │   admin selects doctypes,
              │  │  - Doctype picker    │    │   maps fields, sets cron
              │  │  - Field mapper      │    │   interval, hits "Sync Now"
              │  │  - Cron interval     │    │
              │  │  - "Sync Now" btns   │    │
              │  └──────────┬───────────┘    │
              │             │                │
              │  Sync Engine (Modules):      │
              │  ┌──────────▼───────────┐    │
              │  │  ERPNext Sync Module │    │
              │  │  - frappe-client.ts  │    │  HTTP client to Frappe REST
              │  │  - mapper.ts         │    │  field mapping engine
              │  │  - sync-config repo  │    │  persisted config (DB)
              │  │  - sync-log repo     │    │  audit trail of every run
              │  └──────────┬───────────┘    │
              │             │                │
              │  Triggers:                   │
              │  ┌──────────▼───────────┐    │
              │  │  Subscribers (push)  │    │  on customer.created → push
              │  │  Scheduled Jobs (cron)    │  every N min → reconcile
              │  │  Workflows (manual)  │    │  button click → run sync
              │  └──────────────────────┘    │
              │                              │
              └──────────────┬───────────────┘
                             │
                             │  HTTPS — Frappe REST API
                             │  Authorization: token <key>:<secret>
                             ▼
              ┌──────────────────────────────┐
              │   FRAPPE (test.polemarch.in) │
              │                              │
              │   Standard REST API:         │
              │   /api/resource/Customer     │
              │   /api/resource/Item         │
              │   /api/resource/Sales Order  │
              │   /api/resource/Sales Invoice│
              │   /api/method/frappe.client.*│
              │                              │
              │   No Medusa-specific code.   │
              │   Just doctype controllers + │
              │   business logic.            │
              └──────────────────────────────┘
```

---

## 4. Frappe-side prerequisites (deployment ops, not coding)

Before the Medusa plugin can connect, create an API user on the Frappe site:

```python
# On test.polemarch.in:
bench --site test.polemarch.in execute frappe.client.insert --kwargs '{
  "doc": {
    "doctype": "User",
    "email": "medusa-sync@polemarch.in",
    "first_name": "Medusa",
    "last_name": "Sync",
    "send_welcome_email": 0,
    "enabled": 1,
    "user_type": "System User",
    "roles": [
      {"role": "System Manager"},
      {"role": "Sales Manager"},
      {"role": "Sales User"},
      {"role": "Item Manager"},
      {"role": "Accounts Manager"},
      {"role": "Accounts User"},
      {"role": "Customer"}
    ]
  }
}'

# Generate API key:
bench --site test.polemarch.in execute frappe.core.doctype.user.user.generate_keys --kwargs '{"user": "medusa-sync@polemarch.in"}'
# → returns {"api_key": "...", "api_secret": "..."} (secret shown once)
```

Store both in the Medusa plugin's config (env vars or DB), never in source code.

Scoped-down role set (recommended later, after smoke tests work):
- `Customer` (CRUD on Customer + child tables)
- `Item Manager` (CRUD on Item + Item Group + Brand)
- `Sales Manager` (CRUD on SO, SI)
- `Accounts Manager` (CRUD on Payment Entry, GL Entry)

---

## 5. Medusa plugin file structure

```
src/
├── modules/
│   └── erpnext-sync/
│       ├── service.ts             # Sync orchestrator
│       ├── index.ts               # Module entry / registration
│       ├── frappe-client.ts       # HTTP client (axios) for Frappe REST
│       ├── mapper.ts              # Field mapping engine
│       ├── models/
│       │   ├── sync-config.ts     # Persisted sync configuration
│       │   ├── field-mapping.ts   # Per-doctype field mapping rules
│       │   └── sync-log.ts        # Audit log of every sync run
│       ├── repositories/
│       │   ├── sync-config.ts
│       │   ├── field-mapping.ts
│       │   └── sync-log.ts
│       ├── transforms.ts          # Reusable transforms: Paise→Rupees, ISO date, Upper, etc.
│       └── migrations/            # DB migrations for the 3 tables
│
├── admin/
│   ├── routes/
│   │   └── erpnext-sync/
│   │       └── page.tsx           # /admin/erpnext-sync — main settings page
│   ├── widgets/
│   │   └── erpnext-sync-button.tsx  # "Sync to ERPNext" button on Customer/Order/Product detail pages
│   └── components/
│       ├── doctype-picker.tsx     # Multi-select dropdown of Frappe doctypes
│       ├── field-mapper.tsx       # Source field → target field UI
│       ├── cron-interval.tsx      # Cron interval picker (5min, 15min, hourly, daily)
│       ├── sync-now-buttons.tsx   # "Push Now" + "Pull Now" buttons
│       └── sync-log-table.tsx     # Read-only audit log viewer
│
├── api/
│   └── admin/
│       └── erpnext-sync/
│           ├── route.ts                # GET/PUT /admin/erpnext-sync/config
│           ├── sync-now/
│           │   └── route.ts            # POST /admin/erpnext-sync/sync-now {direction, doctype}
│           ├── status/
│           │   └── route.ts            # GET /admin/erpnext-sync/status (recent runs)
│           ├── doctypes/
│           │   └── route.ts            # GET /admin/erpnext-sync/doctypes (list available Frappe doctypes)
│           └── fields/
│               └── [doctype]/
│                   └── route.ts        # GET /admin/erpnext-sync/fields/<doctype>
│
├── subscribers/
│   ├── customer-created.ts        # Medusa Customer → push to Frappe
│   ├── customer-updated.ts        # Same on update
│   ├── order-placed.ts            # Medusa Order → push SO to Frappe
│   ├── order-payment-captured.ts  # → push SI + PE chain
│   ├── order-canceled.ts          # → cancel SI + SO
│   └── product-updated.ts         # Medusa Product → push Item to Frappe
│
└── jobs/
    └── erpnext-cron.ts            # Scheduled bidirectional reconcile
```

---

## 6. Sync flows

### 6.1 Customer (bidirectional)

**Medusa → Frappe** (on `customer.created` / `customer.updated`):

```typescript
// Query first
const existing = await frappeClient.get('/api/resource/Customer', {
  filters: JSON.stringify([['custom_medusa_customer_id', '=', medusaCustomer.id]]),
  // ⚠️ NOTE: This field no longer exists on Frappe! See Section 9.
  fields: JSON.stringify(['name'])
})

if (existing.data.length === 0) {
  await frappeClient.post('/api/resource/Customer', mapCustomerToFrappe(medusaCustomer))
} else {
  await frappeClient.put(`/api/resource/Customer/${existing.data[0].name}`, mapCustomerToFrappe(medusaCustomer))
}
```

**Frappe → Medusa** (cron poll, every N minutes):

```typescript
const lastSync = await syncConfig.get('customer_last_pull')
const changed = await frappeClient.get('/api/resource/Customer', {
  filters: JSON.stringify([['modified', '>', lastSync]]),
  fields: JSON.stringify(['name', 'email_id', 'mobile_no', 'custom_kyc_status', 'modified'])
})

for (const c of changed.data) {
  await medusaCustomerService.update(/* match by email or custom field */, mapCustomerFromFrappe(c))
}

await syncConfig.set('customer_last_pull', now())
```

### 6.2 Product / Item (bidirectional)

Medusa Product ↔ Frappe Item. The Frappe-side `item.validate` hook auto-forces `item_group=Polemarch Securities`, clears `gst_hsn_code`, and links the `Polemarch - Non-GST` Item Tax Template — you don't need to set those.

### 6.3 Order (Medusa → Frappe only — Frappe doesn't originate orders)

`order.placed`:
```
POST /api/resource/Sales Order
{customer, company, transaction_date, delivery_date, po_no, items: [...]}
```

`order.payment_captured`:
1. Submit SO: `POST /api/method/frappe.client.submit {doctype:"Sales Order", name}`
2. Make SI from SO: `POST /api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice {source_name}`
3. Patch SI with fees if needed (see Section 9 about fee fields — they no longer exist on Frappe; either re-add or skip).
4. Submit SI.
5. Make + submit Payment Entry: `POST /api/method/erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry {dt, dn}`

`order.canceled`:
```
POST /api/method/frappe.client.cancel {doctype:"Sales Invoice", name}
# Then cancel SO too
```

### 6.4 Order status reflection (Frappe → Medusa)

Cron polls SI changes:
```
GET /api/resource/Sales Invoice?filters=[["modified",">","<last_poll>"]]&fields=["name","docstatus","status","modified"]
```

Map `docstatus=1, status=Paid` → mark Medusa order as paid. `docstatus=2` → canceled.

**Tracking Frappe ↔ Medusa correlation:** since the audit fields (`custom_medusa_customer_id`, `custom_medusa_order_id`) were removed, the Medusa plugin needs to either:
- (a) Maintain its own mapping table in Medusa's DB: `medusa_id → frappe_name`
- (b) Match by natural keys (email for Customer, `po_no=display_id` for SO/SI)
- (c) Ask user whether to re-add the audit fields on Frappe (one-liner patch reversal — see Section 9)

Recommended: **(a)** — Medusa-side mapping table. Decouples Frappe schema from Medusa concerns entirely.

---

## 7. Doctype + field selection UI

Build a Medusa admin page at `/admin/erpnext-sync` with:

### Tab 1: "Sync Configuration"
- **Frappe URL**: text input (e.g. `https://test.polemarch.in`)
- **API key + secret**: password fields (encrypted at rest)
- **Test connection**: button → calls `/api/method/frappe.auth.get_logged_user` and shows logged user
- **Cron interval**: select (`5min`, `15min`, `30min`, `1hr`, `6hr`, `12hr`, `daily`, `off`)

### Tab 2: "Doctypes"
For each Frappe doctype to sync, a row with:
- **Frappe DocType** (autocomplete from `GET /api/method/frappe.client.get_list {doctype:"DocType"}`)
- **Medusa Entity** (Customer / Product / Order / Custom)
- **Direction** (Push, Pull, Bidirectional)
- **Enabled** (toggle)
- **Map Fields →** (button → opens Tab 3 for that pair)

Default seeded mappings on first install:
- Frappe `Customer` ↔ Medusa `Customer`
- Frappe `Item` ↔ Medusa `Product`
- Frappe `Sales Order` ← Medusa `Order` (one-way)
- Frappe `Sales Invoice` ← Medusa `Order` payment-captured (one-way)

### Tab 3: "Field Mapping" (per doctype)
Two-column UI:
- **Left:** Medusa entity fields (`first_name`, `email`, `metadata.*`)
- **Right:** Frappe doctype fields (fetched from `GET /api/method/frappe.client.get_value {doctype:"DocType", filters:..., fieldname:"fields"}`)
- **Center:** Connect / transform / direction toggle per row

Per-row config:
- **Transform**: select from `Upper`, `Lower`, `Trim`, `Paise to Rupees`, `ISO Date`, `Concatenate`, `Custom JS`
- **Direction**: arrow (← → ↔)
- **Required**: checkbox

### Tab 4: "Sync Now"
- **Push selected doctypes** button (with multi-select)
- **Pull selected doctypes** button (with multi-select)
- **Push everything** button
- **Pull everything** button
- Each button opens a progress modal: shows rows processed, errors, completion time

### Tab 5: "Sync Log"
Paginated table of recent runs:
- Timestamp, doctype, direction, status (Success/Failed/Partial), rows processed, duration, error message
- Filters by doctype, direction, status
- Click row → full request/response viewer

---

## 8. Cron scheduler (Medusa v2 Scheduled Jobs)

```typescript
// src/jobs/erpnext-cron.ts
import { MedusaContainer } from '@medusajs/types'

export default async function erpnextSyncJob(container: MedusaContainer) {
  const syncService = container.resolve('erpnextSyncService')
  const config = await syncService.getConfig()

  if (!config.cron_enabled) return

  const enabledDoctypes = await syncService.getEnabledDoctypes()
  for (const dt of enabledDoctypes) {
    try {
      if (dt.direction === 'push' || dt.direction === 'bidirectional') {
        await syncService.push(dt.doctype)
      }
      if (dt.direction === 'pull' || dt.direction === 'bidirectional') {
        await syncService.pull(dt.doctype)
      }
    } catch (err) {
      await syncService.logError(dt.doctype, err)
    }
  }
}

export const config = {
  name: 'erpnext-sync',
  schedule: '*/15 * * * *',  // ← override at runtime from config.cron_interval
}
```

Medusa v2 doesn't directly support runtime-configurable cron expressions, so the job runs every minute and checks if it's time to actually sync based on the configured interval:

```typescript
export const config = {
  name: 'erpnext-sync',
  schedule: '* * * * *',  // every minute
}

export default async function erpnextSyncJob(container) {
  const config = await syncService.getConfig()
  const lastRun = await syncService.getLastRun()
  const now = Date.now()
  const interval = parseInterval(config.cron_interval)  // returns ms

  if (now - lastRun < interval) return  // not time yet

  // ...actual sync logic
  await syncService.setLastRun(now)
}
```

---

## 9. Frappe-side state notes (important)

The previous Frappe app had **7 Custom Fields** that the old sync used:
- `Customer.custom_medusa_customer_id`
- `Item.custom_medusa_product_id`
- `Sales Order.custom_medusa_order_id`
- `Sales Invoice.custom_medusa_order_id`
- `Sales Invoice.custom_platform_fee`
- `Sales Invoice.custom_low_order_fee`
- `Sales Invoice.custom_stamp_duty`

**All 7 are now removed.** If the Medusa plugin needs them (e.g., to stamp the Medusa ID on Frappe records for cross-system lookup), you have three options:

1. **Don't use them — maintain a Medusa-side mapping table** (`medusa_id → frappe_name`). Recommended.
2. **Re-add them on Frappe side** — write a small reversal of the `v0_6_0.remove_medusa_custom_fields` patch. Adds the field definitions back but not the deleted data. Patches file location: `/Users/manojmbhat/frappe/frappe-bench/apps/polemarch/polemarch/patches/v0_6_0/remove_medusa_custom_fields.py`. Easy to undo.
3. **Use Frappe's native `Document.tags`** to attach Medusa IDs as searchable tags. Works without schema changes but slower to query.

Same for the fee fields (`platform_fee`, `low_order_fee`, `stamp_duty`) — if the Medusa plugin needs to push fee data to Sales Invoice, decide whether:
- Push them into Frappe's existing `additional_discount_amount` / `taxes` table
- Re-add the custom fields
- Treat fees as separate Sales Invoice line items (using the existing `POLEMARCH-PROC-FEE` and `POLEMARCH-LOW-ORDER-FEE` items in Frappe)

The cleanest fee model: **separate SI lines using the existing fee items**. The Frappe app already creates these as service items under `brand=Mithtech Services` (HSN 997152, 18% GST). The Medusa plugin would POST a Sales Invoice with multiple items:

```json
{
  "items": [
    {"item_code": "INE002A01018", "qty": 10, "rate": 2850.50},
    {"item_code": "POLEMARCH-PROC-FEE", "qty": 1, "rate": 50.00},
    {"item_code": "POLEMARCH-LOW-ORDER-FEE", "qty": 1, "rate": 5.00}
  ]
}
```

This avoids custom fields entirely and uses Frappe's standard taxation flow.

---

## 10. Implementation checklist

### Phase 1: Skeleton (1 day)
- [ ] Scaffold Medusa v2 plugin under `src/modules/erpnext-sync/`
- [ ] Add `frappe-client.ts` with axios + auth header injection
- [ ] Add `sync-config` model + repository + migration
- [ ] Test connection: `GET /api/method/frappe.auth.get_logged_user` returns 200
- [ ] Build minimal admin route `/admin/erpnext-sync` with just the "Test Connection" button

### Phase 2: Manual sync for Customer (1 day)
- [ ] Implement `pushCustomer(medusaCustomerId)` — POSTs to `/api/resource/Customer`
- [ ] Implement `pullCustomer(frappeName)` — GET from Frappe, update Medusa
- [ ] Admin route: customer detail page → "Sync to ERPNext" widget
- [ ] Verify in Frappe Desk that the Customer appeared

### Phase 3: Field mapping engine + UI (2 days)
- [ ] `mapper.ts` — applies field-by-field mapping with transforms
- [ ] Persisted `field-mapping` table (one row per source→target pair)
- [ ] Admin Tab 3 UI: "Map Fields" two-column UI
- [ ] Seed default mappings on first install

### Phase 4: All doctypes + bidirectional (2 days)
- [ ] Add Product / Sales Order / Sales Invoice support
- [ ] `sync-log` model + audit logging on every sync attempt
- [ ] Admin Tab 4: "Sync Now" buttons (push all, pull all, push selected, pull selected)
- [ ] Admin Tab 5: sync log viewer

### Phase 5: Subscribers (event-driven push) (1 day)
- [ ] `customer.created` subscriber → push to Frappe
- [ ] `customer.updated` subscriber → push to Frappe
- [ ] `order.placed` subscriber → push to Frappe (SO)
- [ ] `order.payment_captured` subscriber → push to Frappe (SO + SI + PE chain)
- [ ] `order.canceled` subscriber → cancel in Frappe
- [ ] `product.updated` subscriber → push to Frappe (Item)

### Phase 6: Cron scheduler (1 day)
- [ ] `jobs/erpnext-cron.ts` with every-minute trigger
- [ ] Interval check based on `sync_config.cron_interval`
- [ ] Bidirectional reconcile for all enabled doctypes
- [ ] Cron interval picker in admin UI

### Phase 7: Doctype/field discovery (1 day)
- [ ] `GET /api/method/frappe.client.get_list {doctype:"DocType"}` — list all available doctypes
- [ ] `GET /api/resource/DocType/<name>` — fetch field definitions
- [ ] Admin UI: doctype autocomplete + field dropdown (dynamically populated)

### Phase 8: Hardening (1 day)
- [ ] Retry logic (exponential backoff) for Frappe API calls
- [ ] Rate limiting (Frappe has limits; respect 429s)
- [ ] Idempotency: dedup on Medusa-side mapping table before POST
- [ ] Error handling: log to sync_log + show user-facing error messages
- [ ] Test connection with bad credentials (should fail gracefully)

### Phase 9: Smoke tests (1 day)
- [ ] Push a Medusa customer → appears in Frappe
- [ ] Modify in Frappe → pulls back to Medusa
- [ ] Place a Medusa order → SO created in Frappe
- [ ] Capture payment → SI + PE created in Frappe
- [ ] Cancel in Medusa → SI + SO cancelled in Frappe
- [ ] Update KYC status in Frappe → pulled to Medusa metadata
- [ ] Cron runs every interval and surfaces deltas

**Total estimate: ~10 days for a single developer.**

---

## 11. API reference (Frappe REST)

| Operation | Method + URL |
|---|---|
| Auth probe | `GET /api/method/frappe.auth.get_logged_user` |
| List doctypes | `GET /api/method/frappe.client.get_list?doctype=DocType` |
| Get doctype schema | `GET /api/resource/DocType/<name>` |
| List records | `GET /api/resource/<DocType>?filters=...&fields=...&limit_page_length=N` |
| Get one record | `GET /api/resource/<DocType>/<name>` |
| Create record | `POST /api/resource/<DocType>` body: `{...fields}` |
| Update record | `PUT /api/resource/<DocType>/<name>` body: `{...patch_fields}` |
| Delete record | `DELETE /api/resource/<DocType>/<name>` |
| Submit a submittable | `POST /api/method/frappe.client.submit {doctype, name}` |
| Cancel a submittable | `POST /api/method/frappe.client.cancel {doctype, name}` |
| Run a whitelisted method | `POST /api/method/<module.method>` body: `{...args}` |
| Make SI from SO | `POST /api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice {source_name}` |
| Make PE from SI | `POST /api/method/erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry {dt, dn}` |

All requests require:
```
Authorization: token <api_key>:<api_secret>
Content-Type: application/json
```

Frappe returns:
- `{"data": {...}}` on success (single record)
- `{"data": [...]}` on success (list)
- `{"message": ...}` for method calls
- `{"exception": "...", "exc_type": "...", "exc": "..."}` on error (with HTTP 4xx/5xx)

---

## 12. Deployment (when ready)

1. SSH to `backrow23.polemarch.in`
2. Locate the Medusa codebase (likely `~/medusa/` or `/var/medusa/`)
3. Check if an existing `medusa-plugin-erpnext` or similar exists (the "ERPNext" extension visible in the admin sidebar suggests one might)
4. If yes: read it, decide whether to extend or replace
5. If no: scaffold a fresh plugin under `src/modules/erpnext-sync/` per Section 5
6. Install: `npm install --save axios` (for the Frappe client)
7. Add to `medusa-config.ts`:
   ```typescript
   modules: [
     // ...existing modules
     {
       resolve: './src/modules/erpnext-sync',
       options: {
         frappe_url: process.env.FRAPPE_URL,
         api_key: process.env.FRAPPE_API_KEY,
         api_secret: process.env.FRAPPE_API_SECRET,
       },
     },
   ]
   ```
8. Set env vars in `.env`:
   ```
   FRAPPE_URL=https://test.polemarch.in
   FRAPPE_API_KEY=<new key>
   FRAPPE_API_SECRET=<new secret>
   ```
9. Run migrations: `npx medusa db:migrate`
10. Build admin: `npx medusa build`
11. Restart Medusa
12. Visit `/admin/erpnext-sync` → configure → test connection

---

## 13. Things explicitly NOT to do

- ❌ **Don't add custom doctypes to Frappe** for sync purposes — keep all state on the Medusa side
- ❌ **Don't add webhooks** unless the storefront has hard latency requirements — cron polling at 5-minute intervals is fine for this volume
- ❌ **Don't store Frappe API credentials in source code** — env vars + secret manager only
- ❌ **Don't bypass Frappe's docstatus** (submit/cancel cycle) — always go through `frappe.client.submit` / `frappe.client.cancel`, not by directly PATCHing `docstatus`
- ❌ **Don't sync the legacy `Investment Holding` / `Investment Disposal` doctypes** — those are Frappe-internal accounting records that get created by SI submit hooks, not by external sync
- ❌ **Don't sync `Sales Invoice.taxes` directly** — let Frappe's tax engine compute it from the Item Tax Templates (Polemarch shares are Non-GST automatically; fee items are 18% GST automatically)

---

## 14. Open questions for the implementing team

1. **Existing ERPNext extension** in the Medusa admin — is it stale dead code, a started but abandoned plugin, or actively used? If active, what does it currently do? Decision needed before Phase 1.
2. **Audit field re-introduction**: do you want me to re-add `custom_medusa_*_id` fields on Frappe? Or stick with Medusa-side mapping table? See Section 9.
3. **KYC reverse-sync**: when a Frappe admin marks a customer's KYC as Verified/Rejected, should the storefront immediately reflect that, or is the 5-15 minute cron lag acceptable?
4. **Item creation**: should the Medusa plugin create new ERPNext Items when a Medusa Product is created, or should Items be created in Frappe first and only synced TO Medusa? The latter is safer for ERPNext's brand=Polemarch invariants.
5. **Multi-company support**: Frappe has 21 Companies (mostly test fixtures). Which Company should orders/invoices be created in? Configure in the sync_config or hardcoded?

---

## 15. Reference — current Frappe state

**Site:** `https://test.polemarch.in`
**Frappe version:** v16 (Frappe + ERPNext + India Compliance + HRMS + Polemarch customizations)
**Branch:** `develop` HEAD `5065414`
**Custom Frappe customizations active (NOT for sync):**
- Polemarch Customer Group + KYC custom fields
- Polemarch share Items (brand=Polemarch, Non-GST via Item Tax Template)
- Mithtech Services service items (brand=Mithtech Services, 18% GST, HSN 997152)
- Sales Invoice / Sales Order brand-validation + cost-center allocation hooks
- Investment Holding / Investment Disposal FIFO engine (auto-triggered on SI submit)
- Trading subsystem (17 doctypes): Security, Portfolio, Wallet, Trade Order, Settlement Instruction, Portfolio Transfer, etc.

**Custom Frappe customizations removed (all Medusa sync surface):**
- `polemarch/medusa/` directory (entire module)
- 4 sync doctypes (Medusa Settings, Medusa Sync Log, Polemarch Sync Mapping, Polemarch Field Map Row)
- 7 sync custom fields (medusa IDs + fees)
- Scheduler hourly reconcile
- 3 dashboard number cards
- Workspace links + indicators + JS buttons
- KYC outbound push
