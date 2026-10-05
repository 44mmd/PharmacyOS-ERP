// PharmacyOS POS — platform adapter.
//
// The POS screen (pos.js) is one implementation for every client. This file is the only place where
// the browser and the Windows desktop app differ:
//
// * browser: receipts print through the browser's own print dialog (the cashier chooses the printer;
//   a browser cannot print silently or talk to a thermal printer directly);
// * desktop: the PharmacyOS desktop app's preload bridge (`window.pharmacyosDesktop`) prints the
//   receipt natively — silently to the receipt printer chosen in the app's settings, or with the
//   system dialog when none is chosen. Future device bridges (scanner, cash drawer) belong here too.
//
// Barcode scanners need nothing platform-specific: they type like a keyboard on both.
"use strict";
(function () {
	var bridge = window.pharmacyosDesktop;
	var desktop = bridge && bridge.isDesktop ? bridge : null;

	window.PharmacyOSPlatform = {
		kind: desktop ? "desktop" : "browser",
		nativePrint: Boolean(desktop && typeof desktop.printReceipt === "function"),
		// Resolves {ok, silent, error}. Only used when nativePrint is true.
		printReceipt: function (url) {
			if (!this.nativePrint) return Promise.resolve({ ok: false, error: "no native printing" });
			return desktop.printReceipt(new URL(url, window.location.href).href);
		},
	};
})();
