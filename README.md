# Polemarch Customizations

ERPNext customizations for the **Polemarch** business unit (securities trading: unlisted shares, pre-IPO, bonds, MFs, AIF, REIT, InvIT) operating under **Mithtech Innovative Solutions PVT LTD**, alongside the **Mithtech Services** business unit.

This app provides:

- Brand-aware data model separating Polemarch (no GST — Schedule III, CGST Act) from Mithtech Services (GST 18%)
- A custom **Securities Trading subsystem**: Security Purchase / Sale, Investment Holding with child-table classifications (SiT vs Investment), Customer Holding (CRM snapshot), Portfolio Transfer, Wallet (deposit / withdrawal / reservation / settlement), and append-only Wallet Transactions
- Auto-classification of new lots into Stock-in-Trade vs Investment after a 5-business-day operator window
- A Sale Transfer Order print format for Polemarch sales invoices
- A Polemarch desk workspace with charts, Number Cards (Total / SiT / Investment / Unclassified holdings value), and shortcuts
- Daily reconciliation audit jobs (Investment Account ↔ Holdings, Wallet ledger ↔ balances, Holdings cache ↔ Investment Holdings)

The app is a **passive ERPNext customization** — it exposes Frappe's standard REST API. External integrations (storefront, mobile app, etc.) are the responsibility of whatever upstream system calls Frappe.

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site <your-site> install-app polemarch
bench build --app polemarch
bench --site <your-site> migrate
```

The `after_install` / `after_migrate` hooks idempotently create:

- Module Def `Polemarch Trading`
- Brands `Polemarch` and `Mithtech Services`
- Customer Group `Polemarch`
- Custom fields on Customer, Item, Sales Invoice, Sales Order
- Item Tax Template `Polemarch - Non-GST`
- Service items `POLEMARCH-PROC-FEE` and `POLEMARCH-LOW-ORDER-FEE`
- Polemarch sales naming series `POL-.YYYY.-.#####`

## Brand-aware behaviour

- A Customer is auto-tagged `custom_is_polemarch_customer = 1` if **any** of these is true: a DP Details row, member of the `Polemarch` Customer Group, has a Wallet, has a Customer Holding, is the party on a submitted Security Sale / Purchase, or has any submitted Polemarch invoice.
- A Customer can be opted out with `custom_is_mithtech_only` — the Polemarch + KYC tabs collapse and the auto-flag is forced off.
- A Sales Invoice / Sales Order is auto-tagged `custom_is_polemarch_invoice` (or `_order`) if **all** line items have `brand = Polemarch`. Mixed-brand documents are rejected.
- The Sales Invoice print dialog defaults to **Polemarch Sale Transfer Order** for Polemarch invoices.

## Trading subsystem

See [`docs/purchase-cycle.md`](docs/purchase-cycle.md) and [`docs/sales-cycle.md`](docs/sales-cycle.md) for the full operator-facing lifecycle. In brief:

- **Security Purchase** — books inventory into `Investment Holding`, debits `Securities Inventory`, credits the chosen payment source (Wallet / Bank / Cash / Supplier).
- **Investment Holding** — append-only child-table classifications (SiT / Investment). A new lot lands as Unclassified; operator has 5 business days to classify, then the auto-classifier flips remaining qty to SiT.
- **Security Sale** — consumes from `Investment Holding` via classification-aware FIFO, books realised gain via Capital Gains Auto-JE.
- **Portfolio Transfer** — reclassifies SiT ↔ Investment in-place on the source IH (no fragmentation).
- **Wallet** — bank-passbook-style ledger; all writes go through `apply_delta` / `reverse` in the wallet engine. Form renders the last 100 rows with DR/CR + running balance and a "Load 100 more" pagination control.

## Daily audits

Wired into `scheduler_events.daily`:

- `polemarch.polemarch_trading.audit.verify_inventory_account_matches_holdings` — drift between `Securities Inventory` GL balance and sum of `Investment Holding.remaining_cost`
- `polemarch.polemarch_trading.audit.verify_wallet_balance_matches_ledger` — drift between Wallet doc balances and replayed Wallet Transactions
- `polemarch.polemarch_trading.holdings_cache.audit_security_holdings_cache` — drift between `Security.qty_*` / `cost_total` cache and live IH aggregation

Drift > ₹100 (or any qty mismatch) logs a row to `Polemarch Audit Log`.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Polemarch tab missing on a customer who should see it | `custom_is_polemarch_customer` is set? `custom_is_mithtech_only` is off? Hard-refresh after toggling either. |
| Wrong print format on Sales Invoice | All line items must have `brand = Polemarch`. Mixed brands trigger a validation error and won't auto-tag. |
| Number Cards blank on the workspace | Confirm cards are type=`Document Type` (Frappe v16 doesn't render `Custom` cards in workspaces). Re-run `polemarch.patches.v0_21_0.seed_holdings_number_cards`. |
| Holdings breakdown stale | Re-save the affected Investment Holding (triggers `holdings_cache.on_investment_holding_change`), or run the daily audit manually. |
| Cancel cascade fails on Security Purchase / Sale | Look at `flags._pending_wt_reversal` on the doc — `before_cancel` stashes Wallet Transaction names there for `on_cancel` to reverse. |

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/polemarch
pre-commit install
```

Tools: `ruff`, `eslint`, `prettier`, `pyupgrade`.

## License

MIT
