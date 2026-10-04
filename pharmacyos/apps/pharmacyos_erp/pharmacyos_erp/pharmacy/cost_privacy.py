"""Cost data is for the people who manage stock and money — not for the counter.

Counter staff (Cashier, Pharmacist) must search medicines, see selling prices and availability and
sell; they must not receive purchase prices, valuation rates or stock values. ERPNext hands those to
anyone who can read an Item, a Bin or their own sale, so the red-team found them everywhere: the Item
(valuation / last purchase rate), Bin (stock value), buying Item Prices, the sale's own
`incoming_rate` (cost of goods), its batch bundle, the stock ledger, and ERPNext helpers such as
`get_valuation_rate` or `get_incoming_rate` (even the integration account could call those).

Four layers, all server-side:

* **Field level.** The cost fields below sit at permission level `COST_LEVEL`. Every role that reads
  the document at level 0 gets read (and write, where it writes level 0) at that level too — except
  the counter and integration roles (`NO_COST_ROLES`). Frappe then removes those fields from every
  document, form load, list/report view, `get_value` and doc-method response for counter users, and
  discards any value they send for them (the server recomputes costs itself after that point).
* **Rows.** Buying Item Prices (purchase prices) are not readable at all without cost access.
* **Helpers.** ERPNext's whitelisted helpers that compute costs are wrapped
  (`override_whitelisted_methods`): refused without cost access, or answered without the cost keys
  where the sales screens need the rest of the answer (item details, stock balance, item dashboard).
  Query reports whose columns carry costs are refused.
* **The response boundary.** Field-level permissions are a *read* boundary: Frappe applies them when
  a document is fetched, but not to the copy a save, submit or insert sends back, nor to helpers that
  return `doc.as_dict()` (round-4 findings: `incoming_rate` in a cashier's own sale response,
  `last_purchase_rate` from the Item helper). So every JSON response leaving the server for a user
  without cost access is scrubbed (`scrub_response`, Frappe's `after_request` hook): cost keys are
  removed from documents, helper answers, list views, report results and the version history. The
  stored values are untouched — the server keeps computing and storing costs as before; only the
  presentation to that user changes.

"Cost access" = the user holds a role with read at `COST_LEVEL` on Item, i.e. any role that reads
items except the counter and integration roles (owner, managers, inventory, purchasing, accounts …).
Applied on install and every migrate (`setup.install.ensure_structure`), so upgrades get it too.
"""

import json
import re

import frappe
from frappe import _

COST_LEVEL = 5

# doctype → fields holding purchase prices, valuation or stock value
COST_FIELDS = {
	"Item": ("valuation_rate", "last_purchase_rate"),
	"Bin": ("valuation_rate", "stock_value"),
	"Stock Ledger Entry": (
		"incoming_rate",
		"outgoing_rate",
		"valuation_rate",
		"stock_value",
		"stock_value_difference",
		"stock_queue",
	),
	"Sales Invoice Item": ("incoming_rate",),
	"Delivery Note Item": ("incoming_rate",),
	"Packed Item": ("incoming_rate",),
	"Sales Order Item": ("valuation_rate",),
	"Quotation Item": ("valuation_rate",),
	"Serial and Batch Bundle": ("avg_rate", "total_amount"),
	"Serial and Batch Entry": ("incoming_rate", "outgoing_rate", "stock_value_difference", "stock_queue"),
	"Serial No": ("purchase_rate",),
}
# child tables follow their parent's permissions
PERMISSION_DOCTYPE = {
	"Sales Invoice Item": "Sales Invoice",
	"Delivery Note Item": "Delivery Note",
	"Packed Item": "Sales Invoice",
	"Sales Order Item": "Sales Order",
	"Quotation Item": "Quotation",
	"Serial and Batch Entry": "Serial and Batch Bundle",
}
# roles that never get cost data; everyone else who reads the document does
NO_COST_ROLES = ("Cashier", "Pharmacist", "PharmacyOS Integration", "All", "Guest", "Desk User")
_RIGHTS = ("read", "write")


# ------------------------------------------------------------------ setup


def ensure_cost_privacy() -> None:
	"""Idempotent: permission level on the cost fields + level rows for every cost-reading role."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	from frappe.permissions import add_permission, setup_custom_perms

	for doctype, fields in COST_FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		for fieldname in fields:
			df = meta.get_field(fieldname)
			if df and df.permlevel != COST_LEVEL:
				make_property_setter(
					doctype, fieldname, "permlevel", COST_LEVEL, "Int", validate_fields_for_doctype=False
				)

	for doctype in {PERMISSION_DOCTYPE.get(d, d) for d in COST_FIELDS}:
		if not frappe.db.exists("DocType", doctype):
			continue
		setup_custom_perms(doctype)  # copies the standard rules once, so every role keeps its access
		rows = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": doctype},
			fields=["name", "role", "permlevel", "read", "write", "if_owner"],
		)
		level0 = {}
		for row in rows:
			if row.permlevel == 0 and row.read and not row.if_owner:
				entry = level0.setdefault(row.role, {"read": 0, "write": 0})
				entry["read"] = 1
				entry["write"] = max(entry["write"], row.write)
		existing = {r.role: r for r in rows if r.permlevel == COST_LEVEL}
		for role, rights in level0.items():
			if role in NO_COST_ROLES:
				continue
			current = existing.get(role)
			if current is None:
				name = add_permission(doctype, role, COST_LEVEL, ptype="read")
				current = frappe.get_doc("Custom DocPerm", name)
			else:
				current = frappe.get_doc("Custom DocPerm", current.name)
			if any(current.get(k) != rights[k] for k in _RIGHTS):
				current.update(rights)
				current.save(ignore_permissions=True)
		for role, row in existing.items():
			if role in NO_COST_ROLES:
				frappe.delete_doc("Custom DocPerm", row.name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=doctype)
	for doctype in COST_FIELDS:
		frappe.clear_cache(doctype=doctype)


def remove_cost_privacy() -> None:
	"""Uninstall: back to ERPNext's own field levels."""
	for doctype, fields in COST_FIELDS.items():
		frappe.db.delete(
			"Property Setter",
			{"doc_type": doctype, "field_name": ["in", list(fields)], "property": "permlevel"},
		)
	for doctype in {PERMISSION_DOCTYPE.get(d, d) for d in COST_FIELDS}:
		frappe.db.delete("Custom DocPerm", {"parent": doctype, "permlevel": COST_LEVEL})
		frappe.clear_cache(doctype=doctype)


# ------------------------------------------------------------------ who may see costs


def can_see_costs(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if not user or user == "Guest":
		return False
	roles = set(frappe.get_roles(user)) - set(NO_COST_ROLES)
	if not roles:
		return False
	return bool(
		frappe.db.exists(
			"Custom DocPerm",
			{"parent": "Item", "permlevel": COST_LEVEL, "read": 1, "role": ["in", list(roles)]},
		)
	)


def require_cost_access() -> None:
	if not can_see_costs():
		frappe.throw(
			_("Purchase prices, valuation and stock values are not available to your role."),
			frappe.PermissionError,
		)


# ------------------------------------------------------------------ PharmacyOS pages

# value fields of the PharmacyOS pages (dashboard, batches & expiry, expiry intelligence, inventory
# health, reorder suggestions): stock and risk values at valuation, gross profit
PAGE_COST_KEYS = (
	"value",
	"valuation_rate",
	"inventory_value",
	"total_value",
	"risk_value",
	"expired_value",
	"expiring_30_value",
	"est_rate",
	"gross_profit",
)


def _blank(value):
	if isinstance(value, dict):
		return {k: _blank(v) for k, v in value.items()}
	return None


def page_costs(payload):
	"""A PharmacyOS page payload as this user may see it: cost values blanked (null) without cost access.

	Quantities, counts, dates and statuses are untouched; `costs_visible` tells the page to show "—".
	"""
	if can_see_costs():
		if isinstance(payload, dict):
			payload["costs_visible"] = True
		return payload

	def walk(value):
		if isinstance(value, dict):
			return {k: (_blank(v) if k in PAGE_COST_KEYS else walk(v)) for k, v in value.items()}
		if isinstance(value, list):
			return [walk(v) for v in value]
		return value

	out = walk(payload)
	if isinstance(out, dict):
		out["costs_visible"] = False
	return out


# ------------------------------------------------------------------ buying prices (rows)


def item_price_query_conditions(user: str | None = None) -> str:
	"""permission_query_conditions for Item Price: no purchase prices without cost access."""
	if can_see_costs(user):
		return ""
	return "(ifnull(`tabItem Price`.`buying`, 0) = 0)"


def item_price_has_permission(doc, ptype=None, user=None, debug=False) -> bool:
	if doc.get("buying") and not doc.get("selling") and not can_see_costs(user):
		return False
	return True


# ------------------------------------------------------------------ the response boundary

# keys that carry a purchase price, a valuation, a cost of goods or a stock value wherever they appear
# (documents, helper answers, report rows); `total_amount` only means that on a batch bundle
GENERIC_COST_KEYS = frozenset(
	{
		"valuation_rate",
		"last_purchase_rate",
		"incoming_rate",
		"outgoing_rate",
		"stock_value",
		"stock_value_difference",
		"stock_queue",
		"avg_rate",
		"purchase_rate",
		"buying_rate",
		"buying_amount",
		"base_incoming_rate",
		"base_valuation_rate",
		"landed_cost_voucher_amount",
		"cost_of_goods_sold",
	}
)
ALL_COST_KEYS = GENERIC_COST_KEYS | {f for fields in COST_FIELDS.values() for f in fields}
# query-report columns that show costs, by label
COST_LABEL = re.compile(
	r"\b(valuation|buying (rate|amount|price)|purchase (rate|price|amount|cost)|cost of goods|gross profit"
	r"|stock value|incoming rate|outgoing rate|average (rate|cost)|avg\.? rate|landed cost|unit cost"
	r"|cost price|total cost|cogs|balance value|opening value|in value|out value|val rate)\b",
	re.IGNORECASE,
)
# whitelisted methods whose answer is scrubbed only when it is a document (the 0 placeholders stay)
_VERSION_KEYS = ("changed", "row_changed", "added", "removed")


def _cost_keys_for(doctype) -> frozenset:
	if doctype in COST_FIELDS:
		return GENERIC_COST_KEYS | frozenset(COST_FIELDS[doctype])
	return GENERIC_COST_KEYS


def _column_is_cost(column) -> bool:
	if isinstance(column, dict):
		fieldname = column.get("fieldname") or ""
		label = column.get("label") or ""
	else:
		parts = str(column).split(":")
		label, fieldname = parts[0], frappe.scrub(parts[0]) if parts else ""
	return fieldname in ALL_COST_KEYS or bool(COST_LABEL.search(str(label)))


def _scrub_version(raw: str) -> tuple[str, bool]:
	"""A Version `data` JSON: drop every change entry naming a cost field."""
	try:
		data = json.loads(raw)
	except (TypeError, ValueError):
		return raw, False
	if not isinstance(data, dict):
		return raw, False
	changed = False
	if isinstance(data.get("changed"), list):
		kept = [e for e in data["changed"] if not (isinstance(e, list) and e and e[0] in ALL_COST_KEYS)]
		changed |= len(kept) != len(data["changed"])
		data["changed"] = kept
	if isinstance(data.get("row_changed"), list):
		for entry in data["row_changed"]:
			if isinstance(entry, list) and len(entry) >= 4 and isinstance(entry[3], list):
				kept = [e for e in entry[3] if not (isinstance(e, list) and e and e[0] in ALL_COST_KEYS)]
				changed |= len(kept) != len(entry[3])
				entry[3] = kept
	for key in ("added", "removed"):
		for entry in data.get(key) or []:
			if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], dict):
				changed |= scrub_payload(entry[1])
	return (json.dumps(data) if changed else raw), changed


def scrub_payload(value, doctype=None) -> bool:
	"""Remove cost keys from a JSON payload in place. Returns whether anything was removed.

	Handles documents (keyed by their `doctype`), plain dicts and lists (helper answers), the list view
	shape (`keys` + `values` rows), the query report shape (`columns` + `result` rows) and the version
	history (`versions[].data`).
	"""
	changed = False
	if isinstance(value, dict):
		dt = value.get("doctype") if isinstance(value.get("doctype"), str) else doctype
		keys = _cost_keys_for(dt)
		for key in list(value):
			if key in keys:
				del value[key]
				changed = True
		if isinstance(value.get("keys"), list) and isinstance(value.get("values"), list):
			positions = [i for i, k in enumerate(value["keys"]) if k in ALL_COST_KEYS]
			if positions:
				for row in value["values"]:
					if isinstance(row, list):
						for i in positions:
							if i < len(row):
								row[i] = None
				changed = True
		if isinstance(value.get("columns"), list) and isinstance(value.get("result"), list):
			cost_columns = [(i, c) for i, c in enumerate(value["columns"]) if _column_is_cost(c)]
			if cost_columns:
				names = {
					(c.get("fieldname") if isinstance(c, dict) else frappe.scrub(str(c).split(":")[0]))
					for _i, c in cost_columns
				}
				for row in value["result"]:
					if isinstance(row, dict):
						for name in names:
							if name in row:
								row[name] = None
					elif isinstance(row, list):
						for i, _c in cost_columns:
							if i < len(row):
								row[i] = None
				changed = True
		if isinstance(value.get("versions"), list):
			for version in value["versions"]:
				if isinstance(version, dict) and isinstance(version.get("data"), str):
					version["data"], did = _scrub_version(version["data"])
					changed |= did
		if dt == "Version" and isinstance(value.get("data"), str):
			value["data"], did = _scrub_version(value["data"])
			changed |= did
		for child in value.values():
			if isinstance(child, dict | list):
				changed |= scrub_payload(child, dt)
	elif isinstance(value, list):
		for child in value:
			if isinstance(child, dict | list):
				changed |= scrub_payload(child, doctype)
	return changed


def _should_scrub() -> bool:
	"""Scrub unless the request's user may see costs. Without a database (failed init) scrub anyway."""
	if not getattr(frappe.local, "db", None):
		return True
	try:
		return not can_see_costs()
	except Exception:
		return True


def scrub_response(response=None, request=None, **kwargs) -> None:
	"""after_request hook: strip cost keys from every JSON answer to a user without cost access."""
	if response is None or getattr(response, "direct_passthrough", False):
		return
	if not str(response.mimetype or "").startswith("application/json"):
		return
	if not _should_scrub():
		return
	import orjson

	try:
		body = orjson.loads(response.get_data())
	except Exception:
		return  # not JSON after all: nothing to scrub
	try:
		if scrub_payload(body):
			response.set_data(orjson.dumps(body, default=str))
	except Exception:
		# fail closed: a user without cost access never receives an answer that could not be scrubbed
		frappe.log_error(title="PharmacyOS: cost scrub failed")
		response.set_data(
			orjson.dumps(
				{
					"exc_type": "CostPrivacyError",
					"exception": "The answer could not be filtered for your role and was withheld.",
				}
			)
		)
		response.status_code = 403


# ------------------------------------------------------------------ ERPNext helpers

COST_KEYS = ("valuation_rate", "incoming_rate", "last_purchase_rate", "stock_value", "stock_value_difference")
BUYING_DOCTYPES = (
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Material Request",
	"Supplier Quotation",
	"Request for Quotation",
	"Subcontracting Order",
	"Subcontracting Receipt",
	"Stock Entry",
	"Stock Reconciliation",
)


def _strip(value):
	if isinstance(value, dict):
		for key in COST_KEYS:
			if key in value:
				value[key] = 0
		for child in value.values():
			_strip(child)
	elif isinstance(value, list | tuple):
		for child in value:
			_strip(child)
	return value


def _refuse_buying_context(ctx) -> None:
	"""A buying document or a buying-only price list means purchase prices: cost access only."""
	ctx = frappe.parse_json(ctx) if isinstance(ctx, str) else (ctx or {})
	price_list = ctx.get("price_list") or ctx.get("buying_price_list")
	buying_list = price_list and frappe.db.get_value(
		"Price List", price_list, ["buying", "selling"], as_dict=True
	)
	if ctx.get("doctype") in BUYING_DOCTYPES or (
		buying_list and buying_list.buying and not buying_list.selling
	):
		require_cost_access()


@frappe.whitelist()
def get_item_details(
	ctx: dict | str,
	doc: dict | str | None = None,
	for_validate: bool = False,
	overwrite_warehouse: bool = True,
):
	from erpnext.stock.get_item_details import get_item_details as original

	if can_see_costs():
		return original(ctx, doc=doc, for_validate=for_validate, overwrite_warehouse=overwrite_warehouse)
	_refuse_buying_context(ctx)
	return _strip(original(ctx, doc=doc, for_validate=for_validate, overwrite_warehouse=overwrite_warehouse))


@frappe.whitelist()
def get_item_master_details(item_code: str, company: str | None = None):
	"""`erpnext.stock.doctype.item.item.get_item_details`: the whole Item record, as `as_dict()` — which
	ignores field-level permissions (round-4 finding: last purchase rate for cashiers)."""
	from erpnext.stock.doctype.item.item import get_item_details as original

	result = original(item_code, company)
	if can_see_costs():
		return result
	for key in _cost_keys_for("Item"):
		result.pop(key, None)
	return result


@frappe.whitelist()
def apply_price_list(ctx: dict | str, as_doc: bool = False, doc: dict | str | None = None):
	from erpnext.stock.get_item_details import apply_price_list as original

	if not can_see_costs():
		_refuse_buying_context(ctx)
	return original(ctx, as_doc=as_doc, doc=doc)


@frappe.whitelist()
def get_valuation_rate(item_code: str, company: str, warehouse: str | None = None):
	from erpnext.stock.get_item_details import get_valuation_rate as original

	require_cost_access()
	return original(item_code, company, warehouse)


@frappe.whitelist()
def get_incoming_rate(args: dict | str, raise_error_if_no_rate: bool = True, fallbacks: bool = True):
	from erpnext.stock.utils import get_incoming_rate as original

	require_cost_access()
	return original(args, raise_error_if_no_rate=raise_error_if_no_rate, fallbacks=fallbacks)


@frappe.whitelist()
def get_stock_balance(
	item_code: str,
	warehouse: str | None,
	posting_date: str | None = None,
	posting_time: str | None = None,
	with_valuation_rate: bool = False,
	with_serial_no: bool = False,
	inventory_dimensions_dict: dict | None = None,
):
	from erpnext.stock.utils import get_stock_balance as original

	result = original(
		item_code,
		warehouse,
		posting_date,
		posting_time,
		with_valuation_rate=with_valuation_rate,
		with_serial_no=with_serial_no,
		inventory_dimensions_dict=inventory_dimensions_dict,
	)
	if with_valuation_rate and not can_see_costs() and isinstance(result, list | tuple):
		result = [result[0], 0.0, *result[2:]]  # the quantity, never the rate
	return result


@frappe.whitelist()
def get_item_dashboard_data(
	item_code: str | None = None,
	warehouse: str | None = None,
	item_group: str | None = None,
	start: int = 0,
	sort_by: str = "actual_qty",
	sort_order: str = "desc",
):
	from erpnext.stock.dashboard.item_dashboard import get_data as original

	rows = original(item_code, warehouse, item_group, start, sort_by, sort_order)
	return rows if can_see_costs() else _strip(rows)


@frappe.whitelist()
def get_quick_stock_item_details(
	warehouse: str, date: str, item: str | None = None, barcode: str | None = None
):
	require_cost_access()
	from erpnext.stock.doctype.quick_stock_balance.quick_stock_balance import get_stock_item_details

	return get_stock_item_details(warehouse, date, item, barcode)


@frappe.whitelist()
def get_stock_entry_warehouse_details(args: str | dict):
	require_cost_access()
	from erpnext.stock.doctype.stock_entry.stock_entry import get_warehouse_details

	return get_warehouse_details(args)


@frappe.whitelist()
def get_stock_reconciliation_items(
	warehouse: str,
	posting_date: str,
	posting_time: str,
	company: str,
	item_code: str | None = None,
	ignore_empty_stock: bool = False,
):
	require_cost_access()
	from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_items

	return get_items(warehouse, posting_date, posting_time, company, item_code, ignore_empty_stock)


@frappe.whitelist()
def get_capitalization_warehouse_details(ctx: dict | str):
	require_cost_access()
	from erpnext.assets.doctype.asset_capitalization.asset_capitalization import get_warehouse_details

	return get_warehouse_details(ctx)


@frappe.whitelist()
def get_capitalization_consumed_stock_item_details(ctx: dict | str):
	require_cost_access()
	from erpnext.assets.doctype.asset_capitalization.asset_capitalization import (
		get_consumed_stock_item_details,
	)

	return get_consumed_stock_item_details(ctx)


@frappe.whitelist()
def get_items_tagged_to_wip_composite_asset(params: dict | str):
	require_cost_access()
	from erpnext.assets.doctype.asset_capitalization.asset_capitalization import (
		get_items_tagged_to_wip_composite_asset as original,
	)

	return original(params)


@frappe.whitelist()
def get_stock_value_by_item_group_chart(
	chart_name: str | None = None,
	chart: dict | str | None = None,
	no_cache: int | str | None = None,
	filters: dict | str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	timespan: str | None = None,
	time_interval: str | None = None,
	heatmap_year: str | None = None,
):
	"""Stock value per item group (dashboard chart): an aggregate of costs — cost access only."""
	require_cost_access()
	from erpnext.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group import (
		get,
	)

	return get(
		chart_name=chart_name,
		chart=chart,
		no_cache=no_cache,
		filters=filters,
		from_date=from_date,
		to_date=to_date,
		timespan=timespan,
		time_interval=time_interval,
		heatmap_year=heatmap_year,
	)


# ------------------------------------------------------------------ query reports

# ERPNext reports built on costs: refused outright without cost access (whatever their role rows say)
COST_REPORTS = frozenset(
	{
		"Stock Balance",
		"Stock Ledger",
		"Stock Ageing",
		"Warehouse wise Item Balance Age and Value",
		"Gross Profit",
		"Stock and Account Value Comparison",
		"Incorrect Stock Value Report",
		"Item Prices",
		"Item Price Stock",
		"Cogs By Item Group",
		"FIFO Queue vs Qty After Transaction Comparison",
		"Stock Ledger Variance",
		"Stock Analytics",
		"Serial and Batch Summary",
		"Purchase Analytics",
		"Purchase Register",
		"Item-wise Purchase Register",
		"Item-wise Purchase History",
		"Purchase Order Analysis",
		"Stock Closing Balance",
		"Incorrect Balance Qty After Transaction",
		"Batch-Wise Balance History",
	}
)


def _report_result_has_costs(result) -> bool:
	columns = (result or {}).get("columns") if isinstance(result, dict) else None
	return bool(columns) and any(_column_is_cost(c) for c in columns)


def _refuse_cost_report(report_name: str) -> None:
	if report_name in COST_REPORTS:
		require_cost_access()


@frappe.whitelist()
def run_query_report(
	report_name: str,
	filters: str | dict | None = None,
	user: str | None = None,
	ignore_prepared_report: bool = False,
	custom_columns: str | list | None = None,
	is_tree: bool = False,
	parent_field: str | None = None,
	are_default_filters: bool = True,
	js_filters: str | list | None = None,
):
	"""`frappe.desk.query_report.run`: a report with cost columns is a cost report."""
	from frappe.desk.query_report import run as original

	if can_see_costs():
		return original(
			report_name,
			filters=filters,
			user=user,
			ignore_prepared_report=ignore_prepared_report,
			custom_columns=custom_columns,
			is_tree=is_tree,
			parent_field=parent_field,
			are_default_filters=are_default_filters,
			js_filters=js_filters,
		)
	_refuse_cost_report(report_name)
	result = original(
		report_name,
		filters=filters,
		user=user,
		ignore_prepared_report=ignore_prepared_report,
		custom_columns=custom_columns,
		is_tree=is_tree,
		parent_field=parent_field,
		are_default_filters=are_default_filters,
		js_filters=js_filters,
	)
	if _report_result_has_costs(result):
		require_cost_access()
	return result


@frappe.whitelist()
def export_query_report():
	"""`frappe.desk.query_report.export_query`: the file is not JSON, so the report is refused."""
	from frappe.desk.query_report import export_query as original

	if not can_see_costs():
		report_name = frappe.local.form_dict.get("report_name")
		_refuse_cost_report(report_name)
		filters = frappe.local.form_dict.get("filters")
		from frappe.desk.query_report import run

		if _report_result_has_costs(run(report_name, filters=filters)):
			require_cost_access()
	return original()


@frappe.whitelist()
def make_prepared_report(report_name: str, filters: dict | str | list | None = None):
	"""`frappe.core.doctype.prepared_report.prepared_report.make_prepared_report` (background reports,
	served later as a file): cost reports refused up front."""
	from frappe.core.doctype.prepared_report.prepared_report import make_prepared_report as original

	if not can_see_costs():
		_refuse_cost_report(report_name)
	return original(report_name, filters)


_E = "erpnext"
_P = "pharmacyos_erp.pharmacy.cost_privacy"
OVERRIDES = {
	f"{_E}.stock.get_item_details.get_item_details": f"{_P}.get_item_details",
	f"{_E}.stock.doctype.item.item.get_item_details": f"{_P}.get_item_master_details",
	f"{_E}.stock.get_item_details.apply_price_list": f"{_P}.apply_price_list",
	f"{_E}.stock.get_item_details.get_valuation_rate": f"{_P}.get_valuation_rate",
	f"{_E}.stock.utils.get_incoming_rate": f"{_P}.get_incoming_rate",
	f"{_E}.stock.utils.get_stock_balance": f"{_P}.get_stock_balance",
	f"{_E}.stock.dashboard.item_dashboard.get_data": f"{_P}.get_item_dashboard_data",
	f"{_E}.stock.doctype.quick_stock_balance.quick_stock_balance.get_stock_item_details": f"{_P}.get_quick_stock_item_details",
	f"{_E}.stock.doctype.stock_entry.stock_entry.get_warehouse_details": f"{_P}.get_stock_entry_warehouse_details",
	f"{_E}.stock.doctype.stock_reconciliation.stock_reconciliation.get_items": f"{_P}.get_stock_reconciliation_items",
	f"{_E}.assets.doctype.asset_capitalization.asset_capitalization.get_warehouse_details": f"{_P}.get_capitalization_warehouse_details",
	f"{_E}.assets.doctype.asset_capitalization.asset_capitalization.get_consumed_stock_item_details": f"{_P}.get_capitalization_consumed_stock_item_details",
	f"{_E}.assets.doctype.asset_capitalization.asset_capitalization.get_items_tagged_to_wip_composite_asset": f"{_P}.get_items_tagged_to_wip_composite_asset",
	f"{_E}.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group.get": f"{_P}.get_stock_value_by_item_group_chart",
	"frappe.desk.query_report.run": f"{_P}.run_query_report",
	"frappe.desk.query_report.export_query": f"{_P}.export_query_report",
	"frappe.core.doctype.prepared_report.prepared_report.make_prepared_report": f"{_P}.make_prepared_report",
}
