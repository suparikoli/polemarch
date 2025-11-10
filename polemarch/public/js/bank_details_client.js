// --- Customer Doctype logic ---
frappe.ui.form.on('Customer', {
    refresh(frm) {
        // optional refresh logic
    },

    custom_bank_details_add(frm, cdt, cdn) {
        // Triggered when a new row is added in the Bank Details child table
        const row = frappe.get_doc(cdt, cdn);

        // Auto-mark first bank as primary if none is marked
        if (!frm.doc.custom_bank_details.some(b => b.is_primary == 1 || b.is_primary === "1")) {
            row.is_primary = 1;
            frm.refresh_field('custom_bank_details');
        }
    },

    validate(frm) {
        // Ensure only one primary bank exists
        const primary_rows = frm.doc.custom_bank_details.filter(b => b.is_primary == 1 || b.is_primary === "1");
        if (primary_rows.length > 1) {
            frappe.throw("Only one Bank Account can be marked as Primary.");
        }
    }
});

// --- Validation logic for Bank Details child doctype ---
frappe.ui.form.on('Bank Details', {
    ac_number(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        if (row.ac_number && !/^\d+$/.test(row.ac_number)) {
            frappe.msgprint(`❌ Bank Account Number must be numeric in row ${row.idx}`);
            row.ac_number = '';
            frm.refresh_field('custom_bank_details');
        }
    },

    bank_code(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        if (row.bank_code) {
            const ifscRegex = /^[A-Z]{4}0[A-Z0-9]{6}$/;       // IFSC
            const swiftRegex = /^[A-Z0-9]{8}([A-Z0-9]{3})?$/; // SWIFT/BIC

            if (!ifscRegex.test(row.bank_code) && !swiftRegex.test(row.bank_code)) {
                frappe.msgprint(`❌ Invalid IFSC / SWIFT / BIC in row ${row.idx}`);
                row.bank_code = '';
                frm.refresh_field('custom_bank_details');
            }
        }
    },

    micr(frm, cdt, cdn) {
        const row = frappe.get_doc(cdt, cdn);
        if (row.micr && !/^\d+$/.test(row.micr)) {
            frappe.msgprint(`❌ MICR must be numeric in row ${row.idx}`);
            row.micr = '';
            frm.refresh_field('custom_bank_details');
        }
    },

    is_primary(frm, cdt, cdn) {
        // Automatically uncheck all others if this one is marked primary
        const row = frappe.get_doc(cdt, cdn);
        if (row.is_primary) {
            (frm.doc.custom_bank_details || []).forEach(d => {
                if (d.name !== row.name) d.is_primary = 0;
            });
            frm.refresh_field('custom_bank_details');
        }
    }
});
