// The pharmacy server on THIS computer (single-PC mode): the "PharmacyOS" WSL2 environment.
// Everything here goes through /opt/pharmacyos/bin/pharmacyos-server inside that environment, so the
// desktop app never touches the database itself. On a developer machine (not Windows) the command can
// be replaced with PHARMACYOS_SERVER_CMD (e.g. a script that enters a test container) and the bundle with
// PHARMACYOS_BUNDLE_DIR — in an unpackaged app only: an installed app always runs its own command and its
// own bundle (the setup runs the bundle's script with administrator rights).
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const DISTRO = "PharmacyOS";
const CONTROL = "/opt/pharmacyos/bin/pharmacyos-server";

// Running as the installed app (not `electron .` on a developer machine). Outside Electron (unit tests)
// require("electron") is the npm package, not the API: never packaged.
function isPackaged() {
	try {
		const { app } = require("electron");
		return Boolean(app && app.isPackaged);
	} catch {
		return false;
	}
}

// A developer hook from the environment, ignored by the installed app.
function devHook(name, packaged = isPackaged()) {
	return packaged ? "" : process.env[name] || "";
}

// Where the server bundle shipped with this app lives (resources/server when installed).
function bundleDir(app) {
	const packaged = app ? Boolean(app.isPackaged) : isPackaged();
	const dev = devHook("PHARMACYOS_BUNDLE_DIR", packaged);
	if (dev) return dev;
	return packaged ? path.join(process.resourcesPath, "server") : path.join(__dirname, "..", "server-bundle");
}

function bundleVersion(app) {
	try {
		return fs.readFileSync(path.join(bundleDir(app), "VERSION"), "utf8").trim();
	} catch {
		return null;
	}
}

// The bundle's build identity (BUILD_ID, written by scripts/prepare-server-bundle.js): tells apart two
// bundles of the same version built from different files.
function bundleBuild(app) {
	try {
		return fs.readFileSync(path.join(bundleDir(app), "BUILD_ID"), "utf8").trim() || null;
	} catch {
		return null;
	}
}

// C:\Program Files\PharmacyOS ERP\resources\server → /mnt/c/Program Files/PharmacyOS ERP/resources/server
function toWslPath(windowsPath) {
	const m = /^([A-Za-z]):[\\/](.*)$/.exec(String(windowsPath));
	if (!m) return String(windowsPath).replace(/\\/g, "/");
	return `/mnt/${m[1].toLowerCase()}/${m[2].replace(/\\/g, "/")}`;
}

// PEP 440 versions as PharmacyOS uses them: 1.0.0, 1.0.0rc1, 1.0.1b2, 1.0.0.dev3
function parseVersion(v) {
	const m = /^(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(a|b|rc|dev)\.?(\d+))?$/.exec(String(v || "").trim().replace(/^v/, ""));
	if (!m) return null;
	const pre = { dev: 0, a: 1, b: 2, rc: 3 };
	return [Number(m[1]), Number(m[2]), Number(m[3] || 0), m[4] ? pre[m[4]] : 4, Number(m[5] || 0)];
}

function compareVersions(a, b) {
	const x = parseVersion(a);
	const y = parseVersion(b);
	if (!x || !y) return 0;
	for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
	return 0;
}

function command(args, packaged = isPackaged()) {
	const dev = devHook("PHARMACYOS_SERVER_CMD", packaged);
	if (dev) return { file: dev, args };
	return { file: "wsl.exe", args: ["-d", DISTRO, "--user", "root", "--exec", CONTROL, ...args] };
}

// Runs `pharmacyos-server <args>`; streams lines to onLine; resolves {code, result, output}.
// `result` is the JSON of the last "##PHARMACYOS-RESULT" line, when the command prints one.
function run(args, { onLine, timeoutMs = 0, detached = false } = {}) {
	return new Promise((resolve) => {
		const { file, args: argv } = command(args);
		let child;
		try {
			child = spawn(file, argv, { windowsHide: true, detached, stdio: detached ? "ignore" : ["ignore", "pipe", "pipe"] });
		} catch (error) {
			resolve({ code: -1, result: null, output: String(error) });
			return;
		}
		if (detached) {
			child.on("error", () => {});
			child.unref();
			resolve({ code: 0, result: null, output: "" });
			return;
		}
		let output = "";
		let result = null;
		let buffer = "";
		const take = (chunk) => {
			buffer += chunk.toString("utf8").replace(/\u0000/g, "");
			let i;
			while ((i = buffer.indexOf("\n")) >= 0) {
				const line = buffer.slice(0, i).replace(/\r$/, "");
				buffer = buffer.slice(i + 1);
				output += line + "\n";
				const m = /^##PHARMACYOS-RESULT (.*)$/.exec(line);
				if (m) {
					try {
						result = JSON.parse(m[1]);
					} catch {
						/* not JSON: keep the text */
					}
				} else if (onLine) onLine(line);
			}
		};
		child.stdout.on("data", take);
		child.stderr.on("data", take);
		const timer = timeoutMs ? setTimeout(() => child.kill(), timeoutMs) : null;
		child.on("error", (error) => {
			clearTimeout(timer);
			resolve({ code: -1, result, output: output + String(error) });
		});
		child.on("close", (code) => {
			clearTimeout(timer);
			if (buffer) take("\n");
			resolve({ code, result, output });
		});
	});
}

// Starts the server and keeps the environment running (the same command the boot task runs).
function startKeepAlive() {
	return run(["run"], { detached: true });
}

async function installedVersion() {
	const r = await run(["version"], { timeoutMs: 60000 });
	const v = (r.output || "").trim().split("\n").pop();
	return r.code === 0 && parseVersion(v) ? v : null;
}

// The installed server's BUILD_ID; "unknown" when it has none or is too old to tell (no build-id command).
async function installedBuild() {
	const r = await run(["build-id"], { timeoutMs: 60000 });
	const v = (r.output || "").trim().split("\n").pop().trim().toLowerCase();
	return r.code === 0 && /^[0-9a-f]{64}$/.test(v) ? v : "unknown";
}

// Whether this app's server bundle is offered as an update of the installed server: a newer version, or
// the same version built from other files (a corrected installer). An installed build that cannot be told
// ("unknown", an older server) counts as different only when the versions are equal; an older bundle, or
// an unreadable version, never offers an update.
function offersUpdate({ bundled, installed, bundledBuild, installedBuild }) {
	if (!parseVersion(bundled) || !parseVersion(installed)) return false;
	const c = compareVersions(bundled, installed);
	if (c !== 0) return c > 0;
	return Boolean(bundledBuild) && String(bundledBuild).toLowerCase() !== String(installedBuild || "unknown").toLowerCase();
}

module.exports = {
	DISTRO,
	isPackaged,
	bundleDir,
	bundleVersion,
	bundleBuild,
	toWslPath,
	parseVersion,
	compareVersions,
	command,
	run,
	startKeepAlive,
	installedVersion,
	installedBuild,
	offersUpdate,
};
