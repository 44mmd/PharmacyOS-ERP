"""A21: the exploits found during this QA, replayed against the fixed server as real users over HTTP.

Each must be refused (or have no effect). QA-only; run after qa_seed.py.
"""

import datetime as dt
import json
import subprocess

from qalib import Refused, Results, Session, rid
from qa_seed import OWNER, STAFF_PWD

R = Results()
A = "A21 Security"
POS = "pharmacyos_erp.pos.api."
today = dt.date.today()


def main():
	owner = Session("owner").login(*OWNER)
	cashier = Session("cashier").login("cashier@qa-pharmacy.test", STAFF_PWD)
	manager = Session("manager").login("manager@qa-pharmacy.test", STAFF_PWD)
	wh = owner.get_list("Branch", fields=["pharmacyos_warehouse"], limit=1)[0]["pharmacyos_warehouse"]
	company = owner.value("Global Defaults", "Global Defaults", "default_company")
	main_profile = owner.get_doc("POS Profile", "Main Counter")
	keep = ("company", "warehouse", "currency", "customer", "selling_price_list", "write_off_account", "write_off_cost_center", "cost_center", "update_stock", "allow_discount_change", "allow_rate_change", "print_format")

	def counter(user):
		name = f"QA Counter {user.split('@')[0]}"
		if not owner.get_list("POS Profile", filters=[["name", "=", name]], fields=["name"]):
			owner.insert({"doctype": "POS Profile", "__newname": name, **{k: main_profile[k] for k in keep}, "payments": [{"mode_of_payment": "Cash", "default": 1}], "applicable_for_users": [{"user": user}]})
		return name

	def shift(s, user):
		ctx = s.call(POS + "get_context")
		if ctx.get("shift"):
			return ctx["shift"]["pos_profile"]
		name = counter(user)
		s.call("erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher", pos_profile=name, company=company, balance_details=json.dumps([{"mode_of_payment": "Cash", "opening_amount": 0}]))
		return name

	mine = shift(cashier, "cashier@qa-pharmacy.test")
	batch = owner.get_list("Batch", filters=[["batch_id", "=", "PAN-QA-2602"]], fields=["name"])[0]["name"]

	def invoice(**over):
		doc = {"doctype": "Sales Invoice", "customer": main_profile["customer"], "company": company, "is_pos": 1, "pos_profile": mine, "update_stock": 1, "set_warehouse": wh,
			"items": [{"item_code": "QA-PAN500", "qty": 1, "warehouse": wh, "use_serial_batch_fields": 1, "batch_no": batch}],
			"payments": [{"mode_of_payment": "Cash", "amount": 2500}]}
		for k, v in over.items():
			if k == "row":
				doc["items"][0].update(v)
			else:
				doc[k] = v
		return doc

	def post(s, doc):
		d = s.insert(doc)
		return s.submit(d)["name"]

	R.refused(A, "cashier sells at 1 IQD through the document API", "refused: the price must be the pharmacy's price", lambda: post(cashier, invoice(row={"rate": 1, "price_list_rate": 1}, payments=[{"mode_of_payment": "Cash", "amount": 1}])), must_contain="price")
	R.refused(A, "cashier gives 100% off through the document API", "refused: above the counter discount limit", lambda: post(cashier, invoice(additional_discount_percentage=100, payments=[{"mode_of_payment": "Cash", "amount": 0}])))
	R.refused(A, "cashier makes a credit (non-POS) invoice", "refused: counter staff sell through the POS", lambda: post(cashier, invoice(is_pos=0, pos_profile=None, payments=[])))
	R.refused(A, "cashier back-dates a sale (expiry is checked against the posting date)", "refused: today's date only", lambda: post(cashier, invoice(set_posting_time=1, posting_date=str(today - dt.timedelta(days=30)))))
	R.refused(A, "cashier sells on a counter that is not their open shift", "refused: own shift only", lambda: post(cashier, invoice(pos_profile="Main Counter")))
	R.refused(A, "cashier discount above the 10% ceiling at the POS", "refused before payment (quote)", lambda: cashier.call(POS + "quote", pos_profile=mine, items=[{"item_code": "QA-PAN500", "qty": 1, "discount_percentage": 25}]))
	ok = cashier.call(POS + "checkout", pos_profile=mine, items=[{"item_code": "QA-PAN500", "qty": 1, "discount_percentage": 10}], payments=[{"mode_of_payment": "Cash", "amount": 2250}], request_id=rid())
	R.add(A, "a correct counter sale still works (10% discount, own shift)", "sale submitted for 2,250", (ok["name"], ok["grand_total"]), abs(ok["grand_total"] - 2250) < 1)
	mprofile = shift(manager, "manager@qa-pharmacy.test")
	m = manager.call(POS + "checkout", pos_profile=mprofile, items=[{"item_code": "QA-PAN500", "qty": 1, "discount_percentage": 50}], payments=[{"mode_of_payment": "Cash", "amount": 1250}], request_id=rid())
	R.add(A, "a manager is not limited by the counter ceiling", "50% discount sale by the manager", (m["name"], m["grand_total"]), abs(m["grand_total"] - 1250) < 1)

	# a customer whose default price list is cheaper
	cheap = "QA Cheap List"
	if not owner.get_list("Price List", filters=[["name", "=", cheap]], fields=["name"]):
		owner.insert({"doctype": "Price List", "price_list_name": cheap, "selling": 1, "currency": "IQD"})
		owner.insert({"doctype": "Item Price", "item_code": "QA-PAN500", "price_list": cheap, "price_list_rate": 100})
	if not owner.get_list("Customer", filters=[["customer_name", "=", "QA Cheap Customer"]], fields=["name"]):
		owner.insert({"doctype": "Customer", "customer_name": "QA Cheap Customer", "customer_type": "Individual", "default_price_list": cheap})
	cust = owner.get_list("Customer", filters=[["customer_name", "=", "QA Cheap Customer"]], fields=["name"])[0]["name"]
	R.refused(A, "a customer's cheaper default price list at the counter", "refused: the counter's price list", lambda: cashier.call(POS + "checkout", pos_profile=mine, items=[{"item_code": "QA-PAN500", "qty": 1}], payments=[{"mode_of_payment": "Cash", "amount": 100}], request_id=rid(), customer=cust))

	# return ledger: sell 2, return 2, then try to empty the ledger and return again
	sale = cashier.call(POS + "checkout", pos_profile=mine, items=[{"item_code": "QA-PAN500", "qty": 2}], payments=[{"mode_of_payment": "Cash", "amount": 5000}], request_id=rid())["name"]
	line = cashier.call(POS + "get_return_candidate", invoice=sale)["lines"][0]
	cashier.call(POS + "submit_return", invoice=sale, lines=[{"row": line["row"], "qty": 2}], request_id=rid())
	before = owner.value("Sales Invoice", sale, "pharma_return_ledger")
	try:
		cashier.update("Sales Invoice", sale, {"pharma_return_ledger": '{"returns": {}}'})
	except Refused:
		pass
	after = owner.value("Sales Invoice", sale, "pharma_return_ledger")
	R.add(A, "cashier cannot rewrite a sale's return ledger", "ledger unchanged", "unchanged" if before == after else f"CHANGED to {after}", before == after and bool(before))
	R.refused(A, "the same goods cannot be returned twice", "second full return refused", lambda: cashier.call(POS + "submit_return", invoice=sale, lines=[{"row": line["row"], "qty": 2}], request_id=rid()))

	# cost privacy
	item = cashier.get_doc("Item", "QA-PAN500")
	R.add(A, "cashier never receives purchase cost", "valuation_rate / last_purchase_rate absent", {k: item.get(k) for k in ("valuation_rate", "last_purchase_rate")}, not item.get("valuation_rate") and not item.get("last_purchase_rate"))
	# the bench's Redis needs a password (reachable from every Windows account through WSL localhost)
	out = subprocess.run(["redis-cli", "-p", "13000", "ping"], capture_output=True, text=True).stdout.strip()
	R.add(A, "Redis refuses unauthenticated clients", "NOAUTH", out, "NOAUTH" in out)
	g = Session("guest")
	st, _ = g.raw("POST", "/api/method/pharmacyos_erp.pos.api.checkout", {})
	R.add(A, "guests cannot call the POS API", "403", f"HTTP {st}", st in (401, 403))
	st, _ = g.raw("GET", "/app/pharmacy-report")
	R.add(A, "guests are sent to sign-in for the desk", "login page / redirect", f"HTTP {st}", st in (200, 301, 302, 303, 403))


if __name__ == "__main__":
	main()
