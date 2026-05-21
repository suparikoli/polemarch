frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.doc.brand === "Polemarch") {
			frm.dashboard.add_indicator(__("Polemarch"), "green");
			if (frm.doc.custom_medusa_product_id) {
				// Indicator only — Medusa plugin owns the sync direction.
				frm.dashboard.add_indicator(
					__("Medusa: {0}", [frm.doc.custom_medusa_product_id]),
					"blue"
				);
			}
		}
		_apply_isin_requirement(frm);
	},
	brand(frm) {
		_apply_isin_requirement(frm);
	},
	validate(frm) {
		if (frm.doc.brand === "Polemarch" && !frm.doc.custom_isin) {
			frappe.msgprint({
				title: __("ISIN required"),
				message: __("Polemarch items must have an ISIN. Set it under Share Information."),
				indicator: "red",
			});
			frappe.validated = false;
		}
	},
});

function _apply_isin_requirement(frm) {
	const required = frm.doc.brand === "Polemarch";
	frm.toggle_reqd("custom_isin", required);
}
