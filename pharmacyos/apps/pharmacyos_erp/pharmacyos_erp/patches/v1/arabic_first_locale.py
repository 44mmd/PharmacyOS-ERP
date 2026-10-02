"""Arabic-first localization: Iraqi locale defaults, search keys and compiled PharmacyOS terminology."""

import frappe

from pharmacyos_erp.pharmacy.medicine import compute_search_key
from pharmacyos_erp.setup.install import (
	compile_translations,
	configure_locale,
	configure_pos_search,
	ensure_structure,
)


def execute():
	ensure_structure()  # creates Item.pharma_search_key and adds it to Item/POS search fields
	configure_pos_search()
	configure_locale()
	for name in frappe.get_all("Item", pluck="name"):
		doc = frappe.get_doc("Item", name)
		frappe.db.set_value("Item", name, "pharma_search_key", compute_search_key(doc), update_modified=False)
	compile_translations()
