# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Backups, sales snapshots, retention and health (Flow E)."""

import json
import os
import shutil
import tempfile
from datetime import datetime

import frappe
from frappe.tests import IntegrationTestCase

from pharmacyos_erp.backup import service, snapshots
from pharmacyos_erp.tests.utils import (
	make_batch,
	make_medicine,
	make_sales_invoice,
	make_user,
	receive,
	set_settings,
)


class BackupTestCase(IntegrationTestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix="pharmacyos-data-")
		self._previous = frappe.conf.get("pharmacyos_data_dir")
		frappe.conf["pharmacyos_data_dir"] = self.tmp

	def tearDown(self):
		if self._previous is None:
			frappe.conf.pop("pharmacyos_data_dir", None)
		else:
			frappe.conf["pharmacyos_data_dir"] = self._previous
		shutil.rmtree(self.tmp, ignore_errors=True)

	def fake_backup(self, day, slot, files=True):
		folder = os.path.join(self.tmp, "Backups", day, slot)
		os.makedirs(folder)
		entries = []
		if files:
			with open(os.path.join(folder, service.DB_FILE), "wb") as f:
				f.write(b"x")
			entries = [
				{
					"name": service.DB_FILE,
					"size": 1,
					"sha256": service.sha256(os.path.join(folder, service.DB_FILE)),
				}
			]
		with open(os.path.join(folder, service.METADATA), "w") as f:
			json.dump({"files": entries, "encrypted": True}, f)
		return folder


class TestBackupRun(BackupTestCase):
	def test_transaction_backup_and_sales_snapshot(self):
		item = make_medicine("POS-BK-MED", item_name="Backup Test Medicine", barcode="6290000000222")
		batch = make_batch(item.name, "BK-0001", 200)
		receive(item.name, batch, 10, rate=1000)
		invoice = make_sales_invoice(item.name, batch, 2, submit=True)

		log = frappe.get_doc("PharmacyOS Backup Log", service.run_backup("Manual"))
		self.assertEqual(log.status, "Success", log.error)
		self.assertTrue(log.verified)
		self.assertTrue(log.backup_path.startswith(self.tmp))
		self.assertTrue(service.verify_backup(log.backup_path)["ok"])
		with open(os.path.join(log.backup_path, service.METADATA)) as f:
			meta = json.load(f)
		self.assertEqual(meta["site"], frappe.local.site)
		self.assertIn("pharmacyos_erp", meta["app_versions"])
		self.assertFalse(os.path.exists(os.path.join(log.backup_path, "site_config_backup.json")))

		path = snapshots.write_sales_snapshot(f"{invoice.posting_date} 10:00:00")
		self.assertTrue(path.endswith("-00.xlsx"))
		rows = snapshots.sale_rows(invoice.posting_date)
		mine = [r for r in rows if r[0] == invoice.name]
		self.assertEqual(len(mine), 1)
		row = dict(zip(snapshots.COLUMNS, mine[0], strict=True))
		self.assertEqual(row["Batch"], "BK-0001")
		self.assertEqual(row["Barcode"], "6290000000222")
		self.assertEqual(row["Qty"], 2)

		health = service.backup_health()
		self.assertEqual(health["last_success"].name, log.name)

	def test_tampered_backup_fails_verification(self):
		folder = self.fake_backup("2026-01-01", "10-00")
		self.assertTrue(service.verify_backup(folder)["ok"])
		with open(os.path.join(folder, service.DB_FILE), "ab") as f:
			f.write(b"tamper")
		result = service.verify_backup(folder)
		self.assertFalse(result["ok"])
		self.assertFalse(service.verify_backup(os.path.join(self.tmp, "missing"))["ok"])


class TestRetention(BackupTestCase):
	def test_keeps_recent_hourly_daily_last_and_newest(self):
		set_settings(keep_hourly_days=2, keep_daily_days=10)
		recent = [self.fake_backup("2026-10-02", s) for s in ("08-00", "09-00")]
		old_day = [self.fake_backup("2026-09-28", s) for s in ("08-00", "09-00", "23-00")]
		ancient = self.fake_backup("2026-08-01", "23-00")
		deleted = service.apply_retention(now="2026-10-02 10:00:00")
		for folder in recent:
			self.assertTrue(os.path.isdir(folder))
		self.assertTrue(os.path.isdir(old_day[2]))  # last backup of that day kept as the daily backup
		self.assertFalse(os.path.isdir(old_day[0]))
		self.assertFalse(os.path.isdir(ancient))
		self.assertEqual(len(deleted), 3)

	def test_never_deletes_the_only_good_backup(self):
		only = self.fake_backup("2025-01-01", "23-00")
		broken = self.fake_backup("2025-01-02", "10-00", files=False)
		service.apply_retention(now="2026-10-02 10:00:00")
		self.assertTrue(os.path.isdir(only))
		self.assertFalse(os.path.isdir(broken))

	def test_interrupted_backup_rows_are_reconciled(self):
		good = self.fake_backup("2026-10-02", "07-00")
		rows = []
		for path in (good, os.path.join(self.tmp, "gone")):
			log = frappe.get_doc({"doctype": "PharmacyOS Backup Log", "kind": "Hourly", "status": "Running"})
			log.started_on = "2026-01-01 00:00:00"
			log.backup_path = path
			log.insert(ignore_permissions=True)
			rows.append(log.name)
		service.reconcile_running()
		self.assertEqual(frappe.db.get_value("PharmacyOS Backup Log", rows[0], "status"), "Success")
		self.assertEqual(frappe.db.get_value("PharmacyOS Backup Log", rows[1], "status"), "Failed")


class TestBackupAccess(IntegrationTestCase):
	def test_cashier_cannot_trigger_or_read_backups(self):
		user = make_user("pos-cashier-bk@example.com", ["Cashier"])
		frappe.set_user(user)
		try:
			self.assertRaises(frappe.PermissionError, service.backup_now)
			self.assertRaises(frappe.PermissionError, service.get_backup_status)
		finally:
			frappe.set_user("Administrator")


class TestSchedulerClock(IntegrationTestCase):
	def test_jobs_left_in_the_future_by_a_clock_put_back_run_again(self):
		# the computer's clock was a day ahead, then corrected: Frappe would wait a day for every job
		# (no hourly backups, no error). repair_job_clock puts them back to now.
		from frappe.utils import add_days, now_datetime

		job = frappe.db.get_value("Scheduled Job Type", {"method": "pharmacyos_erp.backup.service.hourly"}, "name")
		self.assertTrue(job)
		frappe.db.set_value("Scheduled Job Type", job, "last_execution", add_days(now_datetime(), 1))
		self.assertFalse(frappe.get_doc("Scheduled Job Type", job).is_event_due(add_days(now_datetime(), 0)))
		self.assertGreaterEqual(service.repair_job_clock(), 1)
		doc = frappe.get_doc("Scheduled Job Type", job)
		self.assertLessEqual(frappe.utils.get_datetime(doc.last_execution), now_datetime())
		# due again within the hour, as an hourly job should be
		self.assertTrue(doc.is_event_due(frappe.utils.add_to_date(now_datetime(), hours=1, minutes=1)))
		self.assertEqual(service.repair_job_clock(), 0)  # nothing left to repair
