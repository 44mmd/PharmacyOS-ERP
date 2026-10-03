# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class PharmacyOSSettings(Document):
	def validate(self):
		if cint(self.critical_days) > cint(self.expiring_soon_days):
			frappe.throw(
				_(
					"The Critical window ({0} days) cannot be longer than the Expiring Soon window ({1} days)."
				).format(cint(self.critical_days), cint(self.expiring_soon_days)),
				title=_("Expiry Windows"),
			)

		for fieldname in ("outbound_endpoint", "cloud_base_url"):
			if self.get(fieldname):
				validate_secure_url(self.get(fieldname), self.meta.get_label(fieldname))

	def on_update(self):
		# Expiry windows and identity are part of the boot payload.
		frappe.clear_cache()


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def is_insecure_url(url: str) -> bool:
	"""True when `url` may not be used for PharmacyOS Cloud traffic.

	HTTPS is required. Plain HTTP is tolerated only for a loopback development server on a site in
	developer mode, never in production.
	"""
	parsed = urlparse((url or "").strip())
	if parsed.scheme == "https" and parsed.hostname:
		return False
	host = parsed.hostname or ""
	local = host in LOCAL_HOSTS or host.endswith(".localhost")
	return not (parsed.scheme == "http" and local and frappe.conf.developer_mode)


def validate_secure_url(url: str, label: str) -> None:
	if is_insecure_url(url):
		frappe.throw(
			_("{0} must be an HTTPS address (for example https://api.example.com).").format(label),
			title=_("Insecure Address"),
		)


def get_settings():
	"""Cached settings document (read-only use)."""
	return frappe.get_cached_doc("PharmacyOS Settings")
