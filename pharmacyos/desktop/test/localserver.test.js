const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const localserver = require("../src/localserver");

const A = "a".repeat(64);
const B = "b".repeat(64);

test("the server update is offered for a newer version, or the same version built from other files", () => {
	const offers = (bundled, installed, bundledBuild, installedBuild) => localserver.offersUpdate({ bundled, installed, bundledBuild, installedBuild });
	assert.equal(offers("1.0.1", "1.0.0", A, A), true, "a newer version, whatever the builds");
	assert.equal(offers("1.0.0", "1.0.0rc2", null, null), true);
	assert.equal(offers("1.0.0", "1.0.1", B, A), false, "an older bundle never replaces a newer server");
	assert.equal(offers("1.0.0", "1.0.1", B, "unknown"), false, "…also when the server cannot tell its build");
	assert.equal(offers("1.0.0", "1.0.0", A, A), false, "the same version and build");
	assert.equal(offers("1.0.0", "1.0.0", A, A.toUpperCase()), false);
	assert.equal(offers("1.0.0", "1.0.0", A, B), true, "the same version from other files: a corrected installer");
	assert.equal(offers("1.0.0", "1.0.0", A, "unknown"), true, "an older server without build-id, same version");
	assert.equal(offers("1.0.0", "1.0.0", A, null), true);
	assert.equal(offers("1.0.0-rc.1", "1.0.0rc1", A, B), true, "equal versions spelled differently");
	assert.equal(offers("1.0.0", "1.0.0", null, B), false, "a bundle without BUILD_ID compares versions only");
	assert.equal(offers("garbage", "1.0.0", A, B), false, "an unreadable version never triggers an update");
	assert.equal(offers("1.0.0", "garbage", A, B), false);
	assert.equal(offers(null, "1.0.0", A, B), false);
	assert.equal(localserver.compareVersions("garbage", "1.0.0"), 0, "compareVersions itself is unchanged");
});

test("the bundle's BUILD_ID is read next to its VERSION", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-build-"));
	process.env.PHARMACYOS_BUNDLE_DIR = dir;
	try {
		const app = { isPackaged: false };
		assert.equal(localserver.bundleBuild(app), null);
		fs.writeFileSync(path.join(dir, "VERSION"), "1.0.0\n");
		fs.writeFileSync(path.join(dir, "BUILD_ID"), A + "\n");
		assert.equal(localserver.bundleVersion(app), "1.0.0");
		assert.equal(localserver.bundleBuild(app), A);
	} finally {
		delete process.env.PHARMACYOS_BUNDLE_DIR;
	}
});

test("developer hooks are ignored by the installed app", () => {
	const savedResources = process.resourcesPath;
	process.env.PHARMACYOS_SERVER_CMD = "/tmp/fake-pharmacyos-server";
	process.env.PHARMACYOS_BUNDLE_DIR = "/tmp/fake-bundle";
	try {
		assert.equal(localserver.isPackaged(), false, "unit tests run outside Electron: never packaged");
		assert.deepEqual(localserver.command(["version"], false), { file: "/tmp/fake-pharmacyos-server", args: ["version"] });
		assert.equal(localserver.command(["version"]).file, "/tmp/fake-pharmacyos-server");
		assert.deepEqual(localserver.command(["version"], true), {
			file: "wsl.exe",
			args: ["-d", "PharmacyOS", "--user", "root", "--exec", "/opt/pharmacyos/bin/pharmacyos-server", "version"],
		});
		assert.equal(localserver.bundleDir({ isPackaged: false }), "/tmp/fake-bundle");
		process.resourcesPath = path.join(os.tmpdir(), "PharmacyOS ERP", "resources");
		assert.equal(localserver.bundleDir({ isPackaged: true }), path.join(process.resourcesPath, "server"));
	} finally {
		delete process.env.PHARMACYOS_SERVER_CMD;
		delete process.env.PHARMACYOS_BUNDLE_DIR;
		if (savedResources === undefined) delete process.resourcesPath;
		else process.resourcesPath = savedResources;
	}
});

// a stand-in for `pharmacyos-server` (a shell script: not on Windows)
test("the installed build is asked from the server; an older server is 'unknown'", { skip: process.platform === "win32" }, async () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-server-cmd-"));
	const cmd = path.join(dir, "pharmacyos-server");
	fs.writeFileSync(cmd, '#!/bin/sh\n[ "$1" = build-id ] || exit 2\nprintf "%s\\n" "$POS_TEST_OUT"\nexit "${POS_TEST_CODE:-0}"\n', { mode: 0o755 });
	process.env.PHARMACYOS_SERVER_CMD = cmd;
	try {
		process.env.POS_TEST_OUT = B;
		assert.equal(await localserver.installedBuild(), B);
		process.env.POS_TEST_OUT = "unknown";
		assert.equal(await localserver.installedBuild(), "unknown");
		process.env.POS_TEST_OUT = "usage: pharmacyos-server {install|run|start|…}";
		process.env.POS_TEST_CODE = "1";
		assert.equal(await localserver.installedBuild(), "unknown", "no build-id command");
	} finally {
		delete process.env.PHARMACYOS_SERVER_CMD;
		delete process.env.POS_TEST_OUT;
		delete process.env.POS_TEST_CODE;
	}
});
