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
from frappe.translate import clear_cache, get_all_translations

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
	"Shift": "الشِفت",
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
		# historical rc.2 screenshot records are kept as they were taken; everything else is scanned
		self.assertTrue(g.skipped(g.REPO / "pharmacyos/docs/qa/local/screenshots/screens.json"))
		self.assertFalse(g.skipped(g.REPO / "pharmacyos/docs/qa/local/FINAL_QA_REPORT.md"))

	def test_app_sources_and_ar_po_use_the_new_word(self):
		offenders = self.guard.scan([str(APP_DIR)])
		self.assertEqual(offenders, [], "\n".join(offenders))

	def test_ar_po_checks_msgstr_only(self):
		po = APP_DIR / "locale" / "ar.po"
		entries = {msgid: msgstr for _line, msgid, msgstr in self.guard.po_entries(po)}
		for msgid, arabic in COUNTER_STRINGS.items():
			self.assertEqual(entries.get(msgid), arabic, msgid)

	def test_served_arabic_reads_shift_with_masculine_agreement(self):
		for msgid, arabic in COUNTER_STRINGS.items():
			self.assertEqual(frappe._(msgid, lang="ar"), arabic, msgid)
		# the report's shift table: a shift was opened / closed, or is still open
		self.assertEqual(frappe._("Opened", lang="ar"), "فُتح")
		self.assertEqual(frappe._("Closed", lang="ar"), "أُغلق")
		self.assertEqual(frappe._("Open", lang="ar"), "مفتوح")

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
