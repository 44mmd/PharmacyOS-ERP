// First-run setup of a single-PC pharmacy on Windows: checks the PC, asks Windows for administrator
// permission once, and runs deploy/windows/install-server.ps1 (from the server bundle) elevated. That
// script writes its progress to %ProgramData%\PharmacyOS\Setup\state.json, which this module reads for
// the setup screen; it survives a restart (the script continues by itself after sign-in).
//
// The owner's password travels in a request file in this Windows user's own app-data folder (not
// readable by other standard users) and is deleted by the script as soon as the owner account exists —
// and by this app when the script will not read it: Windows' permission prompt was refused or failed, or
// the owner leaves a failed setup to enter the details again (see forgetRequest).
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const localserver = require("./localserver");

function programData() {
	return process.env.ProgramData || process.env.PROGRAMDATA || "C:\\ProgramData";
}
function stateFile() {
	return process.env.PHARMACYOS_SETUP_STATE || path.join(programData(), "PharmacyOS", "Setup", "state.json");
}
function logFile() {
	return path.join(programData(), "PharmacyOS", "Logs", "install.log");
}
function requestFile(userData) {
	return path.join(userData, "setup-request.json");
}
function script(app, name) {
	return path.join(localserver.bundleDir(app), "deploy", "windows", name);
}

// What the setup screen asks; validated again by the server's first-run setup.
function validate(values) {
	const v = values || {};
	const errors = {};
	const text = (s) => String(s || "").trim();
	if (text(v.pharmacy_name).length < 2) errors.pharmacy_name = "required";
	if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text(v.owner_email))) errors.owner_email = "email";
	if (String(v.owner_password || "").length < 8) errors.owner_password = "short";
	if (v.owner_password !== v.owner_password_confirm) errors.owner_password_confirm = "mismatch";
	if (text(v.owner_full_name).length < 2) errors.owner_full_name = "required";
	if (v.phone && !/^[+\d][\d\s-]{5,19}$/.test(text(v.phone))) errors.phone = "phone";
	return errors;
}

function powershell(args, { timeoutMs = 120000 } = {}) {
	return new Promise((resolve) => {
		const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", ...args], { windowsHide: true });
		let out = "";
		let err = "";
		child.stdout.on("data", (d) => (out += d));
		child.stderr.on("data", (d) => (err += d));
		const timer = setTimeout(() => child.kill(), timeoutMs);
		child.on("error", (e) => {
			clearTimeout(timer);
			resolve({ code: -1, out, err: String(e) });
		});
		child.on("close", (code) => {
			clearTimeout(timer);
			resolve({ code, out, err });
		});
	});
}

async function systemCheck(app) {
	if (process.platform !== "win32") return { ok: false, checks: [{ key: "windows", ok: false, level: "error", value: process.platform }] };
	const r = await powershell(["-File", script(app, "system-check.ps1")], { timeoutMs: 90000 });
	try {
		return JSON.parse(r.out.trim().split("\n").pop());
	} catch {
		return { ok: false, checks: [{ key: "check_failed", ok: false, level: "error", value: (r.err || r.out).slice(0, 300) }] };
	}
}

// PowerShell single-quoted string
const psq = (s) => "'" + String(s).replace(/'/g, "''") + "'";

// `launch` is replaceable for tests only (the IPC calls pass just the values).
async function start(app, values, launch = launchElevated) {
	const errors = validate(values);
	if (Object.keys(errors).length) return { ok: false, errors };
	const request = {
		pharmacy_name: String(values.pharmacy_name).trim(),
		pharmacy_name_ar: String(values.pharmacy_name_ar || "").trim(),
		owner_full_name: String(values.owner_full_name).trim(),
		owner_email: String(values.owner_email).trim().toLowerCase(),
		owner_password: String(values.owner_password),
		phone: String(values.phone || "").trim(),
		share_on_network: Boolean(values.share_on_network),
	};
	const file = requestFile(app.getPath("userData"));
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, JSON.stringify(request), { encoding: "utf8", mode: 0o600 });
	const r = await launch(app, file);
	if (!r.ok) forgetRequest(app); // the script never started (pressing Install again writes it anew)
	return r;
}

// Deletes the request file (the owner's password), except while Windows restarts in the middle of the
// setup: the script continues after the next sign-in and reads it then.
function forgetRequest(app) {
	const state = readState();
	if (state && state.status === "reboot_required") return false;
	try {
		fs.rmSync(requestFile(app.getPath("userData")), { force: true });
		return true;
	} catch {
		return false;
	}
}

// Asks Windows for administrator permission (one UAC prompt) and starts the setup script hidden.
async function launchElevated(app, file) {
	let previous = null;
	let wrote = false;
	try {
		previous = fs.existsSync(stateFile()) ? fs.readFileSync(stateFile()) : null;
		fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
		fs.writeFileSync(stateFile(), JSON.stringify({ version: 1, step: "check", status: "starting", percent: 0, updated: new Date().toISOString() }));
		wrote = true;
	} catch {
		/* ProgramData not writable without elevation: the script creates it */
	}
	const argv = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script(app, "install-server.ps1"), "-AppExe", process.execPath];
	if (file) argv.push("-RequestFile", file);
	const argList = argv
		.map((a) => psq(/\s/.test(a) ? `"${a}"` : a))
		.join(",");
	const r = await powershell(["-Command", `Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList ${argList}`]);
	if (r.code !== 0) {
		// the script never started: put back the state it would have replaced (a "starting" left behind
		// would show a setup in progress at the next start, with nothing running)
		if (wrote) {
			try {
				if (previous) fs.writeFileSync(stateFile(), previous);
				else fs.rmSync(stateFile(), { force: true });
			} catch {
				/* the next run of the script rewrites it */
			}
		}
		return { ok: false, denied: /canceled|cancelled|operation was canceled/i.test(r.err), error: r.err.slice(0, 400) };
	}
	return { ok: true };
}

// Retry of a failed setup with the details already given. A refused Windows prompt keeps them, so the
// next Retry can still work; "Enter the details again" (or leaving setup) forgets them.
async function resume(app, launch = launchElevated) {
	const file = requestFile(app.getPath("userData"));
	return launch(app, fs.existsSync(file) ? file : "");
}

function readState() {
	try {
		return JSON.parse(fs.readFileSync(stateFile(), "utf8").replace(/^\uFEFF/, ""));
	} catch {
		return null;
	}
}

function logTail(lines = 40) {
	try {
		const text = fs.readFileSync(logFile(), "utf8").replace(/^\uFEFF/, "");
		return text.split(/\r?\n/).slice(-lines).join("\n");
	} catch {
		return "";
	}
}

function inProgress(state) {
	return Boolean(state && ["starting", "running", "reboot_required", "failed"].includes(state.status));
}

module.exports = { validate, systemCheck, start, resume, forgetRequest, readState, logTail, inProgress, stateFile, logFile, requestFile };
