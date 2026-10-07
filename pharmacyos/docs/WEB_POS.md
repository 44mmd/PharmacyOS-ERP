# PharmacyOS POS — one POS, two clients (web browser + Windows desktop)

Status labels as in `DEPLOYMENT.md`: **Tested** = ran here and was checked; **Untested** = written, not run
on its target; **Hardware** = needs a physical device.

## 1. Architecture audit (before this change)

| Question | Finding |
|---|---|
| How the Windows desktop app works | `desktop/` is an Electron shell: one window onto the pharmacy's ERP server (this PC's WSL2 server or a LAN server). It loads the server's own web pages (`/desk`), stores no pharmacy data and no passwords, locks navigation to the server origin, pings the server every 30 s and shows an offline banner. |
| Electron-specific parts | `main.js` (window, menu, navigation guard, silent receipt printing of `/printview` pop-ups to a configured printer, single-PC server start through WSL), `preload.js` (connect-screen bridge, offline banner), `connect.html/js` (first-run setup), `config.js` (`%AppData%` config), `server.js` (ping, WSL start). |
| Ordinary web code | Everything the cashier sees: Frappe/ERPNext pages served by the ERP. |
| Authentication | Frappe's own login (`/login`, PharmacyOS-branded). Session = HTTP-only `sid` cookie (persisted in the Electron partition `persist:pharmacyos`); 12-hour session expiry (System Settings, set by PharmacyOS). |
| Sales Invoices | The ERPNext POS page (`/app/point-of-sale`, "Sales Invoice mode", POS Profile + POS Opening Entry) creates a POS Sales Invoice (`is_pos`, `update_stock`) and submits it as the cashier. Every PharmacyOS rule runs on that document: stock locks + last-unit protection (`stock_guard`, `document_classes`), FEFO guidance (`fefo`), return integrity (`returns`), website-reservation protection, consolidation guard. |
| Stock | ERPNext stock ledger on submit; batches chosen by ERPNext (`pick_serial_and_batch_based_on = Expiry` → FEFO), expired batches rejected by ERPNext's batch engine. |
| Returns | ERPNext's return mapper (`make_sales_return`) + PharmacyOS round-4 validation (root sale, cumulative quantity and money bound, ledger on the sale, credit-note authority, concurrency). |
| Printing | Print formats `PharmacyOS Receipt` (80 mm) / `PharmacyOS Invoice` (A4) rendered by `/printview`; the desktop prints receipt pop-ups silently when a receipt printer is configured. |
| Barcode | Keyboard-wedge scanners in the ERPNext POS search field (ERPNext `scan_barcode`). |
| Users and roles | Role profiles Cashier / Pharmacist / Manager / Owner … with declarative Custom DocPerms (`setup/install.py`), cost fields at permission level 5, `after_request` cost scrub. |
| ERPNext POS | **Themed only** (PRODUCT.md: "a PharmacyOS POS page in this app is the planned replacement"). |
| Shareable between web and desktop | Everything: the desktop already shows server web pages. |

**Conclusion.** The architecture already supports browser operation: the ERP server *is* the POS backend
and the desktop is a browser window onto it. The smallest safe change is therefore **one new POS screen
served by the ERP itself**, used unchanged by both clients, plus a thin platform adapter. No second POS,
no business logic in the client, no change to any validated module.

## 2. Architecture chosen

```
PharmacyOS ERP server (ERPNext + pharmacyos_erp)   ← the only business logic and the only database
  ├─ ERPNext POS endpoints (permission-scoped)        get_items, scan, opening entry, …
  ├─ pharmacyos_erp.pos.api  (orchestration only)     context, search, quote, checkout, returns
  └─ /pos  — the PharmacyOS POS screen (HTML/CSS/JS served by the ERP)
          ▲                                   ▲
   Web browser (Safari, Chrome, Edge)     Windows desktop (Electron)
   platform.js → browser print dialog     platform.js → preload bridge → native print
```

* **Shared POS UI/core:** `www/pos.py`, `templates/pos/shell.html`, `public/js/pos/pos.js`,
  `public/css/pos.css`. Plain JavaScript, no build step, no third-party code.
* **Platform adapter:** `public/js/pos/platform.js` is the only file that differs by client. Browser:
  receipt preview + the browser's print dialog. Desktop: `window.pharmacyosDesktop.printReceipt(url)`
  (preload) → main process prints the server's `/printview` from a hidden window in the signed-in
  session, silently to the configured receipt printer or with the system dialog.
* **Server facade** `pharmacyos_erp/pos/api.py` — what it does and does not do:
  * builds exactly the document the ERPNext POS builds (POS Sales Invoice with the counter's POS
    Profile; POS Invoice if POS Settings say so) and **inserts and submits it as the signed-in user**
    (no `ignore_permissions`), so every validated hook runs unchanged;
  * totals before payment come from ERPNext's `set_missing_values` + `calculate_taxes_and_totals` on an
    unsaved copy (`quote`, saves nothing);
  * returns: ERPNext's return mapper → chosen quantities → ERPNext's recalculated refund
    (`preview_return` shows it, `submit_return` submits it) — the same flow as the round-4 test
    `test_erpnext_return_button_partial_return_still_works`; validation is `pharmacy/returns.py`;
  * batches are never chosen by the client except a scanned batch barcode; otherwise ERPNext picks
    (FEFO). Expired stock is shown as not sellable, and ERPNext refuses it regardless;
  * **stricter than the ERPNext POS screen, never looser:** the counter's `allow_discount_change` /
    `allow_rate_change` switches and its user list are enforced on the server (the ERPNext POS checks
    them only in the browser);
  * **idempotent checkout:** each cart attempt carries a client request ID stored in the new DB-unique,
    hidden, no-copy field `pharmacyos_pos_request_id` (Sales Invoice, POS Invoice). A repeat (double
    click, network retry, page reload, two tabs) returns the first invoice. On failure the facade rolls
    back to a savepoint and does a locking read, so a concurrent duplicate committed after the
    transaction's snapshot is still found.
* **ERPNext's own POS page stays available** (`/app/point-of-sale`); nothing was removed.

Changes outside the new files: one custom field (above), the cashier line on the shared receipt
template, Arabic strings in `locale/ar.po`, the two new modules in the Arabic-coverage test. No
validated module (sales, returns, stock, accounting, batches, FEFO, pricing, permissions, Cloud sync,
concurrency, cost privacy) was modified.

## 3. The POS screen

Arabic first (RTL; English when the user's language is English), IQD, PharmacyOS tokens and Tajawal.

* **Shift:** opens the cashier's own shift on their counter with the cash float (ERPNext
  `create_opening_voucher`). *Close shift* shows the expected amount per payment method — opening float +
  takings − change (`shift_summary`; ERPNext's own closing entry leaves the float out, PharmacyOS adds it back);
  the cashier enters what the drawer holds and `close_shift` submits ERPNext's POS Closing Entry as the
  cashier, recording the difference (never correcting it). The shift row is locked, so a double click never
  closes it twice. The full desk form stays one click away.
* **Search / scan:** one always-focused field. A scanner (keyboard wedge, Enter or Tab suffix) or typed code →
  ERPNext's scan (barcode, batch, serial) → added to the cart; scans are queued, so fast consecutive
  scans all land; the field is cleared synchronously and keeps focus. Typing searches by name, Arabic
  name, generic name (POS search fields). Typing anywhere goes to the field.
* **Cart:** Arabic + English name, scanned batch and expiry, − / + / typed quantity (Arabic-Indic digits
  accepted), line discount % only on counters that allow it, server prices and amounts, warning above
  the sellable stock.
* **Customer:** walk-in by default; search; quick new customer (name + mobile).
* **Hold (F8) / Held sales:** parks the current cart (customer and discount included) to serve another
  customer; *Held sales* lists them with *Resume* / *Discard*. Resuming while a cart is open parks that one in
  its place. Held carts are kept per user on that computer (localStorage, at most 20), never reserve stock, and
  are re-quoted by the server when resumed. A cart whose checkout is in flight cannot be held.
* **Payment (F9 / ⌘↵ / Ctrl+Enter):** the counter's payment methods, quick cash notes, change aid;
  the receipt shows ERPNext's change.
* **Receipt:** the ERP's `PharmacyOS Receipt` print view in a preview; Print (browser dialog / native).
* **Returns:** by invoice number or from *Recent sales*; returnable quantities from ERPNext's mapper
  and the PharmacyOS ledger; ERPNext's refund previewed, then submitted; return receipt.
* **Sales history:** *Recent sales* searches today's and earlier sales by invoice number or customer; voided
  sales carry a *Voided* badge.
* **Void (cancel a sale):** *Void* on a sale in the history, with a reason (required). A user who may cancel
  sales (Pharmacy Manager, Owner, Accountant) voids directly; a cashier can void **only their own sale** and
  only with a manager's email and password typed on the same screen (the cashier stays signed in). The server
  refuses: returned sales, sales not from today, sales in a closed shift, the requester approving themselves,
  and more than 5 wrong approvals in 10 minutes. The sale is cancelled through ERPNext as the approver (stock,
  batches and accounts reversed — never deleted), a **PharmacyOS Void Log** row records invoice, amount,
  requester, approver, counter and reason, and a comment is added to the invoice. A voided sale leaves the
  shift's expected cash. Owners and managers see *Voided Sales* in the Reports menu.
* **Connection:** offline banner, automatic recovery; a checkout interrupted by the network is
  re-asked with the same request ID and the cart is locked meanwhile — "do not re-enter the sale".
* **Session:** cart kept per user across reloads; expired session → sign-in prompt, cart kept; sign-out
  clears it.

## 4. Security

| Concern | How it is handled | Evidence |
|---|---|---|
| Authentication | Frappe login; `/pos` redirects guests to `/login?redirect-to=/pos`; website (non-staff) users refused | `web_pos_check`, `test_web_pos` |
| Cookies | `sid` HttpOnly, SameSite=Lax, host-only; `Secure` over HTTPS | checked over HTTP and HTTPS |
| CSRF | every POST carries Frappe's CSRF token (from the page); a POST without it → 400 | `web_pos_check` |
| CORS | none configured; no `Access-Control-*` headers for foreign origins | checked |
| HTTPS | POS host template: TLS 1.2/1.3, HSTS, HTTP→HTTPS redirect; plain HTTP only for a closed test network | `nginx -t` + HTTPS run |
| CSP | `/pos`: `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'self'` … | header checked |
| Session expiry | 12 h (System Settings); expired → 403 → sign-in prompt | — |
| Permissions | all calls as the user; counter switches + user list enforced server-side; cancel/ledgers refused | `web_pos_check`, `test_web_pos` |
| Secrets | none in the frontend (grep); test passwords generated at install, never in code | checked |
| Integration credentials | the POS never uses the Cloud integration account or API keys | — |
| Cost data | facade returns selling figures only; global `cost_privacy.scrub_response` applies; receipt has no costs | 0 cost keys / values in every cashier response |
| Voids | server decides who may void; approver password checked server-side, rate-limited, audited | `test_v1_operations` |
| Exposed endpoints | 8 new whitelisted methods, all requiring login; no `allow_guest`; Frappe security unchanged | guest → 403 on each |

## 5. Tests

| Suite | Result |
|---|---|
| PharmacyOS ERP suite, `develop` bench (Frappe/ERPNext develop + this fork) | 234 / 234 passed |
| PharmacyOS ERP suite, **commercial target** (Frappe 16.36.1 / ERPNext 16.37.0) | 247 / 247 passed (Oct 2026, v1) |
| `tests/test_web_pos.py` (part of the suite) | 10 / 10 |
| `dev/web_pos_check.py` — HTTP like a browser, ledgers verified, 8 rounds of 6 simultaneous duplicate checkouts and of last-unit races, live PharmacyOS Cloud | develop 66 / 66 (without Cloud), version-16 69 / 69 (with Cloud) |
| Browser E2E (Playwright, Chromium engine = Chrome/Edge): login, shift, Arabic search, fast scans, qty (Arabic digits), customer, cash sale, receipt, return, expired batch, reload, logout/login | 20 / 20 at 1440, 1280, 820, 390 px (develop) and 1440 px (version-16) |
| Desktop: `npm test`; packaged build (Linux dir); Electron E2E (same `/pos`, desktop adapter, sale, native print bridge) | 6 / 6; built; 6 / 6 |
| Cloud backend / storefront / admin (PharmacyOS repo, unchanged) | 102 / 102; 15 / 15 + build; lint + build |

Run them:

```
bench --site test.localhost run-tests --app pharmacyos_erp --module pharmacyos_erp.tests.test_web_pos
python3 pharmacyos/dev/web_pos_check.py --url http://<pos-host> --actors actors.json \
    [--cloud-dir PharmacyOS/backend --cloud-python <venv>/bin/python --bench-site <site>]
```

**Not testable here (no device / no engine):** Safari (WebKit is not installed in this environment —
the code avoids WebKit gaps: no `:has`, `randomUUID` fallback, 16 px inputs against iOS zoom, `dvh`
fallback), Microsoft Edge itself (same Chromium engine as tested), a physical scanner, a thermal
printer, a Windows PC. See section 8.

## 6. Deployment

* **Inside a pharmacy (LAN):** nothing extra. Every PharmacyOS server serves `http://<server>/pos`.
  The desktop app opens it (Settings → *Open at start → Point of Sale*, or menu *Point of Sale*).
* **Dedicated hostname** (e.g. `pos.halfpharmacy.com`): `deploy/pos/setup-pos-host.sh` adds an nginx
  server block of its own (HTTPS via Let's Encrypt, `/` → `/pos`, CSP, HSTS). It does not touch the
  storefront (`halfpharmacy.com`), the admin panel (`admin.halfpharmacy.com`), DNS, or the bench's own
  nginx file.
* **Test server in one command** (fresh Ubuntu 24.04 VPS, fictitious demo pharmacy):
  `deploy/pos/install-pos-test-server.sh` → ERP (version-16) + demo data + two counters + test accounts
  (generated passwords, printed at the end) + `https://POS_HOST/pos`. **Untested end-to-end** (it chains
  the existing, also untested, `install-server.sh`; the parts it adds were run here).

Demo barcodes (EAN-13, in-store range): Paracetamol `2000000000015`, Amoxicillin `2000000000022`,
Ibuprofen `2000000000039`, Omeprazole `2000000000046`, Metformin `2000000000053`, Cetirizine syrup
`2000000000060`, Salbutamol `2000000000077`, Azithromycin `2000000000084`. Expired demo batch to try:
type `PA-2401` (Paracetamol, expired) — the POS refuses it.

## 7. Manual test (Mac or Windows browser)

1. Open the POS URL (e.g. `https://pos.halfpharmacy.com`). You land on the PharmacyOS sign-in.
2. Sign in as the **cashier** test account. The first time, press **فتح الوردية** (Open shift).
3. Sale: type or scan `2000000000015` + Enter (twice = quantity 2), press **دفع** (Pay; F9 on Windows,
   ⌘↵ on a Mac), tap a cash note, **إتمام البيع** (Complete sale).
4. Receipt: the preview opens — **طباعة الوصل** (Print receipt) opens the browser's print dialog
   ("Save as PDF" works without a printer).
5. Return: **آخر المبيعات** (Recent sales) → **مرتجع** (Return) on that sale → quantity 1 → the ERP's
   refund is shown → **تأكيد الإرجاع** (Confirm return) → return receipt.
6. Optional: sign in as the **manager** (Counter 2) to try a discount; type `PA-2401` to see an expired
   batch refused.

## 8. Remaining hardware-only validation

* Safari on macOS (all screens; printing from the receipt preview).
* A USB/Bluetooth barcode scanner (keyboard mode, Enter suffix) on Mac and Windows.
* An 80 mm thermal printer: browser dialog (margins "none", scale 100 %) and the desktop's silent mode.
* The Windows installer on Windows 10/11 (unsigned; SmartScreen), the single-PC WSL2 server, auto-start.
* A cash drawer opened by the receipt printer's driver ("open drawer after printing"); PharmacyOS sends no
  drawer command of its own.
