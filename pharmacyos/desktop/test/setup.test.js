const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const localserver = require("../src/localserver");
const setup = require("../src/setup");

test("Windows paths become the WSL paths the server sees", () => {
	assert.equal(localserver.toWslPath("C:\\Program Files\\PharmacyOS ERP\\resources\\server"), "/mnt/c/Program Files/PharmacyOS ERP/resources/server");
	assert.equal(localserver.toWslPath("D:/Data/x"), "/mnt/d/Data/x");
	assert.equal(localserver.toWslPath("/already/linux"), "/already/linux");
});

test("server versions compare like PEP 440 (release candidates before the release)", () => {
	const c = localserver.compareVersions;
	assert.equal(c("1.0.0rc1", "1.0.0rc1"), 0);
	assert.equal(c("1.0.0rc2", "1.0.0rc1"), 1);
	assert.equal(c("1.0.0", "1.0.0rc9"), 1);
	assert.equal(c("1.0.0rc1", "1.0.0"), -1);
	assert.equal(c("1.0.1", "1.0.0"), 1);
	assert.equal(c("1.1.0", "1.0.9"), 1);
	assert.equal(c("1.0.0b1", "1.0.0rc1"), -1);
	assert.equal(c("1.0.0-rc.1", "1.0.0rc1"), 0);
	assert.equal(c("garbage", "1.0.0"), 0, "an unreadable version never triggers an update");
});

test("the setup asks for what the server's first run needs, and checks it", () => {
	const ok = {
		pharmacy_name: "Al Noor Pharmacy",
		owner_full_name: "Ahmed Ali",
		owner_email: "owner@example.com",
		owner_password: "long-enough",
		owner_password_confirm: "long-enough",
		phone: "0770 123 4567",
	};
	assert.deepEqual(setup.validate(ok), {});
	assert.equal(setup.validate({ ...ok, owner_email: "nope" }).owner_email, "email");
	assert.equal(setup.validate({ ...ok, owner_password: "short", owner_password_confirm: "short" }).owner_password, "short");
	assert.equal(setup.validate({ ...ok, owner_password_confirm: "different" }).owner_password_confirm, "mismatch");
	assert.equal(setup.validate({ ...ok, pharmacy_name: " " }).pharmacy_name, "required");
	assert.equal(setup.validate({ ...ok, phone: "call me" }).phone, "phone");
});

test("setup progress is read from the state file the Windows script writes", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-setup-"));
	const file = path.join(dir, "state.json");
	process.env.PHARMACYOS_SETUP_STATE = file;
	assert.equal(setup.readState(), null);
	fs.writeFileSync(file, "\uFEFF" + JSON.stringify({ step: "server", status: "running", percent: 40 })); // PowerShell writes a BOM
	assert.equal(setup.readState().percent, 40);
	assert.ok(setup.inProgress(setup.readState()));
	assert.ok(!setup.inProgress({ status: "done" }));
	delete process.env.PHARMACYOS_SETUP_STATE;
});

test("the server bundle carries the app and scripts, not tests or caches", () => {
	const out = fs.mkdtempSync(path.join(os.tmpdir(), "pos-bundle-"));
	require("child_process").execFileSync(process.execPath, [path.join(__dirname, "..", "scripts", "prepare-server-bundle.js"), out]);
	for (const rel of ["VERSION", "deploy/server/install-server.sh", "deploy/server/pharmacyos-server", "deploy/windows/install-server.ps1", "deploy/windows/system-check.ps1", "deploy/restore-backup.sh", "dev/setup-dev-bench.sh", "apps/pharmacyos_erp/pharmacyos_erp/hooks.py"]) {
		assert.ok(fs.existsSync(path.join(out, rel)), rel);
	}
	assert.ok(!fs.existsSync(path.join(out, "apps/pharmacyos_erp/pharmacyos_erp/tests")));
	assert.ok(localserver.parseVersion(fs.readFileSync(path.join(out, "VERSION"), "utf8").trim()));
	assert.ok(!fs.readFileSync(path.join(out, "deploy/server/install-server.sh"), "utf8").includes("\r\n"), "LF line endings for Linux");
});

test("Windows setup scripts are UTF-8 with a BOM (Windows PowerShell 5.1 otherwise misreads them)", () => {
	const dir = path.join(__dirname, "..", "..", "deploy", "windows");
	for (const name of fs.readdirSync(dir).filter((n) => n.endsWith(".ps1"))) {
		const bytes = fs.readFileSync(path.join(dir, name));
		const nonAscii = bytes.some((b) => b > 127);
		const bom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
		assert.ok(!nonAscii || bom, `${name}: non-ASCII text without a BOM`);
	}
});
