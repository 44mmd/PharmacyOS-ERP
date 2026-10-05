// Desktop configuration. Lives in the user's app-data folder (never in the install folder), so
// updates and reinstalls keep it. Holds no passwords: sign-in is the normal PharmacyOS login.
const fs = require("fs");
const path = require("path");

const DEFAULTS = {
	mode: null, // "this-pc" (server runs on this computer) | "network" (pharmacy server on the LAN)
	serverUrl: "http://127.0.0.1", // nginx on the pharmacy server (port 80)
	receiptPrinter: "", // empty = ask with the system print dialog
	silentReceipts: false,
	startPage: "desk", // "pos" opens the PharmacyOS POS screen at start (a counter PC), "desk" the ERP
	wslDistro: "PharmacyOS", // single-PC mode: the bundled server environment
};

function file(userData) {
	return path.join(userData, "config.json");
}

function load(userData) {
	try {
		return { ...DEFAULTS, ...JSON.parse(fs.readFileSync(file(userData), "utf8")) };
	} catch {
		return { ...DEFAULTS };
	}
}

function save(userData, config) {
	const clean = { ...DEFAULTS, ...config };
	clean.serverUrl = normaliseUrl(clean.serverUrl);
	clean.startPage = clean.startPage === "pos" ? "pos" : "desk";
	fs.mkdirSync(userData, { recursive: true });
	const tmp = file(userData) + ".tmp";
	fs.writeFileSync(tmp, JSON.stringify(clean, null, 2));
	fs.renameSync(tmp, file(userData)); // atomic: a crash never leaves half a config
	return clean;
}

// Accepts "192.168.1.10", "192.168.1.10:8000", "https://pharmacy.local"; returns an origin URL.
// No port means the production web server (port 80/443).
function normaliseUrl(value) {
	let v = String(value || "").trim();
	if (!v) return DEFAULTS.serverUrl;
	if (!/^https?:\/\//i.test(v)) v = "http://" + v;
	return new URL(v).origin;
}

function isConfigured(config) {
	return Boolean(config.mode && config.serverUrl);
}

module.exports = { DEFAULTS, load, save, normaliseUrl, isConfigured };
