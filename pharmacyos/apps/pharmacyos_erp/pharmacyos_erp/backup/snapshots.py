"""Human-readable sales and stock spreadsheets saved beside the backups.

Hourly: <data>/Sales/<date>/Sales-<HH-00>.xlsx — every sale line of the day so far.
Daily:  <data>/Daily Reports/<date>/ — Daily-Sales, Payment-Summary, Inventory-Summary, Backup-Metadata.

Rows come from submitted Sales Invoices and POS Invoices (returns included, as negative quantities).
POS Invoices that ERPNext later consolidates into a Sales Invoice are counted once: consolidated
Sales Invoices are skipped. Spreadsheets contain business data only — no credentials or settings.
"""

import json
import os
import shutil
from collections import defaultdict
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.xlsxutils import make_xlsx

from pharmacyos_erp.backup.paths import DAILY, SALES, data_dir, sub_dir

COLUMNS = [
	"Invoice",
	"Type",
	"Date",
	"Time",
	"Cashier",
	"Branch",
	"Warehouse",
	"Customer",
	"Item Code",
	"Barcode",
	"Medicine",
	"Batch",
	"Expiry",
	"Qty",
	"UOM",
	"Unit Price",
	"Discount",
	"Line Total",
	"Invoice Tax",
	"Invoice Total",
	"Payment Method",
	"Currency",
	"Status",
]


def _invoices(doctype: str, date) -> list:
	filters = {"docstatus": 1, "posting_date": date}
	if doctype == "Sales Invoice":
		filters["is_consolidated"] = 0
	fields = [
		"name",
		"posting_date",
		"posting_time",
		"owner",
		"customer",
		"customer_name",
		"grand_total",
		"total_taxes_and_charges",
		"currency",
		"status",
		"is_return",
		"base_change_amount",
	]
	if frappe.get_meta(doctype).has_field("branch"):
		fields.append("branch")
	return frappe.get_all(doctype, filters=filters, fields=fields, order_by="posting_date, posting_time")


def sale_rows(date) -> list[list]:
	rows = []
	for doctype in ("Sales Invoice", "POS Invoice"):
		invoices = _invoices(doctype, date)
		if not invoices:
			continue
		names = [i.name for i in invoices]
		item_dt = "Sales Invoice Item" if doctype == "Sales Invoice" else "POS Invoice Item"
		pay_dt = "Sales Invoice Payment"
		items = frappe.get_all(
			item_dt,
			filters={"parent": ["in", names], "parenttype": doctype},
			fields=[
				"parent",
				"item_code",
				"item_name",
				"barcode",
				"batch_no",
				"serial_and_batch_bundle",
				"qty",
				"uom",
				"rate",
				"price_list_rate",
				"discount_amount",
				"amount",
				"warehouse",
			],
			order_by="parent, idx",
		)
		payments = defaultdict(list)
		for p in frappe.get_all(
			pay_dt,
			filters={"parent": ["in", names], "parenttype": doctype, "amount": ["!=", 0]},
			fields=["parent", "mode_of_payment"],
		):
			payments[p.parent].append(p.mode_of_payment)
		bundles = [r.serial_and_batch_bundle for r in items if r.serial_and_batch_bundle]
		bundle_batches = defaultdict(list)
		for e in frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": ["in", bundles or [""]]},
			fields=["parent", "batch_no"],
		):
			if e.batch_no:
				bundle_batches[e.parent].append(e.batch_no)
		batch_names = {r.batch_no for r in items if r.batch_no} | {
			b for v in bundle_batches.values() for b in v
		}
		batches = {
			b.name: b
			for b in frappe.get_all(
				"Batch",
				filters={"name": ["in", list(batch_names) or [""]]},
				fields=["name", "batch_id", "expiry_date"],
			)
		}
		codes = list({r.item_code for r in items})
		barcodes = {}
		for b in frappe.get_all(
			"Item Barcode",
			filters={"parent": ["in", codes or [""]]},
			fields=["parent", "barcode"],
			order_by="idx",
		):
			barcodes.setdefault(b.parent, b.barcode)
		names_ar = dict(
			frappe.get_all(
				"Item", filters={"name": ["in", codes or [""]]}, fields=["name", "pharma_name_ar"], as_list=1
			)
		)
		by_invoice = {i.name: i for i in invoices}
		for r in items:
			inv = by_invoice[r.parent]
			batch_list = [r.batch_no] if r.batch_no else bundle_batches.get(r.serial_and_batch_bundle, [])
			batch_docs = [batches[b] for b in batch_list if b in batches]
			medicine = (
				r.item_name if not names_ar.get(r.item_code) else f"{r.item_name} / {names_ar[r.item_code]}"
			)
			rows.append(
				[
					inv.name,
					_("Return") if inv.is_return else _("Sale"),
					str(inv.posting_date),
					str(inv.posting_time or "")[:8],
					inv.owner,
					inv.get("branch") or "",
					r.warehouse or "",
					inv.customer_name or inv.customer or "",
					r.item_code,
					r.barcode or barcodes.get(r.item_code, ""),
					medicine,
					", ".join(b.batch_id or b.name for b in batch_docs),
					", ".join(str(b.expiry_date) for b in batch_docs if b.expiry_date),
					flt(r.qty),
					r.uom,
					flt(r.rate),
					flt(r.discount_amount),
					flt(r.amount),
					flt(inv.total_taxes_and_charges),
					flt(inv.grand_total),
					", ".join(payments.get(inv.name, [])),
					inv.currency,
					inv.status,
				]
			)
	return rows


def _write_xlsx(path: str, rows: list[list], sheet: str) -> str:
	tmp = path + ".tmp"
	with open(tmp, "wb") as f:
		f.write(make_xlsx(rows, sheet).getvalue())
	os.replace(tmp, path)  # never leave a half-written spreadsheet behind
	return path


def write_sales_snapshot(at=None) -> str:
	at = get_datetime(at or now_datetime())
	folder = sub_dir(SALES, at.strftime("%Y-%m-%d"))
	path = os.path.join(folder, f"Sales-{at.strftime('%H')}-00.xlsx")
	return _write_xlsx(path, [COLUMNS, *sale_rows(at.date())], "Sales")


def payment_summary(date) -> list[list]:
	rows = [["Payment Method", "Sales", "Returns", "Net", "Invoices"]]
	totals = defaultdict(lambda: [0.0, 0.0, set()])
	for doctype in ("Sales Invoice", "POS Invoice"):
		invoices = {i.name: i for i in _invoices(doctype, date)}
		if not invoices:
			continue
		cash_mode = {}
		for p in frappe.get_all(
			"Sales Invoice Payment",
			filters={"parent": ["in", list(invoices)], "parenttype": doctype},
			fields=["parent", "mode_of_payment", "base_amount", "type"],
		):
			inv = invoices[p.parent]
			bucket = totals[p.mode_of_payment]
			bucket[1 if inv.is_return else 0] += flt(p.base_amount)
			bucket[2].add(p.parent)
			if p.type == "Cash":
				cash_mode.setdefault(p.parent, p.mode_of_payment)
		# change handed back is included in the cash payment row; net it out
		for inv in invoices.values():
			if flt(inv.base_change_amount) and inv.name in cash_mode:
				totals[cash_mode[inv.name]][0] -= flt(inv.base_change_amount)
	for mode, (sales, returns, invs) in sorted(totals.items()):
		rows.append([mode, flt(sales, 2), flt(returns, 2), flt(sales + returns, 2), len(invs)])
	return rows


def inventory_summary() -> list[list]:
	rows = [["Item Code", "Medicine", "Warehouse", "Qty", "Valuation Rate", "Stock Value"]]
	bins = frappe.get_all(
		"Bin",
		filters={"actual_qty": ["!=", 0]},
		fields=["item_code", "warehouse", "actual_qty", "valuation_rate", "stock_value"],
		order_by="item_code, warehouse",
	)
	names = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", list({b.item_code for b in bins}) or [""]]},
			fields=["name", "item_name"],
			as_list=1,
		)
	)
	for b in bins:
		rows.append(
			[
				b.item_code,
				names.get(b.item_code, ""),
				b.warehouse,
				flt(b.actual_qty),
				flt(b.valuation_rate),
				flt(b.stock_value),
			]
		)
	return rows


def write_daily_snapshot(date=None) -> str:
	date = getdate(date or nowdate())
	folder = sub_dir(DAILY, date.strftime("%Y-%m-%d"))
	_write_xlsx(os.path.join(folder, "Daily-Sales.xlsx"), [COLUMNS, *sale_rows(date)], "Sales")
	_write_xlsx(os.path.join(folder, "Payment-Summary.xlsx"), payment_summary(date), "Payments")
	_write_xlsx(os.path.join(folder, "Inventory-Summary.xlsx"), inventory_summary(), "Inventory")
	logs = frappe.get_all(
		"PharmacyOS Backup Log",
		filters={"started_on": ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]]},
		fields=[
			"kind",
			"status",
			"started_on",
			"finished_on",
			"backup_path",
			"size_bytes",
			"verified",
			"error",
		],
		order_by="started_on",
	)
	with open(os.path.join(folder, "Backup-Metadata.json"), "w") as f:
		json.dump({"site": frappe.local.site, "date": str(date), "backups": logs}, f, indent=1, default=str)
	return folder


def prune_snapshots():
	"""Spreadsheets follow the daily-backup retention."""
	keep = cint(frappe.db.get_single_value("PharmacyOS Settings", "keep_daily_days")) or 30
	cutoff = (now_datetime() - timedelta(days=keep)).strftime("%Y-%m-%d")
	for kind in (SALES, DAILY):
		root = os.path.join(data_dir(), kind)
		if not os.path.isdir(root):
			continue
		for day in os.listdir(root):
			if day < cutoff:
				shutil.rmtree(os.path.join(root, day), ignore_errors=True)
