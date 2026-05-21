import frappe
from frappe import _
from frappe.model.document import Document


class Portfolio(Document):
    """Trading vs Investment book; per-Company + per-Customer scope.

    Invariants:
      - For owner_kind=Customer, `customer` is required.
      - Exactly one Proprietary portfolio per (company, portfolio_type).
    """

    def validate(self):
        self._validate_customer_required_for_customer_owner()
        self._validate_unique_proprietary_per_company_type()

    def on_trash(self):
        self._block_delete_if_referenced()

    def _validate_customer_required_for_customer_owner(self):
        if self.owner_kind == "Customer" and not self.customer:
            frappe.throw(
                _("Customer is required when Owner Kind is Customer."),
                title=_("Portfolio Owner Missing"),
            )
        if self.owner_kind == "Proprietary" and self.customer:
            frappe.throw(
                _("Proprietary portfolios must not have a Customer attached."),
                title=_("Portfolio Owner Conflict"),
            )

    def _validate_unique_proprietary_per_company_type(self):
        if self.owner_kind != "Proprietary":
            return
        existing = frappe.db.get_value(
            "Portfolio",
            {
                "company": self.company,
                "portfolio_type": self.portfolio_type,
                "owner_kind": "Proprietary",
                "name": ["!=", self.name or ""],
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("Proprietary {0} portfolio already exists for {1}: {2}").format(
                    self.portfolio_type, self.company, existing
                ),
                title=_("Duplicate Proprietary Portfolio"),
            )

    def _block_delete_if_referenced(self):
        # Post-Phase-8: Security Lot is gone, and Portfolio Transfer migrated to
        # `from_classification` / `to_classification` (no longer Portfolio links).
        # `table_exists` short-circuits cleanly if a doctype is absent at call time
        # (e.g. fresh install before later patches land).
        for doctype, field in (
            ("Trade Order", "portfolio"),
            ("Security Position", "portfolio"),
        ):
            if not frappe.db.table_exists(doctype):
                continue
            if frappe.db.exists(doctype, {field: self.name}):
                frappe.throw(
                    _("Cannot delete Portfolio {0}: referenced by {1}.{2}").format(
                        self.name, doctype, field
                    ),
                    title=_("Portfolio In Use"),
                )
