// PharmacyOS POS on the desktop: the same /pos screen the web browser opens, served by the pharmacy
// server. This module holds only the desktop adapter's decisions (pure functions, unit-tested):
// which page the window opens, which URLs may be printed natively, and how.
const START_PATHS = { pos: "/pos", desk: "/desk" };

function startPath(config) {
	return START_PATHS[config && config.startPage] || START_PATHS.desk;
}

// Only the server's own print view may be printed through the native bridge — never another site,
// never a local file.
function isReceiptUrl(url, serverUrl) {
	try {
		const u = new URL(url);
		return u.origin === new URL(serverUrl).origin && u.pathname === "/printview" && u.searchParams.has("name");
	} catch {
		return false;
	}
}

// Silent printing only with a receipt printer chosen in Settings; otherwise the system dialog.
function printOptions(config) {
	if (config && config.silentReceipts && config.receiptPrinter) {
		return { silent: true, deviceName: config.receiptPrinter, printBackground: true };
	}
	return { silent: false, printBackground: true };
}

module.exports = { START_PATHS, startPath, isReceiptUrl, printOptions };
