// Shared building blocks for PharmacyOS pages: chips, states, skeletons, links.
frappe.provide("pharmacyos.ui");

const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
pharmacyos.ui.esc = esc;

pharmacyos.ui.icon = (name, size = "sm") => frappe.utils.icon(name, size);

// Desk base route: "/desk" on Frappe 17, "/app" on version-16.
pharmacyos.ui.base = window.location.pathname.startsWith("/app") ? "/app" : "/desk";

// Technical or numeric value that must read left-to-right inside Arabic text (SKU, batch, 39.9%).
pharmacyos.ui.ltr = (v) => `<bdi dir="ltr">${esc(v)}</bdi>`;

// Expiry status — label + icon + colour (never colour alone).
pharmacyos.ui.EXPIRY_STATUS = {
	expired: { label: () => __("Expired"), icon: "circle-x" },
	critical: { label: () => __("Critical"), icon: "triangle-alert" },
	soon: { label: () => __("Expiring Soon"), icon: "clock" },
	healthy: { label: () => __("Healthy"), icon: "circle-check" },
	none: { label: () => __("No Expiry"), icon: "minus" },
};

pharmacyos.ui.STOCK_STATUS = {
	out: { label: () => __("Out of Stock"), icon: "circle-x" },
	low: { label: () => __("Low Stock"), icon: "trending-down" },
	ok: { label: () => __("Healthy"), icon: "circle-check" },
	expiring: { label: () => __("Expiring"), icon: "clock", tone: "soon" },
	expired: { label: () => __("Has Expired Stock"), icon: "circle-x" },
};

pharmacyos.ui.chip = (status, map = pharmacyos.ui.EXPIRY_STATUS) => {
	const s = map[status] || { label: () => status, icon: "info" };
	const tone = s.tone || status;
	return `<span class="pos-chip" data-status="${esc(tone)}">${pharmacyos.ui.icon(s.icon, "xs")}${esc(
		s.label()
	)}</span>`;
};

pharmacyos.ui.STATE_ICONS = {
	empty: "inbox",
	ok: "circle-check",
	error: "triangle-alert",
	denied: "lock",
	search: "search",
	setup: "settings",
};

pharmacyos.ui.state = ({ kind = "empty", title, text = "", action = "" }) => `
	<div class="pos-state pos-fade-in" data-kind="${esc(kind)}" role="status">
		<div class="pos-state-icon">${pharmacyos.ui.icon(pharmacyos.ui.STATE_ICONS[kind] || "info", "md")}</div>
		<div class="pos-state-title">${esc(title)}</div>
		${text ? `<div class="pos-state-text">${esc(text)}</div>` : ""}
		${action}
	</div>`;

pharmacyos.ui.error_state = (err) =>
	pharmacyos.ui.state({
		kind: err && err.kind === "denied" ? "denied" : "error",
		title: err && err.kind === "denied" ? __("Access restricted") : __("Could not load"),
		text: (err && err.message) || __("Please try again."),
	});

pharmacyos.ui.skeleton_rows = (rows = 6) =>
	`<div style="padding:12px 16px;display:flex;flex-direction:column;gap:12px" aria-busy="true" aria-label="${esc(
		__("Loading")
	)}">${Array.from({ length: rows })
		.map(
			(_, i) =>
				`<div class="pos-skeleton" style="height:14px;width:${90 - ((i * 13) % 40)}%"></div>`
		)
		.join("")}</div>`;

pharmacyos.ui.skeleton_kpis = (n = 4) =>
	Array.from({ length: n })
		.map(
			() =>
				`<div class="pos-kpi" aria-hidden="true"><div class="pos-skeleton" style="width:55%"></div><div class="pos-skeleton" style="height:26px;width:70%"></div></div>`
		)
		.join("");

// Link to a list view with filters, e.g. list_url("Batch", {item: "X"}).
pharmacyos.ui.list_url = (doctype, filters = {}) => {
	const params = new URLSearchParams();
	for (const [k, v] of Object.entries(filters)) {
		params.set(k, typeof v === "string" ? v : JSON.stringify(v));
	}
	const q = params.toString();
	return `${pharmacyos.ui.base}/${frappe.router.slug(doctype)}${q ? "?" + q : ""}`;
};

pharmacyos.ui.form_url = (doctype, name) =>
	`${pharmacyos.ui.base}/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;

// Unambiguous display dates: "27 Oct 2026" / "27 تشرين الأول 2026" (Iraqi month names, Latin
// digits). Storage and API values stay ISO (YYYY-MM-DD); only presentation changes.
const MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
];
pharmacyos.ui.month_name = (index) => {
	const en = MONTHS[index];
	const local = __(en, null, "Month");
	return local === en ? en.slice(0, 3) : local;
};
pharmacyos.ui.date = (d) => {
	if (!d) return "—";
	const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);
	if (!m) return frappe.datetime.str_to_user(d);
	return `${Number(m[3])} ${pharmacyos.ui.month_name(Number(m[2]) - 1)} ${m[1]}`;
};

// Day counts with Arabic plural forms (1, 2, 3–10, 11+) chosen by translation context; English
// uses the same source strings, so it reads naturally either way.
const plural_ctx = (n) => (n === 1 ? "one" : n === 2 ? "two" : n >= 3 && n <= 10 ? "few" : "many");
pharmacyos.ui.days_count = (n) => __("{0} days", [n], plural_ctx(n));
pharmacyos.ui.days_text = (days) => {
	if (days === null || days === undefined) return "";
	if (days === 0) return __("today");
	const n = Math.abs(days);
	return days < 0 ? __("{0} days ago", [n], plural_ctx(n)) : __("in {0} days", [n], plural_ctx(n));
};


// Medicine display name: Arabic name for Arabic users when available, with the commercial name.
pharmacyos.ui.medicine_title = (row) => {
	const ar = frappe.boot.lang === "ar" && row.name_ar;
	return {
		title: ar ? row.name_ar : row.item_name || row.item_code,
		sub: ar ? row.item_name : row.name_ar || "",
	};
};

pharmacyos.ui.debounce = (fn, wait = 250) => {
	let t;
	return (...args) => {
		clearTimeout(t);
		t = setTimeout(() => fn(...args), wait);
	};
};

/** Segmented control: [{value, label, count}] */
pharmacyos.ui.segment = (options, active, name) =>
	`<div class="pos-segment" role="group" aria-label="${esc(name)}">${options
		.map(
			(o) =>
				`<button type="button" data-value="${esc(o.value)}" aria-pressed="${
					o.value === active ? "true" : "false"
				}">${esc(o.label)}${
					o.count !== undefined && o.count !== null
						? `<span class="pos-count">${esc(o.count)}</span>`
						: ""
				}</button>`
		)
		.join("")}</div>`;

pharmacyos.ui.search_box = (placeholder, value = "") =>
	`<label class="pos-search">${pharmacyos.ui.icon("search", "xs")}<input class="pos-input" type="search" value="${esc(
		value
	)}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></label>`;

/** Branch select for page toolbars (only branches the user may read). */
pharmacyos.ui.branch_select = (selected) => {
	const branches = pharmacyos.boot().branches || [];
	if (!branches.length) return "";
	return `<select class="pos-select" data-role="branch" aria-label="${esc(__("Branch"))}">
		<option value="">${esc(__("All branches"))}</option>
		${branches
			.map(
				(b) =>
					`<option value="${esc(b.name)}" ${b.name === selected ? "selected" : ""}>${esc(
						pharmacyos.branch_label(b.name)
					)}</option>`
			)
			.join("")}
	</select>`;
};
