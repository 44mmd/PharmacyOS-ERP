// Connect / setup screen logic (runs inside the sandboxed page; talks to main via the preload API).
const api = window.pharmacyosDesktop;
const params = new URLSearchParams(location.search);
const $ = (sel) => document.querySelector(sel);

function show(view) {
	document.querySelectorAll("[data-view]").forEach((el) => (el.hidden = el.dataset.view !== view));
}

async function setupForm() {
	const form = $("[data-form]");
	const cfg = (await api.getConfig()) || {};
	const printers = await api.listPrinters();
	const select = form.elements.receiptPrinter;
	for (const name of printers) select.add(new Option(name, name));
	form.elements.mode.value = cfg.mode || "this-pc";
	form.elements.serverUrl.value = cfg.mode === "network" ? cfg.serverUrl : "";
	select.value = cfg.receiptPrinter || "";
	form.elements.silentReceipts.checked = Boolean(cfg.silentReceipts);
	const sync = () => ($("[data-slot=address]").hidden = form.elements.mode.value !== "network");
	form.addEventListener("change", sync);
	sync();
	const values = () => ({
		mode: form.elements.mode.value,
		serverUrl: form.elements.mode.value === "this-pc" ? "http://127.0.0.1" : form.elements.serverUrl.value,
		receiptPrinter: select.value,
		silentReceipts: form.elements.silentReceipts.checked,
	});
	const status = (text, tone) => {
		const el = $("[data-slot=status]");
		el.textContent = text;
		el.className = "status " + (tone || "");
	};
	$("[data-action=test]").addEventListener("click", async () => {
		status("جارٍ الاختبار… Testing…");
		const ok = await api.testServer(values().serverUrl);
		status(ok ? "تم الاتصال بالخادم ✓ Connected" : "لا يمكن الوصول إلى الخادم · Server not reachable", ok ? "good" : "bad");
	});
	form.addEventListener("submit", async (e) => {
		e.preventDefault();
		if (values().mode === "network" && !form.elements.serverUrl.value.trim()) {
			status("أدخل عنوان الخادم · Enter the server address", "bad");
			form.elements.serverUrl.focus();
			return;
		}
		await api.saveConfig(values());
	});
}

document.addEventListener("click", (e) => {
	const action = e.target.closest("[data-action]")?.dataset.action;
	if (action === "retry") {
		show("connecting");
		api.retry();
	}
	if (action === "settings") {
		show("setup");
		setupForm();
	}
});

api.onProgress(({ attempt, of }) => {
	$("[data-slot=progress]").textContent = `يتم تشغيل خادم الصيدلية… (${attempt}/${of}) Starting the pharmacy server…`;
});

const state = params.get("state") || "connecting";
if (state === "setup" || state === "settings") {
	show("setup");
	setupForm();
} else {
	show(state);
}
