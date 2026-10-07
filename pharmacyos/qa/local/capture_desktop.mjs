// A16/A17/A23: the PharmacyOS ERP desktop app (Electron, run from source) driving the QA test server on
// this Linux machine — the same screens, IPC and server commands as on Windows, with `wsl.exe` replaced by
// a script that enters the test environment (PHARMACYOS_SERVER_CMD, a developer hook the installed app
// ignores). QA-only. Native dialogs (About) are captured from the X display.
//
//   DISPLAY=:99 node capture_desktop.mjs <phase>      phase: update | backups | recovery
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(process.env.PW_MODULES || "/tmp/claude-0/e2e/node_modules/");
const { _electron: electron } = require("playwright");
const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "../../desktop");
const OUT = path.resolve(HERE, "../../docs/qa/local/screenshots");
const SERVER = "http://127.0.0.1";
const BUNDLE = process.env.QA_BUNDLE || "/var/tmp/wslroot/mnt/c/Program Files/PharmacyOS ERP/resources/server";
const phase = process.argv[2] || "update";
fs.mkdirSync(OUT, { recursive: true });
const record = [];

async function launch(serverCmd, extraEnv = {}) {
	const userData = fs.mkdtempSync(path.join(os.tmpdir(), "pxos-ud-"));
	fs.writeFileSync(path.join(userData, "config.json"), JSON.stringify({ mode: "this-pc", serverUrl: SERVER, startPage: "pos" }));
	const app = await electron.launch({
		executablePath: path.join(APP, "node_modules/electron/dist/electron"),
		args: [APP, "--no-sandbox", "--lang=ar"],
		env: { ...process.env, PHARMACYOS_USER_DATA: userData, PHARMACYOS_SETUP_STATE: path.join(userData, "state.json"), PHARMACYOS_SERVER_CMD: serverCmd, PHARMACYOS_BUNDLE_DIR: BUNDLE, ...extraEnv },
	});
	const win = await app.firstWindow();
	await win.setViewportSize({ width: 1280, height: 800 }).catch(() => {});
	return { app, win };
}

async function shot(win, name, note) {
	await win.waitForTimeout(700);
	await win.screenshot({ path: path.join(OUT, `${name}.png`) });
	const text = (await win.evaluate(() => document.body.innerText).catch(() => "")).replace(/\s+/g, " ").slice(0, 260);
	record.push({ screen: name, url: win.url(), note, visible_text: text });
	console.log("shot", name, "|", text.slice(0, 140));
}

function screenX(name, note) {
	// the whole X display: native dialogs are not part of the page
	execFileSync("import", ["-window", "root", path.join(OUT, `${name}.png`)], { env: process.env });
	record.push({ screen: name, note, source: "X display (native dialog)" });
	console.log("shot", name, "(display)");
}

const visible = (win, sel) => win.locator(sel).first().isVisible().catch(() => false);
async function until(fn, ms, step = 1000) {
	const t = Date.now();
	while (Date.now() - t < ms) {
		if (await fn()) return true;
		await new Promise((r) => setTimeout(r, step));
	}
	return false;
}
const view = (win) => win.evaluate(() => [...document.querySelectorAll("section[data-view]")].find((s) => !s.hidden)?.dataset.view).catch(() => null);
const menuClick = (app, label) => app.evaluate(({ Menu }, l) => Menu.getApplicationMenu().items.flatMap((i) => (i.submenu ? i.submenu.items : [])).find((i) => i.label === l).click(), label);

if (phase === "update") {
	// the server is stopped; the app starts it, finds the newer bundle and offers the update
	const { app, win } = await launch("/var/tmp/srv.sh");
	await until(async () => (await view(win)) === "starting", 20000, 200);
	await shot(win, "37-startup", "startup screen while the app brings the pharmacy server up");
	const offered = await until(async () => (await view(win)) === "update", 300000);
	console.log("update offered:", offered);
	await shot(win, "36-update-offered", "the app carries a newer server build: backup first, automatic rollback");
	await win.click('[data-action="update-now"]');
	await win.waitForTimeout(45000);
	await win.evaluate(() => document.querySelector('[data-view="update"] details')?.setAttribute("open", ""));
	await shot(win, "36b-update-running", "update running: safety backup, then install, migrate, build (live log)");
	await until(() => visible(win, '[data-slot="update-done"]:not([hidden])'), 1200000, 3000);
	await shot(win, "36c-update-result", "update finished: the result the server reported");
	await win.click('[data-action="update-continue"]');
	await win.waitForTimeout(6000);
	await shot(win, "04b-first-launch-after-update", "the app opens the pharmacy server (sign-in for the counter)");
	setTimeout(() => screenX("39-about-version", "About PharmacyOS ERP: desktop app, bundled server and installed server versions"), 2500);
	await menuClick(app, "About PharmacyOS ERP").catch(() => {});
	await win.waitForTimeout(5000);
	await app.close().catch(() => {});
} else if (phase === "backups") {
	const { app, win } = await launch("/var/tmp/srv.sh");
	await until(async () => !(await view(win)) || win.url().startsWith(SERVER), 120000);
	await menuClick(app, "Backups…");
	await until(async () => (await win.locator('[data-slot="backup-list"] li').count()) > 1, 120000);
	await shot(win, "34-backups", "Backups: hourly and daily backups on this PC, Back up now, Restore");
	await win.click('[data-action="backup-now"]');
	await until(async () => /✓|تعذّر|failed/i.test(await win.textContent('[data-slot="backup-status"]')), 900000, 2000);
	await shot(win, "34b-backup-now-done", "Back up now: verified backup folder created");
	// restore the backup just taken (no data is lost: it is the current state)
	await win.waitForTimeout(3000);
	await win.locator('[data-slot="backup-list"] li button').first().click();
	await win.fill('[data-slot="restore-word"]', "RESTORE");
	await shot(win, "35-restore-confirm", "Restore: typed confirmation; the current state is backed up before the restore");
	await win.click('[data-action="restore-go"]');
	await win.waitForTimeout(20000);
	await shot(win, "35b-restore-running", "restore running (maintenance mode on, safety backup, restore, migrate, health check)");
	await until(async () => /✓|تعذّر|failed/i.test(await win.textContent('[data-slot="backup-status"]')) && !/جارٍ/.test(await win.textContent('[data-slot="backup-status"]')), 1500000, 3000);
	await shot(win, "35c-restore-result", "restore finished: the result the server reported");
	await app.close().catch(() => {});
} else if (phase === "recovery") {
	// a server that does not come up by itself (the keep-alive does nothing); Start the server works
	const { app, win } = await launch("/var/tmp/srv-dead.sh", { PHARMACYOS_START_ATTEMPTS: "3" });
	await until(async () => (await view(win)) === "recovery", 120000);
	await shot(win, "38-recovery", "the server does not answer: what to do, in order, with Retry / Start the server / Settings");
	await win.click('[data-action="server-start"]');
	await win.waitForTimeout(3000);
	await shot(win, "38b-recovery-starting", "Start the server pressed: the app starts it and waits");
	await until(async () => win.url().startsWith(SERVER), 300000);
	await win.waitForTimeout(4000);
	await shot(win, "38c-recovered", "the server answered: the app continues to the pharmacy server");
	await app.close().catch(() => {});
}

const log = path.join(OUT, "desktop-screens.json");
const prev = fs.existsSync(log) ? JSON.parse(fs.readFileSync(log, "utf8")) : [];
fs.writeFileSync(log, JSON.stringify([...prev.filter((r) => !record.some((x) => x.screen === r.screen)), ...record], null, 1));
