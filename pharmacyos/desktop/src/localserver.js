// The pharmacy server on THIS computer (single-PC mode): the "PharmacyOS" WSL2 environment.
// Everything here goes through /opt/pharmacyos/bin/pharmacyos-server inside that environment, so the
// desktop app never touches the database itself. On a developer machine (not Windows) the command can
// be replaced with PHARMACYOS_SERVER_CMD (e.g. a script that enters a test container).
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const DISTRO = "PharmacyOS";
const CONTROL = "/opt/pharmacyos/bin/pharmacyos-server";

// Where the server bundle shipped with this app lives (resources/server when installed).
function bundleDir(app) {
	if (process.env.PHARMACYOS_BUNDLE_DIR) return process.env.PHARMACYOS_BUNDLE_DIR;
	return app && app.isPackaged ? path.join(process.resourcesPath, "server") : path.join(__dirname, "..", "server-bundle");
}

function bundleVersion(app) {
	try {
		return fs.readFileSync(path.join(bundleDir(app), "VERSION"), "utf8").trim();
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

function command(args) {
	if (process.env.PHARMACYOS_SERVER_CMD) return { file: process.env.PHARMACYOS_SERVER_CMD, args };
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

module.exports = { DISTRO, bundleDir, bundleVersion, toWslPath, parseVersion, compareVersions, run, startKeepAlive, installedVersion };
