# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Iraqi terminology for a cash/POS shift: «شِفت» (plural «شِفتات», masculine) — 1.0.0-rc.3.

The rule itself lives in the repository's guard, `pharmacyos/qa/check_terminology.py` (also run by CI); this
suite runs the same check on the app's own sources and `locale/ar.po`, and on the Arabic the server actually
serves (every installed app's compiled translations merged), and pins the masculine agreement of the strings a
cashier reads at the counter.
"""

import importlib.util
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.translate import clear_cache, get_all_translations, get_translations_from_apps

APP_DIR = Path(frappe.get_app_path("pharmacyos_erp")).resolve()
GUARD = APP_DIR.parents[2] / "qa" / "check_terminology.py"  # pharmacyos/apps/pharmacyos_erp/pharmacyos_erp → pharmacyos/qa


def load_guard():
	if not GUARD.exists():
		raise AssertionError(f"The terminology guard is missing: {GUARD} (the app's tests run from the repository)")
	spec = importlib.util.spec_from_file_location("pharmacyos_check_terminology", GUARD)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


# what the counter shows, with masculine agreement (msgid → Arabic)
COUNTER_STRINGS = {
	"Shifts": "الشِفتات",
	"Open shift": "فتح الشِفت",
	"Close shift": "إغلاق الشِفت",
	"Shift closed.": "تم إغلاق الشِفت.",
	"No shift is open. Open your shift to start selling.": "لا يوجد شِفت مفتوح. افتح شِفتك لبدء البيع.",
	"No shift is open for you. Ask a pharmacy manager to open your shift.": "لا يوجد شِفت مفتوح لك. اطلب من مدير الصيدلية فتح شِفتك.",
	"You have no open shift.": "ليس لديك شِفت مفتوح.",
	"This shift is already closed.": "هذا الشِفت مغلق بالفعل.",
	"This sale belongs to a closed shift ({0}). Use a return instead.": "هذا البيع ضمن شِفت مغلق ({0}). استخدم الإرجاع بدلًا من ذلك.",
	"Open your shift on {0} before selling or refunding there.": "افتح شِفتك على {0} قبل البيع أو الإرجاع عليه.",
	"Counter staff sell through the point of sale, in their own shift.": "يبيع موظفو الكاونتر من خلال نقطة البيع، ضمن شِفتاتهم.",
	"You are not allowed to close a shift.": "غير مسموح لك بإغلاق شِفت.",
	"Create POS Opening Entry": "فتح شِفت نقطة البيع",
	"Opening Entries": "فتح الشِفتات",
	"Closing Entries": "إغلاق الشِفتات",
	"No shifts in this period": "لا شِفتات في هذه الفترة",
	"POS sessions": "شِفتات نقطة البيع",
}

# generic English words PharmacyOS needs in one sense only: translated with a context (msgctxt), so the same
# word elsewhere in the desk keeps Frappe's / ERPNext's Arabic — e.g. ERPNext's asset-depreciation "Shift",
# Frappe's "Open" button, the "Closed" status of orders. (msgid, context) → Arabic
CONTEXT_STRINGS = {
	("Shift", "POS"): "الشِفت",  # the counter (www/pos.py)
	("Opened", "Shift table"): "فُتح",  # the Pharmacy Report's shift table
	("Closed", "Shift table"): "أُغلق",
	("Open", "Shift status"): "مفتوح",
}


class TestShiftTerminology(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.guard = load_guard()
		# what `bench migrate` does (after_migrate): the served translations come from the compiled .mo
		from pharmacyos_erp.setup.install import compile_translations

		compile_translations()
		clear_cache()

	def test_guard_rule(self):
		g = self.guard
		self.assertTrue(g.is_old_shift_word(g.OLD_SHIFT + "ة"))
		self.assertTrue(g.is_old_shift_word("ال" + g.OLD_SHIFT + "ات"))  # definite plural
		self.assertFalse(g.is_old_shift_word(g.NEW_SHIFT))
		self.assertFalse(g.is_old_shift_word("الموردين"))  # suppliers
		self.assertFalse(g.is_old_shift_word("موردي"))
		self.assertEqual(g.old_shift_words("Shift / Open shift / shift_id"), [])
		# the same word typed on other keyboards or copied from other documents
		self.assertTrue(g.is_old_shift_word(g.OLD_SHIFT.replace("\u064a", "\u06cc") + "ة"))  # Persian/Kurdish YEH
		self.assertEqual(len(g.old_shift_words(g.OLD_SHIFT[:3] + "\u200c" + g.OLD_SHIFT[3:] + "ة")), 1)  # ZWNJ inside
		self.assertTrue(g.is_old_shift_word(g.OLD_SHIFT + "ه"))  # the common HEH spelling
		# the colour pink is not a shift: the masculine word, or a colour phrase
		colour = "\u0627\u0644\u0644\u0648\u0646"  # "the colour"
		self.assertEqual(g.old_shift_words("قرص " + g.OLD_SHIFT), [])
		self.assertEqual(g.old_shift_words("أقراص " + g.OLD_SHIFT + "ة " + colour), [])
		self.assertEqual(len(g.old_shift_words("أقراص " + g.OLD_SHIFT + "ة")), 1)
		self.assertIn(".bat", g.TEXT_SUFFIXES)
		self.assertIn(".cmd", g.TEXT_SUFFIXES)
		# historical rc.2 screenshot records are kept as they were taken; everything else is scanned
		self.assertTrue(g.skipped(g.REPO / "pharmacyos/docs/qa/local/screenshots/screens.json"))
		self.assertFalse(g.skipped(g.REPO / "pharmacyos/docs/qa/local/FINAL_QA_REPORT.md"))

	def test_app_sources_and_ar_po_use_the_new_word(self):
		offenders = self.guard.scan([str(APP_DIR)])
		self.assertEqual(offenders, [], "\n".join(offenders))

	def test_ar_po_checks_msgstr_only(self):
		po = APP_DIR / "locale" / "ar.po"
		entries = {(msgid, context): msgstr for _line, msgid, msgstr, context in self.guard.po_entries(po)}
		for msgid, arabic in COUNTER_STRINGS.items():
			self.assertEqual(entries.get((msgid, None)), arabic, msgid)
		for key, arabic in CONTEXT_STRINGS.items():
			self.assertEqual(entries.get(key), arabic, key)
			# the generic word is not overridden: it stays Frappe's / ERPNext's everywhere else in the desk
			self.assertNotIn((key[0], None), entries, key[0])

	def test_served_arabic_reads_shift_with_masculine_agreement(self):
		for msgid, arabic in COUNTER_STRINGS.items():
			self.assertEqual(frappe._(msgid, lang="ar"), arabic, msgid)
		# the counter's "Shift" and the report's shift table (opened / closed, or still open): by context
		for (msgid, context), arabic in CONTEXT_STRINGS.items():
			self.assertEqual(frappe._(msgid, lang="ar", context=context), arabic, (msgid, context))

	def test_generic_words_keep_upstream_arabic(self):
		"""PharmacyOS does not change "Shift" (ERPNext's asset-depreciation shift), "Open", "Closed" or "Opened"
		for the rest of the desk: they read exactly what Frappe and ERPNext serve without the app."""
		upstream = get_translations_from_apps("ar", [a for a in frappe.get_installed_apps() if a != "pharmacyos_erp"])
		served = get_all_translations("ar")
		for (msgid, _context), arabic in CONTEXT_STRINGS.items():
			self.assertEqual(served.get(msgid), upstream.get(msgid), msgid)
			self.assertNotEqual(frappe._(msgid, lang="ar"), arabic, msgid)

	def test_served_arabic_never_uses_the_old_word_for_a_cash_shift(self):
		allowed = self.guard.ERPNEXT_NOT_A_CASH_SHIFT  # ERPNext's asset-depreciation / workstation shifts
		offenders = [
			(source, arabic)
			for source, arabic in get_all_translations("ar").items()
			if isinstance(arabic, str)
			and self.guard.old_shift_words(arabic)
			and source not in allowed
			and source.rsplit(":", 1)[0] not in allowed
		]
		self.assertEqual(offenders, [])
