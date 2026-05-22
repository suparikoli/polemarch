// Investment Holding — client-side enhancements.
//
// Phase 20: "Split & Classify" form button that drives a Portfolio Transfer
// through Draft → Pending Approval → Approved → Posted in one shot so the
// operator can reclassify part of a Holding without navigating the 4-step
// state machine. Useful when 60 of 100 acquired units are Investment and the
// other 40 should be Stock in Trade.
//
// Phase 24A: when classification = Unallocated, render a deadline banner
// showing how much time remains until the daily scheduler auto-classifies
// the remaining qty as Stock in Trade. Colors: green > 24h, amber 6–24h,
// red < 6h or already past.

frappe.ui.form.on('Investment Holding', {
    refresh(frm) {
        if (frm.is_new()) return;

        // Phase 24A deadline banner — visible whenever classification is
        // Unallocated, regardless of qty. Drives the operator to either
        // classify before deadline or accept the auto-SiT default.
        render_classification_deadline_banner(frm);

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

// ── Phase 24A: classification deadline countdown banner ───────────────────
function render_classification_deadline_banner(frm) {
    // Find the mount point. v13–15 use frm.dashboard.wrapper, v16 uses
    // frm.dashboard.parent. Fall back to the form layout's main section.
    const d = frm.dashboard || {};
    const mount = (d.wrapper && d.wrapper.length) ? d.wrapper
        : (d.parent && d.parent.length) ? d.parent
        : $(frm.wrapper).find('.layout-main-section, .form-page').first();

    mount.find('.polemarch-deadline-banner').remove();

    // Only show for Unallocated. Once classified, the banner is irrelevant.
    if (frm.doc.classification !== 'Unallocated') return;
    if (!frm.doc.classification_deadline) return;

    const deadline = new Date(String(frm.doc.classification_deadline).replace(' ', 'T'));
    const now = new Date();
    const diff_ms = deadline - now;

    let color, icon, headline;
    if (diff_ms <= 0) {
        color = '#ef4444';
        icon = '⚠';
        headline = `<b>Past classification deadline</b> — the daily scheduler will flip this Holding to Stock in Trade on next run`;
    } else {
        const totalMin = Math.floor(diff_ms / 60000);
        const days = Math.floor(totalMin / (60 * 24));
        const hours = Math.floor((totalMin % (60 * 24)) / 60);
        const minutes = totalMin % 60;
        let pretty;
        if (days >= 1) pretty = `${days}d ${hours}h`;
        else if (hours >= 1) pretty = `${hours}h ${minutes}m`;
        else pretty = `${minutes}m`;
        const total_hours = diff_ms / 3600000;
        if (total_hours > 24)      { color = '#22c55e'; icon = '✓'; }
        else if (total_hours > 6)  { color = '#f59e0b'; icon = '⏱'; }
        else                       { color = '#ef4444'; icon = '⚠'; }
        headline = `Auto-classifies as <b>Stock in Trade</b> in <b>${pretty}</b>`;
    }

    const deadline_pretty = moment(deadline).format('D MMM YYYY, HH:mm');
    const html = `
      <div class="polemarch-deadline-banner" style="
          margin: 0 0 12px 0; padding: 10px 14px;
          background: var(--card-bg); color: var(--text-color);
          border: 1px solid var(--border-color); border-left: 4px solid ${color};
          border-radius: 4px; font-size: 13px;">
        <span style="color: ${color}; font-size: 16px; margin-right: 6px;">${icon}</span>
        ${headline}
        &nbsp;·&nbsp; <span style="color: var(--text-muted)">Deadline: ${deadline_pretty}</span>
      </div>
    `;
    mount.prepend(html);
}

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
