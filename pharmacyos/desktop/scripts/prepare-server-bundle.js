// Builds `server-bundle/`: everything the pharmacy's PharmacyOS server needs, shipped inside the
// Windows installer (resources/server). It mirrors the repository's `pharmacyos/` layout, so the
// scripts find each other the same way on a developer machine and in an installed app:
//
//   VERSION                       PharmacyOS ERP app version (from pharmacyos_erp/__init__.py)
//   BUILD_ID                      sha256 of the bundled files: two bundles of one VERSION built from
//                                 different files differ, so a corrected installer is offered as an update
//   deploy/server/…               install-server.sh, pharmacyos-server (run inside Linux)
//   deploy/windows/…              install-server.ps1 (run elevated on Windows by the setup)
//   deploy/restore-backup.sh
//   dev/setup-dev-bench.sh, dev/mariadb-frappe.cnf
//   apps/pharmacyos_erp/          the PharmacyOS ERP app (no tests, caches or build output)
//
// Usage: node scripts/prepare-server-bundle.js [outDir]   (npm run bundle)
"use strict";
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", ".."); // pharmacyos/
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

// Every file under dir as a relative path with "/" separators.
function listFiles(dir, prefix = "") {
	const out = [];
	for (const entry of fs.readdirSync(path.join(dir, prefix), { withFileTypes: true })) {
		const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
		if (entry.isDirectory()) out.push(...listFiles(dir, rel));
		else out.push(rel);
	}
	return out;
}

// The bundle's build identity: sha256 over a manifest of the bundled files — one "<sha256>  <path>" line
// per file (the sha256sum format), paths sorted bytewise (LC_ALL=C sort) — without VERSION and BUILD_ID.
// The same files always give the same id, on any machine; any changed, added or renamed file changes it.
// In a shell, inside the bundle: find . -type f ! -path ./VERSION ! -path ./BUILD_ID | sed 's|^\./||' |
//   LC_ALL=C sort | xargs -d '\n' sha256sum | sha256sum
function buildId(dir) {
	const files = listFiles(dir)
		.filter((rel) => rel !== "VERSION" && rel !== "BUILD_ID")
		.sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
	const manifest = files.map((rel) => `${crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, rel))).digest("hex")}  ${rel}\n`).join("");
	return crypto.createHash("sha256").update(manifest).digest("hex");
}

function appVersion() {
	const init = fs.readFileSync(path.join(ROOT, "apps/pharmacyos_erp/pharmacyos_erp/__init__.py"), "utf8");
	const m = /__version__\s*=\s*"([^"]+)"/.exec(init);
	if (!m) throw new Error("no __version__ in pharmacyos_erp/__init__.py");
	return m[1];
}

function main(out) {
	fs.rmSync(out, { recursive: true, force: true });
	fs.mkdirSync(out, { recursive: true });
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
		copy(src, path.join(out, rel));
	}
	// Linux scripts must keep LF line endings even when built from a Windows checkout (autocrlf)
	for (const rel of ["deploy/server/install-server.sh", "deploy/server/pharmacyos-server", "deploy/restore-backup.sh", "dev/setup-dev-bench.sh", "dev/mariadb-frappe.cnf"]) {
		const file = path.join(out, rel);
		fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n"));
	}
	const version = appVersion();
	fs.writeFileSync(path.join(out, "VERSION"), version + "\n");
	const build = buildId(out);
	fs.writeFileSync(path.join(out, "BUILD_ID"), build + "\n");
	console.log(`server bundle ${version} (build ${build.slice(0, 12)}) → ${out}`);
}

if (require.main === module) main(path.resolve(process.argv[2] || path.join(__dirname, "..", "server-bundle")));

module.exports = { buildId };
