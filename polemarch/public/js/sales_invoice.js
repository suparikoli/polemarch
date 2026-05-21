frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		// Print-format selection by line-item brand:
		//   - Polemarch share-transfer invoices → custom Polemarch
		//     Sale Transfer Order template (DP / CL ID / ISIN block,
		//     no GST rows).
		//   - Everything else (including the Polemarch processing
		//     fee invoice) → ERPNext's Standard template, which
		//     already renders the GST Breakup Table properly.
		// We set both `default_print_format` (controls the dropdown
		// pre-selection in the print preview) and explicitly reset
		// it for non-polemarch docs in case Print Settings has
		// historically been set otherwise.
		if (frm.doc.custom_is_polemarch_invoice) {
			frm.meta.default_print_format = "Polemarch Sale Transfer Order";
			frm.dashboard.add_indicator(__("Polemarch"), "green");
		} else {
			frm.meta.default_print_format = "Standard";
		}
	},
	before_print(frm) {
		// Belt-and-braces: even if the user manually selected another
		// format from the dropdown, force the brand-correct one for
		// the actual print render. A Polemarch invoice rendered as
		// Standard would show empty tax rows (taxable_value=0,
		// CGST/SGST blank) and look broken; a Mithtech invoice
		// rendered as Polemarch Sale Transfer Order would hide its
		// GST breakup. Both are wrong outputs.
		if (!frm.print_preview) return;
		if (frm.doc.custom_is_polemarch_invoice) {
			frm.print_preview.print_format = "Polemarch Sale Transfer Order";
		} else {
			frm.print_preview.print_format = "Standard";
		}
	},
});
