# Copyright (c) 2026, HALF and contributors
# License: GNU General Public License v3. See license.txt
"""Round 5 (M-2): restoring an encrypted backup follows one documented, checked procedure.

Found on the round-4 candidate (independent re-validation): following the documented steps, an
encrypted backup could not be restored onto a new machine — the procedure said to put the key in the
target site's configuration, but the script created that site itself and restored at once, so Frappe
aborted for lack of a key. The script now takes the original site's keys as a file (`--keys-file`),
creates a missing site only when asked (`--create-site`), and checks everything it can — bench
folder, site, checksums, site name, and that the key really decrypts the backup — before it changes
anything. These tests run every refusal against a throw-away bench folder (nothing is created or
restored), and the health check's new "stored passwords decrypt" item.
"""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

SCRIPT = os.path.abspath(
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "deploy", "restore-backup.sh")
)
KEY = "r5-test-backup-key-" + "k" * 24
WRONG = "r5-test-wrong-key-" + "w" * 24


def sha256(path):
	with open(path, "rb") as fh:
		return hashlib.sha256(fh.read()).hexdigest()


class TestRestoreScriptRefusals(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not os.path.exists(SCRIPT) or not shutil.which("gpg"):
			raise __import__("unittest").SkipTest("restore script or gpg not available")

	def setUp(self):
		self.root = tempfile.mkdtemp(prefix="r5-restore-")
		self.bench = os.path.join(self.root, "bench")
		os.makedirs(os.path.join(self.bench, "apps", "frappe"))
		os.makedirs(os.path.join(self.bench, "sites"))
		self.gnupg = os.path.join(self.root, "gnupg")
		os.makedirs(self.gnupg, mode=0o700)

	def tearDown(self):
		shutil.rmtree(self.root, ignore_errors=True)

	def site(self, name, **config):
		os.makedirs(os.path.join(self.bench, "sites", name))
		with open(os.path.join(self.bench, "sites", name, "site_config.json"), "w") as fh:
			json.dump({"db_name": "x", **config}, fh)

	def backup(self, site="pharmacy.local", encrypted=False, damage=False):
		folder = tempfile.mkdtemp(prefix=f"backup-{site}-", dir=self.root)
		dump = os.path.join(folder, "database.sql.gz")
		with gzip.open(dump, "wb") as fh:
			fh.write(b"-- PharmacyOS test dump\nselect 1;\n")
		if encrypted:
			subprocess.run(
				[
					"gpg",
					"--homedir",
					self.gnupg,
					"--batch",
					"--yes",
					"--pinentry-mode",
					"loopback",
					"--passphrase-fd",
					"0",
					"--symmetric",
					"--cipher-algo",
					"AES256",
					"-o",
					dump + ".gpg",
					dump,
				],
				input=KEY.encode(),
				check=True,
				capture_output=True,
			)
			os.replace(dump + ".gpg", dump)
		meta = {
			"site": site,
			"created_at": "2026-10-05 10:00:00",
			"app_versions": {"pharmacyos_erp": "test"},
			"encrypted": encrypted,
			"files": [{"name": "database.sql.gz", "sha256": sha256(dump)}],
		}
		with open(os.path.join(folder, "metadata.json"), "w") as fh:
			json.dump(meta, fh)
		if damage:
			with open(dump, "ab") as fh:
				fh.write(b"x")
		return folder

	def keys_file(self, **keys):
		path = os.path.join(self.root, f"keys-{len(os.listdir(self.root))}.json")
		with open(path, "w") as fh:
			json.dump(keys, fh)
		return path

	def run_script(self, site, folder, *args, bench=None, answer="no"):
		env = {
			**os.environ,
			"BENCH_DIR": bench or self.bench,
			"DB_ROOT_PASSWORD": "not-used",
			"GNUPGHOME": self.gnupg,
		}
		result = subprocess.run(
			["bash", SCRIPT, site, folder, *args],
			input=answer + "\n",
			env=env,
			capture_output=True,
			text=True,
			timeout=120,
		)
		output = result.stdout + result.stderr
		for secret in (KEY, WRONG):
			self.assertNotIn(secret, output, "a key was printed")
		return result.returncode, output

	# ---------------------------------------------------------------- refused before any change

	def test_not_a_bench(self):
		code, out = self.run_script("pharmacy.local", self.backup(), bench=os.path.join(self.root, "nowhere"))
		self.assertNotEqual(code, 0)
		self.assertIn("Not a bench", out)

	def test_missing_site_needs_create_site(self):
		code, out = self.run_script("pharmacy.local", self.backup())
		self.assertNotEqual(code, 0)
		self.assertIn("does not exist", out)
		self.assertIn("--create-site", out)
		self.assertFalse(os.path.exists(os.path.join(self.bench, "sites", "pharmacy.local")))

	def test_create_site_refuses_an_existing_site(self):
		self.site("pharmacy.local")
		code, out = self.run_script("pharmacy.local", self.backup(), "--create-site")
		self.assertNotEqual(code, 0)
		self.assertIn("already exists", out)

	def test_damaged_backup(self):
		self.site("pharmacy.local")
		code, out = self.run_script("pharmacy.local", self.backup(damage=True))
		self.assertNotEqual(code, 0)
		self.assertIn("Backup damaged", out)

	def test_backup_of_another_site(self):
		self.site("pharmacy.local")
		code, out = self.run_script("pharmacy.local", self.backup(site="other.local"))
		self.assertNotEqual(code, 0)
		self.assertIn("--allow-other-site", out)
		code, out = self.run_script("pharmacy.local", self.backup(site="other.local"), "--allow-other-site")
		self.assertIn("Cancelled", out)  # verification passed; the operator declined

	def test_encrypted_without_a_key(self):
		code, out = self.run_script("pharmacy.local", self.backup(encrypted=True), "--create-site")
		self.assertNotEqual(code, 0)
		self.assertIn("no backup_encryption_key", out)
		self.assertIn("Nothing was changed", out)
		self.assertFalse(os.path.exists(os.path.join(self.bench, "sites", "pharmacy.local")))
		self.site("pharmacy.local")  # existing site without a key: the same
		code, out = self.run_script("pharmacy.local", self.backup(encrypted=True, site="pharmacy.local"))
		self.assertNotEqual(code, 0)
		self.assertIn("no backup_encryption_key", out)

	def test_encrypted_with_a_wrong_key(self):
		folder = self.backup(encrypted=True)
		code, out = self.run_script(
			"pharmacy.local",
			folder,
			"--create-site",
			"--keys-file",
			self.keys_file(backup_encryption_key=WRONG, encryption_key="e"),
		)
		self.assertNotEqual(code, 0)
		self.assertIn("wrong key", out)
		self.assertFalse(os.path.exists(os.path.join(self.bench, "sites", "pharmacy.local")))
		self.site("pharmacy.local", backup_encryption_key=WRONG)
		code, out = self.run_script("pharmacy.local", folder)
		self.assertNotEqual(code, 0)
		self.assertIn("wrong key", out)

	def test_keys_of_an_existing_site_are_never_replaced(self):
		self.site("pharmacy.local", backup_encryption_key=WRONG, encryption_key="site-own")
		code, out = self.run_script(
			"pharmacy.local",
			self.backup(encrypted=True),
			"--keys-file",
			self.keys_file(backup_encryption_key=KEY, encryption_key="original"),
		)
		self.assertNotEqual(code, 0)
		self.assertIn("differs", out)
		with open(os.path.join(self.bench, "sites", "pharmacy.local", "site_config.json")) as fh:
			self.assertEqual(json.load(fh)["backup_encryption_key"], WRONG)

	# ---------------------------------------------------------------- verification passes

	def test_right_key_verifies_then_waits_for_the_operator(self):
		folder = self.backup(encrypted=True)
		code, out = self.run_script(
			"pharmacy.local",
			folder,
			"--create-site",
			"--keys-file",
			self.keys_file(backup_encryption_key=KEY, encryption_key="original"),
		)
		self.assertIn("the key decrypts the backup", out)
		self.assertIn("Cancelled", out)
		self.assertNotEqual(code, 0)
		self.assertFalse(os.path.exists(os.path.join(self.bench, "sites", "pharmacy.local")))
		self.site("pharmacy.local", backup_encryption_key=KEY)  # in place: the site's own key
		code, out = self.run_script("pharmacy.local", folder)
		self.assertIn("the key decrypts the backup", out)
		self.assertIn("Cancelled", out)

	def test_new_site_without_encryption_key_is_warned(self):
		_code, out = self.run_script(
			"pharmacy.local",
			self.backup(encrypted=True),
			"--create-site",
			"--keys-file",
			self.keys_file(backup_encryption_key=KEY),
		)
		self.assertIn("passwords stored in the database will not decrypt", out)


class TestHealthCheckStoredPasswords(IntegrationTestCase):
	def test_stored_passwords_must_decrypt(self):
		from cryptography.fernet import Fernet
		from frappe.utils.password import set_encrypted_password

		from pharmacyos_erp.backup.service import health_check, stored_passwords_readable

		set_encrypted_password("PharmacyOS Settings", "PharmacyOS Settings", "r5-secret", "outbound_secret")
		self.assertTrue(stored_passwords_readable())
		self.assertTrue(health_check()["stored_passwords"])
		# a site restored without the original site's encryption_key
		with patch.dict(frappe.local.conf, {"encryption_key": Fernet.generate_key().decode()}):
			self.assertFalse(stored_passwords_readable())
			result = health_check()
			self.assertFalse(result["stored_passwords"])
			self.assertFalse(result["ok"])
		self.assertTrue(stored_passwords_readable())
