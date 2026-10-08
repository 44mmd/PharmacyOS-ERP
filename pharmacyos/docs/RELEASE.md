# PharmacyOS Local ERP — release engineering

## Version

| Part | Where | 1.0.0-rc.3 |
|---|---|---|
| Desktop app and installer | `desktop/package.json` `version` (SemVer) | `1.0.0-rc.3` |
| Server app (`pharmacyos_erp`) | `apps/pharmacyos_erp/pharmacyos_erp/__init__.py` (PEP 440) | `1.0.0rc3` |
| Server bundle | `VERSION` written by `scripts/prepare-server-bundle.js` from the app version | `1.0.0rc3` |

Both move together for a release. The app shows its version on every setup/startup screen and in
*PharmacyOS → About*; the POS context returns the server version; System Status lists app versions.
Any change to shipped text (Arabic included) is a new version: the desktop app offers a server update only when
the bundled version (or, for the same version, the build) differs from the installed one.

### Release notes

| Version | Changes |
|---|---|
| `1.0.0-rc.3` | Arabic terminology (Iraqi usage): a cash/POS shift is **«شِفت»**, plural **«شِفتات»**, masculine — on the POS (open / close shift, the counter bar), in server messages, the Pharmacy Report's shift table (فُتح / أُغلق / مفتوح) and the sidebar (شِفتات نقطة البيع). Display text only (`locale/ar.po` msgstr, docs): no database, DocType, field, API or migration change. The counter's *Shift* and the report's shift-table words are translated with a context (msgctxt), so the generic *Shift* (ERPNext's asset-depreciation shift), *Open*, *Closed* and *Opened* elsewhere in the desk keep Frappe's / ERPNext's Arabic. Guard: `qa/check_terminology.py` (CI step *Arabic terminology*; normalises Persian/Kurdish ی and invisible joiners, scans `.bat` / `.cmd`, allows colour phrases) and `tests/test_terminology.py`. `.gitattributes` keeps LF line endings in a Windows checkout, so the server bundle inside the installer is byte-for-byte the one QA installs (CI fails otherwise). Updating from rc.2 recompiles the translations during `bench migrate` (the app's `after_migrate`). Also: after a failed update is rolled back, `pharmacyos-server` reports the result only once the server answers again (found by the rc.3 QA: the next sign-in got the web server's "back soon" page). Fresh install, the update rc.2 → rc.3 and the full local QA re-run: `docs/qa/local/FINAL_QA_REPORT.md`. |
| `1.0.0-rc.2` | Local ERP v1 final certification (Phase A): `docs/qa/local/FINAL_QA_REPORT.md` at commit `05a68fa`. |

## Pinned dependencies (server)

| Component | Version | Source / check |
|---|---|---|
| Ubuntu (WSL image) | 24.04 `current` | cloud-images.ubuntu.com (the image the suite is verified in) |
| Frappe | v16.36.1 | git tag |
| ERPNext | v16.37.0 | git tag |
| Python | 3.14.6 | uv |
| bench CLI | 5.31.0 | PyPI |
| uv | 0.11.32 | GitHub release, SHA-256 verified |
| Node.js | 24.21.0 | nodejs.org, SHA-256 verified against SHASUMS256.txt |
| yarn | 1.22.22 | npm |
| MariaDB, Redis, nginx, supervisor | Ubuntu 24.04 packages | apt |

The moving `version-16` branch head is deliberately not used (v16.50.0 produced sites without the Gender
DocType, October 2026). Changing a pin means re-running the suite on a fresh site.

## Building the Windows installer

```
cd pharmacyos/desktop
npm ci
npm test
npm run dist:win        # = server bundle (resources/server) + Electron + NSIS
```

Output: `dist/PharmacyOS-Setup-<version>.exe` (≈112 MB). On Windows nothing else is needed; on Linux/macOS
electron-builder also needs Wine. A clean checkout builds reproducibly: dependencies are locked
(`package-lock.json`), Electron 44.5.1 and electron-builder 26.15.3 are pinned exactly, and the server bundle is
generated from the repository each time.

### GitHub Actions — `.github/workflows/pharmacyos-desktop.yml`

* **checks** (Linux): shell and PowerShell scripts parse; Arabic terminology guard (`qa/check_terminology.py`:
  a cash/POS shift is «شِفت»); desktop unit tests; the server bundle built from the Linux checkout (its
  `BUILD_ID` is handed to the Windows job). Push runs also start for changes under `pharmacyos/docs/` and
  `pharmacyos/qa/`, which the guard scans.
* **windows-installer** (windows-latest): scripts parse in Windows PowerShell 5.1 (and are UTF-8 with BOM,
  which 5.1 needs for non-ASCII text); installer helper functions behave on 5.1; the setup's system check runs
  on real Windows; build; SHA-256 — the installer's file name, size in bytes, SHA-256 (`SHA256SUMS.txt`),
  whether it is signed, the commit and the server bundle's version and build are printed in the job log and the
  run summary; the job fails if the bundle built on Windows is not the Linux one (same `BUILD_ID`: the build QA
  installs is the build that ships); silent install →
  installed server bundle present → first launch (smoke run) → silent uninstall keeps pharmacy data. Artifact
  **PharmacyOS-Setup** (installer, `SHA256SUMS.txt`, the smoke run's images), kept 90 days.
* **evidence** (`workflow_dispatch` with `publish_evidence` only): commits the Windows CI images to the branch.
  Not used since 1.0.0-rc.3: screenshots are no longer maintained as QA evidence (requirement withdrawn by the
  product owner); the release facts come from the push run's log and summary.
* **draft-release**: on a tag `pharmacyos-local-v<version>` (e.g. `pharmacyos-local-v1.0.0-rc.3`) the installer
  is attached to a **draft** GitHub release. Nothing is published until a person presses *Publish*.

## Code signing (external dependency)

The build signs automatically when a certificate is provided; it never fakes a signature.

1. Obtain a Windows code-signing certificate (OV or EV) as a `.pfx` for the publisher "HALF".
2. Add repository secrets **`WINDOWS_CSC_LINK`** (the `.pfx`, base64-encoded) and
   **`WINDOWS_CSC_KEY_PASSWORD`** (its password).
3. Builds then sign the app, its helper and the installer (SHA-256, RFC 3161 timestamp
   `http://timestamp.digicert.com`, `desktop/package.json` → `build.win.signtoolOptions`). Tag builds refuse to
   run unsigned. Locally: set `CSC_LINK` and `CSC_KEY_PASSWORD` before `npm run dist:win`.

Without a certificate the installer works but Windows SmartScreen warns "unknown publisher".

## Updates

* **Desktop app**: the newer `PharmacyOS-Setup-<version>.exe` installs over the old one (NSIS upgrade). User
  settings (`%APPDATA%`) and pharmacy data (`%ProgramData%\PharmacyOS`, the WSL environment) are untouched; the
  uninstaller run by an upgrade never removes the server. There is no silent auto-update (no update feed:
  `build.publish` is `null`); a feed can be added later with electron-updater once releases are signed.
* **Server**: the installer carries the server bundle. At the next start the app compares it with the server
  on the PC (`pharmacyos-server version`) and, when newer, offers **Update now / Later**. `pharmacyos-server
  update <bundle>`: verified safety backup → maintenance mode → app copied into
  `/opt/pharmacyos/releases/<version>` → `bench migrate` → `bench build` → restart → health check. Any failure
  switches back to the previous release and restores the safety backup (tested with a real failure).
  The previous release is kept on disk.

## Server layout (inside the PharmacyOS WSL environment)

| Path | What |
|---|---|
| `/etc/pharmacyos/server.env` | site, port, data folder, database root and Administrator passwords (root only) |
| `/opt/pharmacyos/bin/pharmacyos-server` | start, stop, status, health, run, version, backup, list-backups, restore, update |
| `/opt/pharmacyos/releases/<version>` · `/opt/pharmacyos/app` | installed app releases · the active one |
| `/home/frappe/frappe-bench` | Frappe bench (site `pharmacy.local`) |
| `/var/lib/pharmacyos/install/*.done` | completed install steps (resume) |
| `/mnt/c/ProgramData/PharmacyOS` | backups, sales spreadsheets, daily reports, logs (on the Windows disk) |
