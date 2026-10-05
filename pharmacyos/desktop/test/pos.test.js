const test = require("node:test");
const assert = require("node:assert");
const pos = require("../src/pos");
const config = require("../src/config");

test("the window opens the POS or the ERP, the ERP by default", () => {
	assert.equal(pos.startPath({ startPage: "pos" }), "/pos");
	assert.equal(pos.startPath({ startPage: "desk" }), "/desk");
	assert.equal(pos.startPath({}), "/desk");
	assert.equal(pos.startPath({ startPage: "https://evil.example" }), "/desk");
	assert.equal(config.DEFAULTS.startPage, "desk");
});

test("only the server's own print view can be printed natively", () => {
	const server = "http://192.168.1.10";
	assert.ok(pos.isReceiptUrl("http://192.168.1.10/printview?doctype=Sales%20Invoice&name=SINV-1&format=PharmacyOS%20Receipt", server));
	assert.ok(!pos.isReceiptUrl("http://192.168.1.10/printview?doctype=Sales%20Invoice", server), "a document is required");
	assert.ok(!pos.isReceiptUrl("http://192.168.1.10/app/sales-invoice/SINV-1?name=x", server));
	assert.ok(!pos.isReceiptUrl("http://evil.example/printview?name=x", server));
	assert.ok(!pos.isReceiptUrl("http://192.168.1.10:8080/printview?name=x", server), "another port is another origin");
	assert.ok(!pos.isReceiptUrl("file:///C:/Windows/printview?name=x", server));
	assert.ok(!pos.isReceiptUrl("not a url", server));
});

test("silent printing needs a chosen receipt printer", () => {
	assert.deepEqual(pos.printOptions({ silentReceipts: true, receiptPrinter: "EPSON TM-T20" }), {
		silent: true,
		deviceName: "EPSON TM-T20",
		printBackground: true,
	});
	assert.equal(pos.printOptions({ silentReceipts: true, receiptPrinter: "" }).silent, false);
	assert.equal(pos.printOptions({ silentReceipts: false, receiptPrinter: "EPSON" }).silent, false);
});
