frappe.ui.form.on('Know Your Customer', {

    refresh: function(frm) {
        // Optional: any code to run when form is refreshed
    },

    bank_details_add: function(frm, cdt, cdn) {
        // Triggered when a new row is added in Bank Details table
        let row = frappe.get_doc(cdt, cdn);

        // Auto-fill primary check to first bank if none selected
        if (!frm.doc.bank_details.some(b => b.is_primary)) {
            row.is_primary = 1;
            frm.refresh_field('bank_details');
        }
    }

});

// Trigger validation on Bank Details child table
frappe.ui.form.on('Bank Details', {

    ac_number: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);
        if (row.ac_number && !/^\d+$/.test(row.ac_number)) {
            frappe.msgprint(`Bank Account Number must be numeric in row ${row.idx}`);
            row.ac_number = '';
            frm.refresh_field('bank_details');
        }
    },

    bank_code: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);
        if (row.bank_code) {
            const ifscRegex = /^[A-Z]{4}0[A-Z0-9]{6}$/;
            const swiftRegex = /^[A-Z0-9]{8}([A-Z0-9]{3})?$/;
            if (!ifscRegex.test(row.bank_code) && !swiftRegex.test(row.bank_code)) {
                frappe.msgprint(`Invalid IFSC / SWIFT / BIC in row ${row.idx}`);
                row.bank_code = '';
                frm.refresh_field('bank_details');
            }
        }
    },

    micr: function(frm, cdt, cdn) {
        let row = frappe.get_doc(cdt, cdn);
        if (row.micr && !/^\d+$/.test(row.micr)) {
            frappe.msgprint(`MICR must be numeric in row ${row.idx}`);
            row.micr = '';
            frm.refresh_field('bank_details');
        }
    }

});
