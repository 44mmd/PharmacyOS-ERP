"""Unambiguous display dates for prints and server-rendered pages.

"27 Oct 2026" in English and "27 تشرين الأول 2026" in Arabic (Iraqi month names from the
translation catalog, context "Month"; Latin digits). Stored values stay ISO dates; only the
presentation changes. Numeric-only formats such as 10/11/2026 are avoided because day/month
order is ambiguous between users.
"""

import frappe
from frappe.utils import getdate

MONTHS = (
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
)


def month_name(month: int, lang: str | None = None) -> str:
	english = MONTHS[month - 1]
	local = frappe._(english, lang=lang, context="Month")
	return english[:3] if local == english else local


def format_display_date(value, lang: str | None = None) -> str:
	if not value:
		return ""
	date = getdate(value)
	return f"{date.day} {month_name(date.month, lang)} {date.year}"
