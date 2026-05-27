# Security Purchase cycle

The Security Purchase doctype is **Polemarch's only entry point for acquiring inventory**. It covers two distinct business situations (parameterised by `party_type`) and four settlement rails (parameterised by `payment_method`).

```
                      ┌──────────────────────────┐
                      │   Security Purchase      │
                      └────────┬─────────────────┘
                               │
                    party_type ┼─────────────────┐
                               ▼                 ▼
                  ┌─────────────────┐  ┌──────────────────┐
                  │   Supplier      │  │     Customer     │
                  │  (off-market)   │  │  (selling back)  │
                  └────────┬────────┘  └────────┬─────────┘
                           │                    │
            payment_method ┼─────────┐    ┌─────┼─────────┐
                           ▼         ▼    ▼     ▼         ▼
                   Default Payable / Bank / Cash    Customer Wallet
```

## What Security Purchase IS NOT

It does **not** drive customer-buying. Polemarch SELLING to a customer is a `Security Sale` (see `sales-cycle.md`). Security Purchase is always inbound inventory.

It also doesn't run a multi-step order lifecycle. It's an instant submit — wallet / GL / inventory move in one transaction. The old `Trade Order` lifecycle was dropped in Phase 13.

---

## Classification model (Phase 24B — current as of v0_24_0)

**One Security Purchase → one Investment Holding.** No fragmentation. The IH carries a child-table `classifications` that the operator (or scheduler) appends to, never edits or deletes.

```
Investment Holding INV-HOLD-XXXX
├── qty_acquired:               1,000   (immutable)
├── qty_disposed:                  0    (bumped by FIFO sales OR Portfolio Transfer)
├── qty_disposed_sit:             0    (per-class PT-out tracker)
├── qty_disposed_investment:      0
└── classifications  (append-only child rows)
    ├── Row 1: Stock in Trade   ×  400   (added by Classify Qty, 22 May 13:15, by ops@…)
    ├── Row 2: Investment       ×  300   (added by Classify Qty, 22 May 14:02, by ops@…)
    └── Row 3: Stock in Trade   ×  300   (auto, day-5 scheduler)
```

### Computed rollup (read-only on the form)
- `qty_classified_sit`        = Σ SiT rows − qty_disposed_sit
- `qty_classified_investment` = Σ Inv rows − qty_disposed_investment
- `qty_unclassified`          = qty_remaining − qty_classified_sit − qty_classified_investment

These power the **4-cell breakdown panel** at the top of every Investment Holding form (and a similar panel on the Security form summing across all open Holdings).

### The 5-business-day classification window

When an IH is minted, `classification_deadline = acquisition_date + 5 business days` (skipping the company's Holiday List). Within the window:

- **Classify Qty button** (Actions menu) — appends a child row, no JE, no fragmentation. Idiomatic during-window path.
- Operator may classify partial qty (e.g. 400 SiT now, 300 Investment later, leave 300 Unclassified).
- The form shows a colored deadline countdown banner: 🟢 > 24h · 🟠 6–24h · 🔴 < 6h or past.

At the **end of the window** (day 5):

- The daily `auto_classify_expired_unallocated` scheduler flips remaining Unclassified qty → Stock in Trade automatically (adds one child row with `auto_classified=1`).
- The Classify Qty button disappears from the form.
- **Split & Classify** (Portfolio Transfer) becomes the only path for further SiT ↔ Investment movement. PT posts a JE (DR Long-Term Investments, CR Securities Inventory or vice versa) and **adds rows in-place** to the same IH — still no new IH minted (Phase 24B-2).

### Append-only enforcement

Three defensive layers stop tampering with the classifications grid:

1. **Form-level**: the child table is `read_only=1` on the IH form — `+Add Row` and bulk-delete controls are hidden.
2. **Row-level**: each row's `classification` + `qty` fields are `read_only=1`. Opening the row-detail modal shows the values; all inputs are `disabled`.
3. **Server-level**: `_validate_classification_rows` rejects edits to existing rows, deletions, and over-cap qty (Σ classified rows net of qty_disposed_<class> > qty_remaining).

The only path to add a classification row is **Actions → Classify Qty** within the window, or Portfolio Transfer post-window.

---

## Cycle 1 — buying from a Supplier (off-market acquisition)

### Step-by-step

1. **Create Security** (one-time per ISIN — `/app/security/new`)
   - ISIN, security_name, security_type (default `Unlisted Shares`), face_value
2. **Create Supplier** (one-time — `/app/supplier/new`)
3. **Create Security Purchase** (`/app/security-purchase/new`)

| Field | Example |
|---|---|
| Security | `INE0DJ201029` (API Holdings) |
| Party Type | `Supplier` |
| Party | `Acme Sellers LLP` |
| Initial Classification | `Unallocated` (default) / `Stock in Trade` / `Investment` |
| Qty | 1,000 |
| Rate | ₹120 |
| Amount | ₹1,20,000 *(auto)* |
| Payment Method | `Default Payable` (defer cash) / `Bank` / `Cash` |
| Payment Account | auto-defaults from method |

> `Initial Classification` is a **create-time hint only**. After submit, the field disappears from the form. The classifications **child table** is where ongoing reality lives.

4. **Submit** — `on_submit` posts atomically:

   - **Journal Entry:**
     ```
     DR  Securities Inventory - Trading   ₹1,20,000
     CR  <Creditors / Bank / Cash>        ₹1,20,000
     ```
     For Default Payable, the credit row carries `party_type=Supplier, party=Acme Sellers LLP` so the supplier's AP ledger lines up.

   - **Investment Holding** minted with:
     - `security`, `company`, `qty_acquired=1000`, `cost_basis_per_unit=120`
     - `purchase_reference="Security Purchase"`, `purchase_reference_link=<SP name>`
     - `classification_deadline = acquisition_date + 5 business days`
     - One classification child row IF `Initial Classification` was non-Unallocated; else zero child rows (fully Unclassified).

5. **Within the 5-day window** — operator can classify via **Actions → Classify Qty** on the IH form. Each call appends a child row. Sum can be < qty_acquired (leftover goes to day-5 auto-SiT).

6. **At day 5** — scheduler appends one final SiT row for any remaining Unclassified qty.

7. **Settle the supplier:**
   - **Default Payable**: post a separate ERPNext Payment Entry later (`DR Creditors / CR Bank`)
   - **Bank** / **Cash**: nothing more to do — settlement already happened at submit

8. **(Off-system)** Submit DIS / off-market transfer to your DP. Supplier signs, shares move into Polemarch's demat. The Investment Holding from step 4 already represents the inventory — this is the legal/physical move to align with the books.

### End state

```
✓ Journal Entry (DR Inventory, CR Creditors/Bank)
✓ Investment Holding (one doc, all classifications via child rows)
✓ Open AP balance against Supplier (Default Payable case)
Customer Holding: untouched
Customer Wallet:  untouched
```

---

## Cycle 2 — buying from a Customer (Polemarch acquires from a holder)

Polemarch buys shares back from a customer who held them. Typically settles into the customer's wallet so they can roll it into their next purchase or withdraw later.

### Step-by-step

1. **Security** + **Customer** already exist (with active Wallet)
2. **Create Security Purchase**

| Field | Example |
|---|---|
| Security | `INE0DJ201029` |
| Party Type | `Customer` |
| Party | `Ravi Kumar` |
| Initial Classification | `Unallocated` (default — operator can Classify Qty within window) |
| Qty | 50 |
| Rate | ₹130 |
| Amount | ₹6,500 *(auto)* |
| Payment Method | `Customer Wallet` *(typical)* — credits Ravi's wallet. Or `Bank` / `Cash` / `Default Payable` for other rails. |
| Payment Account | auto-defaults: Wallet → Ravi's `Customer Wallet Liability` GL; Bank → company default bank |

3. **Submit** — `on_submit` posts atomically (in order):

   **a. Investment Holding** minted (proprietary):
   - `security`, `qty_acquired=50`, `cost_basis_per_unit=130`, classification child rows per Initial Classification setting (default: zero rows / Unclassified).

   **b. Customer Holding snapshot decremented** (CRM side-effect, NOT on the books):
   - `Ravi Kumar-INE0DJ201029` snapshot → `qty -= 50`, `source="Security Purchase"`, `last_updated=now`
   - If snapshot was 200 before, it's now 150
   - If snapshot < 50 (drift), clamps to 0 + appends a timestamped drift note to `notes`

   **c. Wallet Transaction posted** (only when `payment_method=Customer Wallet`):
   ```
   Credit Sell Payout  amount=₹6,500  on  WAL-Ravi Kumar
   → balance_available + ₹6,500, balance_total + ₹6,500
   ```

   **d. Journal Entry:**
   - Customer Wallet payment:
     ```
     DR  Securities Inventory - Trading   ₹6,500
     CR  Customer Wallet Liability        ₹6,500   (party_type=Customer)
     ```
   - Bank payment:
     ```
     DR  Securities Inventory - Trading   ₹6,500
     CR  <Bank Account>                   ₹6,500
     ```
   - Default Payable payment (deferred):
     ```
     DR  Securities Inventory - Trading   ₹6,500
     CR  Creditors (party=Customer Ravi Kumar)  ₹6,500
     ```

4. **(Off-system)** Shares move from Ravi's demat → Polemarch's. Books already match.

### End state (Customer Wallet path)

```
✓ Journal Entry (DR Inventory, CR Customer Wallet Liability)
✓ Investment Holding (proprietary, classifications child table empty initially)
✓ Wallet Transaction (Credit Sell Payout — wallet balance + ₹6,500)
✓ Customer Holding (qty -= 50, source="Security Purchase")
```

---

## Validation rules

| Combination | Allowed? | Why |
|---|---|---|
| Supplier + Default Payable | ✅ | Standard prop acquisition |
| Supplier + Bank | ✅ | Direct payment |
| Supplier + Cash | ✅ | Cash payment |
| Supplier + Customer Wallet | ❌ | Suppliers don't have wallets — `_validate_party_payment_combo` throws |
| Customer + Customer Wallet | ✅ | Customer sells, gets paid into wallet |
| Customer + Bank | ✅ | Direct bank payout |
| Customer + Cash | ✅ | Cash payout |
| Customer + Default Payable | ✅ | Deferred — JE row carries party_type=Customer |

Security must be `tradable=1, active=1` regardless of party. Qty > 0 and rate ≥ 0 enforced at validate.

---

## Cancel flow

`before_cancel` (Phase 28 fix) severs the linked Wallet Transaction's reference fields so Frappe's check_links allows the cancel to proceed. `on_cancel` reverses everything atomically (in order):

1. **Guard** — throws if the linked Investment Holding has `qty_disposed > 0` or `qty_reserved > 0`. Operator must reverse the downstream Sale / PT first.
2. **Wallet** (if applicable) — `wallet.reverse(wt_name, ...)` posts a Reversal that debits the wallet by the same amount. The original WT is marked `is_cancelled=1` and gets `reversed_by=<new-row>`.
3. **Customer Holding** (if `party=Customer`) — restore via `apply_delta(+qty)` with `source="Security Purchase"` and a "restored on cancel" note.
4. **Investment Holding** — deleted (only when untouched, per the guard).
5. **Journal Entry** — cancelled (ERPNext auto-reverses the GL Entry).

The pre-Phase-28 `LinkExistsError` on customer-side Purchases (Wallet Transaction blocking SP cancel) is now resolved.

---

## Side-effects matrix

| Doctype written | Supplier purchase | Customer purchase (any payment) |
|---|---|---|
| Investment Holding | ✅ minted (proprietary) | ✅ minted (proprietary) |
| └── `classifications` child rows | seeded with Initial Classification (if set), else empty | same |
| Journal Entry | ✅ DR Inventory / CR pay account | ✅ DR Inventory / CR pay account |
| Wallet Transaction | — | only if `payment=Customer Wallet`: ✅ Credit Sell Payout |
| Customer Holding | — | ✅ qty decremented (CRM side-effect, no GL impact) |
| Investment Disposal | — | — (Polemarch doesn't track customer tax events) |
| Supplier ledger (AP) | ✅ open for Default Payable; settled inline for Bank/Cash | — |

---

## Where to read the data after submit

| Surface | Purpose |
|---|---|
| **Investment Holding form** (`/app/investment-holding/<name>`) | Per-lot view: breakdown panel (SiT · Investment · Unclassified · Total) + classifications child table + deadline countdown banner + Classify Qty button |
| **Security form** (`/app/security/<isin>`) | Per-security rollup: same 4-cell breakdown cards + LCM table (cost vs fair value vs lower-of) summed across all open Holdings |
| **Security list view** (`/app/security`) | Cross-security: SiT / Investment / Unclassified columns sortable + filterable (cached on Security via Phase 22) |
| **Polemarch workspace** (`/app/polemarch`) | Holdings Summary cards: Total · SiT Value · Investment Value · Unclassified Value · Open Lots |
| **Query Report** | `Polemarch Holdings by Security` — printable per-security rollup |

---

## Concrete worked example — `INE0DJ201029`

Polemarch buys 1,000 shares of API Holdings from `Acme Sellers LLP` at ₹120 on payment terms (Default Payable), leaves classification undecided:

```
1. /app/security-purchase/new
   ├─ Security:                 INE0DJ201029
   ├─ Party Type:               Supplier
   ├─ Party:                    Acme Sellers LLP
   ├─ Initial Classification:   Unallocated  ← decide later
   ├─ Qty:                      1,000
   ├─ Rate:                     120
   ├─ Amount (auto):            1,20,000
   ├─ Payment Method:           Default Payable
   └─ Submit
        ↓
2. JE posted: DR 1410-Securities Inventory ₹1,20,000
              CR 2110-Creditors            ₹1,20,000  (party_type=Supplier, party=Acme)
3. Investment Holding INV-HOLD-2026-XXXXX minted
   ├─ qty_acquired=1,000  cost_basis_per_unit=120
   ├─ classifications: []   (empty — fully Unclassified)
   ├─ classification_deadline = today + 5 business days
   └─ Form shows: SiT 0 · Investment 0 · Unclassified 1,000 · Total 1,000
4. Open AP balance: ₹1,20,000 owed to Acme

(Day 1 — operator decides to split)
5. Open IH → Actions → Classify Qty
        ↓
   Qty 600, Classification = Stock in Trade.  Submit.
   → child row #1 appended.  Breakdown: SiT 600 · Investment 0 · Unclassified 400.

6. Open IH → Actions → Classify Qty again
        ↓
   Qty 200, Classification = Investment.  Submit.
   → child row #2 appended.  Breakdown: SiT 600 · Investment 200 · Unclassified 200.

(Day 5 — operator hasn't classified the last 200)
7. Scheduler auto_classify_expired_unallocated runs
   → child row #3 appended:  Stock in Trade × 200, auto_classified=1.
   Final breakdown: SiT 800 · Investment 200 · Unclassified 0 · Total 1,000.

(Later, paying Acme)
8. /app/payment-entry/new (against Acme)
        ↓
   DR Creditors ₹1,20,000  CR Bank ₹1,20,000
   → AP closed

(Even later, operator decides 100 SiT should have been Investment)
9. Open IH → Actions → Split & Classify
        ↓
   Qty 100 from SiT → Investment.  Submit (creates Portfolio Transfer POL-PT-XXX).
   → PT posts JE: DR Long-Term Investments ₹600  CR Securities Inventory ₹600.
   → qty_disposed_sit on IH bumps by 100.
   → A new child row appended on the SAME IH: Investment × 100.
   Final: SiT 700 · Investment 300 · Unclassified 0 · Total 1,000.   ← same IH, NO fragmentation.

(Off-system)
10. Acme signs DIS → shares land in Polemarch's demat → reconciled.
```

---

## Why a single doctype handles both Supplier / Customer paths

Conceptually, both flows do the same three things from Polemarch's GL perspective:

1. Polemarch's **Inventory** goes UP (DR Securities Inventory)
2. Polemarch **owes** more cash somewhere (CR Creditors / Bank / Cash / Customer Wallet Liability)
3. An **Investment Holding** row records the new lot for FIFO

The party type and payment method just parameterise the credit account + the side-effects. One doctype, one mental model, one audit trail — instead of separate `Supplier Purchase` and `Customer Buyback` doctypes that would duplicate 90% of the logic.

The classification side-effects (child table, window, auto-classifier, in-place PT) layer on top of this without affecting the at-submit JE posting.

---

## Phase history (recent classification-model changes)

| Phase | What changed |
|---|---|
| **24A** | Classification window bumped 2 → 5 business days. Unclassified row in LCM table + Unclassified Value Number Card on workspace. |
| **24A.2** | Deadline countdown rendered on IH form + LCM table (green/amber/red by urgency). |
| **24B-1** | New `Investment Holding Classification` child doctype + `classifications` Table Custom Field on IH. Backfilled existing IHs into single-row child tables. Classify Qty button + whitelisted API. Breakdown panel on IH form. |
| **24B-2** | Portfolio Transfer's `_transfer_holdings` adds a child row in-place to the source IH instead of minting a new doc. `qty_disposed_<class>` counters subtract from class totals. **No more fragmentation.** |
| **24B-3** | Form cleanup: hide redundant `status` badge, `classification` Select, `classified_on`, `classified_by`, `qty_reserved`. DB columns stay for FIFO + reports. |
| **28** | `before_cancel` severs the WT.reference link so customer-side Purchase / Sale cancels work cleanly. |

---

## Related docs

- `sales-cycle.md` — the Security Sale flow (disposal side)
