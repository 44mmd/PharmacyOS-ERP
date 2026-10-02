"""PharmacyOS Cloud connector: website orders → this pharmacy's ERP.

The pharmacy server is usually behind NAT, so it initiates everything:

* every minute `pull_orders` fetches order work from the Cloud
  (`GET {cloud}/integrations/erp/orders/feed`) and acknowledges each applied version
  (`POST {cloud}/integrations/erp/orders/<id>/ack`);
* products, prices and sellable stock go out through the transactional outbox (outbox.py).

Requests are signed: `X-PharmacyOS-Signature: sha256=HMAC(secret, ts\\nMETHOD\\npath\\nsha256(body))`
with `X-PharmacyOS-Timestamp`, using the shared secret in PharmacyOS Settings (never in code).

Order state machine (Cloud stage → ERP action, each idempotent):
* any open stage, no Sales Order yet → create + submit the Sales Order (reserves stock; rejected
  with a clear error when stock or the product is no longer sellable — the Cloud then moves the
  order to pharmacist review);
* `cancelled` → cancel the Sales Order (releases the reservation); refused once delivered;
* `completed` → deliver + invoice: a stock-updating Sales Invoice against the order with the
  website payment method. Batches are picked First-Expiry-First-Out and expired batches are never
  picked (ERPNext + PharmacyOS validators).
Network failures are not acknowledged, so the same version is retried next minute; business
errors are acknowledged with the error so the order is not retried forever.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from pharmacyos_erp.integration.outbox import queue_event


def _settings():
	return frappe.get_cached_doc("PharmacyOS Settings")


def cloud_enabled() -> bool:
	s = _settings()
	return bool(cint(s.enable_outbound_events) and s.get("cloud_base_url"))


def sign_request(secret: str, ts: str, method: str, path: str, body: bytes) -> str:
	message = f"{ts}\n{method.upper()}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
	return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def cloud_request(method: str, path: str, payload: dict | None = None):
	import requests

	s = _settings()
	secret = s.get_password("outbound_secret", raise_exception=False) or ""
	if not secret:
		frappe.throw(_("Set the shared secret in PharmacyOS Settings → Integration."))
	body = json.dumps(payload, separators=(",", ":"), default=str).encode() if payload is not None else b""
	ts = str(int(time.time()))
	headers = {
		"X-PharmacyOS-Timestamp": ts,
		"X-PharmacyOS-Signature": sign_request(secret, ts, method, path, body),
		"User-Agent": "PharmacyOS-ERP",
	}
	if payload is not None:
		headers["Content-Type"] = "application/json"
	response = requests.request(
		method,
		s.cloud_base_url.rstrip("/") + path,
		data=body or None,
		headers=headers,
		timeout=cint(s.outbound_timeout) or 15,
	)
	response.raise_for_status()
	return response.json()


def _record(ok: bool, error: str | None = None):
	values = {"cloud_last_error": None if ok else (error or "")[:1000]}
	if ok:
		values["cloud_last_pull"] = now_datetime()
	frappe.db.set_single_value("PharmacyOS Settings", values)
	frappe.clear_document_cache("PharmacyOS Settings", "PharmacyOS Settings")


# --------------------------------------------------------------------------- order processing


def _branch_code(entry) -> str:
	if entry.get("branch_code"):
		return entry["branch_code"]
	branch = _settings().get("online_order_branch")
	code = branch and frappe.db.get_value("Branch", branch, "pharmacyos_storefront_code")
	if not code:
		frappe.throw(_("Choose the branch for website orders in PharmacyOS Settings."))
	return code


def _sales_order(order_id: str):
	name = frappe.db.get_value("Sales Order", {"pharmacyos_order_id": order_id})
	return frappe.get_doc("Sales Order", name) if name else None


def _create(entry):
	from pharmacyos_erp.api.v1.orders import create_order

	notes = "\n".join(
		filter(
			None,
			[
				_("Website order {0}").format(entry["order_id"]),
				entry.get("delivery_address") and _("Address: {0}").format(entry["delivery_address"]),
				entry.get("payment_method") and _("Payment: {0}").format(entry["payment_method"]),
				entry.get("notes"),
			],
		)
	)
	create_order(
		order_id=entry["order_id"],
		branch_code=_branch_code(entry),
		items=entry["items"],
		customer=entry.get("customer") or {},
		notes=notes,
	)
	return _sales_order(entry["order_id"])


def _fulfil(so):
	"""Deliver and invoice the order once (stock leaves the shelf here, FEFO batches)."""
	if flt(so.per_delivered) >= 100:
		return
	try:  # ERPNext develop moved the mappers out of sales_order.py; version-16 keeps them there
		from erpnext.selling.doctype.sales_order.mapper import make_sales_invoice
	except ImportError:
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	si = make_sales_invoice(so.name)
	si.update_stock = 1
	si.is_pos = 1
	si.set("payments", [])
	si.append(
		"payments",
		{
			"mode_of_payment": _settings().get("online_payment_mode") or "Cash",
			"amount": flt(si.rounded_total or si.grand_total),
		},
	)
	si.insert()
	si.submit()


def apply_entry(entry: dict) -> dict:
	"""Bring the ERP in line with one Cloud order version. Idempotent."""
	status = entry.get("status")
	so = _sales_order(entry["order_id"])
	if status == "cancelled":
		if so and so.docstatus == 1:
			if flt(so.per_delivered) > 0:
				frappe.throw(
					_("Order {0} was already delivered; record a return instead.").format(entry["order_id"])
				)
			so.cancel()
		return {"erp_ref": so.name if so else None, "erp_status": "Cancelled"}
	if so is None:
		so = _create(entry)
	elif so.docstatus == 2:
		frappe.throw(_("Order {0} is cancelled in the pharmacy system.").format(entry["order_id"]))
	if status == "completed":
		_fulfil(so)
		so.reload()
	return {"erp_ref": so.name, "erp_status": so.status}


def pull_orders() -> dict:
	"""Scheduler entry point (every minute). Never raises: failures are recorded and retried."""
	if not cloud_enabled():
		return {"skipped": True}
	try:
		feed = cloud_request("GET", "/integrations/erp/orders/feed?limit=50")
	except Exception as e:
		_record(False, _("Could not reach PharmacyOS Cloud: {0}").format(e))
		return {"error": str(e)}
	results = {"applied": 0, "rejected": 0, "unacknowledged": 0}
	for entry in feed.get("orders", []):
		ack = {"version": entry.get("version")}
		try:
			ack.update(apply_entry(entry))
			frappe.db.commit()
			results["applied"] += 1
		except Exception as e:
			frappe.db.rollback()
			message = str(e) if isinstance(e, frappe.ValidationError) else repr(e)
			ack["error"] = frappe.utils.strip_html(message)[:1000]
			frappe.log_error(title=f"PharmacyOS website order {entry.get('order_id')}")
			results["rejected"] += 1
		try:
			cloud_request("POST", f"/integrations/erp/orders/{entry['order_id']}/ack", ack)
		except Exception:
			results["unacknowledged"] += 1  # re-applied next minute; every step is idempotent
	_record(True)
	frappe.db.commit()
	return results


# --------------------------------------------------------------------------- catalog


@frappe.whitelist(methods=["POST"])
def resync_catalog() -> int:
	"""Queue every published product (details + sellable stock) for the website. Owner action."""
	frappe.only_for(("System Manager", "Pharmacy Owner"))
	return queue_full_catalog()


def queue_full_catalog() -> int:
	items = frappe.get_all("Item", filters={"pharmacyos_publish": 1}, pluck="name")
	warehouses = frappe.get_all(
		"Branch", filters={"pharmacyos_warehouse": ["is", "set"]}, pluck="pharmacyos_warehouse"
	)
	for code in items:
		queue_event("catalog.changed", "Item", code, f"catalog:{code}")
		for warehouse in warehouses:
			queue_event("availability.changed", "Item", code, f"availability:{code}:{warehouse}")
	return len(items)


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:
	frappe.only_for(("System Manager", "Pharmacy Owner"))
	try:
		cloud_request("GET", "/integrations/erp/ping")
		_record(True)
		return {"ok": True}
	except Exception as e:
		_record(False, str(e))
		return {"ok": False, "error": str(e)[:300]}
