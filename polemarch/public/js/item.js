frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.doc.brand === "Polemarch") {
			frm.dashboard.add_indicator(__("Polemarch"), "green");
			if (frm.doc.custom_medusa_product_id) {
				frm.dashboard.add_indicator(
					__("Medusa: {0}", [frm.doc.custom_medusa_product_id]),
					"blue"
				);
			}
			if (!frm.is_new()) {
				frm.add_custom_button(__("Re-sync to Medusa"), () => {
					frappe.call({
						method: "polemarch.medusa.api.resync_item",
						args: { item_code: frm.doc.name },
						freeze: true,
						freeze_message: __("Pushing to Medusa..."),
						callback: ({ message }) => {
							if (message && message.ok) {
								frappe.show_alert({ message: message.message, indicator: "green" });
								frm.reload_doc();
							}
						},
					});
				}, __("Medusa"));
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
