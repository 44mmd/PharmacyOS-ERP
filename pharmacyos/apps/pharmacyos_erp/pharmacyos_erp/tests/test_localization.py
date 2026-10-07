# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Arabic-first localization: terminology, search normalisation, dates, locale defaults."""

import re

import frappe
from frappe.tests import IntegrationTestCase
from frappe.translate import get_all_translations

from pharmacyos_erp.setup.terminology import apply_rules
from pharmacyos_erp.tests.utils import make_medicine
from pharmacyos_erp.utils.arabic import build_search_key, normalize_arabic
from pharmacyos_erp.utils.dates import format_display_date

# English sources where "batch" means a stock batch (not a payment batch)
STOCK_BATCH = re.compile(r"\b[Bb]atch(es)?\b")
MONEY_CONTEXT = re.compile(r"[Pp]ayment|[Aa]dvance|[Ii]nstal+ment|[Rr]epayment")
PAYMENT_WORDS = ("دفعة", "الدفعة", "دفعات", "الدفعات", "باتش")


class TestArabicNormalisation(IntegrationTestCase):
	def test_letter_variants_fold(self):
		self.assertEqual(normalize_arabic("أموكسيسيلين"), normalize_arabic("اموكسيسيلين"))
		self.assertEqual(normalize_arabic("إيبوبروفين"), "ايبوبروفين")
		self.assertEqual(normalize_arabic("آمن"), "امن")
		self.assertEqual(normalize_arabic("مستشفى"), normalize_arabic("مستشفي"))
		self.assertEqual(normalize_arabic("حبة"), normalize_arabic("حبه"))
		self.assertEqual(normalize_arabic("مُضادّ حَيَوي"), "مضاد حيوي")  # harakat
		self.assertEqual(normalize_arabic("بـــاراسيتامول"), "باراسيتامول")  # tatweel

	def test_digits_and_latin(self):
		self.assertEqual(normalize_arabic("٥٠٠ ملغ"), "500 ملغ")
		self.assertEqual(normalize_arabic("  Demo  AMOX-500 "), "demo amox-500")
		self.assertEqual(normalize_arabic(None), "")

	def test_search_key_dedupes_and_caps(self):
		key = build_search_key("ABC", "abc", "أقراص", None, "اقراص")
		self.assertEqual(key, "abc | اقراص")
		self.assertLessEqual(len(build_search_key("x" * 2000)), 1000)


class TestArabicMedicineSearch(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.item = make_medicine(
			"POS-AR-SEARCH",
			item_name="Loc Amoxicillin 500 mg",
			pharma_name_ar="أموكسيسيلين ٥٠٠ ملغ",
			pharma_generic_name="Amoxicillin",
			barcode="6290000000017",
		)

	def test_search_key_is_maintained_on_save(self):
		key = frappe.db.get_value("Item", self.item.name, "pharma_search_key")
		for part in ("pos-ar-search", "loc amoxicillin 500 mg", "اموكسيسيلين 500 ملغ", "6290000000017"):
			self.assertIn(part, key)

	def test_search_is_tolerant_of_hamza_and_case(self):
		from pharmacyos_erp.pharmacy.medicine import search_medicines

		for query in (
			"اموكسيسيلين",
			"أموكسيسيلين",
			"إموكسيسيلين",
			"AMOXICILLIN",
			"loc amox",
			"500 ملغ",
			"٥٠٠ ملغ",
		):
			names = [r.name for r in search_medicines(query)]
			self.assertIn(self.item.name, names, query)

	def test_exact_barcode_comes_first(self):
		from pharmacyos_erp.pharmacy.medicine import search_medicines

		results = search_medicines("6290000000017")
		self.assertEqual(results[0].name, self.item.name)
		self.assertEqual(results[0].matched_barcode, "6290000000017")
		self.assertEqual(search_medicines("   "), [])

	def test_inventory_search_uses_normalised_key(self):
		from pharmacyos_erp.pharmacy.inventory import _items

		self.assertIn(self.item.name, _items("اموكسيسيلين"))
		self.assertIn(self.item.name, _items("POS-AR-SEARCH"))


class TestTerminology(IntegrationTestCase):
	def test_batch_rules(self):
		self.assertEqual(apply_rules("Batch No", "رقم الدفعة"), "رقم الوجبة")
		self.assertEqual(apply_rules("Batches", "الدفعات"), "الوجبات")
		self.assertEqual(apply_rules("Batch Qty", "كمية الباتش"), "كمية الوجبة")
		# payment batches keep their financial meaning
		self.assertEqual(apply_rules("Payment Batch", "دفعة الدفع"), "دفعة الدفع")
		# a string without the English term is never touched
		self.assertEqual(apply_rules("Advance Payment", "الدفعة المقدمة"), "الدفعة المقدمة")

	def test_stock_warehouse_customer_rules(self):
		self.assertEqual(apply_rules("Stock UOM", "وحدة قياس السهم"), "وحدة قياس المخزون")
		self.assertEqual(apply_rules("Share Stock", "سهم"), "سهم")
		self.assertEqual(apply_rules("Default Warehouse", "المستودع الافتراضي"), "المخزن الافتراضي")
		self.assertEqual(apply_rules("Customer", "العميل"), "الزبون")

	def test_effective_arabic_never_calls_a_stock_batch_a_payment(self):
		messages = get_all_translations("ar")
		offenders = [
			(source, arabic)
			for source, arabic in messages.items()
			if isinstance(arabic, str)
			and STOCK_BATCH.search(source)
			and not MONEY_CONTEXT.search(source)
			and any(word in arabic for word in PAYMENT_WORDS)
		]
		self.assertEqual(offenders, [])

	def test_curated_terms(self):
		expected = {
			"Batch": "الوجبة",
			"Batch No": "رقم الوجبة",
			"Batches & Expiry": "الوجبات وتواريخ الانتهاء",
			"Dashboard": "لوحة التحكم",
			"Medicines": "الأدوية",
			"Warehouse": "المخزن",
			"Customers": "الزبائن",
			"Suppliers": "الموردون",
			"Expiring Soon": "قريب الانتهاء",
			"Expired": "منتهي الصلاحية",
			"Purchase Receipt": "استلام مشتريات",
			"Dosage Form": "الشكل الدوائي",
			"Expiry Intelligence": "مراقبة الصلاحية",
			"Search by item code, serial number or barcode": "بحث عن دواء أو باركود",
			"Checkout": "إتمام البيع",
			"Print Receipt": "طباعة الوصل",
			"IQD": "د.ع",
			"Nos": "قطعة",
		}
		for source, arabic in expected.items():
			self.assertEqual(frappe._(source, lang="ar"), arabic, source)

	def test_brand_names_are_never_translated(self):
		for brand in ("PharmacyOS", "PharmacyOS ERP", "HALF"):
			self.assertEqual(frappe._(brand, lang="ar"), brand)

	def test_english_is_untouched(self):
		self.assertEqual(frappe._("Batch", lang="en"), "Batch")
		self.assertEqual(frappe._("Dashboard", lang="en"), "Dashboard")


class TestDisplayDates(IntegrationTestCase):
	def test_iraqi_month_names(self):
		self.assertEqual(format_display_date("2026-10-27", lang="ar"), "27 تشرين الأول 2026")
		self.assertEqual(format_display_date("2026-01-05", lang="ar"), "5 كانون الثاني 2026")
		self.assertEqual(format_display_date("2026-02-01", lang="ar"), "1 شباط 2026")

	def test_english_is_unambiguous(self):
		self.assertEqual(format_display_date("2026-10-27", lang="en"), "27 Oct 2026")
		self.assertEqual(format_display_date(None), "")


class TestLocaleDefaults(IntegrationTestCase):
	def _settings(self):
		return frappe.db.get_value(
			"System Settings", None, ["country", "language", "time_zone"], as_dict=True
		)

	def _restore(self, before):
		frappe.db.set_single_value(
			"System Settings",
			{"country": before.country, "language": before.language, "time_zone": before.time_zone},
		)

	def test_iraq_defaults_to_arabic_and_baghdad(self):
		from pharmacyos_erp.setup.install import configure_locale

		before = self._settings()
		try:
			frappe.db.set_single_value("System Settings", "country", "Iraq")
			configure_locale()
			after = self._settings()
			self.assertEqual(after.language, "ar")
			self.assertEqual(after.time_zone, "Asia/Baghdad")
		finally:
			self._restore(before)

	def test_other_countries_keep_their_language(self):
		from pharmacyos_erp.setup.install import configure_locale

		before = self._settings()
		try:
			frappe.db.set_single_value("System Settings", {"country": "India", "language": "en"})
			configure_locale()
			self.assertEqual(self._settings().language, "en")
		finally:
			self._restore(before)

	def test_guest_login_follows_site_language(self):
		from frappe.utils import set_request
		from frappe.website.serve import get_response_content

		before = self._settings()
		frappe.set_user("Guest")
		try:
			frappe.db.set_single_value("System Settings", "language", "ar")
			set_request(method="GET", path="/login")
			frappe.local.lang = "en"  # as resolved from an English browser's Accept-Language
			html = get_response_content("login")
			self.assertIn('dir="rtl"', html)
			self.assertIn("العربية", html)
			self.assertIn("English", html)  # the switch keeps English one click away
		finally:
			frappe.set_user("Administrator")
			frappe.local.lang = "en"
			self._restore(before)


class TestAppsScreen(IntegrationTestCase):
	def _boot(self):
		return frappe._dict(
			app_data=[
				{"app_name": name, "on_apps_screen": True} for name in ("frappe", "erpnext", "pharmacyos_erp")
			]
		)

	def test_pharmacy_staff_see_only_pharmacyos(self):
		from pharmacyos_erp.boot import focus_apps_screen

		boot = self._boot()
		focus_apps_screen(boot, {"Cashier", "Sales User"})
		visible = [a["app_name"] for a in boot.app_data if a["on_apps_screen"]]
		self.assertEqual(visible, ["pharmacyos_erp"])

	def test_system_managers_and_other_users_keep_every_app(self):
		from pharmacyos_erp.boot import focus_apps_screen

		for roles in ({"Pharmacy Owner", "System Manager"}, {"Accounts User"}):
			boot = self._boot()
			focus_apps_screen(boot, roles)
			self.assertTrue(all(a["on_apps_screen"] for a in boot.app_data), roles)


class TestRound4MessagesAreTranslated(IntegrationTestCase):
	"""Every message counter staff can meet in the return, refund, reservation, cost-privacy and
	consolidation rules (and the first-run setup) has an Arabic translation (round-4 finding D-8)."""

	MODULES = (
		"pharmacy/returns.py",
		"pharmacy/stock_guard.py",
		"pharmacy/cost_privacy.py",
		"pharmacy/consolidation.py",
		"pharmacy/expiry.py",
		"pharmacy/inventory.py",
		"pharmacy/fefo.py",
		"setup/first_run.py",
		"integration/cloud.py",
		"pharmacyos/doctype/pharmacyos_settings/pharmacyos_settings.py",
		# the POS screen and its server API (web browser and Windows desktop)
		"www/pos.py",
		"pos/api.py",
		# LOCAL ERP v1: voids, disposal, the pharmacy report
		"pharmacy/disposal.py",
		"pharmacy/reports.py",
	)

	def test_user_facing_strings_have_arabic_entries(self):
		import os
		import re

		from babel.messages.pofile import read_po

		app = frappe.get_app_path("pharmacyos_erp")
		with open(os.path.join(app, "locale", "ar.po"), "rb") as f:
			catalog = read_po(f, locale="ar")
		translated = {m.id for m in catalog if m.id and m.string}
		missing = []
		for module in self.MODULES:
			with open(os.path.join(app, module), encoding="utf-8") as f:
				source = f.read()
			for text in re.findall(r'\b_\(\s*"((?:[^"\\]|\\.)*)"', source):
				if text not in translated:
					missing.append((module, text))
		self.assertEqual(missing, [], "user-facing messages without an Arabic translation")

	def test_return_messages_render_in_arabic(self):
		from frappe.translate import clear_cache

		from pharmacyos_erp.setup.install import compile_translations

		compile_translations()  # what every install and migrate does
		clear_cache()
		messages = get_all_translations("ar")
		self.assertEqual(messages.get("Return against a return"), "إرجاع مقابل مرتجع")
		self.assertEqual(messages.get("Refund exceeds sale"), "الاسترداد يتجاوز البيع")
		self.assertEqual(messages.get("Not a consolidated invoice"), "ليست فاتورة مجمّعة")
