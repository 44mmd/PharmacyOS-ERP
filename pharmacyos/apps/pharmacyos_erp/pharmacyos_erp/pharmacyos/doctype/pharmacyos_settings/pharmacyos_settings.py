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

		if self.enable_outbound_events and self.outbound_endpoint:
			parsed = urlparse(self.outbound_endpoint)
			local = parsed.hostname in ("localhost", "127.0.0.1") or (parsed.hostname or "").endswith(
				".localhost"
			)
			if parsed.scheme != "https" and not (local and frappe.conf.developer_mode):
				frappe.throw(_("The PharmacyOS endpoint must use HTTPS."), title=_("Insecure Endpoint"))

	def on_update(self):
		# Expiry windows and identity are part of the boot payload.
		frappe.clear_cache()


def get_settings():
	"""Cached settings document (read-only use)."""
	return frappe.get_cached_doc("PharmacyOS Settings")
