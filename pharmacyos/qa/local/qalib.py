"""QA harness for the PharmacyOS LOCAL ERP: real HTTP sessions against a running server, as real users.

QA-only. Nothing here ships in the installer (the server bundle takes only deploy/, dev/setup-dev-bench.sh
and the app without its tests). Every result is appended to results.json with expected / actual / PASS-FAIL.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("QA_BASE", "http://127.0.0.1")
OUT = os.environ.get("QA_OUT", os.path.join(os.path.dirname(__file__), "results.json"))


class Refused(Exception):
	def __init__(self, status: int, body: str):
		super().__init__(f"{status}: {body[:300]}")
		self.status = status
		self.body = body

	@property
	def message(self) -> str:
		try:
			data = json.loads(self.body)
		except ValueError:
			return self.body[:300]
		msgs = data.get("_server_messages")
		if msgs:
			try:
				return " | ".join(re.sub(r"<[^>]+>", "", json.loads(m).get("message", "")) for m in json.loads(msgs))
			except ValueError:
				pass
		return str(data.get("exception") or data.get("exc_type") or data)[:300]


class Session:
	"""One browser: its own cookie jar and CSRF token."""

	def __init__(self, label: str = ""):
		self.label = label
		self.jar = http.cookiejar.CookieJar()
		self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
		self.csrf = ""
		self.user = None

	def raw(self, method: str, path: str, data=None, form: bool = False, timeout: int = 180):
		if form:
			body = urllib.parse.urlencode(data or {}).encode()
			ctype = "application/x-www-form-urlencoded"
		else:
			body = json.dumps(data).encode() if data is not None else None
			ctype = "application/json"
		req = urllib.request.Request(
			BASE + path.replace(" ", "%20"),
			data=body,
			method=method,
			headers={"Content-Type": ctype, "Accept": "application/json", "X-Frappe-CSRF-Token": self.csrf},
		)
		try:
			with self.opener.open(req, timeout=timeout) as resp:
				return resp.status, resp.read().decode("utf-8", "replace")
		except urllib.error.HTTPError as e:
			return e.code, e.read().decode("utf-8", "replace")

	def req(self, method: str, path: str, data=None):
		status, text = self.raw(method, path, data)
		if status >= 400:
			raise Refused(status, text)
		return json.loads(text or "{}")

	def login(self, usr: str, pwd: str):
		status, text = self.raw("POST", "/api/method/login", {"usr": usr, "pwd": pwd})
		if status != 200:
			raise Refused(status, text)
		status, html = self.raw("GET", "/app")
		m = re.search(r'csrf_token\s*=\s*"([^"]+)"', html)
		if not m:  # website users have no desk; the /pos page also carries the token
			status, html = self.raw("GET", "/pos")
			m = re.search(r'csrf_token\s*=\s*"([^"]+)"', html) or re.search(r'"csrf_token":\s*"([^"]+)"', html)
		self.csrf = m.group(1) if m else ""
		self.user = usr
		return self

	def call(self, method: str, **kwargs):
		return self.req("POST", "/api/method/" + method, kwargs).get("message")

	def get_call(self, method: str, **kwargs):
		q = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in kwargs.items()})
		return self.req("GET", f"/api/method/{method}?{q}").get("message")

	def get_list(self, doctype: str, filters=None, fields=None, limit: int = 500, order_by: str | None = None):
		q = {"limit_page_length": limit}
		if filters is not None:
			q["filters"] = json.dumps(filters)
		if fields:
			q["fields"] = json.dumps(fields)
		if order_by:
			q["order_by"] = order_by
		return self.req("GET", f"/api/resource/{doctype}?" + urllib.parse.urlencode(q))["data"]

	def get_doc(self, doctype: str, name: str):
		return self.req("GET", f"/api/resource/{doctype}/{urllib.parse.quote(name)}")["data"]

	def insert(self, doc: dict):
		return self.req("POST", f"/api/resource/{doc['doctype']}", doc)["data"]

	def update(self, doctype: str, name: str, values: dict):
		return self.req("PUT", f"/api/resource/{doctype}/{urllib.parse.quote(name)}", values)["data"]

	def submit(self, doc: dict):
		return self.call("frappe.client.submit", doc=doc)

	def cancel(self, doctype: str, name: str):
		return self.call("frappe.client.cancel", doctype=doctype, name=name)

	def value(self, doctype: str, name, field):
		r = self.call("frappe.client.get_value", doctype=doctype, filters=name, fieldname=field)
		return (r or {}).get(field) if isinstance(field, str) else r


def rid() -> str:
	return "qa-" + uuid.uuid4().hex[:20]


class Results:
	def __init__(self, path: str = OUT):
		self.path = path
		self.rows = []
		if os.path.exists(path):
			with open(path) as f:
				self.rows = json.load(f)

	def add(self, area: str, check: str, expected: str, actual, ok: bool, evidence: str = ""):
		row = {
			"area": area,
			"check": check,
			"expected": expected,
			"actual": actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False, default=str)[:600],
			"result": "PASS" if ok else "FAIL",
			"evidence": evidence,
			"at": time.strftime("%Y-%m-%d %H:%M:%S"),
		}
		self.rows = [r for r in self.rows if not (r["area"] == area and r["check"] == check)]
		self.rows.append(row)
		print(("  ✔ " if ok else "  ✘ ") + f"[{area}] {check} — {row['actual'][:160]}", flush=True)
		self.save()
		return ok

	def refused(self, area: str, check: str, expected: str, fn, must_contain: str | None = None, evidence: str = ""):
		"""The action must be refused by the server (an HTTP error), optionally with a given message."""
		try:
			out = fn()
		except Refused as e:
			ok = must_contain is None or must_contain.lower() in (e.message + e.body).lower()
			return self.add(area, check, expected, f"refused HTTP {e.status}: {e.message}", ok, evidence)
		return self.add(area, check, expected, f"ACCEPTED (should be refused): {out}", False, evidence)

	def save(self):
		with open(self.path, "w") as f:
			json.dump(self.rows, f, ensure_ascii=False, indent=1)
