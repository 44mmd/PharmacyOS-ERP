"""Pharmacy expenses (rent, electricity, internet, transport, …) as real ledger entries.

"Record Expense" creates and submits an ERPNext Journal Entry — debit the chosen expense account,
credit the cash/bank account of the payment method — through normal document permissions
(Accounts User or above). There is no separate expense table: totals always come from the ledger.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate


def _company():
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


@frappe.whitelist()
def get_expense_form_defaults() -> dict:
	company = _company()
	return {
		"company": company,
		"currency": frappe.get_cached_value("Company", company, "default_currency") if company else None,
		"accounts": frappe.get_list(
			"Account",
			filters={"company": company, "root_type": "Expense", "is_group": 0, "disabled": 0},
			fields=["name", "account_name"],
			order_by="account_name",
			limit_page_length=200,
		)
		if company
		else [],
	}


@frappe.whitelist(methods=["POST"])
def record_expense(
	expense_account: str,
	amount: float,
	mode_of_payment: str,
	posting_date: str | None = None,
	remark: str | None = None,
	branch: str | None = None,
) -> str:
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Enter an amount greater than zero."))
	company = frappe.db.get_value("Account", expense_account, "company")
	if not company or frappe.db.get_value("Account", expense_account, "root_type") != "Expense":
		frappe.throw(_("Choose an expense account."))
	paid_from = frappe.db.get_value(
		"Mode of Payment Account", {"parent": mode_of_payment, "company": company}, "default_account"
	)
	if not paid_from:
		frappe.throw(
			_("Payment method {0} has no cash or bank account for {1}. Set it in Payment Methods.").format(
				mode_of_payment, company
			)
		)
	cost_center = frappe.get_cached_value("Company", company, "cost_center")
	accounting = {"cost_center": cost_center}
	if branch and frappe.get_meta("Journal Entry Account").has_field("branch"):
		accounting["branch"] = branch
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Cash Entry"
			if frappe.db.get_value("Account", paid_from, "account_type") == "Cash"
			else "Bank Entry",
			"company": company,
			"posting_date": posting_date or nowdate(),
			"user_remark": remark or _("Pharmacy expense"),
			"pharma_expense": 1,
			"cheque_no": (remark or _("Expense"))[:140],  # bank entries need a reference
			"cheque_date": posting_date or nowdate(),
			"accounts": [
				{"account": expense_account, "debit_in_account_currency": amount, **accounting},
				{"account": paid_from, "credit_in_account_currency": amount, **accounting},
			],
		}
	)
	je.insert()  # normal permission checks: the user needs Journal Entry create/submit rights
	je.submit()
	return je.name
