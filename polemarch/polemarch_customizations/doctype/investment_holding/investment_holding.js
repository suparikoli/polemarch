// Investment Holding — client-side enhancements.
//
// Phase 20: "Split & Classify" form button that drives a Portfolio Transfer
// through Draft → Pending Approval → Approved → Posted in one shot so the
// operator can reclassify part of a Holding without navigating the 4-step
// state machine. Useful when 60 of 100 acquired units are Investment and the
// other 40 should be Stock in Trade.

frappe.ui.form.on('Investment Holding', {
    refresh(frm) {
        if (frm.is_new()) return;
        if (!frm.doc.classification) return;
        // Only meaningful when there's something to split and the source has
        // a definite classification (not Unallocated).
        const available =
            (frm.doc.qty_acquired || 0)
            - (frm.doc.qty_disposed || 0)
            - (frm.doc.qty_reserved || 0);
        if (available <= 0) return;
        if (frm.doc.classification === 'Unallocated') return;

        frm.add_custom_button(
            __('Split & Classify'),
            () => open_split_dialog(frm, available),
            __('Actions'),
        );
    },
});

function open_split_dialog(frm, available) {
    const current = frm.doc.classification;
    const target_default =
        current === 'Stock in Trade' ? 'Investment' : 'Stock in Trade';

    const d = new frappe.ui.Dialog({
        title: __('Split & Classify'),
        fields: [
            {
                fieldtype: 'HTML',
                options: `
                    <div class="alert alert-info">
                        Source Holding <code>${frappe.utils.escape_html(frm.doc.name)}</code>
                        is in <b>${current}</b> with <b>${available}</b>
                        units available.<br>
                        Choose how many units to move into a new Holding
                        and the target classification.
                    </div>
                `,
            },
            {
                fieldname: 'split_qty',
                label: __('Quantity to Split Off'),
                fieldtype: 'Float',
                reqd: 1,
                default: Math.floor(available / 2),
                description: __(
                    'Units that will be moved into the new Holding. ' +
                    'Source Holding keeps the remainder.',
                ),
            },
            {
                fieldname: 'target_classification',
                label: __('Target Classification'),
                fieldtype: 'Select',
                options: 'Stock in Trade\nInvestment',
                reqd: 1,
                default: target_default,
                description: __(
                    'The new Holding lands in this classification. ' +
                    'A Portfolio Transfer + Journal Entry are created automatically.',
                ),
            },
        ],
        primary_action_label: __('Split'),
        primary_action(values) {
            if (values.target_classification === current) {
                frappe.msgprint({
                    title: __('Same Classification'),
                    message: __('Target must differ from the source ({0}).', [current]),
                    indicator: 'orange',
                });
                return;
            }
            if (values.split_qty <= 0 || values.split_qty > available) {
                frappe.msgprint({
                    title: __('Invalid Quantity'),
                    message: __('Split quantity must be between 0 and {0}.', [available]),
                    indicator: 'orange',
                });
                return;
            }

            frappe.dom.freeze(__('Creating Portfolio Transfer…'));
            frappe.call({
                method:
                    'polemarch.polemarch_customizations.doctype.investment_holding.investment_holding.split_and_classify',
                args: {
                    holding: frm.doc.name,
                    split_qty: values.split_qty,
                    target_classification: values.target_classification,
                },
            })
                .then((r) => {
                    frappe.dom.unfreeze();
                    if (!r.message) return;
                    d.hide();
                    const m = r.message;
                    frappe.show_alert({
                        message: __('Split posted'),
                        indicator: 'green',
                    });
                    frappe.msgprint({
                        title: __('Split & Classify completed'),
                        indicator: 'green',
                        message: `
                            <div>
                              <p>Portfolio Transfer:
                                <a href="/app/portfolio-transfer/${m.portfolio_transfer}">
                                    ${m.portfolio_transfer}
                                </a>
                              </p>
                              <p>Source Holding (reduced):
                                <a href="/app/investment-holding/${m.source_holding}">
                                    ${m.source_holding}
                                </a>
                              </p>
                              <p>New Holding (${values.target_classification}):
                                <a href="/app/investment-holding/${m.new_holding}">
                                    ${m.new_holding}
                                </a>
                              </p>
                            </div>
                        `,
                    });
                    frm.reload_doc();
                })
                .catch((e) => {
                    frappe.dom.unfreeze();
                    // Frappe surfaces the server-side throw message automatically
                    // — keep the dialog open so the user can adjust qty.
                });
        },
    });

    d.show();
}
