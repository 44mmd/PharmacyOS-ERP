# PharmacyOS ERP — Phase 0 Architecture & Upgrade-Safety Plan

Status: Phase 0 (foundation only). No PharmacyOS features have been built and no production system is connected.
Development setup is in [`DEVELOPMENT.md`](DEVELOPMENT.md).

## 1. What this repository is

* A fork of **ERPNext `develop`** (version `17.0.0-dev`, forked at upstream commit `d607837`). It is a
  single Frappe app (`erpnext/`), with a React/Vite banking SPA in `banking/`.
* It depends on **Frappe Framework `>=17.0.0-dev,<18`** (MIT). ERPNext is **GPL-3.0** (`license.txt`).
* Stack: Python 3.14, Node 24 + yarn, MariaDB (utf8mb4), two Redis instances, the bench CLI, and the
  `payments` app for upstream tests. See DEVELOPMENT.md.

### How ERPNext is built (what matters for extending it)

| Concern | Mechanism | Where |
|---|---|---|
| Data model | DocTypes: JSON schema plus a Python controller plus JS form script. About 790 DocTypes across 21 modules | `erpnext/<module>/doctype/<name>/` |
| Extension points | `hooks.py`: `doc_events`, `scheduler_events`, `override_doctype_class` / `extend_doctype_class`, `override_whitelisted_methods`, `doctype_js`, `app_include_js/css`, `permission_query_conditions`, `has_permission`, `boot_session`, `fixtures` | `erpnext/hooks.py` |
| Customisation without code | Custom Field, Property Setter, Client/Server Script, Workflow, Notification, Print Format, Workspace, Translation, Role/User Permissions | Frappe core; exportable as app fixtures |
| Schema migrations | DocType JSON is synced on `bench migrate`; data patches go in `patches.txt` | `erpnext/patches.txt`, `erpnext/patches/` |
| REST | `/api/resource/<DocType>` (v1), `/api/v2/document/<DocType>`, `/api/method/<dotted.path>` for `@frappe.whitelist()` functions, `/api/v2/discovery/*` | `frappe/api/` |
| Auth | Session cookie, `Authorization: token key:secret` (per-user API keys), OAuth2 provider, LDAP/social login | `frappe/integrations/` |
| Outbound events | Webhook DocType: per-DocType events (`after_insert`, `on_submit`, `on_cancel`, ...) with conditions, HMAC-SHA256 signature (`X-Frappe-Webhook-Signature`), background queue and request log | `frappe/integrations/doctype/webhook` |
| Background jobs | RQ on Redis: `frappe.enqueue`, `scheduler_events` (hourly/daily/cron) | |
| Realtime | Socket.IO (`bench socketio`, :9000), `frappe.publish_realtime` | |
| Permissions | Role-based DocPerms (read/write/submit/cancel/amend per role), User Permissions (for example restricting a user to Company/Warehouse/Branch), permlevels on fields, `has_permission` hooks | |
| Audit | `track_changes` gives a Version log. Submitted documents are immutable (cancel and amend). Stock Ledger and GL entries are append-only | |
| i18n | gettext `.po` per app (`erpnext/locale/ar.po`), site-level Translation DocType, RTL switch per user language | |
| Print | Jinja and builder Print Formats per DocType, overridable per site/app (Print Designer is a separate optional app) | |
| Frontend | Desk is an SPA built by Frappe (esbuild bundles `*.bundle.js/css`), with Workspaces as configurable home pages. New Frappe products use Vue 3 + `frappe-ui`, and ERPNext's banking SPA uses React | |
| POS | Desk page `selling/page/point_of_sale` (about 5k lines of JS classes), configured through POS Profile, POS Opening/Closing Entry and POS Settings (Sales Invoice or POS Invoice mode) | |
| Inventory | Item → Bin (qty per warehouse) → Stock Ledger Entry (immutable). Serial and Batch Bundle carries batch movements. Valuation is FIFO / Moving Average per item, with perpetual inventory into the GL | `erpnext/stock` |
| Accounting | Company, Chart of Accounts, GL Entry, Sales/Purchase Invoice, Payment Entry, Journal Entry, Accounting Dimensions, multi-currency with exchange revaluation | `erpnext/accounts` |
| Sales / Buying | Quotation → Sales Order → Delivery Note → Sales Invoice; Material Request → Purchase Order → Purchase Receipt → Purchase Invoice; returns as credit and debit notes | `erpnext/selling`, `erpnext/buying` |

## 2. Recommended architecture

```
                 PharmacyOS Storefront / Admin (existing)
                                │
                 PharmacyOS Backend / Integration Layer   ← owns customer-facing rules, retries, idempotency
                    │  REST (token / OAuth2) ▲  signed webhooks
                    ▼                        │
┌───────────────────────── Frappe bench ───────────────────────────┐
│ frappe (upstream, untouched)                                     │
│ erpnext (this fork; tracks upstream, near-zero diff)             │
│ hrms     (optional, upstream; payroll/attendance/expense claims) │
│ pharmacyos_erp (NEW, separate repo) ← all PharmacyOS code        │
│   ├─ custom DocTypes, Custom Fields/Property Setters (fixtures)  │
│   ├─ doc_events validations, scheduler jobs (expiry, sync)       │
│   ├─ api/v1/*.py  whitelisted, versioned integration API         │
│   ├─ roles, workspaces, print formats, ar translations           │
│   └─ branding/theme assets, custom pages (Vue/frappe-ui)         │
└──────────────────────────────────────────────────────────────────┘
```

**Rule: ERPNext core stays as upstream.** The fork exists so we can pin and audit upstream, carry a
rare emergency hotfix, and keep developer docs (this `pharmacyos/` folder). It is not a place for
features.

### What stays in ERPNext core (no changes)
All accounting, stock ledger, valuation, batch/serial engine, buying/selling flows, POS engine,
pricing rules, permissions engine, migrations and patches, upstream translations, license and metadata.

### What lives in `pharmacyos_erp`
* **Data**: pharmacy Item attributes as Custom Fields (active ingredient, strength, dosage form,
  route, Rx-only, controlled-drug class, MoH registration number, manufacturer, storage conditions).
  It also holds new DocTypes (Active Ingredient, Dosage Form, Prescription, Controlled Drug Register,
  Expiry Disposal, Storefront Sync Log, and so on).
* **Rules**: `doc_events` validations, for example minimum remaining shelf-life on Purchase Receipt,
  Rx check on sale, and return restrictions. It also covers scheduled jobs for near-expiry and
  expired stock, and for availability sync.
* **Integration API**: `pharmacyos_erp.api.v1.*` whitelisted methods with a stable contract (see §4).
* **Config as code**: roles (Pharmacist, Cashier, Branch Manager, Inventory Clerk, Integration),
  workspaces, Notifications, Print Formats (Arabic/IQD invoice), Stock and Accounts Settings defaults,
  and Iraqi CoA template. These ship as `fixtures` or `after_install` / patches.
* **Translations**: `pharmacyos_erp/locale/ar.po` for new strings and for overriding upstream
  pharmacy terms (installed after erpnext, so its translations win).
* **UI/branding**: app assets, theme CSS, custom pages and portals (see §7).

### What might genuinely require fork changes
Expected to be none for the foreseeable roadmap. Candidates only if a hook point is missing:
1. A hard-coded behaviour in POS JS that cannot be extended. The preferred fix is our own POS page in
   the app, not editing core.
2. A bug fix needed before upstream merges it. Carry it as a small, isolated commit, upstream it as a
   PR, and drop it when upstream lands.
Every fork commit outside `pharmacyos/` should be listed in a `pharmacyos/FORK_PATCHES.md` (to be
created when the first one happens).

### Branch choice (decision needed)
This fork tracks **`develop` (pre-release v17)**. That branch is unstable and can break schema and
APIs between commits. For a product, base production on **`version-16`** (stable; latest v16.37.0, with
the same Python 3.14 / Node 24 stack) and write `pharmacyos_erp` against v16. Move to v17 after it
is released. Keep `develop` only for forward-compatibility CI.

## 3. Pharmacy feature gap matrix

A = ERPNext handles it well; B = handled, PharmacyOS needs customisation/configuration; C = needs a custom module/DocType.

| Area | Class | ERPNext today | PharmacyOS work |
|---|---|---|---|
| Medicines/products | **B** | Item, UOMs and conversions (box/strip/tablet), variants, Item Group | Pharma fields via Custom Fields. Masters such as Active Ingredient and Dosage Form are **C** |
| Brands | **A** | Brand | |
| Categories | **A** | Item Group tree | Optional therapeutic/ATC classification (C) |
| Batches/lots, batch numbers | **A** | Batch plus Serial and Batch Bundle; auto batch numbering (`batch_number_series`) | Enable `enable_serial_and_batch_no_for_item`. Batch `name` is a hash, so integrate on `batch_id` |
| Expiry dates | **A** | `Batch.expiry_date`, `Item.has_expiry_date`, `shelf_life_in_days` | |
| Near-expiry alerts | **B** | "Batch Item Expiry Status" and "Available Batch Report" reports. Frappe Notification supports "Days Before" on a date field | Thresholds per item/category, dashboard, daily job, alerts per branch |
| Expired inventory | **B** | Sales with an expired batch are **blocked** (`BatchExpiredError`, verified). Auto batch picking skips expired batches | Quarantine warehouse, auto-move job, disposal/return-to-supplier record (**C**: Expiry Disposal) |
| Stock quantities | **A** | Bin / Stock Ledger, projected qty, reserved stock | |
| Multiple warehouses | **A** | Warehouse tree, transit warehouses | |
| Multiple branches | **B** | Branch master, Accounting Dimensions, Cost Centers, User Permissions, POS Profiles | Decide the model: one Company with Branch as a dimension plus a warehouse per branch (recommended), or multi-company |
| Suppliers | **A** | Supplier, supplier groups, scorecards | Licence/registration fields (B) |
| Purchase orders | **A** | Material Request → PO, auto-reorder | |
| Purchase receipts | **A** | Batch and expiry captured on receipt | Enforce expiry entry and minimum remaining shelf-life (B, a hook) |
| Sales | **A** | Sales Order / Delivery Note / Sales Invoice | |
| Invoices | **A** | Sales Invoice, taxes, credit notes | Arabic/IQD print format (B) |
| Returns | **A** | Returns against invoice, batch-aware | Pharmacy return policy validation (B) |
| Discounts | **A** | Pricing Rules, Promotional Schemes, Coupon Codes, Loyalty | |
| Customer accounts | **A** | Customer, credit limit, receivables, portal | Patient/prescription data (**C**) |
| Payment methods | **A** | Mode of Payment, split payments in POS | Iraqi wallets/cards are payment-gateway integrations (**C**, if needed) |
| Expenses | **B** | Purchase Invoice / Journal Entry / Payment Entry | Expense Claim is in the separate **HRMS** app. Petty-cash flow config |
| Accounting | **B** | Full double-entry, IQD available | No Iraqi chart of accounts ships (only generic/standard). Build an Iraqi CoA template; set IQD precision |
| Employees | **B** | Employee, Department, Designation, Branch | Payroll/attendance/leave need the **HRMS** app (GPL, separate) |
| Roles/permissions | **B** | Role DocPerms, User Permissions by Company/Warehouse/Branch | Pharmacy role set as fixtures |
| Audit history | **A/B** | Version log on track_changes DocTypes, immutable submitted docs and ledgers | Enable track_changes on more DocTypes (Property Setter). Controlled Drug Register (**C**) |
| POS | **B** | Full POS with batch selection, barcode, multiple payment modes, opening/closing | Pharmacy UX (generic/ingredient search, substitution, Rx capture) through our own POS page or extension, not core edits |
| Barcode scanning | **A** | Item Barcode (several per item, EAN/UPC), scanner input in POS and forms (onscan.js) | GS1 DataMatrix (GTIN plus batch plus expiry) parsing (B) |
| Stock transfers | **A** | Stock Entry (Material Transfer), transit, Material Request | |
| Reorder levels | **A** | Item Reorder per warehouse plus the daily `reorder_item` job (with Stock Settings auto-indent) | |
| Inventory valuation | **A** | FIFO / Moving Average, batch-wise valuation, perpetual inventory | |

Beyond the requested list, these are **C** and pharmacy-specific: prescriptions, controlled/narcotic
drug register, generic substitution, drug-interaction checks, and MoH regulatory reporting.

## 4. PharmacyOS ↔ ERP integration

Use a **combination**, with the PharmacyOS backend as the only caller and receiver. The storefront never talks to
ERP directly.

| Flow | Mechanism |
|---|---|
| Inventory → storefront | Push: Webhook or `doc_events` on Stock Ledger Entry / Bin change, debounced into an RQ job that posts signed availability deltas (item, warehouse/branch, sellable qty excluding expired and near-expiry). Pull fallback: `GET /api/method/pharmacyos_erp.api.v1.availability` for full or paged resync |
| Online order → ERP | `POST /api/method/pharmacyos_erp.api.v1.orders.create` with an **idempotency key** (the PharmacyOS order ID stored on the Sales Order). The method validates, maps the customer and items, creates the Sales Order and optionally reserves stock, then returns the ERP IDs. Fulfilment (Delivery Note / Sales Invoice) happens in ERP, and status changes are pushed back by webhook |
| Catalogue ERP ↔ storefront | ERP is the source of truth for items, prices and stock. The storefront owns marketing content (images, descriptions, SEO). Sync goes through `api.v1.catalog` (changed-since cursor on `modified`) and webhooks on Item / Item Price |

Principles:
* Use **custom whitelisted, versioned methods** (`api/v1`) as the contract, rather than raw `/api/resource`.
  This keeps the ERPNext schema an internal detail that upstream upgrades can change.
* Give the integration its own **dedicated user** ("PharmacyOS Integration" role) with minimal permissions and an
  API key/secret, or OAuth2 client credentials. Never use Administrator.
* Verify webhook HMAC signatures. Retries are safe because all writes are idempotent. Log every sync in
  a Sync Log DocType.
* Map identifiers explicitly: `item_code`, `batch_id` (not `Batch.name`), warehouse to branch.

## 5. Arabic / IQD readiness

| Need | Status |
|---|---|
| Arabic UI and RTL | **Works.** A user with language `ar` gets `<html dir=rtl lang="ar">` (verified) |
| Arabic translations | **Partial.** `erpnext/locale/ar.po` has 2,837 of 10,832 strings untranslated (about 74% done). Frappe core is separate. Some pharmacy terms are wrong for Iraqi usage, for example "Batch" renders as "الدفعات" (reads as payments). Use Iraqi terms such as "رقم التشغيلة" / "الوجبة". Override them in `pharmacyos_erp/locale/ar.po` |
| IQD | **Present but needs config.** Frappe ships IQD (symbol ع.د, fraction fils 1/1000, `#,###.###`) but **disabled**. Set it enabled, choose the displayed precision (whole dinars), and set rounding |
| Country/timezone | Iraq / Asia/Baghdad supported in the setup wizard (verified) |
| Chart of accounts | No Iraqi template. Use "Standard" and build an Iraqi CoA template in the app |
| Print | Need Arabic/bilingual invoice and receipt formats (RTL, Arabic numerals choice, thermal 80 mm) |
| Regional/tax | No `regional/iraq`. Iraq retail sales normally need no VAT engine; add configuration only if required |

## 6. UI strategy (upgrade-safe, layered)

1. **Branding through hooks and settings**: app logo, `app_include_css` theme (CSS variables over Frappe's
   design tokens), Website/Navbar Settings, login page, email header, favicon. This does not touch core.
   ERPNext's trademark policy forbids using the ERPNext name or logo as our product branding anyway.
2. **Workspaces and roles**: pharmacy-specific workspaces (Dispensary, Inventory, Expiry, Purchasing,
   Reports) shipped as fixtures, with the default ERPNext workspaces hidden per role.
3. **Desk extensions**: `doctype_js` form tweaks, list/report views, dashboards, number cards.
4. **Custom pages in the app (Vue 3 + frappe-ui)** for premium high-traffic flows: pharmacy POS,
   expiry dashboard, receiving. These run on the same backend APIs and do not fork Desk.
5. **Separate PharmacyOS frontend** (the existing Admin) only for flows that belong to that product.
   It goes through the integration API.

Avoid overriding core Desk JS/CSS selectors or monkey-patching core JS classes. Those break on upgrades.

## 7. Licensing and upstream awareness (from the repository's own files)

* ERPNext is **GPL-3.0** (`license.txt`, `package.json`). Frappe Framework is **MIT**. `attributions.md` lists
  third-party works. Keep all of these files and copyright headers intact.
* GPL-3.0 obligations apply when the software is **conveyed** (distributed) to others. Then the
  corresponding source for the covered work must be offered under GPL-3.0. Whether `pharmacyos_erp`
  counts as a derivative work, and whether hosted (SaaS) use counts as conveying, are legal questions.
  Get legal advice before deciding to license `pharmacyos_erp` under anything other than GPL-3.0.
  Building `pharmacyos_erp` as GPL-3.0 is the low-risk default. The separate PharmacyOS storefront/backend
  only talks over HTTP APIs, which is an architectural boundary worth preserving.
* **Trademark** (`TRADEMARK_POLICY.md`): "ERPNext" and its logo cannot be used in our product, company or
  domain name or branding. We may describe the product as "built on ERPNext". The product name "PharmacyOS ERP" is fine.
  Replacing ERPNext branding in the UI is consistent with the policy. Do not remove copyright and license notices.

## 8. Technical risks

1. **Tracking `develop`**: unstable schema and APIs. Mitigate by moving to `version-16` (see above).
2. **Fork drift**: any core edit multiplies merge cost. Mitigate by keeping all features in the app, logging
   fork patches, and merging upstream regularly (monthly) with CI.
3. **Batch identity**: `Batch.name` is a hash in v17. Integrations keyed on the wrong field will create
   duplicates.
4. **IQD precision and rounding**: a 3-decimal default against whole-dinar reality causes rounding
   differences in POS and GL. Configure before the first transaction. Precision is hard to change later.
5. **Stock sync correctness**: overselling online. Mitigate by reserving stock on the Sales Order, keeping
   safety stock per branch, and excluding near-expiry batches from sellable quantity.
6. **POS customisation**: core POS is large imperative JS, and patching it is fragile. Build our own page.
7. **Arabic quality**: about 26% of ERPNext strings are untranslated, plus terminology issues. Budget
   translation work.
8. **Performance**: per-change webhooks on high-volume stock tables. Batch and debounce them in RQ jobs.
9. **HRMS dependency** if payroll or expense claims are needed: another upstream app to track.

## 9. Recommended Phase 1

1. Decide the base branch (`version-16` recommended) and rebase this fork's `pharmacyos/` docs onto it.
2. Create the repository **`44mmd/pharmacyos_erp`** (GPL-3.0). Scaffold with `bench new-app` and set up
   CI that installs frappe + erpnext + the app and runs the app's tests.
3. Add configuration as code only: roles, Stock Settings (batch tracking on, FEFO picking
   `pick_serial_and_batch_based_on = Expiry`), IQD enabled with precision, branch model
   (warehouses plus Branch accounting dimension), and pharmacy Custom Fields on Item.
4. Add seed and test data fixtures (fictitious medicines, batches, suppliers) and tests for the
   receive → sell (FEFO) → expire flow.
5. Write the integration API contract (OpenAPI document for `api.v1`) and stub it with tests. Connect
   nothing to production.

## 10. Blockers / decisions before development

* **Branch decision**: `develop` vs `version-16` (recommendation: version-16).
* **Repository for `pharmacyos_erp`**: needs creating (not done in Phase 0, by design).
* **Branch/company model** and whether **HRMS** is in scope.
* **Licensing decision** for `pharmacyos_erp` (legal review if not GPL-3.0).
* Iraqi pharmacy domain inputs: MoH registration fields, controlled-drug rules, preferred Arabic
  terminology list, and invoice and receipt requirements.
