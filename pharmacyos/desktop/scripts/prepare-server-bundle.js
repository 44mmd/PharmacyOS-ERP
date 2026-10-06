// Builds `server-bundle/`: everything the pharmacy's PharmacyOS server needs, shipped inside the
// Windows installer (resources/server). It mirrors the repository's `pharmacyos/` layout, so the
// scripts find each other the same way on a developer machine and in an installed app:
//
//   VERSION                       PharmacyOS ERP app version (from pharmacyos_erp/__init__.py)
//   deploy/server/…               install-server.sh, pharmacyos-server (run inside Linux)
//   deploy/windows/…              install-server.ps1 (run elevated on Windows by the setup)
//   deploy/restore-backup.sh
//   dev/setup-dev-bench.sh, dev/mariadb-frappe.cnf
//   apps/pharmacyos_erp/          the PharmacyOS ERP app (no tests, caches or build output)
//
// Usage: node scripts/prepare-server-bundle.js [outDir]   (npm run bundle)
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", ".."); // pharmacyos/
const OUT = path.resolve(process.argv[2] || path.join(__dirname, "..", "server-bundle"));
const SKIP = new Set(["__pycache__", "node_modules", "tests", ".pytest_cache", "dist", ".git"]);

function copy(src, dest) {
	const stat = fs.statSync(src);
	if (stat.isDirectory()) {
		if (SKIP.has(path.basename(src))) return;
		fs.mkdirSync(dest, { recursive: true });
		for (const name of fs.readdirSync(src).sort()) copy(path.join(src, name), path.join(dest, name));
	} else if (!/\.(pyc|pyo)$/.test(src)) {
		fs.mkdirSync(path.dirname(dest), { recursive: true });
		fs.copyFileSync(src, dest);
		// keep shell scripts executable (Linux side); Windows ignores the mode
		if (/\.sh$|pharmacyos-server$/.test(src)) fs.chmodSync(dest, 0o755);
	}
}

function appVersion() {
	const init = fs.readFileSync(path.join(ROOT, "apps/pharmacyos_erp/pharmacyos_erp/__init__.py"), "utf8");
	const m = /__version__\s*=\s*"([^"]+)"/.exec(init);
	if (!m) throw new Error("no __version__ in pharmacyos_erp/__init__.py");
	return m[1];
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
for (const rel of [
	"deploy/server",
	"deploy/windows",
	"deploy/restore-backup.sh",
	"dev/setup-dev-bench.sh",
	"dev/mariadb-frappe.cnf",
	"apps/pharmacyos_erp",
	"LICENSE-NOTICES.txt",
]) {
	const src = path.join(ROOT, rel);
	if (!fs.existsSync(src)) throw new Error(`missing ${rel}`);
	copy(src, path.join(OUT, rel));
}
// Linux scripts must keep LF line endings even when built from a Windows checkout (autocrlf)
for (const rel of ["deploy/server/install-server.sh", "deploy/server/pharmacyos-server", "deploy/restore-backup.sh", "dev/setup-dev-bench.sh", "dev/mariadb-frappe.cnf"]) {
	const file = path.join(OUT, rel);
	fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n"));
}
const version = appVersion();
fs.writeFileSync(path.join(OUT, "VERSION"), version + "\n");
console.log(`server bundle ${version} → ${OUT}`);
