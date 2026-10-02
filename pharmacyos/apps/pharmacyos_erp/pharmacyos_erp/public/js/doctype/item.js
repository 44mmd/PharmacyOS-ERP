// Item form: medicine mode. Only affects items marked as medicines; ERPNext behaviour is unchanged.
frappe.ui.form.on("Item", {
	pharma_is_medicine(frm) {
		if (frm.doc.pharma_is_medicine && frm.is_new()) {
			// Sensible pharmacy defaults for a new medicine (editable).
			frm.set_value("is_stock_item", 1);
			frm.set_value("has_batch_no", 1);
			frm.set_value("has_expiry_date", 1);
		}
	},

	refresh(frm) {
		if (!frm.doc.pharma_is_medicine || frm.is_new()) return;

		frm.add_custom_button(
			__("Batches & Expiry"),
			() => frappe.set_route("batches-expiry", { search: frm.doc.name }),
			__("View")
		);

		frappe
			.xcall("pharmacyos_erp.pharmacy.expiry.get_item_expiry_summary", { item_code: frm.doc.name })
			.then((s) => {
				if (!s || !s.batches) return;
				const parts = [];
				if (s.status_counts.expired) {
					parts.push(`<span class="pos-chip" data-status="expired">${__("Expired")}: ${s.status_counts.expired}</span>`);
				}
				if (s.status_counts.critical) {
					parts.push(`<span class="pos-chip" data-status="critical">${__("Critical")}: ${s.status_counts.critical}</span>`);
				}
				if (s.status_counts.soon) {
					parts.push(`<span class="pos-chip" data-status="soon">${__("Expiring Soon")}: ${s.status_counts.soon}</span>`);
				}
				const nearest = s.nearest
					? __("Nearest expiry: batch {0} on {1} ({2})", [
							`<span class="pos-code">${pharmacyos.ui.esc(s.nearest.batch_id)}</span>`,
							pharmacyos.ui.date(s.nearest.expiry_date),
							pharmacyos.ui.days_text(s.nearest.days),
					  ])
					: __("No unexpired batch in stock.");
				frm.dashboard.set_headline_alert(
					`<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">${parts.join("")}<span>${nearest}</span></div>`,
					s.status_counts.expired ? "red" : s.status_counts.critical ? "orange" : "green"
				);
			});
	},
});
