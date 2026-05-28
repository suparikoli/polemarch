frappe.listview_settings["Sales Invoice"] = frappe.listview_settings["Sales Invoice"] || {};

(function () {
	const prev = frappe.listview_settings["Sales Invoice"].onload;
	frappe.listview_settings["Sales Invoice"].onload = function (listview) {
		if (typeof prev === "function") prev(listview);
		listview.page.add_inner_button(__("+ Polemarch Trade"), () => {
			polemarch_open_trade_dialog(() => listview.refresh());
		}).removeClass("btn-default").addClass("btn-primary");
	};
})();

window.polemarch_open_trade_dialog = function (on_done) {
	const d = new frappe.ui.Dialog({
		title: __("Book Polemarch Trade"),
		size: "extra-large",
		fields: [
			{ fieldtype: "Section Break", label: __("Trade") },
			{
				fieldname: "customer",
				fieldtype: "Link",
				label: __("Customer"),
				options: "Customer",
				reqd: 1,
				// Polemarch invoices apply to every customer EXCEPT the
				// mithtech-only opt-outs. Legacy `custom_is_polemarch_
				// customer` flag retired in v0_26_0.
				get_query: () => ({ filters: { custom_is_mithtech_only: 0 } }),
			},
			{
				fieldname: "posting_date",
				fieldtype: "Date",
				label: __("Posting Date"),
				default: frappe.datetime.get_today(),
			},
			{ fieldtype: "Column Break" },
			{ fieldname: "po_no", fieldtype: "Data", label: __("Reference / PO No") },
			{
				fieldname: "submit",
				fieldtype: "Check",
				label: __("Submit immediately"),
				default: 1,
				description: __("Uncheck to save as Draft for review."),
			},
			{ fieldtype: "Section Break", label: __("Lines") },
			{
				fieldname: "items",
				fieldtype: "Table",
				label: __("Items"),
				cannot_add_rows: false,
				in_place_edit: true,
				data: [],
				fields: [
					{
						fieldname: "item_code",
						fieldtype: "Link",
						options: "Item",
						in_list_view: 1,
						columns: 4,
						label: __("Item"),
						reqd: 1,
						get_query: () => ({ filters: { brand: "Polemarch", disabled: 0 } }),
					},
					{
						fieldname: "qty",
						fieldtype: "Float",
						in_list_view: 1,
						columns: 2,
						label: __("Qty"),
						default: 1,
						reqd: 1,
					},
					{
						fieldname: "rate",
						fieldtype: "Currency",
						in_list_view: 1,
						columns: 3,
						label: __("Rate"),
						reqd: 1,
					},
				],
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
		],
		primary_action_label: __("Book Trade"),
		primary_action(values) {
			const items = (values.items || []).filter((r) => r.item_code);
			if (!items.length) {
				frappe.msgprint({
					title: __("No lines"),
					message: __("Add at least one trade line."),
					indicator: "red",
				});
				return;
			}
			d.disable_primary_action();
			frappe.call({
				method: "polemarch.api.trades.create_polemarch_trade",
				args: {
					payload: {
						customer: values.customer,
						posting_date: values.posting_date,
						po_no: values.po_no,
						remarks: values.remarks,
						submit: !!values.submit,
						items,
					},
				},
				freeze: true,
				freeze_message: __("Booking trade..."),
				callback: ({ message }) => {
					if (message && message.name) {
						frappe.show_alert({
							message: __("Sales Invoice {0} booked.", [message.name]),
							indicator: "green",
						});
						d.hide();
						if (typeof on_done === "function") on_done();
						frappe.set_route("Form", "Sales Invoice", message.name);
					}
				},
				error: () => d.enable_primary_action(),
			});
		},
	});
	d.show();
};
