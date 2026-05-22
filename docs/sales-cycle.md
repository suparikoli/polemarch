# Security Sale cycle

The Security Sale doctype is **Polemarch's only entry point for disposing inventory**. Like Security Purchase, it's parameterised by `party_type` (who's buying) and `payment_method` (how they're paying).

```
                      ┌──────────────────────────┐
                      │     Security Sale        │
                      └────────┬─────────────────┘
                               │
                    party_type ┼─────────────────┐
                               ▼                 ▼
                  ┌─────────────────┐  ┌──────────────────┐
                  │    Customer     │  │     Supplier     │
                  │ (retail buyer)  │  │  (block sale)    │
                  └────────┬────────┘  └────────┬─────────┘
                           │                    │
            payment_method ┼─────────┐    ┌─────┼─────────┐
                           ▼         ▼    ▼     ▼         ▼
                  Default Receivable / Bank / Cash    Customer Wallet
```

## What Security Sale always does

Regardless of party / payment combination, every Security Sale:

1. **FIFO-consumes** Polemarch's proprietary Investment Holdings (filtered by `from_classification` — either `Stock in Trade` or `Investment`, never mixed)
2. Creates one **Investment Disposal** with the consumed lots, LTCG / STCG breakdown captured per lot
3. Posts a **Cost Recognition JE**: `DR Trading COGS / CR <Inventory bucket>`
4. Posts a **Revenue JE**: `DR <Payment Account> / CR Trading Revenue - Securities`

When `party_type=Customer`:
5. Bumps the **Customer Holding** snapshot (`qty += sold qty`) — CRM side-effect, no GL impact

When `payment_method=Customer Wallet`:
6. Posts a **Wallet Transaction** (Debit Buy Settlement) on the customer's wallet

---

## Cycle 1 — Polemarch sells to a Customer (retail buyer, wallet-paid)

The bread-and-butter flow. Customer Ravi wants 50 of API Holdings; he has a Polemarch wallet pre-funded.

### Step-by-step

1. **Verify Polemarch has inventory** to sell. Check `/app/investment-holding?security=INE0DJ201029&status=Open`. Need ≥50 Stock-in-Trade Holdings, or run a Security Purchase first.
2. **Verify the customer has wallet headroom**. `/app/wallet/WAL-Ravi%20Kumar` — `balance_available` ≥ `qty × rate`.
3. **Create Security Sale** (`/app/security-sale/new`)

| Field | Example |
|---|---|
| Security | `INE0DJ201029` |
| Party Type | `Customer` |
| Party | `Ravi Kumar` |
| From Classification | `Stock in Trade` *(or `Investment` — single bucket per sale)* |
| Qty | 50 |
| Rate | ₹140 |
| Amount | ₹7,000 *(auto)* |
| Payment Method | `Customer Wallet` |
| Payment Account | auto = Ravi's `Customer Wallet Liability` GL |
| Revenue Account | auto = `Trading Revenue - Securities - <abbr>` |

4. **Submit** — `on_submit` posts in order:

   **a. FIFO plan**: `fifo.consume(security=INE0DJ..., classification='Stock in Trade', qty_to_sell=50, sale_date=today)`
   Returns a list of `ConsumedHolding` instances oldest-first, totalling 50.

   **b. Investment Disposal** created + submitted:
   - One Disposal doc, lots = FIFO plan
   - Each lot snapshots `cost_basis_per_unit` from the source Holding, `sale_price_per_unit=140`
   - LTCG / STCG calculated per lot based on `acquisition_date` (730-day cutoff)
   - On disposal.on_submit, `qty_disposed` on each source Holding bumps

   **c. Customer Holding snapshot upserted**:
   ```
   Ravi Kumar-INE0DJ201029 → qty += 50, source="Security Sale"
   ```
   If row didn't exist, it's created with qty=50.

   **d. COGS JE** posted (via `accounting.post_cost_recognition_je_for_disposal`):
   ```
   DR  Trading COGS - Securities         <total cost basis from FIFO>
   CR  Securities Inventory - Trading    <same>
   ```
   (For Investment-classification lots, CR Long-Term Investments instead.)

   **e. Wallet Transaction** (Customer Wallet path):
   ```
   Debit Buy Settlement  amount=₹7,000  on  WAL-Ravi Kumar
   → balance_available -₹7,000, balance_total -₹7,000
   ```

   **f. Revenue JE**:
   ```
   DR  Customer Wallet Liability        ₹7,000   (party_type=Customer)
   CR  Trading Revenue - Securities     ₹7,000
   ```

5. **(Off-system)** Polemarch transfers shares from its demat → Ravi's demat. Books already reflect the disposal; this is the legal/physical transfer.

### End state

```
✓ Investment Disposal (proprietary, with LTCG/STCG breakdown per lot)
✓ COGS JE     (DR Trading COGS / CR Securities Inventory)
✓ Revenue JE  (DR Customer Wallet Liability / CR Trading Revenue)
✓ Wallet Transaction (Debit Buy Settlement — wallet balance -₹7,000)
✓ Customer Holding (qty += 50, source="Security Sale")
Investment Holding (proprietary): qty_disposed bumped on consumed lots
```

---

## Cycle 2 — Polemarch sells to a Customer (Bank / Cash / AR settlement)

Same flow except no wallet transaction. The Revenue JE's DR side hits the chosen account.

| Payment Method | JE Debit |
|---|---|
| `Bank` | DR `<Bank Account>` |
| `Cash` | DR `<Cash Account>` |
| `Default Receivable` | DR `Debtors` (party_type=Customer) |

For Default Receivable, settle later via Payment Entry against the Customer.

---

## Cycle 3 — Polemarch sells to a Supplier (block sale, rare)

Operator-recorded direct disposal to a counterparty that isn't a Polemarch retail customer. No customer Holding snapshot is touched (the buyer isn't tracked as a retail customer).

| Field | Example |
|---|---|
| Party Type | `Supplier` |
| Party | the buying counterparty (recorded as a Supplier in ERPNext) |
| Payment Method | `Bank` / `Cash` / `Default Receivable` |

All other behaviour is the same: FIFO consume + Disposal + COGS JE + Revenue JE. No wallet, no customer-holding bump.

---

## Validation rules

| Combination | Allowed? | Why |
|---|---|---|
| Customer + Customer Wallet | ✅ | Standard retail buy via wallet |
| Customer + Bank | ✅ | Direct receipt |
| Customer + Cash | ✅ | Cash payment from customer |
| Customer + Default Receivable | ✅ | Deferred — JE row carries party_type=Customer |
| Supplier + Customer Wallet | ❌ | Suppliers don't have wallets — `_validate_party_payment_combo` throws |
| Supplier + Bank / Cash / Default Receivable | ✅ | Standard block sale |

Inventory checks:
- `from_classification` must have enough open Holdings — partial fills throw rather than silently under-deliver
- Security must be `tradable=1, active=1`

---

## Cancel flow

`on_cancel` reverses everything atomically:

1. **Wallet** (if applicable) — `wallet.reverse(wt_name, ...)` posts a Reversal that credits the wallet back.
2. **Revenue JE** + **COGS JE** — both cancelled (ERPNext auto-reverses GL).
3. **Investment Disposal** — cancelled. Its own `on_cancel` decrements `qty_disposed` on each Holding (releasing inventory back).
4. **Customer Holding** (if `party=Customer`) — `apply_delta(-qty, source="Security Sale", note="reversed on Sale cancel")`. If the snapshot drifted (e.g. operator manually edited it lower), clamps to 0 + drift note.

---

## Side-effects matrix

| Doctype written | Customer Sale (Wallet) | Customer Sale (Bank/Cash/AR) | Supplier Sale |
|---|---|---|---|
| Investment Disposal | ✅ | ✅ | ✅ |
| Investment Holding `qty_disposed` | ✅ bumped on consumed lots | ✅ | ✅ |
| Cost Recognition JE | ✅ | ✅ | ✅ |
| Revenue JE | ✅ DR Wallet Liability | ✅ DR Bank/Cash/Debtors | ✅ DR Bank/Cash/Debtors |
| Wallet Transaction | ✅ Debit Buy Settlement | — | — |
| Customer Holding snapshot | ✅ `qty += sold` | ✅ `qty += sold` | — |

---

## Concrete worked example — Customer Wallet sale

Polemarch sells 50 of API Holdings to Ravi Kumar at ₹140 via his wallet:

```
Pre-state:
  Polemarch Stock-in-Trade Holdings of INE0DJ201029:
    INV-HOLD-A: qty_remaining=100, cost_basis_per_unit=120, acquired 2024-01-15
    INV-HOLD-B: qty_remaining=50,  cost_basis_per_unit=125, acquired 2024-03-20
  Customer Holding "Ravi Kumar-INE0DJ201029": qty=0 (or not exists yet)
  Wallet WAL-Ravi Kumar: balance_available=15000

1. /app/security-sale/new
   ├─ Security:        INE0DJ201029
   ├─ Party Type:      Customer
   ├─ Party:           Ravi Kumar
   ├─ From Class:      Stock in Trade
   ├─ Qty:             50
   ├─ Rate:            140
   ├─ Amount (auto):   7,000
   ├─ Payment Method:  Customer Wallet
   └─ Submit
        ↓
2. FIFO plan: [INV-HOLD-A: 50 @ ₹120]
   (older lot covers entire quantity)
        ↓
3. Investment Disposal INV-DISP-2026-NNN created:
     Lot 1: holding=INV-HOLD-A, qty_consumed=50, cost_basis=120, sale_price=140
            holding_days = (today - 2024-01-15) → > 730 → LTCG
            realised_gain = (140-120) × 50 = ₹1,000  (LTCG)
   INV-HOLD-A: qty_disposed bumped from 0 to 50
        ↓
4. Customer Holding "Ravi Kumar-INE0DJ201029":
     created with qty=50, source="Security Sale"
        ↓
5. COGS JE posted:
     DR Trading COGS - Securities        ₹6,000
     CR Securities Inventory - Trading   ₹6,000
        ↓
6. Wallet Transaction posted (Debit Buy Settlement):
     amount=₹7,000 on WAL-Ravi Kumar
     balance_available: 15,000 → 8,000
     balance_total:     15,000 → 8,000
        ↓
7. Revenue JE posted:
     DR Customer Wallet Liability   ₹7,000  (party=Customer Ravi Kumar)
     CR Trading Revenue              ₹7,000

(Off-system)
8. Polemarch's DP transfers 50 shares to Ravi's demat.
   Books already reflect the disposal.
```

Post-state P&L impact (LTCG visible on the Disposal record):

```
Revenue   (CR)                 ₹7,000
Less COGS (DR)                 ₹6,000
─────────────────────────────────────
Trading gross profit           ₹1,000
```

For tax reporting, the LTCG figure on the Disposal (₹1,000 in this case) feeds the long-term capital gains schedule. The trading-gross-profit number on the GL is the same value, reached via the inventory-accounting route.

---

## Customer Wallet sale — money flow summary

```
Pre:    Wallet ₹15,000      Polemarch's books: ₹15,000 CR (we owe Ravi)
        Inventory ₹6,000 DR
        
Post:   Wallet ₹8,000       Polemarch's books: ₹8,000 CR (we owe Ravi less)
        Inventory ₹0 DR     (lot fully disposed)
        Revenue ₹7,000 CR   (sale recognised)
        COGS ₹6,000 DR      (cost recognised)
        → P&L gain ₹1,000

Net change to Polemarch's net worth: +₹1,000  ✓
   (Inventory -₹6,000, Wallet liability -₹7,000 = -₹13,000 on the liability side
    Revenue ₹7,000, COGS ₹6,000 → +₹1,000 net on the P&L side
    All ties to ₹1,000 LTCG)
```

---

## Edge cases worth knowing

| Scenario | What happens |
|---|---|
| Customer's wallet doesn't have enough | wallet.apply_delta's non-negativity invariant throws under row lock — Sale doesn't submit |
| FIFO can't cover qty (insufficient inventory) | Controller throws at on_submit before any side-effect lands |
| Operator picks `from_classification=Investment` but Polemarch only has Stock-in-Trade | FIFO returns empty / partial → controller throws |
| Customer already had Holdings (CRM snapshot) before this sale | snapshot increments — `qty += sold qty`, doesn't reset |
| Customer Holding row had drift (operator-edited lower) | New qty = old + sold, regardless of historical drift |
| Cancel a Sale whose Disposal had FURTHER trades after | Reversal would propagate; the controller doesn't currently block this — be careful (future hardening candidate) |

---

## Related docs

- `purchase-cycle.md` — Security Purchase flow (acquisition side)
- `medusa_erpnext_sync.md` — Medusa-side sync spec (storefront integration)
