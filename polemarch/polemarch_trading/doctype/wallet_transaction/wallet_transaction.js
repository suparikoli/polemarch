// Wallet Transaction — client-side enhancements.
//
// The server enforces a strict (direction, txn_type) pairing via
// polemarch.polemarch_trading.wallet._DELTA_MAP. Operators picking an
// invalid combo (e.g. Direction=Credit + Type=Withdrawal) only learn
// at submit when the throw fires. This script mirrors the map on the
// client and:
//   - Filters the txn_type dropdown to the types valid for the current
//     direction, so invalid combos can't be picked.
//   - When the operator picks txn_type first, auto-sets direction when
//     only one direction is valid for that type (Adjustment / Reversal
//     work both ways, so direction stays whatever was picked).

const VALID = {
    'Credit': [
        'Deposit',
        'Reservation Release',
        'Sell Payout',
        'Fee Refund',
        'Adjustment',
        'Reversal',
    ],
    'Debit': [
        'Withdrawal',
        'Reservation',
        'Buy Settlement',
        'Fee',
        'Adjustment',
        'Reversal',
    ],
};

// Inverse: for each txn_type, list which direction(s) are valid.
const DIRECTIONS_FOR_TYPE = (() => {
    const out = {};
    for (const dir of Object.keys(VALID)) {
        for (const t of VALID[dir]) {
            if (!out[t]) out[t] = [];
            out[t].push(dir);
        }
    }
    return out;
})();

const ALL_TXN_TYPES = Array.from(new Set([...VALID.Credit, ...VALID.Debit]));

frappe.ui.form.on('Wallet Transaction', {
    refresh(frm) {
        sync_txn_type_options(frm);
    },
    direction(frm) {
        const valid = VALID[frm.doc.direction] || ALL_TXN_TYPES;
        // If the current txn_type isn't valid for the new direction, clear it
        // so the operator picks a valid one.
        if (frm.doc.txn_type && !valid.includes(frm.doc.txn_type)) {
            frm.set_value('txn_type', null);
        }
        sync_txn_type_options(frm);
    },
    txn_type(frm) {
        if (!frm.doc.txn_type) return;
        const valid_dirs = DIRECTIONS_FOR_TYPE[frm.doc.txn_type] || [];
        if (valid_dirs.length === 1 && frm.doc.direction !== valid_dirs[0]) {
            // Single-direction type — auto-set the direction.
            frm.set_value('direction', valid_dirs[0]);
        } else if (valid_dirs.length > 1 && frm.doc.direction && !valid_dirs.includes(frm.doc.direction)) {
            // Direction was set to something incompatible — clear it.
            frm.set_value('direction', null);
        }
    },
});

// Rebuild the `txn_type` Select options to only show types valid for the
// current `direction`. When direction is empty, show everything.
function sync_txn_type_options(frm) {
    const valid = VALID[frm.doc.direction] || ALL_TXN_TYPES;
    // Frappe Select.options can be set as either a "\n"-joined string or
    // an array of {label, value} pairs. The string form drops invalid
    // entries cleanly.
    const opts = [''].concat(valid).join('\n');
    frm.set_df_property('txn_type', 'options', opts);
    // Refresh the field so the new options take effect immediately.
    frm.refresh_field('txn_type');
}
