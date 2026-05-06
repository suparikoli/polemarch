frappe.ui.form.on("Medusa Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.dashboard.clear_headline();
			frappe.call({
				method: "polemarch.polemarch_customizations.doctype.medusa_settings.medusa_settings.test_connection",
				freeze: true,
				freeze_message: __("Pinging Medusa..."),
				callback: ({ message }) => {
					if (!message) return;
					if (message.ok) {
						frm.dashboard.set_headline_alert(message.message, "green");
					} else {
						frm.dashboard.set_headline_alert(message.message, "red");
					}
				},
			});
		});

		frm.add_custom_button(__("Rotate Webhook Secret"), () => {
			frappe.warn(
				__("Rotate webhook secret?"),
				__(
					"The current secret will be replaced immediately. " +
					"Inbound webhooks signed with the old value will be rejected " +
					"until you update ERPNEXT_WEBHOOK_SECRET on the Medusa side and redeploy."
				),
				() => {
					frappe.call({
						method: "polemarch.polemarch_customizations.doctype.medusa_settings.medusa_settings.rotate_webhook_secret",
						freeze: true,
						freeze_message: __("Generating new secret..."),
						callback: ({ message }) => {
							if (!message || !message.secret) return;
							_show_secret_once(message.secret, message.message);
							frm.reload_doc();
						},
					});
				},
				__("Rotate")
			);
		}, __("Actions"));

		frm.add_custom_button(__("Run Full Re-sync"), () => {
			frappe.confirm(
				__("This will re-pull all Polemarch entities from Medusa. Continue?"),
				() => {
					frappe.call({
						method: "polemarch.medusa.reconcile.full_resync",
						freeze: true,
						freeze_message: __("Re-syncing — this may take a minute..."),
						callback: () => {
							frappe.show_alert({ message: __("Re-sync complete."), indicator: "green" });
							frm.reload_doc();
						},
					});
				}
			);
		}, __("Actions"));

		if (frm.doc.last_full_sync_at) {
			frm.dashboard.add_indicator(
				__("Last reconcile: {0}", [frappe.datetime.prettyDate(frm.doc.last_full_sync_at)]),
				"blue"
			);
		}
	},
});

function _show_secret_once(secret, message) {
	const d = new frappe.ui.Dialog({
		title: __("New Webhook Secret"),
		size: "small",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p style="color:#b54708;margin-bottom:10px"><b>${frappe.utils.escape_html(message)}</b></p>`,
			},
			{
				fieldname: "secret_value",
				fieldtype: "Code",
				label: __("Secret"),
				default: secret,
				read_only: 1,
				options: "Text",
			},
		],
		primary_action_label: __("Copy & Close"),
		primary_action() {
			navigator.clipboard.writeText(secret).then(() => {
				frappe.show_alert({ message: __("Secret copied to clipboard."), indicator: "green" });
				d.hide();
			});
		},
	});
	d.show();
}
