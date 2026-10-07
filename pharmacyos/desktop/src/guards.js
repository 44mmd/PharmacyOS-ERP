// Who may use the desktop shell, and where its windows may go (pure functions, unit-tested).
//
// * Privileged calls (settings, setup, server update, backups…) are accepted only from the app's own
//   local screen: the SENDING frame must be the window's main frame, still attached, showing exactly
//   connect.html. The window's current URL is not enough: a server page that is being replaced by the
//   local screen (pagehide) still sends with its own code while the window already shows connect.html.
// * Navigation: a window stays on the pharmacy server (the main window also on the app's own pages);
//   links elsewhere open in the system browser — web and e-mail links only, never another Windows
//   protocol handler (search-ms:, ms-word:, …).
"use strict";
const { pathToFileURL } = require("url");

const EXTERNAL_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

// The same scheme, host and port; opaque origins (file:, data:, about:…) never match anything.
function sameOrigin(url, serverUrl) {
	try {
		const origin = new URL(url).origin;
		return origin !== "null" && origin === new URL(serverUrl).origin;
	} catch {
		return false;
	}
}

// A file:// URL in one spelling: no query string or fragment, percent-escapes decoded, the drive letter in
// capitals (Chromium writes C:), and — Windows paths ignore case — lower case on Windows.
function fileKey(url, platform) {
	let u;
	try {
		u = new URL(url);
	} catch {
		return null;
	}
	if (u.protocol !== "file:") return null;
	u.search = "";
	u.hash = "";
	let key = u.href.replace(/%(?![0-9A-Fa-f]{2})/g, "%25");
	try {
		key = decodeURI(key);
	} catch {
		/* keep the escapes */
	}
	key = key.replace(/^file:\/\/\/([a-z]):/, (_m, d) => `file:///${d.toUpperCase()}:`);
	return platform === "win32" ? key.toLowerCase() : key;
}

// Exactly one of the app's own pages (file paths), with any query string: connect.html?view=… is the
// local screen; any other file — or a URL that merely contains "connect.html" — is not.
function isLocalPage(url, pages, platform = process.platform) {
	const key = fileKey(url, platform);
	return Boolean(key) && pages.some((page) => fileKey(pathToFileURL(page, { windows: platform === "win32" }).href, platform) === key);
}

// An IPC event sent by the local screen itself: the main frame of the window (not an iframe, not a page
// that is being navigated away), currently showing `page`.
function isFromLocalScreen(event, page, platform = process.platform) {
	const frame = event && event.senderFrame;
	if (!frame || frame.detached || !event.sender || frame !== event.sender.mainFrame) return false;
	return isLocalPage(frame.url, [page], platform);
}

// The link to hand to the system browser / mail app, or null: http, https and mailto only.
function externalUrl(url) {
	try {
		const u = new URL(url);
		return EXTERNAL_PROTOCOLS.has(u.protocol) ? u.href : null;
	} catch {
		return null;
	}
}

module.exports = { sameOrigin, isLocalPage, isFromLocalScreen, externalUrl };
