const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const localserver = require("../src/localserver");
const setup = require("../src/setup");
const { buildId } = require("../scripts/prepare-server-bundle.js");

const PHARMACY = {
	pharmacy_name: "Al Noor Pharmacy",
	owner_full_name: "Ahmed Ali",
	owner_email: "owner@example.com",
	owner_password: "long-enough",
	owner_password_confirm: "long-enough",
	phone: "0770 123 4567",
};

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
	const ok = PHARMACY;
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

// The launch of the elevated script is replaced here: a test never asks Windows for administrator rights.
test("the owner's password does not stay on disk when the setup script will not read it", async () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-request-"));
	const app = { getPath: () => dir, isPackaged: false };
	const stateFile = path.join(dir, "state.json");
	const file = setup.requestFile(dir);
	const state = (status) => fs.writeFileSync(stateFile, JSON.stringify({ status, step: "wsl", percent: 20 }));
	process.env.PHARMACYOS_SETUP_STATE = stateFile;
	try {
		// Windows' permission prompt refused (or failed): written for the script, then deleted
		let given = null;
		const refused = await setup.start(app, PHARMACY, async (_app, f) => {
			given = JSON.parse(fs.readFileSync(f, "utf8"));
			return { ok: false, denied: true };
		});
		assert.equal(refused.denied, true);
		assert.equal(given.owner_password, "long-enough");
		assert.ok(!fs.existsSync(file), "deleted when the script never started");
		// the script started: it deletes the file itself once the owner account exists
		assert.equal((await setup.start(app, PHARMACY, async () => ({ ok: true }))).ok, true);
		assert.ok(fs.existsSync(file));
		// a restart is pending: the script continues after sign-in and reads it then
		state("reboot_required");
		assert.equal(setup.forgetRequest(app), false);
		await setup.resume(app, async () => ({ ok: false, denied: true }));
		assert.ok(fs.existsSync(file), "kept while a restart is pending");
		// a failed setup's Retry whose prompt is refused
		state("failed");
		const retried = await setup.resume(app, async (_app, f) => {
			assert.equal(f, file, "Retry uses the details already given");
			return { ok: false, denied: true };
		});
		assert.equal(retried.ok, false);
		assert.ok(fs.existsSync(file), "kept after a refused Retry, so the next Retry can still work");
		// the setup failed and the owner goes back to enter the details again
		assert.equal((await setup.start(app, PHARMACY, async () => ({ ok: true }))).ok, true);
		assert.equal(setup.forgetRequest(app), true);
		assert.ok(!fs.existsSync(file));
		assert.equal(setup.forgetRequest(app), true, "nothing left to delete is fine");
		// invalid details are never written
		const invalid = await setup.start(app, { ...PHARMACY, owner_password_confirm: "other" }, async () => assert.fail("not launched"));
		assert.equal(invalid.errors.owner_password_confirm, "mismatch");
		assert.ok(!fs.existsSync(file));
	} finally {
		delete process.env.PHARMACYOS_SETUP_STATE;
	}
});

test("BUILD_ID tells apart bundles of one version built from different files", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-buildid-"));
	const write = (rel, text) => {
		fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
		fs.writeFileSync(path.join(dir, rel), text);
	};
	write("deploy/server/install-server.sh", "echo one\n");
	write("apps/pharmacyos_erp/setup.py", "x = 1\n");
	write("VERSION", "1.0.0\n");
	const first = buildId(dir);
	assert.match(first, /^[0-9a-f]{64}$/);
	assert.equal(buildId(dir), first, "deterministic");
	write("VERSION", "1.0.1\n");
	write("BUILD_ID", first + "\n");
	assert.equal(buildId(dir), first, "VERSION and BUILD_ID are not part of it");
	write("deploy/server/install-server.sh", "echo two\n");
	const changed = buildId(dir);
	assert.notEqual(changed, first, "a changed file");
	fs.renameSync(path.join(dir, "apps/pharmacyos_erp/setup.py"), path.join(dir, "apps/pharmacyos_erp/setup2.py"));
	const renamed = buildId(dir);
	assert.notEqual(renamed, changed, "a renamed file");
	write("deploy/new.txt", "");
	assert.notEqual(buildId(dir), renamed, "an added (even empty) file");
});

test("the server bundle carries the app and scripts, not tests or caches", () => {
	const out = fs.mkdtempSync(path.join(os.tmpdir(), "pos-bundle-"));
	const again = fs.mkdtempSync(path.join(os.tmpdir(), "pos-bundle-"));
	require("child_process").execFileSync(process.execPath, [path.join(__dirname, "..", "scripts", "prepare-server-bundle.js"), out]);
	require("child_process").execFileSync(process.execPath, [path.join(__dirname, "..", "scripts", "prepare-server-bundle.js"), again]);
	const build = fs.readFileSync(path.join(out, "BUILD_ID"), "utf8").trim();
	assert.match(build, /^[0-9a-f]{64}$/);
	assert.equal(build, buildId(out));
	assert.equal(fs.readFileSync(path.join(again, "BUILD_ID"), "utf8").trim(), build, "the same sources give the same BUILD_ID");
	for (const rel of ["VERSION", "BUILD_ID", "deploy/server/install-server.sh", "deploy/server/pharmacyos-server", "deploy/windows/install-server.ps1", "deploy/windows/system-check.ps1", "deploy/restore-backup.sh", "dev/setup-dev-bench.sh", "apps/pharmacyos_erp/pharmacyos_erp/hooks.py"]) {
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
