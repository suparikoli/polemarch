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

        // Phase 24B-1 classification breakdown — always visible. Shows the
        // SiT / Investment / Unclassified split at a glance.
        render_classification_breakdown(frm);

        // Phase 24B-1 "Classify Qty" button — only meaningful while there's
        // Unclassified qty to assign and the window hasn't expired. Lets
        // operators classify a partial qty without forking a new Holding
        // via Portfolio Transfer.
        const qty_unclass = frm.doc.qty_unclassified;
        const deadline_open = !frm.doc.classification_deadline
            || new Date(String(frm.doc.classification_deadline).replace(' ', 'T')) > new Date();
        if (qty_unclass !== undefined && qty_unclass > 0 && deadline_open) {
            frm.add_custom_button(
                __('Classify Qty'),
                () => open_classify_dialog(frm, qty_unclass),
                __('Actions'),
            );
        }

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

// ── Phase 24B-1: classification breakdown panel ──────────────────────────
function render_classification_breakdown(frm) {
    const d = frm.dashboard || {};
    const mount = (d.wrapper && d.wrapper.length) ? d.wrapper
        : (d.parent && d.parent.length) ? d.parent
        : $(frm.wrapper).find('.layout-main-section, .form-page').first();
    mount.find('.polemarch-classification-breakdown').remove();

    // Skip if the Phase 24B Custom Fields aren't installed yet.
    if (frm.doc.qty_unclassified === undefined) return;

    const sit  = Number(frm.doc.qty_classified_sit || 0);
    const inv  = Number(frm.doc.qty_classified_investment || 0);
    const unc  = Number(frm.doc.qty_unclassified || 0);
    const total = sit + inv + unc;
    if (total === 0) return;  // empty Holdings get no panel

    const cost = Number(frm.doc.cost_basis_per_unit || 0);
    const fmt_n = (v) => frappe.format(v, { fieldtype: 'Float', precision: 0 });
    const fmt_c = (v) => frappe.format(v * cost, { fieldtype: 'Currency' });
    const pct = (v) => total ? ((v / total) * 100).toFixed(1) + '%' : '0%';

    // Each cell: large qty, then small total cost + percentage.
    // Color cues mirror the workspace Number Cards: SiT cyan, Inv green,
    // Unclassified grey-muted, Total bold.
    function cell(label, qty, color) {
        const muted = qty === 0;
        return `
          <td style="padding: 10px 14px; vertical-align: top; ${muted ? 'opacity: 0.5;' : ''}">
            <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">${label}</div>
            <div style="font-size: 20px; font-weight: 600; color: ${color}; margin-top: 2px; white-space: nowrap;">${fmt_n(qty)}</div>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">${fmt_c(qty)} · ${pct(qty)}</div>
          </td>
        `;
    }

    const html = `
      <div class="polemarch-classification-breakdown" style="
          margin: 0 0 12px 0;
          background: var(--card-bg);
          border: 1px solid var(--border-color);
          border-radius: 6px; overflow: hidden;">
        <table style="width: 100%; border-collapse: collapse; margin: 0;">
          <tr>
            ${cell('Stock in Trade', sit, '#0ea5e9')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Investment', inv, '#22c55e')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Unclassified', unc, '#9ca3af')}
            <td style="width:1px; background: var(--border-color);"></td>
            ${cell('Total Remaining', total, 'var(--text-color)')}
          </tr>
        </table>
      </div>
    `;
    mount.prepend(html);
}


// ── Phase 24B-1: Classify Qty dialog ─────────────────────────────────────
function open_classify_dialog(frm, max_qty) {
    const d = new frappe.ui.Dialog({
        title: __('Classify Qty'),
        fields: [
            {
                fieldtype: 'HTML',
                options: `
                    <div class="alert alert-info">
                        <b>${frappe.utils.escape_html(frm.doc.name)}</b> has
                        <b>${max_qty}</b> shares awaiting classification.<br>
                        Once classified, the row is final (append-only). To reclassify later,
                        use Portfolio Transfer (creates a JE).
                    </div>
                `,
            },
            {
                fieldname: 'qty',
                label: __('Qty to Classify'),
                fieldtype: 'Float',
                reqd: 1,
                default: max_qty,
                description: __('Must be > 0 and ≤ {0}.', [max_qty]),
            },
            {
                fieldname: 'classification',
                label: __('Classification'),
                fieldtype: 'Select',
                options: 'Stock in Trade\nInvestment',
                reqd: 1,
                default: 'Stock in Trade',
            },
            {
                fieldname: 'notes',
                label: __('Notes (optional)'),
                fieldtype: 'Small Text',
            },
        ],
        primary_action_label: __('Classify'),
        primary_action(values) {
            if (values.qty <= 0 || values.qty > max_qty) {
                frappe.msgprint({
                    title: __('Invalid Qty'),
                    message: __('Qty must be between 0 and {0}.', [max_qty]),
                    indicator: 'orange',
                });
                return;
            }
            frappe.dom.freeze(__('Classifying…'));
            frappe.call({
                method:
                    'polemarch.polemarch_customizations.doctype.investment_holding.investment_holding.classify_qty',
                args: {
                    holding: frm.doc.name,
                    qty: values.qty,
                    classification: values.classification,
                    notes: values.notes || '',
                },
            }).then((r) => {
                frappe.dom.unfreeze();
                if (!r.message) return;
                d.hide();
                frappe.show_alert({
                    message: __('Classified ${0} shares as ${1}',
                                [values.qty, values.classification]),
                    indicator: 'green',
                });
                frm.reload_doc();
            }).catch(() => {
                frappe.dom.unfreeze();
                // Server-side throw message surfaces automatically.
            });
        },
    });
    d.show();
}

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
