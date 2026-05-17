// Polemarch Sync Mapping form script.
//
// Wires the ERPNext-field column on each child table to an autocomplete
// that pulls from the section's selected target DocType. Six sections,
// each with its own (target_doctype_field, child_table_field) pair.
//
// On refresh: register an `awesomplete` source on every grid's
// `erpnext_field` column. When the user changes the target DocType,
// re-fetch the field list and refresh the source.

const SECTIONS = [
    { target: "customer_target_doctype",          table: "customer_mappings" },
    { target: "bank_account_target_doctype",      table: "bank_account_mappings" },
    { target: "demat_account_target_doctype",     table: "demat_account_mappings" },
    { target: "item_target_doctype",              table: "item_mappings" },
    { target: "order_target_doctype",             table: "order_mappings" },
    { target: "order_item_target_doctype",        table: "order_item_mappings" },
];

frappe.ui.form.on("Polemarch Sync Mapping", {
    refresh(frm) {
        SECTIONS.forEach(s => _wire_section(frm, s.target, s.table));
    },
});

// Re-wire each section's child grid when the target DocType changes,
// so the autocomplete dropdown reflects the new doctype's fields.
SECTIONS.forEach(s => {
    frappe.ui.form.on("Polemarch Sync Mapping", {
        [s.target](frm) { _wire_section(frm, s.target, s.table); },
    });
});

function _wire_section(frm, target_field, table_field) {
    const target_doctype = frm.doc[target_field];
    if (!target_doctype) return;

    const grid = frm.fields_dict[table_field] && frm.fields_dict[table_field].grid;
    if (!grid) return;

    const field_obj = grid.get_docfield("erpnext_field");
    if (!field_obj) return;

    // Fetch the target doctype's fields and use as autocomplete options.
    frappe.call({
        method: "polemarch.polemarch_customizations.doctype.polemarch_sync_mapping.polemarch_sync_mapping.get_target_doctype_fields",
        args: { doctype: target_doctype },
        callback: (r) => {
            const options = (r.message || []).map(f => f.value);
            // Stash a description map on the form for tooltip use later.
            grid._erpnext_field_descriptions = grid._erpnext_field_descriptions || {};
            (r.message || []).forEach(f => {
                grid._erpnext_field_descriptions[f.value] = f.description;
            });

            // Frappe grid Data fields don't expose a clean awesomplete
            // hook, but they do honour `options` when fieldtype is
            // Autocomplete. We dynamically switch the column on first
            // wire so the user gets a dropdown instead of a free-form
            // input.
            if (field_obj.fieldtype !== "Autocomplete") {
                field_obj.fieldtype = "Autocomplete";
            }
            field_obj.options = options.join("\n");
            // Refresh the visible grid so the change takes effect.
            if (grid.grid_rows) {
                grid.grid_rows.forEach(row => {
                    if (row && row.refresh_field) {
                        row.refresh_field("erpnext_field");
                    }
                });
            }
        },
    });
}
