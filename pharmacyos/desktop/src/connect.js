// The app's local screens: first-run setup, startup/recovery, server update, backups, connection and
// printer settings. Runs in the sandboxed page; every action goes through the preload API.
const api = window.pharmacyosDesktop;
const params = new URLSearchParams(location.search);
const $ = (sel) => document.querySelector(sel);
const slot = (name) => document.querySelector(`[data-slot="${name}"]`);

const CHECK_TEXT = {
	windows: ["إصدار Windows", "Windows version", "يتطلب Windows 10 (2004) أو Windows 11، 64-بت. ثبّت تحديثات Windows.", "Needs 64-bit Windows 10 (2004+) or 11. Install Windows updates."],
	memory: ["الذاكرة (RAM)", "Memory", "يُنصح بـ 8 غيغابايت أو أكثر.", "8 GB or more is recommended."],
	disk: ["المساحة الفارغة", "Free disk space", "يحتاج 15 غيغابايت فارغة على الأقل على القرص C.", "Needs at least 15 GB free on drive C."],
	virtualization: ["المحاكاة الافتراضية", "Virtualization", "فعّل Virtualization (VT-x / AMD-V / SVM) من إعدادات BIOS ثم أعد المحاولة.", "Turn on virtualization (VT-x / AMD-V / SVM) in the BIOS, then retry."],
	wsl: ["بيئة الخادم (WSL2)", "Server environment (WSL2)", "سيُفعَّل أثناء التثبيت (قد يتطلب إعادة تشغيل).", "Will be turned on during setup (may need one restart)."],
	existing_server: ["خادم PharmacyOS سابق", "Existing PharmacyOS server", "يوجد خادم PharmacyOS على هذا الجهاز: سيُكمل الإعداد ما ينقصه دون حذف البيانات.", "A PharmacyOS server exists here: setup completes what is missing without deleting data."],
	port80: ["المنفذ 80", "Port 80", "يستخدمه برنامج آخر: سيعمل PharmacyOS على المنفذ 8780.", "Used by another program: PharmacyOS will use port 8780."],
	administrator: ["صلاحيات المسؤول", "Administrator", "سجّل الدخول إلى Windows بحساب مسؤول (الحساب المستخدم عند الكاونتر) ثم شغّل الإعداد من جديد. يُثبَّت خادم الصيدلية لحساب Windows هذا.", "Sign in to Windows with an administrator account (the account used at the counter) and run setup again. The pharmacy server is installed for this Windows account."],
	check_failed: ["الفحص", "Check", "تعذّر فحص الجهاز.", "The check could not run."],
};
const ERR_TEXT = {
	required: "مطلوب · Required",
	email: "بريد غير صالح · Invalid email",
	short: "8 أحرف على الأقل · At least 8 characters",
	mismatch: "كلمتا المرور غير متطابقتين · Passwords do not match",
	phone: "رقم غير صالح · Invalid number",
};
const STEP_KEYS = ["check", "wsl", "distro", "server", "autostart", "finish"];
const BUSY_TEXT = {
	update: "يجري تحديث خادم الصيدلية · The pharmacy server is being updated",
	restore: "تجري استعادة نسخة احتياطية · A backup is being restored",
	backup: "يجري أخذ نسخة احتياطية · A backup is being taken",
	start: "يجري تشغيل خادم الصيدلية · The pharmacy server is starting",
};
const BUSY_REFUSED = "عملية أخرى جارية على خادم الصيدلية — يرجى الانتظار حتى تنتهي · Another operation is already running — please wait until it finishes";

let current = null;
let pharmacyValues = null;
let pollTimer = null;
let startedAt = Date.now();

function show(view) {
	current = view;
	document.querySelectorAll("[data-view]").forEach((el) => (el.hidden = el.dataset.view !== view));
	window.scrollTo(0, 0);
}

function onlyFor(mode) {
	document.querySelectorAll("[data-only]").forEach((el) => (el.hidden = el.dataset.only !== mode));
}

function el(tag, attrs, ...children) {
	const node = document.createElement(tag);
	for (const [k, v] of Object.entries(attrs || {})) {
		if (k === "class") node.className = v;
		else if (k === "text") node.textContent = v;
		else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
		else node.setAttribute(k, v);
	}
	for (const c of children) if (c !== null && c !== undefined) node.append(c);
	return node;
}

// ------------------------------------------------------------------ first run

function readPharmacyForm() {
	const form = $('[data-form="pharmacy"]');
	const data = Object.fromEntries(new FormData(form).entries());
	data.share_on_network = form.elements.share_on_network.checked;
	return data;
}

function validatePharmacy(v) {
	const e = {};
	if ((v.pharmacy_name || "").trim().length < 2) e.pharmacy_name = "required";
	if ((v.owner_full_name || "").trim().length < 2) e.owner_full_name = "required";
	if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((v.owner_email || "").trim())) e.owner_email = "email";
	if ((v.owner_password || "").length < 8) e.owner_password = "short";
	if (v.owner_password !== v.owner_password_confirm) e.owner_password_confirm = "mismatch";
	if (v.phone && !/^[+\d][\d\s-]{5,19}$/.test(v.phone.trim())) e.phone = "phone";
	return e;
}

function showErrors(errors) {
	document.querySelectorAll("[data-err]").forEach((n) => (n.textContent = ERR_TEXT[errors[n.dataset.err]] || ""));
	const first = Object.keys(errors)[0];
	if (first) document.getElementById(first)?.focus();
}

async function runCheck() {
	show("check");
	slot("check-spinner").hidden = false;
	slot("checks").textContent = "";
	slot("check-note").hidden = true;
	slot("install-error").textContent = "";
	const install = $('[data-action="install"]');
	install.disabled = true;
	const result = await api.setup.check();
	slot("check-spinner").hidden = true;
	for (const c of result.checks || []) {
		const t = CHECK_TEXT[c.key] || [c.key, c.key, "", ""];
		const level = c.ok ? "ok" : c.level === "error" ? "error" : "warn";
		const icon = c.ok ? "✓" : level === "error" ? "✗" : "!";
		const value = typeof c.value === "boolean" ? "" : String(c.value ?? "");
		slot("checks").append(
			el("li", { class: level }, el("span", { class: "i", text: icon }), el("div", {}, el("b", { text: `${t[0]} · ${t[1]} ` }), el("bdi", { dir: "ltr", text: value }), c.ok ? null : el("small", { text: `${t[2]} ${t[3]}` })))
		);
	}
	slot("check-note").hidden = !result.ok;
	install.disabled = !result.ok;
}

function renderInstall(state, log) {
	const s = state || { step: "check", status: "starting", percent: 0 };
	slot("bar").style.width = `${s.percent || 0}%`;
	$(".progress").setAttribute("aria-valuenow", String(s.percent || 0));
	slot("pct").textContent = `${s.percent || 0}%`;
	const minutes = Math.floor((Date.now() - startedAt) / 60000);
	slot("elapsed").textContent = `${minutes} دقيقة · ${minutes} min`;
	const steps = slot("steps");
	if (!steps.children.length) {
		const titles = {
			check: "فحص الجهاز · Checking this computer",
			wsl: "تفعيل بيئة الخادم (WSL2) · Turning on WSL2",
			distro: "تنزيل نظام الخادم · Downloading the server system",
			server: "تثبيت خادم الصيدلية · Installing the pharmacy server",
			autostart: "التشغيل التلقائي مع Windows · Starting with Windows",
			finish: "التحقق النهائي · Final check",
		};
		for (const k of STEP_KEYS) steps.append(el("li", { "data-step": k, text: titles[k] }));
	}
	const index = STEP_KEYS.indexOf(s.step);
	steps.querySelectorAll("li").forEach((li, i) => {
		li.className = s.status === "done" || i < index ? "done" : i === index ? "now" : "";
	});
	slot("install-msg").textContent = s.message || "";
	slot("log").textContent = log || "";
	slot("install-failed").hidden = s.status !== "failed";
	if (s.status === "failed") slot("fail-msg").textContent = s.message || "";
}

function pollInstall() {
	clearInterval(pollTimer);
	const tick = async () => {
		const { state, log } = await api.setup.state();
		if (current !== "install") return;
		renderInstall(state, log);
		if (state && state.status === "reboot_required") {
			clearInterval(pollTimer);
			show("reboot");
		} else if (state && state.status === "done") {
			clearInterval(pollTimer);
			show("done");
			$('[data-action="finish"]').dataset.url = state.server_url || "http://127.0.0.1";
		}
	};
	tick();
	pollTimer = setInterval(tick, 1500);
}

// ------------------------------------------------------------------ connection & printer

async function settingsForm() {
	const form = $('[data-form="settings"]');
	const cfg = (await api.getConfig()) || {};
	const printers = await api.listPrinters();
	const select = form.elements.receiptPrinter;
	if (select.options.length === 1) for (const name of printers) select.add(new Option(name, name));
	form.elements.mode.value = cfg.mode || "network";
	form.elements.serverUrl.value = cfg.mode === "network" ? cfg.serverUrl : "";
	select.value = cfg.receiptPrinter || "";
	form.elements.silentReceipts.checked = Boolean(cfg.silentReceipts);
	form.elements.startPage.value = cfg.startPage === "pos" ? "pos" : "desk";
	const sync = () => (slot("address").hidden = form.elements.mode.value !== "network");
	form.onchange = sync;
	sync();
	const values = () => ({
		mode: form.elements.mode.value,
		serverUrl: form.elements.mode.value === "this-pc" ? cfg.serverUrl && cfg.mode === "this-pc" ? cfg.serverUrl : "http://127.0.0.1" : form.elements.serverUrl.value,
		receiptPrinter: select.value,
		silentReceipts: form.elements.silentReceipts.checked,
		startPage: form.elements.startPage.value,
	});
	const status = (text, tone) => {
		slot("status").textContent = text;
		slot("status").className = "status " + (tone || "");
	};
	$('[data-action="test"]').onclick = async () => {
		status("جارٍ الاختبار… Testing…");
		const ok = await api.testServer(values().serverUrl);
		status(ok ? "تم الاتصال بالخادم ✓ Connected" : "لا يمكن الوصول إلى الخادم · Server not reachable", ok ? "good" : "bad");
	};
	$('[data-action="test-print"]').onclick = async () => {
		await api.saveConfig({ ...values() }).catch(() => {});
		const r = await api.testPrinter();
		status(r && r.ok ? "أُرسل الوصل التجريبي إلى الطابعة ✓ Test receipt sent" : `لم تتم الطباعة · Not printed: ${(r && r.error) || ""}`, r && r.ok ? "good" : "bad");
	};
	form.onsubmit = async (e) => {
		e.preventDefault();
		if (values().mode === "network" && !form.elements.serverUrl.value.trim()) {
			status("أدخل عنوان الخادم · Enter the server address", "bad");
			form.elements.serverUrl.focus();
			return;
		}
		await api.saveConfig(values());
	};
}

// ------------------------------------------------------------------ backups

function sizeText(bytes) {
	const mb = bytes / (1024 * 1024);
	return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
}

async function loadBackups() {
	const list = slot("backup-list");
	list.textContent = "";
	list.append(el("li", { class: "muted", text: "جارٍ التحميل… Loading…" }));
	const r = await api.backups.list();
	list.textContent = "";
	if (!r || !r.ok) {
		list.append(el("li", { class: "muted", text: "تعذّر عرض النسخ (هل الخادم يعمل؟) · Could not list backups (is the server running?)" }));
		return;
	}
	if (!r.rows.length) list.append(el("li", { class: "muted", text: "لا توجد نسخ بعد · No backups yet" }));
	for (const b of r.rows.slice(0, 40)) {
		const m = b.metadata || {};
		const size = (m.files || []).reduce((s, f) => s + (f.size || 0), 0);
		const when = (m.created_at || "").replace("T", " ").slice(0, 16);
		list.append(
			el(
				"li",
				{},
				el("div", {}, el("b", { dir: "ltr", text: when || b.display }), el("small", { dir: "ltr", text: `${m.kind || ""} · ${sizeText(size)}${m.encrypted ? " · encrypted" : ""} · ${b.display}` })),
				m.site ? el("button", { text: "استعادة · Restore", onclick: () => confirmRestore(b, when) }) : null
			)
		);
	}
}

let restoreTarget = null;
function confirmRestore(backup, when) {
	restoreTarget = backup;
	slot("restore-name").textContent = when;
	slot("restore-word").value = "";
	slot("restore-files").checked = false;
	slot("restore-confirm").hidden = false;
	slot("restore-word").focus();
}

// ------------------------------------------------------------------ one server operation at a time

// An update, restore, backup or server start is already running (this screen was opened again
// meanwhile): nothing can be started until it ends; then the screen continues by itself.
async function waitWhileBusy(operation) {
	show("busy");
	let last = operation;
	for (;;) {
		slot("busy-what").textContent = BUSY_TEXT[last] || "";
		await new Promise((resolve) => setTimeout(resolve, 2000));
		const s = await api.busy();
		if (!s || !s.busy) break;
		last = s.operation;
	}
	if (last === "start") return; // the server start opens the app by itself
	if (params.get("view") === "backups") {
		show("backups");
		loadBackups();
	} else api.openApp(); // the app — or the update screen, if the server still needs the update
}

// What the server reports after an update or a restore, said exactly (pharmacyos-server prints the outcome)
function updateMessage(result) {
	const status = result && result.status;
	const v = (result && result.version) || "";
	const backup = (result && result.backup) || "";
	if (status === "updated") return `✓ تم التحديث إلى ${v} · Updated to ${v}`;
	if (status === "current") return `الخادم محدَّث مسبقًا (${v}). · The server is already up to date (${v}).`;
	if (status === "rolled_back")
		return `تعذّر التحديث، وأُعيد الخادم إلى النسخة السابقة ${v} ببياناته كاملة. تواصل مع الدعم. · The update failed; the server is back on ${v} with all its data. Contact support.`;
	if (status === "rollback_failed")
		return `تعذّر التحديث، وتعذّرت أيضًا إعادة البيانات تلقائيًا. الخادم متوقف عن البيع (وضع الصيانة) حتى لا يُباع على بيانات ناقصة. تواصل مع الدعم فورًا — النسخة الآمنة: ${backup} · The update failed and putting the data back automatically failed too. The server stays in maintenance mode (no sales on incomplete data). Contact support now — safety backup: ${backup}`;
	return "تعذّر التحديث. الخادم لم يتغير. · The update could not run; nothing changed.";
}

function restoreMessage(r) {
	if (r && r.ok) return "✓ تمت الاستعادة · Restored. سجّل الدخول من جديد · Sign in again.";
	const status = r && r.status;
	if (status === "failed_reverted") return "تعذّرت الاستعادة، وأُعيدت البيانات كما كانت قبلها من النسخة الآمنة. · The restore failed; the data was put back as it was before (safety backup).";
	if (status === "failed_maintenance")
		return "تعذّرت الاستعادة وتعذّرت إعادة النسخة الآمنة تلقائيًا. الخادم في وضع الصيانة (لا بيع). تواصل مع الدعم فورًا. · The restore failed and the safety backup could not be put back automatically. The server stays in maintenance mode (no sales). Contact support now.";
	if (status === "failed_unchanged" || status === "invalid_folder") return "تعذّرت الاستعادة؛ البيانات الحالية لم تتغير. · The restore could not run; the current data is unchanged.";
	return `تعذّرت الاستعادة · Restore failed ${(r && r.error) || ""}`;
}

// ------------------------------------------------------------------ actions

document.addEventListener("click", async (e) => {
	const btn = e.target.closest("[data-action]");
	if (!btn || btn.disabled) return;
	const action = btn.dataset.action;
	if (action === "welcome-next") {
		const role = $('input[name="role"]:checked').value;
		if (role === "network") {
			show("settings");
			await settingsForm();
			$('[data-form="settings"]').elements.mode.value = "network";
			slot("address").hidden = false;
		} else show("pharmacy");
	} else if (action === "back-welcome") show("welcome");
	else if (action === "back-pharmacy") show("pharmacy");
	else if (action === "install") {
		btn.disabled = true;
		const r = await api.setup.start(pharmacyValues);
		if (r && r.ok) {
			startedAt = Date.now();
			show("install");
			pollInstall();
		} else {
			btn.disabled = false;
			slot("install-error").textContent = r && r.denied ? "لم يُمنح إذن المسؤول. اضغط «تثبيت» ووافق على طلب Windows. · Administrator permission was not given." : `تعذّر بدء التثبيت · Setup could not start. ${(r && r.error) || ""}`;
		}
	} else if (action === "install-retry") {
		btn.disabled = true;
		slot("retry-error").textContent = "";
		const r = await api.setup.resume();
		btn.disabled = false;
		if (r && !r.ok) {
			slot("retry-error").textContent = r.denied
				? "لم يُمنح إذن المسؤول. أعد المحاولة ووافق على طلب Windows، أو أدخل البيانات من جديد. · Administrator permission was not given: retry and accept the Windows prompt, or enter the details again."
				: `تعذّر بدء الإعداد · Setup could not start. ${r.error || ""}`;
		}
		pollInstall();
	} else if (action === "install-restart") {
		// leave the failed setup: its saved details (with the owner's password) are deleted; Install writes them anew
		clearInterval(pollTimer);
		await api.setup.forget();
		show("pharmacy");
	} else if (action === "reboot") api.setup.reboot();
	else if (action === "reboot-later") window.close();
	else if (action === "finish") api.setup.finish(btn.dataset.url);
	else if (action === "retry") {
		show("starting");
		api.retry();
	} else if (action === "server-start") {
		slot("recovery-status").textContent = "جارٍ تشغيل الخادم… Starting the server…";
		show("starting");
		const r = await api.server.start();
		if (r && r.busy) waitWhileBusy(r.operation);
	} else if (action === "settings") {
		show("settings");
		settingsForm();
	} else if (action === "update-now") {
		slot("update-actions").hidden = true;
		slot("update-spinner").hidden = false;
		slot("update-msg").textContent = "جارٍ التحديث — لا تطفئ الجهاز · Updating — do not turn off the computer";
		const r = await api.server.update();
		if (r && r.busy) return waitWhileBusy(r.operation);
		slot("update-spinner").hidden = true;
		slot("update-msg").textContent = updateMessage(r && r.result);
		slot("update-done").hidden = false;
	} else if (action === "update-later" || action === "update-continue") api.server.continue();
	else if (action === "back-app") api.openApp();
	else if (action === "backup-now") {
		btn.disabled = true;
		slot("backup-status").textContent = "جارٍ النسخ الاحتياطي… Backing up…";
		const r = await api.backups.create();
		btn.disabled = false;
		slot("backup-status").textContent = r && r.busy ? BUSY_REFUSED : r && r.ok ? `✓ تم: ${r.folder} · Done` : `تعذّر النسخ · Backup failed ${(r && r.error) || ""}`;
		slot("backup-status").className = "status " + (r && r.ok ? "good" : "bad");
		loadBackups();
	} else if (action === "backup-open") api.backups.open();
	else if (action === "restore-cancel") slot("restore-confirm").hidden = true;
	else if (action === "restore-go") {
		const word = slot("restore-word").value.trim();
		if (word !== "استعادة" && word.toUpperCase() !== "RESTORE") {
			slot("restore-word").focus();
			return;
		}
		btn.disabled = true;
		slot("backup-status").textContent = "جارٍ الاستعادة — لا تطفئ الجهاز · Restoring — do not turn off the computer";
		const r = await api.backups.restore(restoreTarget.folder, slot("restore-files").checked);
		btn.disabled = false;
		slot("restore-confirm").hidden = true;
		slot("backup-status").textContent = r && r.busy ? BUSY_REFUSED : restoreMessage(r);
		slot("backup-status").className = "status " + (r && r.ok ? "good" : "bad");
		loadBackups();
	}
});

$('[data-form="pharmacy"]').addEventListener("submit", (e) => {
	e.preventDefault();
	const values = readPharmacyForm();
	const errors = validatePharmacy(values);
	showErrors(errors);
	if (Object.keys(errors).length) return;
	pharmacyValues = values;
	runCheck();
});

api.onStartup(({ attempt, of }) => {
	slot("starting-msg").textContent = `يتم تشغيل خادم الصيدلية… (${attempt}/${of}) · Starting the pharmacy server…`;
});
api.server.onLine((line) => {
	for (const name of ["update-log", "backup-log", "busy-log"]) {
		const pre = slot(name);
		pre.textContent = (pre.textContent + line + "\n").slice(-20000);
		pre.scrollTop = pre.scrollHeight;
	}
});

(async () => {
	const info = await api.info();
	slot("version").textContent = info.version ? `v${info.version}` : "";
	slot("foot-version").textContent = info.version ? `PharmacyOS ERP ${info.version}` : "";
	const cfg = (await api.getConfig()) || {};
	onlyFor(cfg.mode === "network" ? "network" : "this-pc");
	const view = params.get("view") || "starting";
	const running = await api.busy();
	if (running && running.busy) return waitWhileBusy(running.operation); // never offer to start it twice
	if (view === "settings") {
		show("settings");
		settingsForm();
	} else if (view === "install") {
		show("install");
		pollInstall();
	} else if (view === "update") {
		slot("from").textContent = params.get("installed") || "";
		slot("to").textContent = params.get("bundled") || "";
		show("update");
	} else if (view === "backups") {
		show("backups");
		loadBackups();
	} else show(view);
})();
