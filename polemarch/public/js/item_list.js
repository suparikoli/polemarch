frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {};

(function () {
	const prev = frappe.listview_settings["Item"].onload;
	frappe.listview_settings["Item"].onload = function (listview) {
		if (typeof prev === "function") prev(listview);
		listview.page.add_inner_button(__("+ Polemarch Item"), () => {
			polemarch_open_item_dialog(() => listview.refresh());
		}).removeClass("btn-default").addClass("btn-primary");
	};
})();

window.polemarch_open_item_dialog = function (on_done) {
	const d = new frappe.ui.Dialog({
		title: __("Add Polemarch Item"),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("Instrument") },
			{
				fieldname: "item_name",
				fieldtype: "Data",
				label: __("Company / Instrument Name"),
				reqd: 1,
				description: __("e.g. FUSION TECHSTACK LIMITED"),
			},
			{
				fieldname: "isin",
				fieldtype: "Data",
				label: __("ISIN"),
				reqd: 1,
				description: __("12-character ISIN, e.g. INE678L01012"),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "item_code",
				fieldtype: "Data",
				label: __("Item Code"),
				description: __("Defaults to ISIN if left blank."),
			},
			{
				fieldname: "stock_uom",
				fieldtype: "Link",
				label: __("UOM"),
				options: "UOM",
				default: "Nos",
			},

			{ fieldtype: "Section Break", label: __("Listing Details") },
			{ fieldname: "rta", fieldtype: "Data", label: __("RTA / Registrar") },
			{
				fieldname: "last_traded_price",
				fieldtype: "Currency",
				label: __("Last Traded Price (per share)"),
			},
			{ fieldtype: "Column Break" },
			{ fieldname: "description", fieldtype: "Small Text", label: __("Description") },
		],
		primary_action_label: __("Create Item"),
		primary_action(values) {
			if (!/^[A-Z]{2}[A-Z0-9]{9}\d$/.test((values.isin || "").toUpperCase())) {
				frappe.msgprint({
					title: __("Invalid ISIN"),
					message: __("ISIN must be 12 characters: 2 letters + 9 alphanumeric + 1 digit."),
					indicator: "red",
				});
				return;
			}
			d.disable_primary_action();
			frappe.call({
				method: "polemarch.api.onboarding.create_polemarch_item",
				args: { payload: values },
				freeze: true,
				freeze_message: __("Creating Polemarch item..."),
				callback: ({ message }) => {
					if (message && message.name) {
						frappe.show_alert({
							message: __("Item {0} created.", [message.name]),
							indicator: "green",
						});
						d.hide();
						if (typeof on_done === "function") on_done();
						frappe.set_route("Form", "Item", message.name);
					}
				},
				error: () => d.enable_primary_action(),
			});
		},
	});
	d.show();
};
