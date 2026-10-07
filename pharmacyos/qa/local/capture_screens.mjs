// A23: screenshots of the real LOCAL ERP, driven like a person would (Playwright + Chromium against the QA
// test server). QA-only. Every screen is the running application; nothing is mocked or edited.
//
//   QA_BASE=http://127.0.0.1 node capture_screens.mjs [only-prefix]
//
// Writes PNGs to docs/qa/local/screenshots/ and a log of what each screen showed (screens.json).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(process.env.PW_MODULES || "/tmp/claude-0/e2e/node_modules/");
const { chromium } = require("playwright");
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "../../docs/qa/local/screenshots");
const B = process.env.QA_BASE || "http://127.0.0.1";
const PWD = "QaStaff-2026!";
const OWNER = ["owner@qa-pharmacy.test", process.env.QA_OWNER_PWD || "QaOwner-2026!"];
const ONLY = process.argv[2] || "";
fs.mkdirSync(OUT, { recursive: true });
const record = [];
const browser = await chromium.launch({ executablePath: process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });

async function session(user, password, { width = 1440, height = 900 } = {}) {
	const ctx = await browser.newContext({ viewport: { width, height }, locale: "ar" });
	const p = await ctx.newPage();
	p.errors = [];
	p.on("pageerror", (e) => p.errors.push(e.message));
	p.on("console", (m) => /Content Security Policy|Refused to/.test(m.text()) && p.errors.push("CSP: " + m.text().slice(0, 200)));
	if (user) {
		await p.goto(B + "/login");
		await p.fill("#login_email", user);
		await p.fill("#login_password", password);
		await p.click(".btn-login");
		await p.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });
	}
	return p;
}

async function shot(p, name, note = "", { fullPage = false } = {}) {
	if (ONLY && !name.startsWith(ONLY)) return;
	await p.waitForTimeout(900);
	const file = path.join(OUT, `${name}.png`);
	await p.screenshot({ path: file, fullPage });
	const text = (await p.evaluate(() => document.body.innerText).catch(() => "")).replace(/\s+/g, " ").slice(0, 220);
	record.push({ screen: name, url: p.url(), note, visible_text: text, page_errors: [...p.errors] });
	console.log("shot", name, p.errors.length ? `(errors: ${p.errors.join(" | ").slice(0, 200)})` : "");
}

async function desk(p, route, wait = 2500) {
	await p.goto(B + "/app/" + route);
	await p.waitForLoadState("networkidle").catch(() => {});
	await p.waitForTimeout(wait);
}

const dialogText = async (p) => (await p.textContent(".px-dialog").catch(() => "")).replace(/\s+/g, " ").slice(0, 300);
const scan = async (p, code, suffix = "Enter") => {
	await p.keyboard.type(code, { delay: 5 });
	await p.keyboard.press(suffix);
	await p.waitForTimeout(1000);
};
async function api(p, method, args = {}) {
	return p.evaluate(
		async ([m, a]) => {
			const r = await fetch("/api/method/" + m, { method: "POST", headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.frappe?.csrf_token || window.csrf_token || "" }, body: JSON.stringify(a) });
			const j = await r.json();
			if (!r.ok) throw new Error(JSON.stringify(j).slice(0, 300));
			return j.message;
		},
		[method, args],
	);
}

// ------------------------------------------------------------------ guest
{
	const p = await session(null);
	await p.goto(B + "/login");
	await shot(p, "05-login", "Frappe sign-in page, PharmacyOS branded");
	await p.context().close();
}

// ------------------------------------------------------------------ cashier: a counter shift
const cashier = await session("cashier@qa-pharmacy.test", PWD);
{
	const p = cashier;
	await p.goto(B + "/pos");
	await p.waitForTimeout(3000);
	if (await p.isVisible(".px-dialog input[data-mode]")) {
		await p.fill(".px-dialog input[data-mode]", "25000");
		await shot(p, "19-shift-open", "opening float 25,000 IQD typed before opening the shift");
		await p.click(".px-dialog .px-btn-primary");
		await p.waitForTimeout(3000);
	}
	await shot(p, "07-pos", "the counter screen, shift open, empty cart");
	await p.click("body", { position: { x: 1200, y: 650 } });
	await scan(p, "6291100000011"); // Panadol 500 (HID scanner: keystrokes + Enter)
	await scan(p, "6291100000011"); // same medicine again → quantity 2
	await scan(p, "6291100000035", "Tab"); // Brufen (Tab suffix)
	await p.fill("#px-scan", "فولتارين");
	await p.waitForTimeout(1500);
	await shot(p, "08b-pos-arabic-search", "Arabic name search (فولتارين) with results");
	await p.keyboard.press("Enter");
	await p.waitForTimeout(1800);
	await p.fill("#px-scan", "");
	const disc = await p.$$(".px-line .px-disc-input");
	if (disc.length >= 2) {
		await disc[1].fill("10");
		await disc[1].press("Enter");
		await p.waitForTimeout(2000);
	}
	await shot(p, "08-populated-cart", "two scans of one medicine (qty 2), a Tab-suffixed scan, an Arabic search pick, 10 % line discount");
	await p.keyboard.press("F9");
	await p.waitForTimeout(1500);
	const pay = await p.$(".px-dialog .px-pay-input");
	if (pay) await pay.fill("20000");
	await p.waitForTimeout(800);
	await shot(p, "09-payment", "cash payment of 20,000 with the change shown");
	await p.keyboard.press("Enter");
	await p.waitForTimeout(4500);
	await shot(p, "10-receipt-80mm-pos", "sale completed: the receipt shown at the counter (80 mm)");
	const saleName = await p.evaluate(() => (document.querySelector(".px-dialog iframe")?.src || "").match(/name=([^&]+)/)?.[1]);
	console.log("sale", saleName);
	await p.keyboard.press("Escape");
	await p.waitForTimeout(800);
	// hold / resume
	await p.click("body", { position: { x: 1200, y: 650 } });
	await scan(p, "6291100000097");
	await scan(p, "6291100000134");
	await p.keyboard.press("F8");
	await p.waitForTimeout(1500);
	await shot(p, "12-hold", "the cart parked (held-sales badge shows 1), counter free for the next customer");
	await p.click("[data-action=held]");
	await p.waitForTimeout(1200);
	await shot(p, "13-resume-list", "held sales of this counter, with Resume / Discard");
	await p.click(".px-dialog .px-btn-primary");
	await p.waitForTimeout(2500);
	await shot(p, "13b-resumed-cart", "the held cart back in the counter, re-priced");
	await p.keyboard.press("F9");
	await p.waitForTimeout(1300);
	await p.keyboard.press("Enter");
	await p.waitForTimeout(4000);
	// this sale stays valid (the first one is voided below): its receipt is printed from the ERP later
	globalThis.keptSale = await p.evaluate(() => (document.querySelector(".px-dialog iframe")?.src || "").match(/name=([^&]+)/)?.[1]);
	console.log("kept sale", globalThis.keptSale);
	await p.keyboard.press("Escape");
	await p.waitForTimeout(800);
	// history
	await p.click("[data-action=recent]");
	await p.waitForTimeout(2200);
	await shot(p, "14-sales-history", "this counter's recent sales with Reprint / Return / Void");
	// return 1 of the resumed sale (newest)
	const row = await p.$(".px-recent");
	const btns = row ? await row.$$("button") : [];
	if (btns.length > 1) {
		await btns[1].click();
		await p.waitForTimeout(2500);
		const q = await p.$$(".px-dialog .px-qty-input, .px-dialog input.px-num");
		if (q.length) {
			await q[q.length - 1].fill("1");
			await p.waitForTimeout(2000);
		}
		await shot(p, "16-return", "return of one unit against the original sale (bounded by what was sold)");
		await p.click(".px-dialog .px-btn-pay");
		await p.waitForTimeout(4500);
		await shot(p, "16b-return-receipt", "return completed, refund receipt");
		await p.keyboard.press("Escape");
		await p.waitForTimeout(800);
	}
	// void with a manager's approval (the cashier stays signed in)
	await p.click("[data-action=recent]");
	await p.waitForTimeout(2000);
	if (saleName) {
		await p.fill(".px-dialog input[type=search]", saleName);
		await p.waitForTimeout(1500);
	}
	const vrow = await p.$(".px-recent");
	const vbtn = vrow ? await vrow.$(".px-btn-danger") : null;
	if (vbtn) {
		await vbtn.click();
		await p.waitForTimeout(1000);
		await p.fill(".px-dialog textarea", "الزبون غيّر رأيه قبل المغادرة — QA");
		await p.fill(".px-dialog input[type=email]", "manager@qa-pharmacy.test");
		await p.fill(".px-dialog input[type=password]", PWD);
		await shot(p, "17-void-approval", "void requested by the cashier; the manager approves with their own password on this screen");
		await p.click(".px-dialog .px-btn-danger");
		await p.waitForTimeout(4500);
		if (saleName) {
			await p.fill(".px-dialog input[type=search]", saleName).catch(() => {});
			await p.waitForTimeout(1500);
		}
		await shot(p, "18-void-result", "the sale shows as voided; the cashier is still the signed-in user");
		await p.keyboard.press("Escape");
		await p.waitForTimeout(600);
	}
	globalThis.saleName = saleName;
}

// ------------------------------------------------------------------ owner: the ERP
const owner = await session(...OWNER);
{
	const p = owner;
	await desk(p, "pharmacy-dashboard", 4000);
	await shot(p, "06-dashboard", "Pharmacy Dashboard (owner)");
	if (globalThis.saleName) {
		await desk(p, "sales-invoice/" + globalThis.saleName, 3500);
		await shot(p, "15-sale-detail", "the voided sale in the ERP: items, batch, payments, status Cancelled");
	}
	if (globalThis.keptSale) {
		await p.goto(B + `/printview?doctype=Sales%20Invoice&name=${globalThis.keptSale}&format=PharmacyOS%20Receipt&no_letterhead=1`);
		await p.waitForTimeout(2000);
		await shot(p, "10-receipt-80mm", "PharmacyOS Receipt print format, 80 mm paper (Arabic + English names, cash and change)");
		// 58 mm: the owner switches the paper width in PharmacyOS Settings; the same receipt again
		await api(p, "frappe.client.set_value", { doctype: "PharmacyOS Settings", name: "PharmacyOS Settings", fieldname: "receipt_paper_width", value: "58mm" });
		await p.goto(B + `/printview?doctype=Sales%20Invoice&name=${globalThis.keptSale}&format=PharmacyOS%20Receipt&no_letterhead=1`);
		await p.waitForTimeout(2000);
		await shot(p, "11-receipt-58mm", "the same receipt on 58 mm paper (PharmacyOS Settings → Receipt Paper Width)");
		await api(p, "frappe.client.set_value", { doctype: "PharmacyOS Settings", name: "PharmacyOS Settings", fieldname: "receipt_paper_width", value: "80mm" });
	}
	await desk(p, "item?item_group=Analgesics");
	await shot(p, "21-medicines", "Medicines (Item list)");
	await desk(p, "item/QA-PAN500", 3500);
	await shot(p, "21b-medicine-form", "a medicine: Arabic name, generic, barcodes, batches and expiry");
	await desk(p, "inventory-health", 4000);
	await shot(p, "22-inventory", "Inventory Health: low stock, out of stock, stock value");
	await desk(p, "batches-expiry", 4000);
	await shot(p, "23-batches", "Batches & Expiry: every batch with its expiry and quantity (FEFO order)");
	await desk(p, "expiry-intelligence", 4000);
	await shot(p, "24-expiry", "Expiry Intelligence: expired and near-expiry stock");
	await desk(p, "batches-expiry", 3500);
	const expiredTab = await p.$('[data-slot="buckets"] [data-value="expired"]');
	if (expiredTab) {
		await expiredTab.click().catch(() => {});
		await p.waitForTimeout(2500);
		await shot(p, "24b-expired-batches", "expired batches: never sellable, value at risk, Dispose");
	}
	const dbtn = await p.$("[data-dispose]");
	if (dbtn) {
		await dbtn.click();
		await p.waitForTimeout(1500);
		await shot(p, "25-expired-disposal", "disposing of an expired batch: quantity and reason, recorded as a stock write-off (the A12 test disposes of it for real)");
		await p.keyboard.press("Escape");
	}
	await desk(p, "supplier");
	await shot(p, "26-suppliers", "Suppliers");
	const po = await api(p, "frappe.client.get_list", { doctype: "Purchase Order", filters: { docstatus: 1 }, fields: ["name"], order_by: "creation desc", limit_page_length: 1 });
	if (po.length) {
		await desk(p, "purchase-order/" + po[0].name, 3500);
		await shot(p, "27-purchase-order", "a submitted purchase order");
	}
	const pr = await api(p, "frappe.client.get_list", { doctype: "Purchase Receipt", filters: { docstatus: 1, is_return: 0 }, fields: ["name"], order_by: "creation desc", limit_page_length: 5 });
	const prOne = pr.find(Boolean);
	if (prOne) {
		await desk(p, "purchase-receipt/" + prOne.name, 3500);
		await shot(p, "28-receiving", "receiving: purchase receipt with batch numbers, expiry and cost");
	}
	const ret = await api(p, "frappe.client.get_list", { doctype: "Purchase Receipt", filters: { docstatus: 1, is_return: 1 }, fields: ["name"], order_by: "creation desc", limit_page_length: 1 });
	if (ret.length) {
		await desk(p, "purchase-receipt/" + ret[0].name, 3500);
		await shot(p, "29-purchase-return", "purchase return to the supplier (negative quantities, against the original receipt)");
	}
	await desk(p, "pharmacy-report", 5000);
	await shot(p, "30-reports", "Pharmacy Report: sales, returns, voids, shifts, margins (owner sees costs)", { fullPage: true });
	await desk(p, "employee");
	await shot(p, "31-employees", "Employees (staff directory)");
	await desk(p, "user/cashier@qa-pharmacy.test", 3500);
	const rolesTab = await p.$("a[data-fieldname='sb1_tab'], .form-tabs a:has-text('الأدوار'), .form-tabs a:has-text('Roles')");
	if (rolesTab) {
		await rolesTab.click().catch(() => {});
		await p.waitForTimeout(1500);
	}
	await shot(p, "32-roles-permissions", "a staff account: role profile Cashier and its roles");
	await desk(p, "pharmacyos-settings", 3500);
	await shot(p, "33-settings", "PharmacyOS Settings (grouped tabs: pharmacy, receipt, sales & counter, stock, backups, Cloud)");
	await desk(p, "system-status", 4000);
	await shot(p, "33b-system-status", "System Status: version, backups, services");
}

// ------------------------------------------------------------------ permission denied
{
	const p = cashier;
	await desk(p, "pharmacy-report", 3500);
	await shot(p, "40-permission-denied", "a cashier opening the Pharmacy Report: refused by the server");
}

// ------------------------------------------------------------------ shift close (−500 variance)
{
	const p = cashier;
	await p.goto(B + "/pos");
	await p.waitForTimeout(3000);
	await p.click("[data-action=close-shift]");
	await p.waitForTimeout(2500);
	const cells = await p.$$eval(".px-dialog table tbody tr td", (tds) => tds.map((t) => t.innerText));
	const expected = Number(String(cells[1] || "0").replace(/[^\d]/g, ""));
	await p.fill(".px-dialog input[data-mode]", String(expected - 500));
	await p.waitForTimeout(900);
	await shot(p, "20-shift-close", `closing count ${expected - 500} against ${expected} expected: −500 shown before closing`);
	await p.click(".px-dialog .px-btn-primary");
	await p.waitForTimeout(4500);
	await shot(p, "20b-shift-closed", "shift closed; the next sign-in asks for a new opening float");
}

fs.writeFileSync(path.join(OUT, "screens.json"), JSON.stringify(record, null, 1));
await browser.close();
