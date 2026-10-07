# Installing PharmacyOS on a Windows PC — for the pharmacy owner

PharmacyOS Local ERP 1.0.0-rc.2. No technical knowledge is needed: there is one file to run and a few
questions to answer. The pharmacy then works on this computer **without internet**.

## What you need

* A PC with **Windows 10 (version 2004 or newer) or Windows 11, 64-bit**, with the Windows updates installed.
* **8 GB of memory** recommended (4 GB minimum), **15 GB free** on drive C.
* **Virtualization turned on** in the PC's BIOS/UEFI (usually already on; the setup checks it and says so).
* **Internet during installation only** (it downloads about 1.5 GB). Afterwards the pharmacy runs offline.
* The Windows account you install with should be the one used at the counter (the pharmacy server is set up
  for that account and starts with Windows for it).
* The password of a Windows administrator (Windows asks for it once).

## Installation (20–40 minutes)

1. **Run `PharmacyOS-Setup-1.0.0-rc.2.exe`.** Accept the licence, keep the suggested folder, press Install.
   PharmacyOS opens when the installer finishes.
2. **Choose "This computer is the pharmacy server"** (هذا الجهاز هو خادم الصيدلية) and press Continue.
3. **Enter the pharmacy's details:** pharmacy name (English and Arabic), the owner's name, the owner's email
   (this is the sign-in name), a password of at least 8 characters, and optionally a phone number. Tick
   *Let other PCs in the pharmacy connect* only if other counter PCs will use this one (Windows 11).
4. **The computer is checked** (Windows version, virtualization, memory, disk). Press **Install**.
5. **Windows asks for permission once** — choose *Yes* (enter an administrator password if asked).
6. **Wait.** The screen shows each step and the percentage. You can leave the PC; do not turn it off.
7. **If Windows needs a restart** (only the first time WSL is turned on), PharmacyOS says so: press
   **Restart now**. After you sign in again, PharmacyOS opens and **continues by itself** from where it
   stopped — nothing has to be started again.
8. **"PharmacyOS is ready"**: press **Open PharmacyOS** and sign in with the owner's email and password.

The pharmacy server now starts by itself every time Windows starts.

## First things to do

| Task | Where |
|---|---|
| Add medicines (name, Arabic name, barcode, price, cost, reorder level) | Pharmacy → Medicines → **Add Medicine** |
| Add suppliers | Purchasing → Suppliers |
| Receive stock (batch number and expiry are required for medicines) | Purchasing → Purchase Receipts |
| Add staff and choose their role (Cashier, Pharmacist, Pharmacy Manager, …) | Team → Users → *Role Profile* |
| Choose the receipt printer and print a test receipt | PharmacyOS menu → **Connection & Printer** |
| Make a counter PC open the Point of Sale directly | Connection & Printer → *Open at start* → Point of Sale |

A counter called **Main Counter** with cash payments is created during setup, so cashiers can sell at once.

## Every day

* Open **PharmacyOS ERP** from the desktop or the Start menu. If the server is still starting (just after
  turning the PC on) a "Starting PharmacyOS…" screen is shown until it is ready.
* Cashiers sign in and open their shift with the cash in the drawer; at the end they **Close shift** and enter
  the cash counted — the difference is recorded.
* If PharmacyOS ever says *The pharmacy server is not answering*, press **Start the server**, then wait two
  minutes. Restarting the PC also fixes most problems. No saved sale is ever lost.

## Barcode scanner and receipt printer

* **USB barcode scanners** work as soon as they are plugged in (they type like a keyboard). Scan at any time
  on the Point of Sale screen; scanners that end the code with *Enter* or *Tab* both work.
* **Receipt printers**: install the printer's Windows driver, then choose it in PharmacyOS → *Connection &
  Printer* and press *Test receipt*. Tick *Print receipts directly* to print without a dialog. 58 mm printers:
  set PharmacyOS Settings → *Receipt Paper Width* to 58mm. A cash drawer connected to the printer opens when
  its driver is set to "open drawer after printing".

## Backups

* PharmacyOS backs up the database **every hour** and everything **every day** into
  `C:\ProgramData\PharmacyOS\Backups`. **Copy that folder to an external drive or USB stick regularly.**
* PharmacyOS menu → **Backups…**: *Back up now*, *Open the folder*, and *Restore* (asks you to type
  RESTORE; the current state is backed up first, so a restore can itself be undone).

## Updates

Run the newer `PharmacyOS-Setup-<version>.exe` over the installed one. Data and settings are kept. When the new
version also updates the pharmacy server, PharmacyOS asks **Update now / Later** at the next start: it takes a
backup first and, if anything fails, returns the server to the previous version with all its data.

## Other PCs in the pharmacy

Install the same Setup on the other PC, choose **Connect to the pharmacy server on the network**, and enter
the server PC's address (shown in Windows network settings, e.g. `192.168.1.10`). Sharing needs Windows 11 on
the server PC and the *Let other PCs connect* option.

## Removing PharmacyOS

Windows Settings → Apps → PharmacyOS ERP → Uninstall. You are asked whether to also remove the pharmacy server
and its database; the answer **No** (the default) keeps everything. Backups are never deleted.
