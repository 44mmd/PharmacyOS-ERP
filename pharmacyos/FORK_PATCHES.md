# ERPNext fork patches

Every change this fork makes to ERPNext code outside `pharmacyos/` must be listed here with its reason,
files, upstream-compatibility risk and removal path.

**Current state: none.** All PharmacyOS functionality lives in the `pharmacyos_erp` app
(`pharmacyos/apps/pharmacyos_erp`) and uses Frappe/ERPNext extension points only: hooks, doc_events,
custom fields, property setters, app-shipped DocTypes / Pages / Sidebars / Dock / Print Formats,
template overrides (`www/login.html` extends Frappe's login; `footer_powered.html`), translations and
whitelisted API methods.

Extension points used for consistency and least privilege (still no ERPNext file changed):

| Extension | What | Why |
|---|---|---|
| `override_doctype_class` | Sales Invoice, POS Invoice, Delivery Note, Sales Order → subclasses in `pharmacy/document_classes.py` that only add one step: lock the document's Bin rows before Frappe reloads the stored copy | One lock order for every sale, delivery and website order, so competing last-unit transactions wait instead of deadlocking. Another app that overrides the same classes would conflict; none is installed. |
| `doc_events` | `before_validate` Bin lock on sales documents; `validate`: return integrity (`pharmacy/returns.py`) and website-reservation protection (`pharmacy/stock_guard.py`) | Returns can never exceed what was sold; counter sales cannot take units reserved for website orders. |
| `permission_query_conditions` / `has_permission` on User | The PharmacyOS Integration account sees only its own User record | Cloud credentials cannot enumerate staff accounts. |

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
| Role profiles Cashier, Pharmacist, PharmacyOS Integration | Accounts User and Stock User **removed** (revoked from existing profile users at upgrade) | Those standard roles allow Journal Entries, Payment Entries, GL access and arbitrary Stock Entries. Counter roles get exactly the permissions below instead. |
| Custom DocPerm on POS Opening/Closing Entry (read, create, write, submit, print, **if_owner**) | Cashier, Pharmacist | So cashiers run their own shift. ERPNext reserves shifts for Sales Manager. No cancel or delete, and no access to other cashiers' shifts. |
| Custom DocPerm on Serial and Batch Bundle (read, create, write, submit) | Cashier, Pharmacist | Selling a batch medicine creates a bundle, which ERPNext reserves for stock roles. Without it a cashier cannot sell any batch-tracked medicine (found and tested in `test_pos_shift`). |
| Custom DocPerm on Sales Invoice and POS Invoice (read, create, write, submit, print, email — no cancel) | Cashier, Pharmacist | Counter sales without Accounts User. Cancelling an invoice stays with managers/accountants. |
| Custom DocPerm on POS Profile and Mode of Payment (read) | Cashier, Pharmacist | The POS screen loads its profile and payment methods. |
| Custom DocPerm on Batch (read) and Stock Ledger Entry (read, report) | Pharmacist | Stock and expiry questions at the counter, without any stock-creating permission. |
| System Settings → session expiry | 12:00 | A forgotten counter session ends after one shift of inactivity. |
| PharmacyOS Settings → Protect Website Reservations | on | Counter sales cannot sell units reserved by submitted website orders. |
| System Settings → language / time zone (only when country = Iraq) | `ar` / Asia/Baghdad | Arabic-first Iraqi deployment |
| Custom DocPerm on Item Price (read + select only) | Pharmacy Owner, Pharmacy Manager, Pharmacist, Cashier | ERPNext's POS barcode search reads Item Price with the user's permissions, but only Sales/Purchase Master Manager can read it. Frappe copies the standard rules first. The rows are removed on uninstall. |

All Custom DocPerm rows are created by `setup/install.py` (`ensure_cashier_shift_access`,
`ensure_price_read_access`) on install and migrate, and removed on uninstall.

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
