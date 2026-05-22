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

It also doesn't run a multi-step order lifecycle. It's an instant submit — wallet/GL/inventory move in one transaction. The old `Trade Order` lifecycle was dropped in Phase 13.

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
| Classification | `Unallocated` (default — decide within 2 working days), or `Stock in Trade` / `Investment` upfront |
| Qty | 1,000 |
| Rate | ₹120 |
| Amount | ₹1,20,000 *(auto)* |
| Payment Method | `Default Payable` (defer cash) / `Bank` / `Cash` |
| Payment Account | auto-defaults from method |

4. **Submit** — `on_submit` posts atomically:
   - **Journal Entry:**
     ```
     DR  Securities Inventory - Trading   ₹1,20,000
     CR  <Creditors / Bank / Cash>        ₹1,20,000
     ```
     For Default Payable, the credit row carries `party_type=Supplier, party=Acme Sellers LLP` so the supplier's AP ledger lines up.
   - **Investment Holding** minted:
     - `security`, `company`, `qty_acquired=1000`, `cost_basis_per_unit=120`
     - `classification` = whatever you picked
     - `purchase_reference="Security Purchase"`, `purchase_reference_link=<SP name>`
     - `status="Open"`

5. **If classification = Unallocated** → daily scheduler auto-classifies to `Stock in Trade` after 2 working days. Within that window, an authorised user can manually re-classify to `Investment`.

6. **Settle the supplier:**
   - **Default Payable**: post a separate ERPNext Payment Entry later: `DR Creditors / CR Bank`
   - **Bank** / **Cash**: nothing more to do — settlement already happened at submit

7. **(Off-system)** Submit DIS / off-market transfer to your DP. Supplier signs, shares move into Polemarch's demat. **The Investment Holding from step 4 already represents the inventory** — this is the legal/physical move to align with the books.

### End state

```
✓ Journal Entry (DR Inventory, CR Creditors/Bank)
✓ Investment Holding (proprietary)
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
| Classification | forced to `Stock in Trade` by the controller (Polemarch acquired to resell) |
| Qty | 50 |
| Rate | ₹130 |
| Amount | ₹6,500 *(auto)* |
| Payment Method | `Customer Wallet` *(typical)* — credits Ravi's wallet. Or `Bank` / `Cash` / `Default Payable` for other rails. |
| Payment Account | auto-defaults: Wallet → Ravi's `Customer Wallet Liability` GL; Bank → company default bank |

3. **Submit** — `on_submit` posts atomically (in order):

   **a. Investment Holding** minted (proprietary, Stock in Trade):
   - `security`, `qty_acquired=50`, `cost_basis_per_unit=130`, classification=`Stock in Trade`

   **b. Customer Holding snapshot decremented** (CRM side-effect, NOT on the books):
   - `WAL`-pair `Ravi Kumar-INE0DJ201029` → `qty -= 50`, `source="Security Purchase"`, `last_updated=now`
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
✓ Investment Holding (proprietary, Stock in Trade)
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

`on_cancel` reverses everything atomically (in order):

1. **Guard** — throws if the linked Investment Holding has `qty_disposed > 0` or `qty_reserved > 0`. Operator must reverse the downstream Sale first.
2. **Wallet** (if applicable) — `wallet.reverse(wt_name, ...)` posts a Reversal that debits the wallet by the same amount.
3. **Customer Holding** (if `party=Customer`) — restore via `apply_delta(+qty)` with `source="Security Purchase"` and a "restored on cancel" note.
4. **Investment Holding** — deleted (only when untouched, per the guard).
5. **Journal Entry** — cancelled (ERPNext auto-reverses the GL Entry).

---

## Side-effects matrix

| Doctype written | Supplier purchase | Customer purchase (any payment) |
|---|---|---|
| Investment Holding | ✅ minted (proprietary) | ✅ minted (proprietary, Stock in Trade) |
| Journal Entry | ✅ DR Inventory / CR pay account | ✅ DR Inventory / CR pay account |
| Wallet Transaction | — | only if `payment=Customer Wallet`: ✅ Credit Sell Payout |
| Customer Holding | — | ✅ qty decremented (CRM side-effect, no GL impact) |
| Investment Disposal | — | — (Polemarch doesn't track customer tax events) |
| Supplier ledger (AP) | ✅ open for Default Payable; settled inline for Bank/Cash | — |

---

## Concrete worked example — `INE0DJ201029`

Polemarch buys 100 shares of API Holdings from `Acme Sellers LLP` at ₹120 on payment terms (Default Payable):

```
1. /app/security-purchase/new
   ├─ Security:             INE0DJ201029
   ├─ Party Type:           Supplier
   ├─ Party:                Acme Sellers LLP
   ├─ Classification:       Unallocated
   ├─ Qty:                  100
   ├─ Rate:                 120
   ├─ Amount (auto):        12,000
   ├─ Payment Method:       Default Payable
   └─ Submit
        ↓
2. JE posted: DR 1410-Securities Inventory ₹12,000
              CR 2110-Creditors            ₹12,000  (party_type=Supplier, party=Acme)
3. Investment Holding INV-HOLD-2026-NNNNN minted (Unallocated; deadline = today + 2 working days)
4. Open AP balance: ₹12,000 owed to Acme

(Later, when you pay)
5. /app/payment-entry/new (against Acme)
        ↓
   DR Creditors ₹12,000  CR Bank ₹12,000
   → AP closed

(Within 2 working days)
6. Open the Holding → set Classification = Investment
   (or leave it; auto becomes Stock in Trade)

(Off-system)
7. Acme signs DIS → shares land in Polemarch's demat → reconciled.
```

---

## Why a single doctype handles both

Conceptually, both flows do the same three things from Polemarch's GL perspective:

1. Polemarch's **Inventory** goes UP (DR Securities Inventory)
2. Polemarch **owes** more cash somewhere (CR Creditors / Bank / Cash / Customer Wallet Liability)
3. An **Investment Holding** row records the new lot for FIFO

The party type and payment method just parameterise the credit account + the side-effects. One doctype, one mental model, one audit trail — instead of separate `Supplier Purchase` and `Customer Buyback` doctypes that would duplicate 90% of the logic.

---

## Related docs

- `sales-cycle.md` — the Security Sale flow (disposal side)
- `medusa_erpnext_sync.md` — Medusa-side sync spec (storefront integration)
