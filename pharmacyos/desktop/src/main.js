// PharmacyOS ERP — Windows desktop shell (Electron).
//
// One PharmacyOS window onto the pharmacy's own server (this PC or the pharmacy LAN server). The
// server holds the authoritative database; this app stores no pharmacy data and no passwords.
// Security: context isolation + sandbox, no Node in pages, navigation locked to the server origin,
// external links open in the system browser.
const { app, BrowserWindow, Menu, ipcMain, shell, dialog, session } = require("electron");
const path = require("path");
const config = require("./config");
const server = require("./server");

const CONNECT_PAGE = path.join(__dirname, "connect.html");
const PING_EVERY_MS = 30000; // quiet health check; only a failure is shown
let win = null;
let cfg = null;
let online = null;
let lastAppUrl = null;
let monitor = null;

if (!app.requestSingleInstanceLock()) {
	app.quit(); // a second launch focuses the existing window instead of opening another
} else {
	app.on("second-instance", () => {
		if (win) {
			if (win.isMinimized()) win.restore();
			win.focus();
		}
	});
	app.whenReady().then(start);
}

app.on("window-all-closed", () => app.quit());

function isServerUrl(url) {
	try {
		return cfg && new URL(url).origin === new URL(cfg.serverUrl).origin;
	} catch {
		return false;
	}
}

function isConnectPage(contents) {
	return contents.getURL().startsWith("file://") && contents.getURL().includes("connect.html");
}

function webPreferences() {
	return {
		preload: path.join(__dirname, "preload.js"),
		contextIsolation: true,
		sandbox: true,
		nodeIntegration: false,
		spellcheck: false,
		partition: "persist:pharmacyos", // keeps the sign-in session across restarts
	};
}

function createWindow() {
	win = new BrowserWindow({
		width: 1440,
		height: 900,
		minWidth: 380,
		minHeight: 600,
		title: "PharmacyOS ERP",
		backgroundColor: "#F8FBFA",
		icon: path.join(__dirname, "..", "build", "icon.png"),
		autoHideMenuBar: true,
		show: false,
		webPreferences: webPreferences(),
	});
	win.once("ready-to-show", () => {
		win.maximize();
		win.show();
	});
	guard(win.webContents);
	win.webContents.on("did-navigate", (_e, url) => {
		if (isServerUrl(url)) lastAppUrl = url;
	});
	win.webContents.on("did-fail-load", (_e, code, _desc, url, isMainFrame) => {
		if (isMainFrame && code !== -3 && isServerUrl(url)) showConnect("offline");
	});
	win.on("page-title-updated", (e) => e.preventDefault()); // the window is always "PharmacyOS ERP"
}

// Lock navigation to the PharmacyOS server; print previews open as in-app windows.
function guard(contents) {
	contents.on("will-navigate", (event, url) => {
		if (!isServerUrl(url) && !url.startsWith("file://")) {
			event.preventDefault();
			shell.openExternal(url);
		}
	});
	contents.setWindowOpenHandler(({ url }) => {
		if (isServerUrl(url)) {
			return {
				action: "allow",
				overrideBrowserWindowOptions: { autoHideMenuBar: true, title: "PharmacyOS ERP", webPreferences: webPreferences() },
			};
		}
		if (/^https?:/i.test(url)) shell.openExternal(url);
		return { action: "deny" };
	});
	contents.on("did-create-window", (child) => {
		guard(child.webContents);
		child.webContents.on("did-finish-load", () => maybeSilentPrint(child));
	});
}

// Receipts: with a receipt printer chosen in Settings, print previews of POS receipts print
// directly to it; otherwise the normal print dialog is used.
function maybeSilentPrint(child) {
	const url = child.webContents.getURL();
	if (!cfg.silentReceipts || !cfg.receiptPrinter || !url.includes("/printview")) return;
	child.webContents.print({ silent: true, deviceName: cfg.receiptPrinter, printBackground: true }, (ok, reason) => {
		if (!ok) dialog.showErrorBox("PharmacyOS ERP", `Receipt was not printed: ${reason}`);
		else child.close();
	});
}

function showConnect(state) {
	online = false;
	win.loadFile(CONNECT_PAGE, { query: { state } });
}

async function openApp() {
	showConnect("connecting");
	let ok = await server.ping(cfg.serverUrl);
	if (!ok && cfg.mode === "this-pc") {
		await server.startLocalServer(cfg.wslDistro);
		ok = await server.waitFor(cfg.serverUrl, {
			attempts: 45,
			onAttempt: (i, n) => win.webContents.send("connect:progress", { attempt: i, of: n }),
		});
	}
	if (!ok) return showConnect("offline");
	online = true;
	win.loadURL(lastAppUrl && isServerUrl(lastAppUrl) ? lastAppUrl : new URL("/desk", cfg.serverUrl).href);
}

function startMonitor() {
	clearInterval(monitor);
	monitor = setInterval(async () => {
		if (!cfg || !config.isConfigured(cfg) || isConnectPage(win.webContents)) return;
		const ok = await server.ping(cfg.serverUrl);
		if (ok !== online) {
			online = ok;
			// a banner inside the page; the page is not reloaded, so nothing on screen is lost
			win.webContents.send("connection:changed", { online: ok });
		}
	}, PING_EVERY_MS);
}

function buildMenu() {
	const template = [
		{
			label: "PharmacyOS ERP",
			submenu: [
				{ label: "Connection & Printer…", click: () => showConnect("settings") },
				{ type: "separator" },
				{ role: "reload" },
				{ role: "togglefullscreen" },
				{ role: "resetZoom" },
				{ role: "zoomIn" },
				{ role: "zoomOut" },
				{ type: "separator" },
				{ role: "quit" },
			],
		},
		{ label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
	];
	if (!app.isPackaged) template.push({ label: "Developer", submenu: [{ role: "toggleDevTools" }] });
	Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc() {
	const fromConnectPage = (event) => isConnectPage(event.sender);
	ipcMain.handle("config:get", (event) => (fromConnectPage(event) ? cfg : null));
	ipcMain.handle("config:test", async (event, url) => {
		if (!fromConnectPage(event)) return false;
		try {
			return await server.ping(config.normaliseUrl(url));
		} catch {
			return false;
		}
	});
	ipcMain.handle("config:save", async (event, values) => {
		if (!fromConnectPage(event)) return null;
		cfg = config.save(app.getPath("userData"), { ...cfg, ...values });
		openApp();
		return cfg;
	});
	ipcMain.handle("connect:retry", (event) => (fromConnectPage(event) ? openApp() : null));
	ipcMain.handle("printers:list", async (event) =>
		fromConnectPage(event) ? (await event.sender.getPrintersAsync()).map((p) => p.name) : []
	);
	ipcMain.handle("app:info", () => ({ version: app.getVersion(), platform: process.platform }));
}

// Automated smoke test: PHARMACYOS_SMOKE=<png> captures the first loaded page and quits.
function smokeTest() {
	const out = process.env.PHARMACYOS_SMOKE;
	if (!out) return;
	const fs = require("fs");
	win.webContents.on("did-finish-load", () => {
		setTimeout(async () => {
			const image = await win.webContents.capturePage();
			fs.writeFileSync(out, image.toPNG());
			console.log(JSON.stringify({ url: win.webContents.getURL(), title: win.getTitle(), online }));
			app.quit();
		}, Number(process.env.PHARMACYOS_SMOKE_DELAY || 4000));
	});
}

async function start() {
	if (process.env.PHARMACYOS_USER_DATA) app.setPath("userData", process.env.PHARMACYOS_USER_DATA);
	app.setAppUserModelId("com.half.pharmacyos.erp");
	cfg = config.load(app.getPath("userData"));
	// the server's own permissions decide everything; the shell grants no extra web permissions
	session.fromPartition("persist:pharmacyos").setPermissionRequestHandler((_wc, permission, cb) =>
		cb(["notifications", "clipboard-sanitized-write", "fullscreen"].includes(permission))
	);
	buildMenu();
	registerIpc();
	createWindow();
	smokeTest();
	startMonitor();
	if (config.isConfigured(cfg)) openApp();
	else showConnect("setup");
}
