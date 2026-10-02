"""PharmacyOS data folder layout (outside the application code, survives updates).

	<data>/Backups/<YYYY-MM-DD>/<HH-MM>/      database.sql.gz [+ files], metadata.json
	<data>/Sales/<YYYY-MM-DD>/Sales-<HH-00>.xlsx
	<data>/Daily Reports/<YYYY-MM-DD>/        Daily-Sales.xlsx, Payment-Summary.xlsx, ...

The folder is resolved from, in order: the server's site config `pharmacyos_data_dir` (set by the
installer), PharmacyOS Settings → Data Folder, or `<bench>/pharmacyos-data/<site>`.
"""

import os

import frappe
from frappe.utils import get_bench_path

BACKUPS = "Backups"
SALES = "Sales"
DAILY = "Daily Reports"


def data_dir() -> str:
	path = (
		frappe.conf.get("pharmacyos_data_dir")
		or frappe.db.get_single_value("PharmacyOS Settings", "data_directory")
		or os.path.join(get_bench_path(), "pharmacyos-data", frappe.local.site)
	)
	path = os.path.abspath(os.path.expanduser(path))
	os.makedirs(path, exist_ok=True)
	return path


def sub_dir(*parts) -> str:
	path = os.path.join(data_dir(), *parts)
	os.makedirs(path, exist_ok=True)
	return path
