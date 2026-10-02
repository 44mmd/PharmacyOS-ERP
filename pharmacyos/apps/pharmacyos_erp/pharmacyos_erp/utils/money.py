"""Money presentation for PharmacyOS screens and print formats.

Presentation only: amounts are stored with ERPNext's normal precision. An all-zero fraction is
hidden ("25,000 IQD"), a real fraction is shown ("333.333 IQD"), so no value is ever misrepresented.
"""

import frappe
from frappe.utils import flt, fmt_money


def format_money(amount, currency: str | None = None, lang: str | None = None) -> str:
	currency = currency or frappe.db.get_default("currency") or "IQD"
	amount = flt(amount)
	precision = 0 if float(amount).is_integer() else None
	formatted = fmt_money(amount, precision=precision, currency=None)
	symbol = frappe.db.get_value("Currency", currency, "symbol", cache=True) or currency
	lang = lang or frappe.local.lang
	symbol = frappe._(symbol, lang=lang)
	return f"{formatted} {symbol}"
