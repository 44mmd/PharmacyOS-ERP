// Receiving medicines: one clear checklist for batch number + expiry per medicine row.
// Batches are created with the user's own permissions (receiving.create_receiving_batch);
// quantities, valuation and accounting stay ERPNext's. Server-side checks in receiving.py are
// authoritative (expired batches and missing expiry are rejected on save).
["Purchase Receipt", "Purchase Invoice"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 0 || frm.doc.is_return) return;
			if (doctype === "Purchase Invoice" && !frm.doc.update_stock) return;
			pharmacyos_receiving.refresh(frm);
		},
	});
});

const pharmacyos_receiving = {
	async medicine_rows(frm) {
		const codes = [...new Set((frm.doc.items || []).map((r) => r.item_code).filter(Boolean))];
		if (!codes.length) return [];
		const meds = await frappe.db.get_list("Item", {
			filters: { name: ["in", codes], pharma_is_medicine: 1, has_batch_no: 1 },
			fields: ["name", "item_name", "pharma_name_ar", "shelf_life_in_days"],
			limit: codes.length,
		});
		const by_code = Object.fromEntries(meds.map((m) => [m.name, m]));
		return (frm.doc.items || []).filter((r) => by_code[r.item_code]).map((r) => ({ row: r, item: by_code[r.item_code] }));
	},

	async refresh(frm) {
		const rows = await this.medicine_rows(frm);
		if (!rows.length) return;
		const missing = rows.filter((x) => !x.row.batch_no && !x.row.serial_and_batch_bundle);
		frm.add_custom_button(__("Batch & Expiry"), () => this.dialog(frm, rows), null).addClass("btn-primary");
		if (missing.length) {
			frm.dashboard.set_headline_alert(
				__("{0} of {1} medicine rows still need a batch number and expiry date.", [missing.length, rows.length]) +
					` <a href="#" class="pos-receive-link">${__("Record now")}</a>`,
				"orange"
			);
			frm.$wrapper.find(".pos-receive-link").on("click", (e) => {
				e.preventDefault();
				this.dialog(frm, rows);
			});
		} else {
			frm.dashboard.set_headline_alert(__("All medicine rows have a batch and expiry date."), "green");
		}
	},

	dialog(frm, rows) {
		const data = rows.map(({ row, item }) => ({
			row_name: row.name,
			idx: row.idx,
			item_code: row.item_code,
			medicine: item.item_name,
			qty: row.qty,
			batch_id: "",
			expiry_date: "",
			manufacturing_date: "",
			done: row.batch_no ? 1 : 0,
		}));
		const d = new frappe.ui.Dialog({
			title: __("Batch & Expiry"),
			size: "extra-large",
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="pos-note" style="margin-top:0">${__(
						"Enter the batch number and expiry date exactly as printed on each pack. Rows that already have a batch are skipped."
					)}</p>`,
				},
				{
					fieldname: "rows",
					fieldtype: "Table",
					cannot_add_rows: true,
					cannot_delete_rows: true,
					in_place_edit: true,
					data: data.filter((r) => !r.done),
					fields: [
						{ fieldname: "idx", fieldtype: "Int", label: __("Row"), read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "medicine", fieldtype: "Data", label: __("Medicine"), read_only: 1, in_list_view: 1, columns: 3 },
						{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "batch_id", fieldtype: "Data", label: __("Batch No"), reqd: 1, in_list_view: 1, columns: 2 },
						{ fieldname: "expiry_date", fieldtype: "Date", label: __("Expiry Date"), reqd: 1, in_list_view: 1, columns: 2 },
						{ fieldname: "manufacturing_date", fieldtype: "Date", label: __("Manufacturing Date"), in_list_view: 1, columns: 1 },
						{ fieldname: "row_name", fieldtype: "Data", hidden: 1 },
						{ fieldname: "item_code", fieldtype: "Data", hidden: 1 },
					],
				},
			],
			primary_action_label: __("Apply to receipt"),
			primary_action: async ({ rows: entered }) => {
				const todo = (entered || []).filter((r) => r.batch_id && r.expiry_date);
				if (!todo.length) {
					frappe.msgprint(__("Enter at least one batch number and expiry date."));
					return;
				}
				d.disable_primary_action();
				try {
					for (const r of todo) {
						const batch = await frappe.xcall("pharmacyos_erp.pharmacy.receiving.create_receiving_batch", {
							item_code: r.item_code,
							batch_id: r.batch_id,
							expiry_date: r.expiry_date,
							manufacturing_date: r.manufacturing_date || null,
							supplier: frm.doc.supplier || null,
						});
						await frappe.model.set_value(frm.doctype === "Purchase Invoice" ? "Purchase Invoice Item" : "Purchase Receipt Item", r.row_name, {
							use_serial_batch_fields: 1,
							batch_no: batch.name,
						});
					}
					d.hide();
					frappe.show_alert({ message: __("Batch details applied to {0} rows", [todo.length]), indicator: "green" });
					frm.refresh_field("items");
					frm.dirty();
				} finally {
					d.enable_primary_action();
				}
			},
		});
		if (!data.some((r) => !r.done)) {
			frappe.msgprint(__("All medicine rows already have a batch."));
			return;
		}
		d.show();
	},
};
