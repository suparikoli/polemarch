"""Settlement Run — batched settlement orchestrator.

Submit enqueues the run: it picks every Pending Settlement Instruction whose
`expected_settlement_date <= settlement_date_filter` (defaulting to run_date),
optionally scoped to a Company, and processes them via the canonical
`polemarch.trading.settlement` engine.

The batch is chunked (50 per inner transaction) so a single failure doesn't
roll back the whole run — each item commits independently. Per-item outcome
is recorded on the child table for forensic review.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class SettlementRun(Document):
    def validate(self):
        if not self.settlement_date_filter:
            self.settlement_date_filter = self.run_date

    def before_submit(self):
        if self.status not in ("Draft", None, ""):
            frappe.throw(
                _("Settlement Run can only be submitted from Draft (got {0}).").format(self.status),
                title=_("Invalid State"),
            )

    def on_submit(self):
        self._populate_items()
        self._execute()

    def on_cancel(self):
        # Cancelling a Settlement Run does NOT roll back the underlying
        # state transitions on individual Settlement Instructions — those
        # are independently submitted. Cancel here just marks the run.
        self.flags.from_state_transition = True
        self.status = "Cancelled"

    # ── batch lifecycle ──────────────────────────────────────────────────

    def _populate_items(self):
        filters = {
            "settlement_state": "Pending",
            "expected_settlement_date": ["<=", self.settlement_date_filter or self.run_date],
            "docstatus": 1,
        }
        if self.company:
            filters["company"] = self.company

        candidates = frappe.get_all(
            "Settlement Instruction",
            filters=filters,
            fields=["name"],
            order_by="expected_settlement_date ASC, creation ASC",
            limit_page_length=10000,
        )

        for row in candidates:
            self.append("items", {"settlement_instruction": row.name, "status": "Queued"})

        self.items_queued = len(candidates)
        self.started_at = now_datetime()
        self.status = "Running"
        self.db_update()
        self.update_children()
        frappe.db.commit()

    def _execute(self):
        from polemarch.trading import settlement as settlement_engine

        funded = cleared = failed = skipped = 0
        do_clear = self.run_mode == "Fund and Clear"

        for item in self.items or []:
            try:
                # Re-read state — another runner may have transitioned the SI.
                current_state = frappe.db.get_value(
                    "Settlement Instruction", item.settlement_instruction, "settlement_state"
                )
                if current_state != "Pending":
                    item.status = "Skipped"
                    item.error_message = f"State was {current_state}, not Pending"
                    skipped += 1
                    item.db_update()
                    continue

                settlement_engine.fund(item.settlement_instruction)
                funded += 1

                if do_clear:
                    settlement_engine.clear(item.settlement_instruction)
                    cleared += 1
                    item.status = "Cleared"
                else:
                    item.status = "Funded"
                item.db_update()
                frappe.db.commit()

            except Exception as exc:
                failed += 1
                item.status = "Failed"
                item.error_message = str(exc)[:500]
                try:
                    item.db_update()
                    frappe.db.commit()
                except Exception:
                    pass
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Polemarch Settlement Run {self.name} item {item.settlement_instruction}",
                )
                # Reset txn so subsequent items aren't poisoned.
                frappe.db.rollback()

        self.items_funded = funded
        self.items_cleared = cleared
        self.items_failed = failed
        self.items_skipped = skipped
        self.finished_at = now_datetime()
        self.status = (
            "Completed" if (failed == 0 and skipped == 0)
            else "Partial" if (funded > 0 or cleared > 0)
            else "Failed"
        )
        self.db_update()
        frappe.db.commit()


# ── scheduler entry (optional; ops typically trigger runs manually) ─────


def run_daily_batch():
    """Hourly catch-up wrapper that creates a Fund-Only Settlement Run if
    there are due Pending instructions and no live run is already executing.
    Defensive — designed to be safe to run every hour."""
    if not frappe.db.table_exists("Settlement Run"):
        return

    in_flight = frappe.db.exists("Settlement Run", {"status": "Running"})
    if in_flight:
        return

    today = frappe.utils.today()
    has_pending = frappe.db.exists(
        "Settlement Instruction",
        {
            "settlement_state": "Pending",
            "expected_settlement_date": ["<=", today],
            "docstatus": 1,
        },
    )
    if not has_pending:
        return

    run = frappe.get_doc(
        {
            "doctype": "Settlement Run",
            "run_date": today,
            "settlement_date_filter": today,
            "run_mode": "Fund Only",
            "status": "Draft",
            "notes": "Auto-created by polemarch.trading.doctype.settlement_run.settlement_run.run_daily_batch",
        }
    )
    run.flags.ignore_permissions = True
    run.insert(ignore_permissions=True)
    run.submit()
