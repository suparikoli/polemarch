// Polemarch Holdings by Security — report filters.
frappe.query_reports["Polemarch Holdings by Security"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 0,
        },
        {
            fieldname: "security_type",
            label: __("Security Type"),
            fieldtype: "Link",
            options: "Security Type",
            reqd: 0,
        },
    ],

    // Highlight the TOTAL row + colour unrealised gain green/red.
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (data && data.security === "TOTAL") {
            return `<b>${out}</b>`;
        }
        if (column.fieldname === "unrealised_gain" && value !== null && value !== undefined) {
            const colour = Number(value) >= 0 ? "green" : "red";
            return `<span style="color: ${colour}">${out}</span>`;
        }
        return out;
    },
};
