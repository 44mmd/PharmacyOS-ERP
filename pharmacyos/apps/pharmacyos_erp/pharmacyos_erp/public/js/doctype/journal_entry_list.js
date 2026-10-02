// Expenses list: "Record Expense" opens a short pharmacy form that posts a real Journal Entry.
frappe.listview_settings["Journal Entry"] = Object.assign(frappe.listview_settings["Journal Entry"] || {}, {
	onload(listview) {
		if (!frappe.model.can_create("Journal Entry")) return;
		listview.page.add_inner_button(__("Record Expense"), () => pharmacyos.record_expense(() => listview.refresh()));
	},
});

pharmacyos.record_expense = (done) => {
	frappe.call("pharmacyos_erp.pharmacy.expenses.get_expense_form_defaults").then(({ message: d }) => {
		const dialog = new frappe.ui.Dialog({
			title: __("Record Expense"),
			fields: [
				{
					fieldname: "expense_account",
					fieldtype: "Link",
					options: "Account",
					label: __("Expense Type"),
					reqd: 1,
					get_query: () => ({ filters: { company: d.company, root_type: "Expense", is_group: 0, disabled: 0 } }),
					description: __("e.g. Rent, Electricity, Internet, Transport, Maintenance"),
				},
				{ fieldname: "amount", fieldtype: "Currency", options: "currency", label: __("Amount"), reqd: 1 },
				{ fieldname: "currency", fieldtype: "Link", options: "Currency", hidden: 1, default: d.currency },
				{ fieldname: "mode_of_payment", fieldtype: "Link", options: "Mode of Payment", label: __("Paid From"), reqd: 1, default: "Cash" },
				{ fieldname: "posting_date", fieldtype: "Date", label: __("Date"), default: frappe.datetime.get_today(), reqd: 1 },
				{ fieldname: "branch", fieldtype: "Link", options: "Branch", label: __("Branch"), default: pharmacyos.current_branch() || "" },
				{ fieldname: "remark", fieldtype: "Small Text", label: __("Note") },
			],
			primary_action_label: __("Save Expense"),
			primary_action(values) {
				dialog.disable_primary_action();
				frappe
					.call({ method: "pharmacyos_erp.pharmacy.expenses.record_expense", type: "POST", args: values, freeze: true })
					.then((r) => {
						dialog.hide();
						frappe.show_alert({ message: __("Expense saved ({0}).", [r.message]), indicator: "green" });
						done && done(r.message);
					})
					.finally(() => dialog.enable_primary_action());
			},
		});
		dialog.show();
	});
};
