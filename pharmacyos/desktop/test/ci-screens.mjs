// CI only (GitHub Actions, windows-latest): drives the INSTALLED PharmacyOS ERP.exe through its first-run
// screens and saves screenshots — welcome, pharmacy details, the real system check of this Windows machine,
// and the installation / restart / done screens rendered from setup state files (the state the elevated
// installer writes; here written by this script, so those three are labelled "state-driven").
// It never presses Install: that would turn on WSL2 on the build machine. Not shipped (package "files" is src/**).
//
//   node test/ci-screens.mjs "<path to PharmacyOS ERP.exe>" <output folder>
import { _electron as electron } from "playwright-core";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const [exe, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });

// local run against the unpacked app: CI_SCREENS_APPDIR=<desktop folder> (exe = node_modules/electron/dist/electron)
const base = process.env.CI_SCREENS_APPDIR ? [process.env.CI_SCREENS_APPDIR, "--no-sandbox"] : [];

async function launch(env, extra = []) {
	const userData = fs.mkdtempSync(path.join(os.tmpdir(), "pharmacyos-ci-"));
	const app = await electron.launch({ executablePath: exe, args: [...base, ...extra], env: { ...process.env, PHARMACYOS_USER_DATA: userData, ...env } });
	const win = await app.firstWindow();
	await win.setViewportSize({ width: 1180, height: 860 });
	await win.waitForLoadState("domcontentloaded");
	return { app, win };
}

const visible = (win, view) => win.waitForFunction((v) => { const n = document.querySelector(`[data-view="${v}"]`); return n && !n.hidden; }, view, { timeout: 120000 });

// 1-3: welcome → pharmacy details → system check (real, this machine)
{
	const { app, win } = await launch({});
	await visible(win, "welcome");
	await win.waitForTimeout(800);
	await win.screenshot({ path: path.join(out, "01-setup-welcome.png") });
	await win.click('[data-action="welcome-next"]');
	await visible(win, "pharmacy");
	await win.fill("#pharmacy_name", "QA TEST Pharmacy");
	await win.fill("#pharmacy_name_ar", "صيدلية الاختبار (QA)");
	await win.fill("#owner_full_name", "QA Owner");
	await win.fill("#owner_email", "owner@qa-pharmacy.test");
	await win.fill("#owner_password", "example-only-1");
	await win.fill("#owner_password_confirm", "example-only-1");
	await win.fill("#phone", "07700000000");
	await win.screenshot({ path: path.join(out, "02-setup-pharmacy-details.png") });
	await win.locator('[data-form="pharmacy"] button[type="submit"]').click();
	await visible(win, "check");
	await win.waitForFunction(() => document.querySelector('[data-slot="check-spinner"]').hidden, null, { timeout: 120000 });
	await win.waitForTimeout(500);
	await win.screenshot({ path: path.join(out, "03-setup-system-check-windows.png") });
	const checks = await win.$$eval('[data-slot="checks"] li', (lis) => lis.map((li) => `${li.className}: ${li.innerText.replace(/\s+/g, " ")}`));
	fs.writeFileSync(path.join(out, "03-system-check.txt"), checks.join("\n") + "\n");
	console.log(checks.join("\n"));
	await app.close();
}

// 4-6: installation progress, restart needed, ready — the screens the installer's state file drives
for (const [file, state] of [
	["04-setup-installing.png", { status: "running", step: "server", percent: 62, message: "Installing the pharmacy server — this takes 15–30 minutes" }],
	["05-setup-restart-needed.png", { status: "reboot_required", step: "wsl", percent: 22, message: "Windows must restart to finish turning on WSL2" }],
	["06-setup-done.png", { status: "done", step: "finish", percent: 100, message: "PharmacyOS is ready", server_url: "http://127.0.0.1" }],
]) {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pharmacyos-state-"));
	const stateFile = path.join(dir, "state.json");
	fs.writeFileSync(stateFile, JSON.stringify({ ...state, updated: new Date().toISOString(), version: "ci" }));
	fs.writeFileSync(path.join(dir, "setup.log"), "##PHARMACYOS-STEP 5 9 bench\n(state-driven screenshot in CI)\n");
	// "done" is shown when setup continues (--resume-setup, as after the restart); the others on any start
	const { app, win } = await launch({ PHARMACYOS_SETUP_STATE: stateFile, PHARMACYOS_START_ATTEMPTS: "1" }, state.status === "done" ? ["--resume-setup"] : []);
	await win.waitForTimeout(3000);
	await win.screenshot({ path: path.join(out, file) });
	await app.close();
}
console.log("screens saved to", out);
