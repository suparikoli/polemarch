// Securities Passbook — report filters. View "Counterparties" groups by
// (security, party); expand a row to see that party's movements.
frappe.query_reports["Securities Passbook"] = {
    filters: [
        { fieldname: "view", label: __("View"), fieldtype: "Select", options: "Passbook\nCounterparties", default: "Passbook" },
        { fieldname: "security", label: __("Security"), fieldtype: "Link", options: "Security" },
        { fieldname: "party_type", label: __("Party Type"), fieldtype: "Select", options: "\nCustomer\nSupplier" },
        {
            fieldname: "party", label: __("Party"), fieldtype: "Dynamic Link",
            get_options() {
                return frappe.query_report.get_filter_value("party_type") || "Customer";
            },
        },
        { fieldname: "from_date", label: __("From Transfer Date"), fieldtype: "Date" },
        { fieldname: "to_date", label: __("To Transfer Date"), fieldtype: "Date" },
        { fieldname: "direction", label: __("Buy / Sell"), fieldtype: "Select", options: "\nIn\nOut\nReclass" },
    ],

    tree: true,
    name_field: "row_id",
    parent_field: "parent_row",
    initial_depth: 0,

    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (data && data.row_id && !data.parent_row) return `<b>${out}</b>`;
        if (column.fieldname === "balance_qty" && Number(value) < 0) return `<span style="color: var(--red-500)">${out}</span>`;
        return out;
    },
};
