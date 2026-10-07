// PharmacyOS ERP — Windows desktop app (Electron): the LOCAL ERP's window, setup and caretaker.
//
// * One window onto the pharmacy's own server (this PC's PharmacyOS environment or the pharmacy LAN
//   server). The server holds the database; this app stores no pharmacy data and no passwords.
// * First run on a single-PC pharmacy: a setup screen collects the pharmacy's details and installs the
//   server (setup.js → install-server.ps1, one administrator prompt, continues by itself after a restart).
// * Every start: a branded startup screen until the server answers (starting it if needed), a recovery
//   screen if it does not, and — when this app brings a newer server — a guided server update (backup
//   first, automatic rollback). Backups can be taken, found and restored from the app's menu.
// Security: context isolation + sandbox, no Node in pages, navigation locked to the server origin,
// web and e-mail links open in the system browser; privileged calls are accepted only from the local
// screen's own frame (guards.js), never from a server page.
const { app, BrowserWindow, Menu, ipcMain, shell, dialog, session } = require("electron");
const path = require("path");
const config = require("./config");
const guards = require("./guards");
const server = require("./server");
const pos = require("./pos");
const setup = require("./setup");
const localserver = require("./localserver");

const CONNECT_PAGE = path.join(__dirname, "connect.html");
const TEST_RECEIPT = path.join(__dirname, "test-receipt.html");
const LOCAL_PAGES = [CONNECT_PAGE, TEST_RECEIPT]; // the only files the main window may show
const PING_EVERY_MS = 30000; // quiet health check; only a failure is shown
const START_ATTEMPTS = Number(process.env.PHARMACYOS_START_ATTEMPTS) || 90; // × 2 s: a cold Windows boot can take a few minutes to bring the server up
let win = null;
let cfg = null;
let online = null;
let lastAppUrl = null;
let monitor = null;
let busy = null; // the server operation running ("update", "restore", "backup", "start"): one at a time, no navigation away from the local screen

if (!app.requestSingleInstanceLock()) {
	app.quit(); // a second launch focuses the existing window instead of opening another
} else {
	app.on("second-instance", () => {
		if (win) {
			if (win.isMinimized()) win.restore();
			win.show();
			win.focus();
		}
	});
	app.whenReady().then(start);
}

app.on("window-all-closed", () => app.quit());

function isServerUrl(url) {
	return Boolean(cfg) && guards.sameOrigin(url, cfg.serverUrl);
}

// The main window shows the local screen (startup, recovery, settings…), not a server page.
function onLocalScreen() {
	return guards.isLocalPage(win.webContents.getURL(), [CONNECT_PAGE]);
}

// A privileged IPC call: from the local screen's own main frame, in the main window — not from a server
// page (also not one being navigated away, nor after history.back()).
function fromLocalScreen(event) {
	return Boolean(win) && event.sender === win.webContents && guards.isFromLocalScreen(event, CONNECT_PAGE);
}

// Web and e-mail links only; any other scheme (search-ms:, ms-word:, file:…) is dropped.
function openExternal(url) {
	const safe = guards.externalUrl(url);
	if (safe) shell.openExternal(safe);
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
	guard(win.webContents, LOCAL_PAGES);
	win.webContents.on("did-navigate", (_e, url) => {
		if (!isServerUrl(url)) return;
		lastAppUrl = url;
		forgetLocalScreens(win.webContents);
	});
	win.webContents.on("did-fail-load", (_e, code, _desc, url, isMainFrame) => {
		if (isMainFrame && code !== -3 && isServerUrl(url)) showScreen("recovery");
	});
	win.on("page-title-updated", (e) => e.preventDefault()); // the window is always "PharmacyOS ERP"
	win.on("close", (e) => {
		if (!busy || busy === "start") return;
		e.preventDefault(); // never interrupt an update or a restore
		dialog.showMessageBox(win, {
			type: "warning",
			title: "PharmacyOS ERP",
			message: "PharmacyOS is updating or restoring the pharmacy server. Please wait until it finishes.\nيجري تحديث خادم الصيدلية أو استعادته. يرجى الانتظار حتى ينتهي.",
		});
	});
}

// Lock a window to the PharmacyOS server (the main window also to the app's own pages, exactly); links
// elsewhere go to the system browser. Print previews open as in-app windows and print with the normal
// dialog: only the POS receipt (pos:print-receipt) prints silently to the receipt printer.
function guard(contents, localPages = []) {
	const leave = (event, url) => {
		if (isServerUrl(url) || guards.isLocalPage(url, localPages)) return;
		event.preventDefault();
		openExternal(url);
	};
	// links and location changes of the window itself (frames inside a server page are the page's own)
	contents.on("will-frame-navigate", (details) => details.isMainFrame && leave(details, details.url));
	contents.on("will-navigate", (details) => leave(details, details.url));
	// a server-side redirect cannot take the window off the server either (also for the app's own loads)
	contents.on("will-redirect", (details) => details.isMainFrame && leave(details, details.url));
	contents.setWindowOpenHandler(({ url }) => {
		if (isServerUrl(url)) {
			return {
				action: "allow",
				overrideBrowserWindowOptions: { autoHideMenuBar: true, title: "PharmacyOS ERP", webPreferences: webPreferences() },
			};
		}
		openExternal(url);
		return { action: "deny" };
	});
	contents.on("did-create-window", (child) => guard(child.webContents));
}

// history.back() on a server page must never return to a local screen: once a server page is shown,
// the window's history holds no local screen.
function forgetLocalScreens(contents) {
	const history = contents.navigationHistory;
	if (history.getAllEntries().some((entry) => !isServerUrl(entry.url))) history.clear();
}

// Prints a page from a hidden window that shares the signed-in session (same partition), so the
// server renders it with the cashier's own permissions — exactly what the browser preview shows.
function printPage(url, { local = false } = {}) {
	return new Promise((resolve) => {
		const printer = new BrowserWindow({ show: false, webPreferences: webPreferences() });
		guard(printer.webContents);
		const done = (result) => {
			if (!printer.isDestroyed()) printer.close();
			resolve(result);
		};
		printer.webContents.once("did-fail-load", (_e, _code, description) => done({ ok: false, error: description }));
		printer.webContents.once("did-finish-load", () => {
			const options = pos.printOptions(cfg);
			printer.webContents.print(options, (ok, reason) => done({ ok, silent: options.silent, error: ok ? null : reason }));
		});
		if (local) printer.loadFile(url);
		else printer.loadURL(url);
	});
}

function openPath(pathname) {
	if (!cfg || !config.isConfigured(cfg) || !online || busy) return;
	win.loadURL(new URL(pathname, cfg.serverUrl).href);
}

function showScreen(view, query = {}) {
	if (["recovery", "starting", "update", "install", "welcome"].includes(view)) online = false;
	win.loadFile(CONNECT_PAGE, { query: { view, ...query } });
}

function isThisPc() {
	return cfg && cfg.mode === "this-pc";
}

// Brings the server up (single-PC: starts it if needed), then opens the app — or the recovery screen.
async function openApp() {
	showScreen("starting");
	let ok = await server.ping(cfg.serverUrl);
	if (!ok && isThisPc()) {
		await localserver.startKeepAlive();
		ok = await server.waitFor(cfg.serverUrl, {
			attempts: START_ATTEMPTS,
			onAttempt: (i, n) => win.webContents.send("startup:progress", { attempt: i, of: n }),
		});
	}
	if (!ok) return showScreen("recovery");
	online = true;
	if (isThisPc()) {
		const pending = await serverUpdateAvailable();
		if (pending) return showScreen("update", pending);
	}
	win.loadURL(lastAppUrl && isServerUrl(lastAppUrl) ? lastAppUrl : new URL(pos.startPath(cfg), cfg.serverUrl).href);
}

// A newer PharmacyOS ERP bundled with this app than the one installed on this PC's server — or the same
// version built from other files (a corrected installer; see localserver.offersUpdate).
async function serverUpdateAvailable() {
	const bundled = localserver.bundleVersion(app);
	if (!bundled) return null;
	const installed = await localserver.installedVersion();
	if (!installed) return null;
	const bundledBuild = localserver.bundleBuild(app);
	const sameVersion = localserver.compareVersions(bundled, installed) === 0;
	const installedBuild = sameVersion && bundledBuild ? await localserver.installedBuild() : null;
	if (!localserver.offersUpdate({ bundled, installed, bundledBuild, installedBuild })) return null;
	if (!sameVersion) return { installed, bundled };
	// the same version on both sides: the update screen tells the builds apart
	const short = (build) => (/^[0-9a-f]{64}$/i.test(build || "") ? build.slice(0, 8) : "?");
	return { installed: `${installed} (${short(installedBuild)})`, bundled: `${bundled} (${short(bundledBuild)})` };
}

function startMonitor() {
	clearInterval(monitor);
	monitor = setInterval(async () => {
		if (!cfg || !config.isConfigured(cfg) || onLocalScreen()) return;
		const ok = await server.ping(cfg.serverUrl);
		if (ok !== online) {
			online = ok;
			// a banner inside the page; the page is not reloaded, so nothing on screen is lost
			win.webContents.send("connection:changed", { online: ok });
		}
	}, PING_EVERY_MS);
}

async function showAbout() {
	const installed = isThisPc() ? await localserver.installedVersion() : null;
	dialog.showMessageBox(win, {
		type: "info",
		title: "PharmacyOS ERP",
		message: `PharmacyOS ERP ${app.getVersion()}`,
		detail: [
			"Pharmacy Operating System — LOCAL ERP",
			`Desktop app: ${app.getVersion()}`,
			`Server bundled with this app: ${localserver.bundleVersion(app) || "—"}`,
			isThisPc() ? `Server on this PC: ${installed || "not running"}` : `Pharmacy server: ${cfg ? cfg.serverUrl : "—"}`,
			"Built on ERPNext and the Frappe Framework (GPL-3.0). Designed & developed by HALF.",
		].join("\n"),
	});
}

function buildMenu() {
	// no Reload in the installed app: reloading the local screen during an update or a restore would offer
	// to start it again (the main process refuses that too: one server operation at a time)
	const template = [
		{
			label: "PharmacyOS ERP",
			submenu: [
				{ label: "Point of Sale", accelerator: "CmdOrCtrl+Shift+P", click: () => openPath(pos.START_PATHS.pos) },
				{ label: "ERP", click: () => openPath(pos.START_PATHS.desk) },
				{ type: "separator" },
				{ label: "Backups…", click: () => !busy && showScreen("backups") },
				{ label: "Connection & Printer…", click: () => !busy && showScreen("settings") },
				{ label: "Print a test receipt", click: () => printPage(TEST_RECEIPT, { local: true }) },
				{ type: "separator" },
				{ label: "About PharmacyOS ERP", click: showAbout },
				{ type: "separator" },
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
	if (!app.isPackaged) template.push({ label: "Developer", submenu: [{ role: "reload" }, { role: "toggleDevTools" }] });
	Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// WSL paths of the server (/mnt/c/ProgramData/…) as Windows paths for people (C:\ProgramData\…)
function windowsPath(p) {
	const m = /^\/mnt\/([a-z])\/(.*)$/.exec(String(p || ""));
	return m && process.platform === "win32" ? `${m[1].toUpperCase()}:\\${m[2].replace(/\//g, "\\")}` : p;
}

// One server operation at a time. A second request while one runs — a double click, or the local screen
// opened again meanwhile — is refused without starting anything; the screen then asks to wait.
function exclusive(operation, handler) {
	return async (...args) => {
		if (busy) return { ok: false, busy: true, operation: busy };
		busy = operation;
		try {
			return await handler(...args);
		} finally {
			busy = null;
		}
	};
}

function registerIpc() {
	const local = (handler) => (event, ...args) => (fromLocalScreen(event) ? handler(...args) : null);

	// connection and printer settings
	ipcMain.handle("config:get", local(() => cfg));
	ipcMain.handle("config:test", local(async (url) => {
		try {
			return await server.ping(config.normaliseUrl(url));
		} catch {
			return false;
		}
	}));
	ipcMain.handle("config:save", local(async (values) => {
		cfg = config.save(app.getPath("userData"), { ...cfg, ...values });
		openApp();
		return cfg;
	}));
	ipcMain.handle("connect:retry", local(() => openApp()));
	ipcMain.handle("printers:list", async (event) =>
		fromLocalScreen(event) ? (await event.sender.getPrintersAsync()).map((p) => p.name) : []
	);
	ipcMain.handle("printers:test", local(() => printPage(TEST_RECEIPT, { local: true })));
	ipcMain.handle("app:info", () => ({
		version: app.getVersion(),
		platform: process.platform,
		serverVersion: localserver.bundleVersion(app),
		mode: cfg && cfg.mode,
	}));
	ipcMain.handle("app:busy", local(() => ({ busy: Boolean(busy), operation: busy })));

	// first-run setup of this PC as the pharmacy server
	ipcMain.handle("setup:check", local(() => setup.systemCheck(app)));
	ipcMain.handle("setup:start", local((values) => setup.start(app, values)));
	ipcMain.handle("setup:resume", local(() => setup.resume(app)));
	ipcMain.handle("setup:state", local(() => ({ state: setup.readState(), log: setup.logTail(30) })));
	ipcMain.handle("setup:forget", local(() => setup.forgetRequest(app)));
	ipcMain.handle("setup:reboot", local(() => {
		require("child_process").spawn("shutdown.exe", ["/r", "/t", "10", "/c", "PharmacyOS setup continues after the restart."], { windowsHide: true, detached: true }).unref();
		return true;
	}));
	ipcMain.handle("setup:finish", local((serverUrl) => {
		cfg = config.save(app.getPath("userData"), { ...cfg, mode: "this-pc", serverUrl: serverUrl || "http://127.0.0.1" });
		openApp();
		return cfg;
	}));

	// server on this PC: start, update, back up, restore
	ipcMain.handle("server:start", local(exclusive("start", async () => {
		await localserver.run(["start"], { timeoutMs: 180000 });
		openApp();
	})));
	ipcMain.handle("server:update", local(exclusive("update", async () => {
		const bundle = localserver.toWslPath(localserver.bundleDir(app));
		return await localserver.run(["update", bundle], { onLine: (line) => win.webContents.send("server:line", line) });
	})));
	ipcMain.handle("server:continue", local(() => {
		online = true;
		win.loadURL(new URL(pos.startPath(cfg), cfg.serverUrl).href);
	}));
	ipcMain.handle("backups:list", local(async () => {
		const r = await localserver.run(["list-backups"], { timeoutMs: 120000 });
		try {
			const rows = JSON.parse(r.output.trim().split("\n").pop());
			return { ok: true, rows: rows.map((b) => ({ ...b, display: windowsPath(b.folder) })) };
		} catch {
			return { ok: false, error: r.output.slice(-400) };
		}
	}));
	ipcMain.handle("backups:create", local(exclusive("backup", async () => {
		const r = await localserver.run(["backup"], { timeoutMs: 900000 });
		return { ok: r.code === 0, folder: r.result && windowsPath(r.result.folder), error: r.code === 0 ? null : r.output.slice(-400) };
	})));
	ipcMain.handle("backups:restore", local(exclusive("restore", async (folder, withFiles) => {
		const args = ["restore", String(folder)];
		if (withFiles) args.push("--with-files");
		const r = await localserver.run(args, { onLine: (line) => win.webContents.send("server:line", line) });
		return { ok: r.code === 0 && r.result && r.result.status === "restored", error: r.code === 0 ? null : r.output.slice(-600) };
	})));
	ipcMain.handle("backups:open", local(() => shell.openPath(path.join(process.env.ProgramData || "C:\\ProgramData", "PharmacyOS", "Backups"))));
	ipcMain.handle("app:open", local(() => openApp()));

	// the POS screen's native printing (platform adapter): the server's print view only, asked by a frame
	// of the server itself — the only print that goes silently to the receipt printer
	ipcMain.handle("pos:print-receipt", (event, url) => {
		const frame = event.senderFrame;
		if (!cfg || !frame || frame.detached || !isServerUrl(frame.url) || !pos.isReceiptUrl(url, cfg.serverUrl)) {
			return { ok: false, error: "not allowed" };
		}
		return printPage(url);
	});
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
			console.log(JSON.stringify({ url: win.webContents.getURL(), title: win.getTitle(), online, version: app.getVersion() }));
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
	const state = setup.readState();
	if (process.argv.includes("--resume-setup") || (!config.isConfigured(cfg) && setup.inProgress(state))) {
		showScreen("install");
	} else if (config.isConfigured(cfg)) {
		openApp();
	} else {
		showScreen("welcome");
	}
}
