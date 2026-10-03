"""Sales documents with PharmacyOS's stock lock order (registered via `override_doctype_class`).

Frappe reloads the stored copy of a document `FOR UPDATE` — including its child rows — at the very
start of every save/submit of an existing document (`load_doc_before_save`), before any doc_event hook
runs. Those child-row locks (with their index gaps) collide with a concurrent cashier inserting a new
invoice, while the Bin lock taken later in `before_validate` collides the other way round: a deadlock.

These classes take the Bin locks (`stock_guard.lock_document_stock`) first, so every transaction that
moves or reserves stock locks in the same order: Bin rows, then everything else. New documents have no
stored copy; for them the `before_validate` hook is the first lock. The ERPNext classes are extended,
not modified.
"""

from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder
from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote

from pharmacyos_erp.pharmacy.stock_guard import lock_document_stock


class StockLockFirst:
	def load_doc_before_save(self, *, raise_exception: bool = False):
		lock_document_stock(self)
		return super().load_doc_before_save(raise_exception=raise_exception)


class PharmacySalesInvoice(StockLockFirst, SalesInvoice):
	pass


class PharmacyPOSInvoice(StockLockFirst, POSInvoice):
	pass


class PharmacyDeliveryNote(StockLockFirst, DeliveryNote):
	pass


class PharmacySalesOrder(StockLockFirst, SalesOrder):
	pass
