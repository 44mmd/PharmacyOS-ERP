// Bridge between pages and the desktop shell. Pages get a tiny, explicit API — never Node.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("pharmacyosDesktop", {
	isDesktop: true,
	info: () => ipcRenderer.invoke("app:info"),
	// the app's own local screens only (the main process ignores these calls from any other page)
	getConfig: () => ipcRenderer.invoke("config:get"),
	testServer: (url) => ipcRenderer.invoke("config:test", url),
	saveConfig: (values) => ipcRenderer.invoke("config:save", values),
	retry: () => ipcRenderer.invoke("connect:retry"),
	openApp: () => ipcRenderer.invoke("app:open"),
	listPrinters: () => ipcRenderer.invoke("printers:list"),
	testPrinter: () => ipcRenderer.invoke("printers:test"),
	onStartup: (cb) => ipcRenderer.on("startup:progress", (_e, p) => cb(p)),
	setup: {
		check: () => ipcRenderer.invoke("setup:check"),
		start: (values) => ipcRenderer.invoke("setup:start", values),
		resume: () => ipcRenderer.invoke("setup:resume"),
		state: () => ipcRenderer.invoke("setup:state"),
		reboot: () => ipcRenderer.invoke("setup:reboot"),
		finish: (serverUrl) => ipcRenderer.invoke("setup:finish", serverUrl),
	},
	server: {
		start: () => ipcRenderer.invoke("server:start"),
		update: () => ipcRenderer.invoke("server:update"),
		continue: () => ipcRenderer.invoke("server:continue"),
		onLine: (cb) => ipcRenderer.on("server:line", (_e, line) => cb(line)),
	},
	backups: {
		list: () => ipcRenderer.invoke("backups:list"),
		create: () => ipcRenderer.invoke("backups:create"),
		restore: (folder, withFiles) => ipcRenderer.invoke("backups:restore", folder, Boolean(withFiles)),
		open: () => ipcRenderer.invoke("backups:open"),
	},
	// PharmacyOS POS (/pos): native receipt printing — silently to the receipt printer chosen in
	// Settings, or with the system print dialog. The main process accepts only the server's own
	// print view, from the server's own pages.
	printReceipt: (url) => ipcRenderer.invoke("pos:print-receipt", String(url)),
});

// Connection banner for PharmacyOS pages: shown only while the server is unreachable.
ipcRenderer.on("connection:changed", (_e, { online }) => {
	const id = "pharmacyos-desktop-offline";
	let el = document.getElementById(id);
	if (online) {
		el && el.remove();
		return;
	}
	if (el) return;
	const ar = document.documentElement.lang === "ar";
	el = document.createElement("div");
	el.id = id;
	el.setAttribute("role", "alert");
	el.style.cssText =
		"position:fixed;inset-inline:0;top:0;z-index:99999;padding:8px 16px;background:#B45309;color:#fff;" +
		"font:500 13px/1.5 Tajawal,Inter,sans-serif;text-align:center";
	el.textContent = ar
		? "انقطع الاتصال بخادم الصيدلية. لن تُحفظ العمليات حتى يعود الاتصال — لا تعِد إدخال البيع."
		: "Connection to the pharmacy server was lost. Nothing can be saved until it returns — do not re-enter the sale.";
	document.body.appendChild(el);
});
