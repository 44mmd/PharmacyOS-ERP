# ERPNext fork patches

Every change this fork makes to ERPNext code outside `pharmacyos/` must be listed here with its reason,
files, upstream-compatibility risk and removal path.

**Current state: none.** All PharmacyOS functionality lives in the `pharmacyos_erp` app
(`pharmacyos/apps/pharmacyos_erp`) and uses Frappe/ERPNext extension points only: hooks, doc_events,
custom fields, property setters, app-shipped DocTypes / Pages / Sidebars / Dock / Print Formats,
template overrides (`www/login.html` extends Frappe's login; `footer_powered.html`), translations and
whitelisted API methods.

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
| System Settings → language / time zone (only when country = Iraq) | `ar` / Asia/Baghdad | Arabic-first Iraqi deployment |
| Custom DocPerm on Item Price (read + select only) | Pharmacy Owner, Pharmacy Manager, Pharmacist, Cashier | ERPNext v17's POS barcode search reads Item Price with the user's permissions, but only Sales/Purchase Master Manager can read it. Frappe copies the standard rules first. The rows are removed on uninstall. This is the one exception to "no Custom DocPerm on core DocTypes". |

## Template for a future patch

```
### <short title>
- Reason:
- Files changed:
- Upstream compatibility risk:
- Upstream PR / issue:
- Removal path:
```
