// PharmacyOS Settings — website (PharmacyOS Cloud) connection actions.
frappe.ui.form.on("PharmacyOS Settings", {
	refresh(frm) {
		if (!frappe.user.has_role("System Manager") && !frappe.user.has_role("Pharmacy Owner")) return;
		frm.add_custom_button(
			__("Test Connection"),
			() =>
				frappe
					.call({ method: "pharmacyos_erp.integration.cloud.test_connection", type: "POST", freeze: true })
					.then(({ message }) => {
						frappe.show_alert({
							message: message.ok ? __("Connected to PharmacyOS Cloud.") : __("Could not connect: {0}", [message.error]),
							indicator: message.ok ? "green" : "red",
						});
						frm.reload_doc();
					}),
			__("Website")
		);
		frm.add_custom_button(
			__("Sync Catalog Now"),
			() =>
				frappe
					.call({ method: "pharmacyos_erp.integration.cloud.resync_catalog", type: "POST", freeze: true })
					.then(({ message }) =>
						frappe.show_alert({ message: __("{0} products queued for the website.", [message]), indicator: "green" })
					),
			__("Website")
		);
	},
});
