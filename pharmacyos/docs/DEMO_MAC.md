# Quick demo on a Mac (Apple silicon, Parallels) — PharmacyOS Local ERP

A temporary demo of **our customized ERP** (Frappe 16.36.1 / ERPNext 16.37.0 + the `pharmacyos_erp` app:
pharmacy workspaces, Arabic, the counter POS, batches/expiry, reports) with **fictitious demo data**.
It never connects to the Cloud (Local ↔ Cloud sync stays off) and must not be used for a real pharmacy.

Windows 11 on ARM cannot run the Windows installer (it needs WSL2 with an x64 Linux image), so the demo
runs in an **Ubuntu 24.04 ARM64** virtual machine instead. The Windows x64 installer stays the product for
pharmacy PCs.

## 1. Create the Ubuntu virtual machine (≈10 minutes)

1. Open **Parallels Desktop** → **File → New…**
2. Choose **Download Ubuntu Linux** (Parallels downloads the ARM version by itself) → **Continue**.
3. Before it starts, set **Hardware → CPU & Memory**: 4 CPUs, **8 GB** memory; disk at least **40 GB**.
4. Finish Ubuntu's first-start questions (language, user name, password — remember the password).

## 2. Get the PharmacyOS code (the repository is private)

Open **Terminal** in Ubuntu (Ctrl+Alt+T) and run, one line at a time:

```bash
sudo apt update && sudo apt install -y git gh
gh auth login
```

Answer `gh auth login`: **GitHub.com** → **HTTPS** → **Yes** → **Login with a web browser**; copy the
one-time code it shows, press Enter, paste the code in the browser that opens and sign in as **44mmd**.
Then:

```bash
gh repo clone 44mmd/Private-ERP -- --depth 1 --branch claude/vibrant-ritchie-jzbdjp
```

## 3. Install the demo — one command (20–40 minutes)

```bash
sudo bash Private-ERP/pharmacyos/deploy/demo/mac-demo.sh 2>&1 | tee ~/pharmacyos-demo.log
```

It installs everything (database, server, our ERP), loads the demo data and prints the address and the
sign-ins. Keep the Mac awake and on power. If it stops with an error, run the same command again (it
continues where it stopped); if it still fails, send the last 40 lines of `~/pharmacyos-demo.log`.

## 4. Open it

The last screen shows an address like `http://10.211.55.5/`. Open it in **Safari on the Mac** (or Firefox
in Ubuntu):

| Who | Sign-in | Password |
|---|---|---|
| Owner | `owner@demo.invalid` | `DemoOwner-2026` |
| Pharmacy manager | `manager@pos-test.invalid` | `DemoManager-2026` |
| Cashier | `cashier@pos-test.invalid` | `DemoCashier-2026` |

* The counter (Point of Sale): `http://<address>/pos`; demo barcodes start with `200000…` (type them and
  press Enter).
* Things to try: Medicines, Batches and expiry, Purchase Receipt, the counter (open shift → sell → close
  shift), returns, the Pharmacy Report.
* Recording on the Mac: **Shift ⌘ 5** → *Record Selected Portion* around the Safari or Parallels window.

## Stop, start, remove

```bash
sudo /opt/pharmacyos/bin/pharmacyos-server stop
sudo /opt/pharmacyos/bin/pharmacyos-server start
```

To throw the demo away, delete the virtual machine in Parallels. Nothing else on the Mac is changed.
