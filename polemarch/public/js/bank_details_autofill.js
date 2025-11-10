frappe.ui.form.on('Customer', {
    refresh: function(frm) {
        // Any refresh logic if needed later
    },

    // Trigger when a new row is added in the Bank Details child table
    bank_details_add: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);

        // If no primary bank is selected yet, make this first entry primary
        if (!frm.doc.bank_details.some(b => b.is_primary)) {
            frappe.model.set_value(cdt, cdn, 'is_primary', 1);
            frm.refresh_field('bank_details');
        }
    }
});

// ----------------------
// BANK DETAILS VALIDATION
// ----------------------

frappe.ui.form.on('Bank Details', {

    // ✅ Validate Bank Account Number — must be numeric
    ac_number: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.ac_number && !/^\d+$/.test(row.ac_number)) {
            frappe.msgprint(`⚠️ Bank Account Number must be numeric (Row ${row.idx})`);
            frappe.model.set_value(cdt, cdn, 'ac_number', '');
        }
    },

    // ✅ Validate IFSC / SWIFT / BIC Code
    bank_code: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.bank_code) {
            const ifscRegex = /^[A-Z]{4}0[A-Z0-9]{6}$/;      // IFSC
            const swiftRegex = /^[A-Z0-9]{8}([A-Z0-9]{3})?$/; // SWIFT/BIC
            if (!ifscRegex.test(row.bank_code) && !swiftRegex.test(row.bank_code)) {
                frappe.msgprint(`⚠️ Invalid IFSC / SWIFT / BIC format (Row ${row.idx})`);
                frappe.model.set_value(cdt, cdn, 'bank_code', '');
            }
        }
    },

    // ✅ Validate MICR — must be numeric
    micr: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.micr && !/^\d+$/.test(row.micr)) {
            frappe.msgprint(`⚠️ MICR must be numeric (Row ${row.idx})`);
            frappe.model.set_value(cdt, cdn, 'micr', '');
        }
    },

    // ✅ Ensure only one primary bank
    is_primary: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.is_primary) {
            frm.doc.bank_details.forEach(r => {
                if (r.name !== row.name) r.is_primary = 0;
            });
            frm.refresh_field('bank_details');
        }
    }
});
