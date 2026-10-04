"""Round 4 (D-5): `(voucher_type, voucher_no)` indexes on the batch bundle tables (pharmacy/locking.py).

Without them, cancelling any stock document scans and locks the batch entries of every item, which
deadlocks with concurrent sales of unrelated items (HTTP 508). Idempotent: `add_index` does nothing
when the index exists; `ensure_structure` keeps it on every migrate and creates it on fresh installs.
"""

from pharmacyos_erp.pharmacy.locking import ensure_voucher_indexes


def execute():
	ensure_voucher_indexes()
