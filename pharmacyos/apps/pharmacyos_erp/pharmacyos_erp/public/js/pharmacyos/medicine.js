// "Add Medicine" dialog — focused medicine creation (server: pharmacy/medicine.py:create_medicine).
frappe.provide("pharmacyos");

pharmacyos.medicine_dialog = function (defaults = {}) {
	const d = new frappe.ui.Dialog({
		title: __("Add Medicine"),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("Identity") },
			{ fieldname: "item_name", fieldtype: "Data", label: __("Commercial Name"), reqd: 1, description: __("As printed on the pack, e.g. Panadol 500 mg") },
			{ fieldname: "pharma_name_ar", fieldtype: "Data", label: __("Arabic Name") },
			{ fieldname: "pharma_generic_name", fieldtype: "Data", label: __("Generic / Scientific Name") },
			{ fieldtype: "Column Break" },
			{ fieldname: "barcode", fieldtype: "Data", label: __("Barcode"), description: __("Scan the pack barcode") },
			{ fieldname: "item_code", fieldtype: "Data", label: __("SKU / Item Code"), description: __("Leave empty to generate one") },
			{ fieldname: "item_group", fieldtype: "Link", options: "Item Group", label: __("Category"), reqd: 1, default: defaults.item_group || "Products" },

			{ fieldtype: "Section Break", label: __("Formulation") },
			{ fieldname: "active_ingredient", fieldtype: "Link", options: "Active Ingredient", label: __("Active Ingredient") },
			{ fieldname: "pharma_strength", fieldtype: "Data", label: __("Strength"), description: __("e.g. 500 mg") },
			{ fieldname: "pharma_dosage_form", fieldtype: "Link", options: "Dosage Form", label: __("Dosage Form") },
			{ fieldtype: "Column Break" },
			{ fieldname: "pharma_pack_size", fieldtype: "Data", label: __("Pack Size") },
			{ fieldname: "stock_uom", fieldtype: "Link", options: "UOM", label: __("Stock Unit"), reqd: 1, default: "Nos" },
			{ fieldname: "pharma_dispensing", fieldtype: "Select", label: __("Dispensing"), options: ["", "Over the Counter", "Prescription Required"].map((v) => ({ value: v, label: v ? __(v) : "" })) },

			{ fieldtype: "Section Break", label: __("Supplier & Brand"), collapsible: 1 },
			{ fieldname: "brand", fieldtype: "Link", options: "Brand", label: __("Brand") },
			{ fieldname: "pharma_manufacturer", fieldtype: "Link", options: "Manufacturer", label: __("Manufacturer") },
			{ fieldtype: "Column Break" },
			{ fieldname: "default_supplier", fieldtype: "Link", options: "Supplier", label: __("Preferred Supplier") },

			{ fieldtype: "Section Break", label: __("Price & Stock") },
			{ fieldname: "standard_rate", fieldtype: "Currency", label: __("Selling Price") },
			{ fieldname: "buying_rate", fieldtype: "Currency", label: __("Purchase Price") },
			{ fieldname: "shelf_life_in_days", fieldtype: "Int", label: __("Typical Shelf Life (Days)") },
			{ fieldtype: "Column Break" },
			{ fieldname: "reorder_warehouse", fieldtype: "Link", options: "Warehouse", label: __("Reorder Warehouse"), get_query: () => ({ filters: { is_group: 0 } }) },
			{ fieldname: "reorder_level", fieldtype: "Float", label: __("Reorder Level"), depends_on: "reorder_warehouse" },
			{ fieldname: "reorder_qty", fieldtype: "Float", label: __("Reorder Quantity"), depends_on: "reorder_warehouse" },
			{
				fieldtype: "HTML",
				options: `<p class="pos-note">${__("Medicines are created with batch and expiry tracking. The supplier's batch number and expiry date are entered when the stock is received.")}</p>`,
			},
		],
		primary_action_label: __("Create Medicine"),
		primary_action(values) {
			d.disable_primary_action();
			frappe
				.xcall("pharmacyos_erp.pharmacy.medicine.create_medicine", values)
				.then((name) => {
					d.hide();
					frappe.show_alert({ message: __("Medicine {0} created", [values.item_name]), indicator: "green" });
					frappe.set_route("Form", "Item", name);
				})
				.finally(() => d.enable_primary_action());
		},
	});
	d.show();
	return d;
};
