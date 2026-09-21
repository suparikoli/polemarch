frappe.query_reports["Polemarch Holdings"] = {
    filters: [
        { fieldname: "view", label: __("View"), fieldtype: "Select",
          options: ["Summary", "Lots", "Ageing"].join("\n"), default: "Summary", reqd: 1 },
        { fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
          default: frappe.defaults.get_user_default("Company") },
        { fieldname: "security", label: __("Security"), fieldtype: "Link", options: "Security" },
        { fieldname: "classification", label: __("Holding Type"), fieldtype: "Select",
          options: ["", "Stock in Trade", "Investment", "Unallocated"].join("\n") },
        { fieldname: "include_closed", label: __("Include fully disposed lots"), fieldtype: "Check", default: 0 },
        { fieldname: "blocks_by", label: __("Ageing blocks by"), fieldtype: "Select",
          options: ["Age (days)", "Purchase Month", "Purchase Quarter", "Financial Year"].join("\n"),
          default: "Age (days)", depends_on: "eval:doc.view=='Ageing'" },
        { fieldname: "range1", label: __("Age Range 1"), fieldtype: "Int", default: 90, depends_on: "eval:doc.view=='Ageing'" },
        { fieldname: "range2", label: __("Age Range 2"), fieldtype: "Int", default: 180, depends_on: "eval:doc.view=='Ageing'" },
        { fieldname: "range3", label: __("Age Range 3"), fieldtype: "Int", default: 365, depends_on: "eval:doc.view=='Ageing'" },
        { fieldname: "show_cost", label: __("Blocks in cost, not qty"), fieldtype: "Check", default: 0,
          depends_on: "eval:doc.view=='Ageing'" },
    ],
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (data && (data.security === "TOTAL" || data.lot === "TOTAL")) return `<b>${out}</b>`;
        if (["unrealised_gain", "unrealised_gain_pct"].includes(column.fieldname)
            && value !== null && value !== undefined && value !== "") {
            return `<span style="color:${Number(value) >= 0 ? "green" : "red"}">${out}</span>`;
        }
        if (column.fieldname === "long_term_qty" && Number(value) > 0) return `<span style="color:green">${out}</span>`;
        if (column.fieldname === "classification") {
            if (value === "Investment") return `<span style="color:#1a6dd4">${out}</span>`;
            if (String(value).startsWith("Split")) return `<span style="color:#7a4bbf">${out}</span>`;
            if (value === "Unallocated") return `<span style="color:#b36b00">${out}</span>`;
        }
        return out;
    },
};
