// Item list: "Add Medicine" — a focused creation flow instead of the full Item schema.
// Extends (does not replace) ERPNext's own listview settings for Item.
(function () {
	const settings = (frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {});
	const previous_onload = settings.onload;

	settings.onload = function (listview) {
		if (previous_onload) previous_onload.apply(this, arguments);
		if (!frappe.model.can_create("Item")) return;
		listview.page.add_inner_button(__("Add Medicine"), () => pharmacyos.medicine_dialog(), null, "primary");
	};
})();
