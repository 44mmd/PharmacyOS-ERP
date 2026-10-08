# ERPNext fork patches

Every change this fork makes to ERPNext code outside `pharmacyos/` must be listed here with its reason,
files, upstream-compatibility risk and removal path.

**Current state: none.** All PharmacyOS functionality lives in the `pharmacyos_erp` app
(`pharmacyos/apps/pharmacyos_erp`) and uses Frappe/ERPNext extension points only: hooks, doc_events,
custom fields, property setters, app-shipped DocTypes / Pages / Sidebars / Dock / Print Formats,
template overrides (`www/login.html` extends Frappe's login; `footer_powered.html`), translations and
whitelisted API methods.

ERPNext's Arabic translation (`erpnext/locale/ar.po`) needs **no patch** for the 1.0.0-rc.3 terminology change
(a cash/POS shift is «شِفت», plural «شِفتات», masculine), so none is made. Its POS / cashier strings (POS Opening
Entry, POS Closing Entry, Cashier Closing, outdated opening entry, …) do not use the older shift word at all; the
only 7 strings that do (8 occurrences) are about asset-depreciation shifts (Asset Shift Allocation / Asset Shift
Factor: *Depreciate based on shifts*, *Shift Name*, the shift-based depreciation message) and workstation /
Item Lead Time shifts (*No of Shift*, *Shift Time (In Hours)*, the per-day formula, the Workstation working-hours
help) — a different thing from a cash shift, left as upstream ships them. Everything a pharmacy reads about a cash
shift (the POS, the shift dialogs, server messages, the Pharmacy Report, the sidebar, and ERPNext's own
*Create POS Opening Entry* button) takes its Arabic from the app's `locale/ar.po`, which wins over ERPNext's
(the installer also takes ERPNext from the upstream v16.37.0 tag, not from this tree). `qa/check_terminology.py`
names those 7 msgids (`ERPNEXT_NOT_A_CASH_SHIFT`) and fails on any other ERPNext string with the older word.

The app's `ar.po` overrides a generic English word only where PharmacyOS wants the same Arabic everywhere in the
desk. Words it needs in one sense only carry a **context** (`msgctxt`), so the rest of the desk keeps Frappe's and
ERPNext's Arabic: the counter's *Shift* (`_("Shift", context="POS")` in `www/pos.py`) and the Pharmacy Report's
shift table (*Opened* / *Closed* — context *Shift table*, *Open* — context *Shift status*). ERPNext's own *Shift*
field and column on the asset-depreciation screens (Depreciation Schedule, the asset's schedule table) therefore
read upstream's Arabic for the generic word, Frappe's *Open* button and the *Closed* status of orders read
Frappe's; `tests/test_terminology.py` (`test_generic_words_keep_upstream_arabic`) and the QA's desk check verify
that PharmacyOS leaves those four generic words as upstream serves them.

Extension points used for consistency and least privilege (still no ERPNext file changed):

| Extension | What | Why |
|---|---|---|
| `override_doctype_class` | Sales Invoice, POS Invoice, Delivery Note, Sales Order → subclasses in `pharmacy/document_classes.py` that only add one step: take the document's locks (Bin rows, then a return's original sale) before Frappe reloads the stored copy — on save, submit **and cancel** | One lock order for every sale, return, delivery and website order, so competing transactions wait instead of deadlocking, and returns against one sale run one after the other. Another app that overrides the same classes would conflict; none is installed. |
| `doc_events` on sales documents | `before_validate` lock; `validate`: return integrity (`pharmacy/returns.py`) and website-reservation protection (`pharmacy/stock_guard.py`); `on_submit` / `on_cancel`: the original sale's return ledger | Returns can never exceed what was sold — also for simultaneous requests (see below); counter sales cannot take units reserved for website orders. |
| Custom field `pharma_return_ledger` (Sales Invoice, POS Invoice, Delivery Note; hidden, no-copy) | Quantities returned against the document, one entry per submitted return, written in the return's transaction and read with a locking read of the (locked) original row | MariaDB's REPEATABLE READ snapshot hides a return committed while the second one waited for the lock; a locking read of the ledger does not. Filled for existing sales by patch `v1.return_ledger`. |
| `doc_events` on Item Price (`on_update`, `on_trash`) and Selling Settings | Queue `catalog.changed` (+ availability) for published items | The ERP price reaches the website without an Item save. |
| `permission_query_conditions` / `has_permission` on User | The PharmacyOS Integration account sees only its own User record | Cloud credentials cannot enumerate staff accounts. |
| Cancelling an original sale (same `override_doctype_class` step) | Locks the sale's row right after its Bin rows and refuses (`SaleHasActiveReturnsError`) from a locking read of the return ledger; submitting a return re-reads the original's docstatus under the same lock (`ReturnAgainstCancelledSaleError`) | Frappe's back-link check and ERPNext's "return against must be submitted" check read the REPEATABLE READ snapshot: a return racing the cancellation of its sale could leave a cancelled sale with a live return (phantom stock, refund against a reversed sale). |
| `validate` on returns: credit-note authority (`returns.check_credit_note_authority`) | Without a manager/owner/accountant role a return must reference a sale the user can open and bring the goods back when that sale took them from stock | Counter staff could issue free-standing or money-only credit notes. |
| Property Setters `permlevel = 5` on cost fields + Custom DocPerms at level 5 (`pharmacy/cost_privacy.py`, applied on every migrate) | Item valuation/last purchase rate, Bin valuation/stock value, Stock Ledger Entry rates and values, Sales Invoice Item incoming rate, Serial and Batch Bundle / Entry rates and values; level 5 granted to every role that reads the document at level 0 except Cashier, Pharmacist, PharmacyOS Integration | Counter staff must not receive purchase prices, valuation or stock values. Roles that later gain level-0 read get level 5 on the next migrate. Removed on uninstall. |
| `permission_query_conditions` / `has_permission` on Item Price | Buying Item Prices only for users with cost access | Purchase prices. |
| `override_whitelisted_methods` (`cost_privacy.OVERRIDES`) | ERPNext helpers that compute costs: `get_valuation_rate`, `get_incoming_rate`, quick stock balance, stock-entry / reconciliation / asset-capitalization helpers and the stock-value-by-item-group chart refused without cost access; `get_item_details`, `apply_price_list` (buying side refused), `get_stock_balance` and the item dashboard answer without cost keys | These helpers only check Item/Bin read (any desk user), some no permission at all. Another app overriding the same methods would conflict; none is installed. Review the list when upgrading ERPNext. |
| `doc_events` on Price List + scheduler job `outbox.queue_price_validity_changes` (every minute, acts once per date) | Re-send the catalog when the selling list is enabled/disabled, and items whose price starts or ends at midnight | The website price is the public selling price valid today (`api/v1/catalog.public_prices`); nothing is saved when a price expires. |
| `after_request` hook (`cost_privacy.scrub_response`) | Every JSON answer to a user without cost access leaves without cost keys (documents echoed by save/insert/submit and REST v1/v2, helper answers, list and report rows, version history) | Field-level permissions apply on reads only; write endpoints and whole-record helpers returned `incoming_rate`, `last_purchase_rate`, `valuation_rate` to counter staff (round 4). Stored values untouched. |
| `override_whitelisted_methods` on `erpnext.stock.doctype.item.item.get_item_details`, `frappe.desk.query_report.run` / `export_query`, `prepared_report.make_prepared_report` | The Item helper answers without cost fields; query reports whose columns carry costs are refused without cost access | Round 4 cost-confidentiality boundary. |
| `override_doctype_class` POS Invoice Merge Log + `before_validate` on Sales Invoice (`pharmacy/consolidation.py`) | `is_consolidated` is accepted only while POS closing merges the shift's invoices; anywhere else a controlled validation error | A client-set flag reached ERPNext's skipped calculations and died with an unhandled `TypeError` (HTTP 500). |
| `validate` on returns: root sale and money (`pharmacy/returns.py`) | A return must reference the original sale (never another return); cumulative quantities *and* amounts (row/document discounts, taxes, UOM) stay within the root sale; the ledger on the sale carries quantities and amounts | Round 4: return chains and refunds above what was paid. Patch `return_chain_integrity` rebuilds ledgers for historical chains and lists them in an Error Log entry. |
| Custom field `pharmacyos_pos_request_id` (Sales Invoice, POS Invoice; Data, **unique**, hidden, read-only, no-copy) + whitelisted `pharmacyos_erp.pos.api` + website page `www/pos` | The PharmacyOS POS screen (`/pos`, web browser and Windows desktop). The API only orchestrates ERPNext's own POS documents as the signed-in user (no `ignore_permissions`); the field makes checkout idempotent (a retried checkout returns the first invoice). See `docs/WEB_POS.md` | One POS for two clients without a second implementation; network retries and double clicks never sell twice. Empty on every document not made by `/pos`. Removed with the app's custom fields on uninstall. |

Site-level configuration applied by the app (reversible from the UI, not code changes):

| Setting | Value | Why |
|---|---|---|
| Stock Settings → enable serial & batch | on | medicines are batch tracked |
| Stock Settings → pick batch based on | Expiry | ERPNext's own FEFO selection |
| Currency IQD | enabled, symbol "IQD" (translated "د.ع"), right-side | Iraqi Dinar |
| System Settings → app name / default app | PharmacyOS ERP / pharmacyos_erp | product identity |
| System Settings → standard email footer | disabled | removes "Sent via ERPNext" |
| System Settings → onboarding | disabled | ERPNext "Getting Started" panels |
| Website / Navbar Settings → logo, favicon, splash | PharmacyOS mark | product identity |
| Accounting Dimension "Branch" | created | branch-level accounting |
| Property Setter Item.search_fields | + Arabic name, generic name, normalized search key | search |
| Property Setter Sales Invoice.default_print_format | PharmacyOS Invoice | printing |
| POS Settings search fields | + Arabic name, generic name, normalized search key | POS search |
| Role profiles Cashier, Pharmacist, PharmacyOS Integration | only their PharmacyOS role: Accounts User, Stock User and **Sales User removed** (revoked from existing profile users at upgrade) | Accounts User allows Journal/Payment Entries and GL access, Stock User arbitrary Stock Entries, Sales User Sales Orders, Stock Reservation Entries (which reserve the shelf) and Delivery Notes (which ship stock without payment). |
| Custom DocPerms on PharmacyOS roles (`setup/install.py` → `CUSTOM_PERMISSIONS`, declarative: each row is set to exactly its rights on every migrate) | see below | Least privilege |
| System Settings → session expiry | 12:00 | A forgotten counter session ends after one shift of inactivity. |
| Database indexes `voucher_type_voucher_no_index` on `tabSerial and Batch Bundle` / `tabSerial and Batch Entry` (`pharmacy/locking.py`, install + migrate) | plain composite indexes | ERPNext marks a cancelled document's batch bundle rows by `voucher_type` + `voucher_no` and ships no index for it: the statement locked every item's rows and deadlocked with concurrent sales (round 4). No schema or data change; Frappe's schema sync never drops multi-column indexes. |
| PharmacyOS Settings → Protect Website Reservations | on | Counter sales cannot sell units reserved by submitted website orders. |
| System Settings → language / time zone (only when country = Iraq) | `ar` / Asia/Baghdad | Arabic-first Iraqi deployment |

`CUSTOM_PERMISSIONS` (permission level 0, on the PharmacyOS role only):

| Role | Rights |
|---|---|
| Cashier | Sales Invoice / POS Invoice read, create, submit, print (no cancel, no delete); own POS Opening/Closing Entry (if_owner, no cancel); Serial and Batch Bundle read, create, submit; Customer read, create, write; read of the masters the POS and invoice form use (Item, Item Group, Item Price, Price List, Brand, UOM, Warehouse, Bin, Company, Currency, Customer Group, Territory, Address, Contact, Branch, POS Profile, POS Settings, Mode of Payment, Sales Taxes Template, Terms, Accounts/Selling/Stock Settings, Fiscal Year); select only on Batch, Account, Cost Center, Item Tax Template, Tax Category, Loyalty Program |
| Pharmacist | Cashier's rights + Batch read/report + Stock Ledger Entry read/report |
| Pharmacy Owner | Item Price full (incl. delete); Branch read/create/write |
| Pharmacy Manager | Item Price full (incl. delete); Sales Invoice / POS Invoice cancel + amend; Branch read |
| Inventory Manager | Item Price read/create/write (no delete); Branch read |
| Pharmacy Accountant | Item Price read; Serial and Batch Bundle read/write/cancel (cancelling a batch sale cancels its bundle; Frappe checks write on cancel); Branch read |
| Purchasing Officer, Branch Manager | Item Price read; Branch read |
| PharmacyOS Integration | none — it works only through the PharmacyOS API (`api/v1`), whose order endpoint builds the Sales Order with system authority and records the integration account as its owner |

Frappe copies a DocType's standard rules into Custom DocPerm the first time one is added (so other roles keep
their access); from then on, upstream changes to that DocType's standard permissions no longer apply
automatically on these sites — review `CUSTOM_PERMISSIONS` when upgrading ERPNext. The rows are removed on
uninstall. Proof: `tests/test_permissions_matrix.py` (HTTP, real logins) and `dev/permission_check.py`
(the same matrix against a running server).


Repository-level additions outside `pharmacyos/` (new files, no ERPNext file changed):

| File | Why |
|---|---|
| `.github/workflows/pharmacyos-desktop.yml` | Builds the Windows installer. It runs manually, on `pharmacyos-desktop-v*` tags, and on `claude/**` branches when the desktop app or the workflow changes — never on ordinary ERPNext pushes. |

## Template for a future patch

```
### <short title>
- Reason:
- Files changed:
- Upstream compatibility risk:
- Upstream PR / issue:
- Removal path:
```
