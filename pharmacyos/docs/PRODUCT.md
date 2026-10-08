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

## Arabic-first

Iraqi sites default to Arabic (RTL) and Baghdad time. Terminology follows Iraqi pharmacy usage:
**الوجبة** for a batch (never الدفعة), **الشِفت / الشِفتات** for a cash/POS shift (masculine: الشِفت الحالي),
المخزن, الزبائن, الشكل الدوائي and لوحة التحكم.

Other parts:
* Arabic-tolerant search (أ/إ/آ/ا, ى/ي, ة/ه);
* mixed-direction handling;
* IQD as `25,000 د.ع`;
* Iraqi month names.

Details: `LOCALIZATION.md`.

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

## Roles (role profiles; ERPNext standard roles plus narrow PharmacyOS permissions)

| Profile | Includes |
|---|---|
| Pharmacy Owner | all PharmacyOS roles + Accounts/Stock/Sales/Purchase Manager, Item Manager, Report Manager; maintains prices (incl. delete) and branches. The owner created by first-run setup also gets **System Manager** (needed to add staff accounts and change settings); the setup wizard's other roles (HR, Projects, …) are removed. |
| Pharmacy Manager | Stock Manager, Sales Manager, Purchase User, Accounts User, Item Manager + maintains prices (incl. delete) + cancels sales |
| Pharmacist | **only** the Pharmacist role: counter permissions (Sales/POS Invoice create & submit, own POS shift, batch bundles, walk-in customers, read of the masters the POS uses) + read of Batch and Stock Ledger |
| Cashier | **only** the Cashier role: counter permissions |
| Inventory Manager | Stock Manager, Stock User, Item Manager, Purchase User + creates/edits prices (no delete) |
| Purchasing Officer | Purchase User/Manager; suppliers, purchase orders and receipts (creates the supplier's batch with its expiry on receipt), supplier returns; reads prices. **Not** Stock User: no stock write-off, no disposal, no adjustments |
| Pharmacy Accountant | Accounts User/Manager; cancels sales (incl. batch medicines); reads prices |
| PharmacyOS Integration | API only (`api/v1`): **no ERPNext document role**. Sees only its own User record. |

**Counter rules (Cashier and Pharmacist, enforced on every Sales/POS Invoice whatever sends it — the POS,
the desk form or the document API; `pharmacy/counter_sales.py`):** a sale or counter return is a POS invoice
in the user's **own open shift** on that counter (ERPNext allows one open shift per counter), **dated today**,
at the **counter's price list** (a customer's or group's cheaper list is refused) and at ERPNext's own prices;
discounts only where the counter allows them and at most **PharmacyOS Settings → Maximum Counter Discount (%)**
(default 10 %) per line and on the sale. Automatic offers (ERPNext Pricing Rules) always apply and do not
count toward the ceiling. Above it a manager or the owner completes the sale. Managers, the owner and the
accountant are not limited.

Cashier, Pharmacist and the integration user hold none of ERPNext's broad roles: Accounts User (Journal and
Payment Entries, GL), Stock User (arbitrary Stock Entries) and Sales User (Sales Orders, Stock Reservation
Entries and Delivery Notes — reserving the shelf or shipping stock without a payment). Upgrading removes them
from existing users of these profiles. The exact rights are the table `CUSTOM_PERMISSIONS` in
`setup/install.py` (see `FORK_PATCHES.md`).

Verified over HTTP with real logins, one account per profile (`tests/test_permissions_matrix.py`; the same
matrix against a running multi-worker server: `dev/permission_check.py`). ✓ allowed (200), ✗ refused (403),
– not asserted:

| | Owner | Manager | Cashier | Pharmacist | Accountant | Inventory | Integration | Guest |
|---|---|---|---|---|---|---|---|---|
| Counter sale (save + submit) | ✓ | ✓ | ✓ | ✓ | – | ✗ | ✗ | ✗ |
| Whole POS shift (open, list, sell, close) | ✓ | ✓ | ✓ | ✓ | – | – | ✗ | ✗ |
| Return against a sale | ✓ | ✓ | ✓ | ✓ | – | ✗ | ✗ | ✗ |
| Free-standing credit note (no original sale) | ✓ | ✓ | ✗ | ✗ | – | ✗ | ✗ | ✗ |
| Money-only credit note against a stock sale | ✓ | ✓ | ✗ | ✗ | – | ✗ | ✗ | ✗ |
| Cancel a batch-medicine sale (stock, batch, GL reversed) | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| Sales Order create / submit / cancel | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Delivery Note create / submit / cancel | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| Stock Reservation Entry | – | – | ✗ | ✗ | ✗ | – | ✗ | ✗ |
| Journal Entry / Payment Entry | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| Stock Entry | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| GL read | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| Item Price read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ |
| Item Price create / edit; Add Medicine with a price | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| Item Price delete | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Open a batch / read the stock ledger | ✓ | ✓ | ✗ | ✓ | – | ✓ | ✗ | ✗ |
| Branches | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ |
| Website order API (`create_order`) | ✗ | ✗ | ✗ | ✗ | – | – | ✓ | ✗ |

Cashiers open and close **their own** POS shift (narrow Custom DocPerm, `if_owner`); they cannot
cancel invoices or see other cashiers' shifts. Counter sessions expire after 12 idle hours.

### Returns and credit notes

A counter return (Cashier, Pharmacist) must reference a sale the user can open, bring the goods back
when that sale took them from stock, and stay within the quantities (per item and per batch) and the
rates of that sale — whoever sold it. Free-standing credit notes and money-only credit notes against
a stock sale need a pharmacy manager, the owner or the accountant (`returns.CREDIT_NOTE_ROLES`,
`CreditNoteAuthorityError`, HTTP 403). Medicine returns must reference the original sale for everyone.

### Cost data (purchase prices, valuation, stock value)

Counter staff search medicines, see selling prices, quantities, batches and expiry, and sell — but
never receive what the pharmacy paid (`pharmacy/cost_privacy.py`):

* **Field level** — Item valuation and last purchase rate, Bin valuation and stock value, every rate and
  value of the stock ledger, the sale's own cost of goods (`incoming_rate`) and the batch bundle's
  rates sit at permission level 5, granted to every role that reads those documents *except* Cashier,
  Pharmacist and the integration account. Frappe strips them from documents, form loads, lists, report
  views, `get_value`, doc-method answers; aggregates and filters on them are refused. Values a counter
  user sends for them are discarded; the server computes and stores costs as before.
* **Rows** — buying Item Prices (purchase prices) are invisible to them.
* **ERPNext helpers** that compute costs (`get_valuation_rate`, `get_incoming_rate`, quick stock balance,
  stock-entry/reconciliation/asset helpers, the stock-value chart) are refused; item details, stock
  balance, the item dashboard and the Item DocType's own `get_item_details` answer without the cost
  keys; buying-side item details are refused; query reports whose columns carry costs are refused.
* **The response boundary** (round 4) — field-level permissions are a *read* boundary: Frappe applies
  them when a document is fetched, not to the copy a save, submit or insert sends back (REST v1/v2
  included) nor to helpers that return whole records. Every JSON answer to a user without cost access
  therefore leaves the server scrubbed (`cost_privacy.scrub_response`, the `after_request` hook):
  cost keys are removed from documents, helper answers, list views, report rows and the version
  history. Stored values are untouched; the server keeps computing and storing costs as before.
* **PharmacyOS pages** (dashboard, batches & expiry, expiry intelligence, inventory health, reorder)
  send `null` for every value figure (shown as "—") and never rank by value; quantities, counts and
  expiry status are unchanged.

Red-team proof: `tests/test_cost_exposure.py` / `dev/permission_check.py --cost` seed a medicine with
distinctive costs and search every answer of ~65 routes per profile for those values (owner, manager,
inventory and accountant must see them — the control); `dev/cost_matrix_check.py` runs the wider
round-4 sentinel matrix (71 surfaces × every profile and Guest) against a live server.

Roles that see cost data by design: Pharmacy Owner, Pharmacy Manager, Inventory Manager, Purchasing
Officer, Pharmacy Accountant, Branch Manager (and Administrator). Never: Cashier, Pharmacist, the
PharmacyOS Integration account, Guest.

## Pharmacy workflows

* **Medicines**: Add Medicine dialog (batch & expiry tracked by default; commercial/Arabic/generic
  names, ingredient + strength, dosage form, pack, barcode, prices, reorder level, preferred
  supplier); medicine section on Item; expiry summary on the medicine form.
* **Batches & Expiry**: Expired / ≤30 / ≤60 / ≤90 buckets with counts and value at risk; chips with
  icon + text; real `batch_id` (never the hash); supplier and source receipt.
* **FEFO**: ERPNext auto-selection by expiry; PharmacyOS warns (default) or blocks when a
  later-expiring batch is chosen while an earlier one is available; expired sales rejected by ERPNext.
  Expiry policy: a batch is sellable **through** its expiry date and expired from the next day
  (ERPNext's `expiry_date >= today` rule, also used for website availability). A pharmacy that must
  stop selling earlier (e.g. at the start of the expiry month) needs a business decision and a
  setting; it is not configurable today.
* **Returns**: every return resolves to its **root sale** — a submitted document that is not itself a
  return; a return referencing another return is refused ("Return against a return", round 4). Against
  that root sale, cumulatively across all its submitted returns, a return can never exceed what was
  sold — per item and per batch, at no more than the rate charged per stock unit in any UOM — **nor
  what was paid**: per item, the refunded net amount stays within the returned portion's share of the
  item's net amount on the sale (row and document discounts included), and the refund total (taxes
  included) within that share scaled by the sale's own gross/net ratio ("Refund exceeds sale"). A
  partial return is worth its portion; a tax, rate or discount the sale never had is not refundable;
  rounding of distributed discounts is tolerated up to one currency unit per unit returned. A
  medicine return must reference the original sale. Applies to returns built in the POS, the desk or
  the REST API, and to simultaneous returns: returns against one sale are serialised on the sale's
  row and read what was already returned with a locking read (a ledger of quantities and amounts on
  the sale), so the loser of a race gets "Return exceeds sale", never a double refund
  (`tests/test_return_integrity_round4.py`, `tests/test_return_concurrency.py`,
  `dev/concurrency_check.py`). ERPNext's consolidated credit notes (POS closing) reference the
  consolidated invoice — an original — and are recorded in its ledger, never re-validated; a
  client-set `is_consolidated` is refused ("Not a consolidated invoice").
* **Last unit**: every sale, delivery and website order locks the item's stock rows in one order, so
  competing cashiers get a clear stock error instead of a database deadlock; counter sales cannot
  take units reserved by submitted website orders (*Protect Website Reservations*, on by default).
  Cancelling any document locks only that document's batch-bundle rows (round 4: composite
  `(voucher_type, voucher_no)` indexes on the bundle tables, `pharmacy/locking.py`), so a sale of an
  unrelated medicine never deadlocks with it (`tests/test_concurrency_round4.py`,
  `dev/concurrency_check.py --only unrelated_cancel`).
* **Receiving**: "Batch & Expiry" checklist on Purchase Receipt/Invoice; expired batches rejected;
  short shelf life warns/blocks; missing expiry rejected.
* **Inventory Health / Reorder Suggestions / Expiry Intelligence / Dashboard** — real data only.
* **Branches**: Branch → warehouse; Branch accounting dimension auto-filled from the warehouse;
  isolation through User Permissions; branch chip in the sidebar.
* **Printing**: A4 invoice and 80 mm receipts with batch/expiry per line and the pharmacy's identity.

## Remaining stock ERPNext/Frappe surfaces (honest list)

* Standard document forms and list views (Sales Invoice, Purchase Order, Item, …) are ERPNext's,
  themed by tokens and wrapped in PharmacyOS navigation — not redesigned.
* The **PharmacyOS POS** (`/pos`, `WEB_POS.md`) is the counter screen for the web browser and the Windows
  desktop app. ERPNext's own POS page (`/app/point-of-sale`) is still available and themed only.
* The `/desk` apps screen and app switcher show only PharmacyOS ERP to pharmacy staff. This is
  presentation only, in `boot.py:focus_apps_screen`; System Managers still see ERPNext and Framework.
* Website error pages (404/500), setup wizard, system emails' body templates and most reports keep
  Frappe/ERPNext layouts (they use the PharmacyOS logo/name where Frappe reads Website Settings).
* Some upstream help texts/messages mention ERPNext; the most visible one on the Item form is
  overridden; others remain.
* Arabic: see `LOCALIZATION.md` for the full list. In short:
  * PharmacyOS strings, Iraqi pharmacy terminology and misleading upstream labels on daily screens are
    covered;
  * about 26% of upstream ERPNext strings have no Arabic upstream and show in English.

## Known pharmacy gaps (next phases)

Built in Local ERP v1 (October 2026): voids with manager approval and audit, expired-stock disposal with
reasons and history (expired batches cannot be re-dated by staff), the Pharmacy Report page, supplier returns.

Future modules, outside Local ERP v1: prescriptions, controlled-drug register and regulator-confirmed rules
(extension point exists: `pharmacyos_sale_validators`), payroll/attendance (HRMS), GS1 DataMatrix
(GTIN + batch + expiry) scanning, generic substitution / ingredient search in POS, Iraqi chart-of-accounts
template, near-expiry notifications (email/push), a client-side offline queue for counter PCs that lose the
LAN server, and the CLOUD ERP ↔ LOCAL ERP sync engine.
