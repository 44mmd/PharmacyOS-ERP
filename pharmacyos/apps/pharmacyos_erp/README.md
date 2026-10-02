# PharmacyOS ERP

**Pharmacy Operating System** — designed & developed by HALF.

`pharmacyos_erp` is a Frappe application that turns an ERPNext installation into PharmacyOS ERP:
pharmacy navigation and workspaces, medicine data, batch & expiry operations (FEFO), inventory
health, reorder suggestions, branch context, Arabic/IQD foundations, PharmacyOS branding and a
versioned integration API.

It runs **on top of** unmodified ERPNext and Frappe. ERPNext remains the accounting, stock-ledger,
batch, purchasing, sales and permission engine; this app only adds hooks, DocTypes, custom fields,
pages, assets, translations and API methods.

## Requirements

* Frappe Framework and ERPNext `>=17.0.0-dev,<18` (see `pyproject.toml`)
* See `../../docs/DEVELOPMENT.md` for the full development stack

## Install (development)

```bash
# from a bench
ln -s /path/to/PharmacyOS-ERP/pharmacyos/apps/pharmacyos_erp apps/pharmacyos_erp   # or: bench get-app <repo-url>
bench pip install -e apps/pharmacyos_erp
echo pharmacyos_erp >> sites/apps.txt
bench --site <site> install-app pharmacyos_erp
bench build --app pharmacyos_erp
```

Run the tests on a **test site** (never the dev site):

```bash
bench --site test.localhost run-tests --app pharmacyos_erp --lightmode
```

## Moving to its own repository

The app is self-contained in this directory so it can be split out without history loss:

```bash
git subtree split --prefix=pharmacyos/apps/pharmacyos_erp -b pharmacyos_erp-split
git push git@github.com:44mmd/pharmacyos_erp.git pharmacyos_erp-split:main
```

## License

GPL-3.0 (see `license.txt`) — the same license as ERPNext, on which this app is built.
See `NOTICE.md` for upstream attribution.
