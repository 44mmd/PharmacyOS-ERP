const test = require("node:test");
const assert = require("node:assert");
const path = require("path");
const { pathToFileURL } = require("url");
const guards = require("../src/guards");

const CONNECT = path.join(__dirname, "..", "src", "connect.html");
const RECEIPT = path.join(__dirname, "..", "src", "test-receipt.html");
const connectUrl = pathToFileURL(CONNECT).href;

// an IPC event as Electron gives it: the sending frame and the window's contents
function ipcEvent({ frameUrl, mainFrame = true, detached = false, noFrame = false }) {
	const frame = { url: frameUrl, detached };
	const main = mainFrame ? frame : { url: connectUrl, detached: false };
	return { senderFrame: noFrame ? null : frame, sender: { mainFrame: main, getURL: () => main.url } };
}

test("only the app's own local pages count as local, with any query string", () => {
	const pages = [CONNECT, RECEIPT];
	assert.ok(guards.isLocalPage(connectUrl, pages));
	assert.ok(guards.isLocalPage(`${connectUrl}?view=update&installed=1.0.0`, pages));
	assert.ok(guards.isLocalPage(`${connectUrl}#top`, pages));
	assert.ok(guards.isLocalPage(pathToFileURL(RECEIPT).href, pages));
	assert.ok(!guards.isLocalPage(pathToFileURL(RECEIPT).href, [CONNECT]), "the test receipt is not the local screen");
	assert.ok(!guards.isLocalPage(pathToFileURL(path.join(__dirname, "connect.html")).href, pages), "another folder");
	assert.ok(!guards.isLocalPage(pathToFileURL(path.join(path.dirname(CONNECT), "main.js")).href, pages), "another file");
	assert.ok(!guards.isLocalPage(`${connectUrl}.evil.html`, pages));
	assert.ok(!guards.isLocalPage("file:///C:/Users/x/Downloads/connect.html", pages), "a name containing connect.html is not enough");
	assert.ok(!guards.isLocalPage("http://192.168.1.10/connect.html?file:///connect.html", pages), "a server page never");
	assert.ok(!guards.isLocalPage("file:///etc/passwd", pages));
	assert.ok(!guards.isLocalPage("not a url", pages));
	assert.ok(!guards.isLocalPage(connectUrl, []));
});

test("an installed app's page matches however Windows spells the path", () => {
	const page = "C:\\Program Files\\PharmacyOS ERP\\resources\\app.asar\\src\\connect.html";
	const win = (url) => guards.isLocalPage(url, [page], "win32");
	assert.ok(win("file:///C:/Program%20Files/PharmacyOS%20ERP/resources/app.asar/src/connect.html?view=welcome"));
	assert.ok(win("file:///c:/program%20files/pharmacyos%20erp/resources/app.asar/src/connect.html"), "Windows paths ignore case");
	assert.ok(!win("file:///C:/Program%20Files/PharmacyOS%20ERP/resources/app.asar/src/other.html"));
	assert.ok(!win("file:///D:/Program%20Files/PharmacyOS%20ERP/resources/app.asar/src/connect.html"), "another drive");
	// a folder name chosen at install time: Arabic, %, #, braces
	const odd = "D:\\برامج\\50% {x} #1\\src\\connect.html";
	const oddUrl = pathToFileURL(odd, { windows: true }).href;
	assert.ok(guards.isLocalPage(`${oddUrl}?view=x`, [odd], "win32"));
	assert.ok(guards.isLocalPage("file:///D:/%D8%A8%D8%B1%D8%A7%D9%85%D8%AC/50%25%20%7Bx%7D%20%231/src/connect.html", [odd], "win32"));
	assert.ok(!guards.isLocalPage("file:///D:/%D8%A8%D8%B1%D8%A7%D9%85%D8%AC/50%25%20%7Bx%7D%20/src/connect.html", [odd], "win32"));
	// Linux paths keep their case
	if (process.platform !== "win32") assert.ok(!guards.isLocalPage(connectUrl.toUpperCase().replace("FILE:", "file:"), [CONNECT], "linux"));
});

test("privileged calls are accepted only from the local screen's own main frame", () => {
	assert.ok(guards.isFromLocalScreen(ipcEvent({ frameUrl: `${connectUrl}?view=welcome` }), CONNECT));
	assert.ok(!guards.isFromLocalScreen(ipcEvent({ frameUrl: connectUrl, noFrame: true }), CONNECT), "no sending frame (navigated or destroyed)");
	assert.ok(!guards.isFromLocalScreen(ipcEvent({ frameUrl: connectUrl, detached: true }), CONNECT), "a detached frame");
	assert.ok(!guards.isFromLocalScreen(ipcEvent({ frameUrl: connectUrl, mainFrame: false }), CONNECT), "an iframe, even of the same page");
	// the server page's pagehide handler while the window already shows connect.html
	const pagehide = ipcEvent({ frameUrl: "http://192.168.1.10/app", mainFrame: false });
	assert.equal(pagehide.sender.getURL(), connectUrl);
	assert.ok(!guards.isFromLocalScreen(pagehide, CONNECT));
	assert.ok(!guards.isFromLocalScreen(ipcEvent({ frameUrl: "http://192.168.1.10/app" }), CONNECT), "a server page");
	assert.ok(!guards.isFromLocalScreen(ipcEvent({ frameUrl: pathToFileURL(RECEIPT).href }), CONNECT), "another local page");
	assert.ok(!guards.isFromLocalScreen({}, CONNECT));
	assert.ok(!guards.isFromLocalScreen(null, CONNECT));
});

test("only web and e-mail links are handed to the system", () => {
	assert.equal(guards.externalUrl("https://pharmacyos.example/help"), "https://pharmacyos.example/help");
	assert.equal(guards.externalUrl("http://example.com"), "http://example.com/");
	assert.equal(guards.externalUrl("HTTPS://Example.com/A"), "https://example.com/A");
	assert.equal(guards.externalUrl("mailto:support@example.com"), "mailto:support@example.com");
	for (const url of [
		"search-ms:query=invoice&crumb=location:\\\\evil\\share",
		"ms-word:ofe|u|https://evil.example/a.docx",
		"ms-settings:",
		"ms-msdt:/id PCWDiagnostic",
		"file:///C:/Windows/System32/calc.exe",
		"javascript:alert(1)",
		"data:text/html,<script>alert(1)</script>",
		"vbscript:msgbox(1)",
		"\\\\evil\\share\\x.exe",
		"",
		"not a url",
	]) {
		assert.equal(guards.externalUrl(url), null, url);
	}
});

test("the server origin is the scheme, host and port", () => {
	const server = "http://192.168.1.10";
	assert.ok(guards.sameOrigin("http://192.168.1.10/app/sales-invoice", server));
	assert.ok(!guards.sameOrigin("http://192.168.1.10:8080/app", server));
	assert.ok(!guards.sameOrigin("https://192.168.1.10/app", server));
	assert.ok(!guards.sameOrigin("http://192.168.1.100/app", server));
	assert.ok(!guards.sameOrigin(connectUrl, server));
	assert.ok(!guards.sameOrigin("data:text/html,x", "data:text/html,y"), "opaque origins never match");
	assert.ok(!guards.sameOrigin("garbage", server));
});
