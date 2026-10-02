"""Iraqi pharmacy terminology layer over upstream Arabic translations.

Upstream (Frappe/ERPNext) Arabic is partly machine-translated and inconsistent for pharmacy work:
"Stock" appears as السهم/الأسهم (equity shares), "Batch" as الدفعة/الباتش (reads as a payment),
"Warehouse" as مستودع, "Customer" as العميل. PharmacyOS never edits upstream translation files;
instead this module derives *overrides* from them:

	bench --site <site> execute pharmacyos_erp.setup.terminology.write_overrides

It rewrites term families inside every upstream Arabic string whose English source contains the
term, and writes the result to `pharmacyos_erp/translations/ar.csv` (loaded by Frappe after
upstream catalogs). Hand-curated wording in `pharmacyos_erp/locale/ar.po` loads after the CSV and
always wins. Re-run after upgrading ERPNext/Frappe so new upstream strings are covered.
"""

import csv
import os
import re

# (English source pattern, [(upstream Arabic, PharmacyOS Arabic), ...], English exclusion pattern)
# Longer Arabic forms come first so shorter ones do not pre-empt them.
RULES = [
	(
		r"\b[Bb]atch(es)?\b",
		[
			("الدفعات", "الوجبات"),
			("دفعات", "وجبات"),
			("الدفعة", "الوجبة"),
			("دفعة", "وجبة"),
			("الباتش", "الوجبة"),
			("باتش", "وجبة"),
		],
		# money context keeps the financial meaning
		r"[Pp]ayment|[Aa]dvance|[Ii]nstal+ment|[Rr]epayment",
	),
	(
		r"\b[Ss]tock\b",
		[("الأسهم", "المخزون"), ("السهم", "المخزون"), ("أسهم", "مخزون"), ("سهم", "مخزون")],
		r"[Ss]hare",
	),
	(
		r"\b[Ww]arehouses?\b",
		[("المستودعات", "المخازن"), ("مستودعات", "مخازن"), ("المستودع", "المخزن"), ("مستودع", "مخزن")],
		None,
	),
	(
		r"\b[Cc]ustomers?\b",
		[("العملاء", "الزبائن"), ("عملاء", "زبائن"), ("العميل", "الزبون"), ("عميل", "زبون")],
		None,
	),
]


def load_upstream(apps=("frappe", "erpnext")) -> dict[str, str]:
	import frappe
	from babel.messages.pofile import read_po

	catalog = {}
	for app in apps:
		path = os.path.join(frappe.get_app_path(app), "locale", "ar.po")
		if not os.path.exists(path):
			continue
		with open(path, "rb") as f:
			for message in read_po(f):
				if (
					message.id
					and message.string
					and isinstance(message.id, str)
					and isinstance(message.string, str)
				):
					catalog[message.id] = message.string
	return catalog


def apply_rules(source: str, arabic: str) -> str:
	result = arabic
	for pattern, replacements, exclude in RULES:
		if not re.search(pattern, source) or (exclude and re.search(exclude, source)):
			continue
		for old, new in replacements:
			result = result.replace(old, new)
	return result


def build_overrides(catalog: dict[str, str]) -> dict[str, str]:
	return {src: new for src, ar in catalog.items() if (new := apply_rules(src, ar)) != ar}


def write_overrides() -> int:
	import frappe

	overrides = build_overrides(load_upstream())
	path = os.path.join(frappe.get_app_path("pharmacyos_erp"), "translations", "ar.csv")
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", newline="", encoding="utf-8") as f:
		writer = csv.writer(f)
		for src in sorted(overrides):
			writer.writerow([src.replace("\n", "\\n"), overrides[src].replace("\n", "\\n")])
	print(f"{len(overrides)} terminology overrides written to {path}")
	return len(overrides)
