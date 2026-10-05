// Bridge between pages and the desktop shell. Pages get a tiny, explicit API — never Node.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("pharmacyosDesktop", {
	isDesktop: true,
	info: () => ipcRenderer.invoke("app:info"),
	// connect/settings screen only (the main process ignores these calls from other pages)
	getConfig: () => ipcRenderer.invoke("config:get"),
	testServer: (url) => ipcRenderer.invoke("config:test", url),
	saveConfig: (values) => ipcRenderer.invoke("config:save", values),
	retry: () => ipcRenderer.invoke("connect:retry"),
	listPrinters: () => ipcRenderer.invoke("printers:list"),
	onProgress: (cb) => ipcRenderer.on("connect:progress", (_e, p) => cb(p)),
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
