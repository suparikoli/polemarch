frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.custom_is_polemarch_invoice) {
			frm.meta.default_print_format = "Polemarch Sale Transfer Order";
			frm.dashboard.add_indicator(__("Polemarch"), "green");
			if (frm.doc.custom_medusa_order_id) {
				frm.dashboard.add_indicator(
					__("Medusa: {0}", [frm.doc.custom_medusa_order_id]),
					"blue"
				);
			}
		} else if (frm.doc.docstatus !== undefined && !frm.is_new()) {
			frm.dashboard.add_indicator(__("Mithtech Services"), "grey");
		}

		if (frm.doc.docstatus === 1 && frm.doc.custom_medusa_order_id) {
			frm.add_custom_button(__("Mirror status to Medusa (paid)"), () => {
				_mirror(frm, "paid");
			}, __("Medusa"));
			frm.add_custom_button(__("Mirror status to Medusa (canceled)"), () => {
				_mirror(frm, "canceled");
			}, __("Medusa"));
		}
	},
	before_print(frm) {
		if (frm.doc.custom_is_polemarch_invoice && frm.print_preview) {
			frm.print_preview.print_format = "Polemarch Sale Transfer Order";
		}
	},
});

function _mirror(frm, status) {
	frappe.call({
		method: "polemarch.medusa.api.resync_invoice_status",
		args: { invoice: frm.doc.name, status },
		freeze: true,
		freeze_message: __("Pushing status to Medusa..."),
		callback: ({ message }) => {
			if (message && message.ok) {
				frappe.show_alert({ message: message.message, indicator: "green" });
			}
		},
	});
}
