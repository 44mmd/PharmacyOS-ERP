# PharmacyOS ERP — product transformation (milestone 1)

PharmacyOS ERP is the product; HALF is the developer; ERPNext and Frappe remain the engine
(credited in the About dialog, `/attribution`, `NOTICE.md` and the GPL licence). Everything below
lives in the `pharmacyos_erp` app; no ERPNext file is modified (`pharmacyos/FORK_PATCHES.md`).

## Identity & design system

* **Central branding**: `pharmacyos_erp/branding.py` (platform identity, upstream credits) and
  *PharmacyOS Settings* (the pharmacy's own business identity for invoices/receipts —
  "Al Noor Pharmacy … Powered by PharmacyOS").
* **Tokens** (`public/css/pharmacyos.bundle.css`): PharmacyOS palette (Deep `#063F36`, Core `#087B61`,
  Emerald `#0AA77E`, Mint `#63E6BE`, Mint surface `#EAFBF5`, Clinical `#F8FBFA`, Ink `#10231F`,
  secondary `#63736F`, borders `rgba(6,63,54,.11–.17)`, restrained red, warm amber, calm blue) mapped
  onto Frappe's Espresso tokens, so stock components inherit it. Green is used for navigation state,
  primary actions, focus and key numbers; operational surfaces stay neutral.
* **Type**: Inter (Latin, shipped by Frappe) + Tajawal (Arabic, OFL, self-hosted, `unicode-range`
  limited to Arabic) — mixed Arabic/English medicine names render each script in its own face. Arabic
  pages lead with Tajawal. Thamanya was not used: no licensed asset was available in this repository.
* **Mark**: interim PharmacyOS mark (`public/images/pharmacyos-mark.svg`) — replace with the official
  PharmacyOS asset at the same path. HALF appears as text only (no HALF logo was available).
* **Motion**: 120–180 ms opacity/transform only; skeleton shimmer; `prefers-reduced-motion` respected.
* **States**: every PharmacyOS page has loading (skeleton), empty, no-results, error and
  permission-denied states (`pharmacyos.ui.state`).

## Information architecture (Desk shell)

App-shipped Dock (icon rail) + Sidebars, generated from `setup/navigation.py`:

| Dock | Contents |
|---|---|
| Overview | Dashboard, Batches & Expiry, Inventory Health, Reorder Suggestions |
| Pharmacy | Medicines (Item where medicine), All Products, Active Ingredients, Dosage Forms, Regulatory Classes, Categories, Brands, Manufacturers, Item Prices, Price Lists |
| Inventory | Batches & Expiry, Inventory Health, Batches, Warehouses, Stock Transfers, Stock Entries, Stock Counts, Material Requests, stock reports |
| Sales | POS, Sales Invoices, Returns, Customers, Sales Orders, POS profiles/opening/closing, Pricing Rules, Coupons |
| Purchasing | Suppliers, Purchase Orders, Purchase Receipts, Supplier Invoices, Supplier Returns, Reorder Suggestions, analysis, supplier obligations |
| Finance | Payments, Journal Entries, Payment Methods, Chart of Accounts, receivables, payables, GL, P&L |
| Reports | sales, inventory and purchasing reports |
| Team | Employees, Branches, Users, Role Profiles, Role Permissions |
| Intelligence | Expiry Intelligence, Inventory Health, Reorder Suggestions, Sales/Purchase Analytics |
| System | PharmacyOS Settings, Stock Settings, Company, Sync Events, Webhooks, Document History, Activity Log |

PharmacyOS sidebars *own* their entities (`is_default_module`), so direct links to e.g. a Purchase
Receipt open inside PharmacyOS navigation. Links the user has no permission for are hidden by Frappe.
Login lands on the PharmacyOS dashboard (`System Settings → default app`).

## Roles (role profiles; ERPNext standard roles do the document permissions)

| Profile | Includes |
|---|---|
| Pharmacy Owner | all PharmacyOS roles + Accounts/Stock/Sales/Purchase Manager, Item Manager, Report Manager (no System Manager) |
| Pharmacy Manager | Stock Manager, Sales Manager, Purchase User, Accounts User, Item Manager |
| Pharmacist | Sales User, Accounts User (required by v17 to invoice), Stock User |
| Cashier | Sales User, Accounts User |
| Inventory Manager | Stock Manager, Stock User, Item Manager, Purchase User |
| Purchasing Officer | Purchase User/Manager, Stock User |
| Pharmacy Accountant | Accounts User/Manager |
| PharmacyOS Integration | API only: Sales User, Stock User |

Decision: ERPNext v17 requires **Sales Manager** for POS Opening/Closing Entries. Cashiers are *not*
given Sales Manager; a manager opens and closes the cashier's shift (POS Opening Entry has a user
field). Revisit if pharmacies need cashier-owned shifts (would need a narrow Custom DocPerm).

## Pharmacy workflows

* **Medicines**: Add Medicine dialog (batch & expiry tracked by default; commercial/Arabic/generic
  names, ingredient + strength, dosage form, pack, barcode, prices, reorder level, preferred
  supplier); medicine section on Item; expiry summary on the medicine form.
* **Batches & Expiry**: Expired / ≤30 / ≤60 / ≤90 buckets with counts and value at risk; chips with
  icon + text; real `batch_id` (never the hash); supplier and source receipt.
* **FEFO**: ERPNext auto-selection by expiry; PharmacyOS warns (default) or blocks when a
  later-expiring batch is chosen while an earlier one is available; expired sales rejected by ERPNext.
* **Receiving**: "Batch & Expiry" checklist on Purchase Receipt/Invoice; expired batches rejected;
  short shelf life warns/blocks; missing expiry rejected.
* **Inventory Health / Reorder Suggestions / Expiry Intelligence / Dashboard** — real data only.
* **Branches**: Branch → warehouse; Branch accounting dimension auto-filled from the warehouse;
  isolation through User Permissions; branch chip in the sidebar.
* **Printing**: A4 invoice and 80 mm receipts with batch/expiry per line and the pharmacy's identity.

## Remaining stock ERPNext/Frappe surfaces (honest list)

* Standard document forms and list views (Sales Invoice, Purchase Order, Item, …) are ERPNext's,
  themed by tokens and wrapped in PharmacyOS navigation — not redesigned.
* The ERPNext **POS page** is themed only; a PharmacyOS POS (Vue/frappe-ui page in this app) is the
  planned replacement (see ARCHITECTURE.md).
* The `/desk` apps screen still lists the ERPNext and Framework apps (advanced access).
* Website error pages (404/500), setup wizard, system emails' body templates and most reports keep
  Frappe/ERPNext layouts (they use the PharmacyOS logo/name where Frappe reads Website Settings).
* Some upstream help texts/messages mention ERPNext; the most visible one on the Item form is
  overridden; others remain.
* Arabic: PharmacyOS strings and key terms are translated; ~26% of upstream ERPNext strings are still
  untranslated upstream.

## Known pharmacy gaps (next phases)

Prescriptions, controlled-drug register and regulator-confirmed rules (extension point exists:
`pharmacyos_sale_validators`), expired-stock quarantine/disposal workflow, GS1 DataMatrix
(GTIN + batch + expiry) scanning, PharmacyOS POS, generic substitution / ingredient search in POS,
Iraqi chart-of-accounts template, near-expiry notifications (email/push), offline POS.
