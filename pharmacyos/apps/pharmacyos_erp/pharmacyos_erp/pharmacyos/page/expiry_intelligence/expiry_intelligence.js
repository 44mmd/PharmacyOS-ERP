// Expiry Intelligence — where expiry risk sits (value, warehouse, supplier, medicine).
// Real aggregation of current batch stock; no predictions. Data: expiry.get_expiry_intelligence.
frappe.pages["expiry-intelligence"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Expiry Intelligence"), single_column: true });
	wrapper.pos_view = new PharmacyOSExpiryIntelligence(page);
};
frappe.pages["expiry-intelligence"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.load();
};

// bucket -> expiry status tone (status colours always come with a label)
const POS_BUCKET_TONE = { expired: "expired", "0_30": "critical", "31_60": "soon", "61_90": "soon", over_90: "healthy", no_expiry: "none" };

class PharmacyOSExpiryIntelligence {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.state = { horizon: 90, branch: pharmacyos.current_branch() || "" };
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		page.add_inner_button(__("Batches & Expiry"), () => frappe.set_route("batches-expiry"));
		this.render_shell();
		$(document).on("pharmacyos:branch-changed", (_e, branch) => {
			this.state.branch = branch || "";
			this.$root.find('[data-role="branch"]').val(this.state.branch);
			this.load();
		});
	}

	render_shell() {
		const ui = this.ui;
		this.$root.html(`
			<div class="pos-toolbar">
				<div data-slot="horizon"></div>
				<span class="pos-spacer"></span>
				${ui.branch_select(this.state.branch)}
			</div>
			<div class="pos-kpis" data-slot="kpis">${ui.skeleton_kpis(4)}</div>
			<div class="pos-grid">
				<section class="pos-panel pos-col-5">
					<div class="pos-panel-head"><h3 class="pos-panel-title">${__("Stock value by expiry window")}</h3></div>
					<div data-slot="buckets">${ui.skeleton_rows(6)}</div>
				</section>
				<section class="pos-panel pos-col-7">
					<div class="pos-panel-head"><h3 class="pos-panel-title">${__("Medicines with the most value at risk")}</h3></div>
					<div data-slot="items">${ui.skeleton_rows(6)}</div>
				</section>
				<section class="pos-panel pos-col-6">
					<div class="pos-panel-head"><h3 class="pos-panel-title">${__("At risk by warehouse")}</h3></div>
					<div data-slot="warehouses">${ui.skeleton_rows(4)}</div>
				</section>
				<section class="pos-panel pos-col-6">
					<div class="pos-panel-head"><h3 class="pos-panel-title">${__("At risk by supplier")}</h3></div>
					<div data-slot="suppliers">${ui.skeleton_rows(4)}</div>
				</section>
			</div>
			<p class="pos-note">${__(
				"Values are estimates: batch quantity × current valuation rate. “At risk” means expired or expiring within the selected window."
			)}</p>`);
		this.render_horizon();
		this.$root.find('[data-role="branch"]').on("change", (e) => {
			this.state.branch = e.target.value;
			this.load();
		});
	}

	render_horizon() {
		const opts = [
			{ value: "0", label: __("Today (expired)") },
			{ value: "30", label: __("30 days") },
			{ value: "60", label: __("60 days") },
			{ value: "90", label: __("90 days") },
		];
		const $slot = this.$root.find('[data-slot="horizon"]');
		$slot.html(this.ui.segment(opts, String(this.state.horizon), __("Risk window")));
		$slot.find("button").on("click", (e) => {
			this.state.horizon = Number($(e.currentTarget).attr("data-value"));
			this.render_horizon();
			this.load();
		});
	}

	load() {
		const token = (this.token = Math.random());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.expiry.get_expiry_intelligence", {
				horizon: this.state.horizon,
				branch: this.state.branch || null,
			})
			.then((d) => token === this.token && this.render(d))
			.catch((err) => {
				if (token !== this.token) return;
				this.$root.find('[data-slot="kpis"]').empty();
				this.$root.find(".pos-grid").html(`<div class="pos-panel pos-col-12">${this.ui.error_state(err)}</div>`);
			});
	}

	render(d) {
		const ui = this.ui;
		const money = pharmacyos.format_cost; // every figure on this page is a cost (null without cost access)
		const expired = d.buckets.find((b) => b.key === "expired") || { value: 0, batches: 0 };
		const window_label = d.horizon ? __("within {0} days", [d.horizon]) : __("expired only");
		this.$root.find('[data-slot="kpis"]').html(`
			<a class="pos-kpi is-primary" href="${pharmacyos.ui.base}/batches-expiry?bucket=${d.horizon ? d.horizon : "expired"}">
				<div class="pos-kpi-label">${ui.icon("calendar-x", "xs")} ${__("Value at risk")}</div>
				<div class="pos-kpi-value">${money(d.risk_value)}</div>
				<div class="pos-kpi-sub">${ui.esc(window_label)}</div>
			</a>
			<a class="pos-kpi" href="${pharmacyos.ui.base}/batches-expiry?bucket=expired">
				<div class="pos-kpi-label">${ui.icon("circle-x", "xs")} ${__("Already expired")}</div>
				<div class="pos-kpi-value">${money(expired.value)}</div>
				<div class="pos-kpi-sub">${__("{0} batches", [expired.batches])}</div>
			</a>
			<div class="pos-kpi">
				<div class="pos-kpi-label">${ui.icon("pill-bottle", "xs")} ${__("Batches at risk")}</div>
				<div class="pos-kpi-value">${d.risk_batches}</div>
				<div class="pos-kpi-sub">${ui.esc(window_label)}</div>
			</div>
			<div class="pos-kpi">
				<div class="pos-kpi-label">${ui.icon("package", "xs")} ${__("Batch-tracked stock value")}</div>
				<div class="pos-kpi-value">${money(d.total_value)}</div>
				<div class="pos-kpi-sub">${d.total_value ? __("{0}% at risk", [Math.round((100 * d.risk_value) / d.total_value)]) : "—"}</div>
			</div>`);

		// distribution: one thin bar per window, labelled with status chip + value
		const max = Math.max(...d.buckets.map((b) => b.value), 1);
		const tone_bar = { expired: "var(--pos-danger)", critical: "#c2410c", soon: "#d08a1c", healthy: "var(--pos-emerald)", none: "var(--gray-400)" };
		const status_for = { expired: "expired", critical: "critical", soon: "soon", healthy: "healthy", none: "none" };
		this.$root.find('[data-slot="buckets"]').html(
			`<ul class="pos-list">${d.buckets
				.map((b) => {
					const tone = POS_BUCKET_TONE[b.key];
					const pct = Math.max(b.value ? 2 : 0, Math.round((100 * b.value) / max));
					return `<li title="${ui.esc(b.label)}: ${ui.esc(money(b.value))} · ${ui.esc(__("{0} batches", [b.batches]))}">
						<div class="pos-list-main">
							<div style="display:flex;align-items:center;gap:8px">${ui.chip(status_for[tone])}<span>${ui.esc(b.label)}</span></div>
							<div class="pos-bar"><span style="width:${pct}%;background:${tone_bar[tone]}"></span></div>
						</div>
						<div class="pos-list-end">${money(b.value)}<div class="pos-days">${__("{0} batches", [b.batches])}</div></div>
					</li>`;
				})
				.join("")}</ul>`
		);

		const list = (rows, label_fn, link_fn, empty) =>
			rows.length
				? `<ul class="pos-list">${rows
						.map(
							(r) => `<li>
							<div class="pos-list-main"><a href="${link_fn(r)}">${label_fn(r)}</a>
								<div class="pos-days">${__("{0} batches", [r.batches])}${
								r.expired_value ? ` · ${__("expired")}: ${money(r.expired_value)}` : ""
							}</div></div>
							<div class="pos-list-end">${money(r.value)}</div></li>`
						)
						.join("")}</ul>`
				: ui.state({ kind: "ok", title: empty });

		this.$root.find('[data-slot="items"]').html(
			list(
				d.by_item,
				(r) => {
					const m = ui.medicine_title({ item_code: r.key, item_name: r.item_name, name_ar: r.name_ar });
					return `<span class="pos-cell-title">${ui.esc(m.title)}</span><span class="pos-cell-sub">${ui.esc(m.sub || r.key)}</span>`;
				},
				(r) => `${pharmacyos.ui.base}/batches-expiry?search=${encodeURIComponent(r.key)}`,
				__("No medicine at risk in this window")
			)
		);
		this.$root.find('[data-slot="warehouses"]').html(
			list(d.by_warehouse, (r) => ui.esc(r.key), (r) => ui.form_url("Warehouse", r.key), __("No warehouse at risk"))
		);
		this.$root.find('[data-slot="suppliers"]').html(
			list(
				d.by_supplier,
				(r) => (r.key ? ui.esc(r.key) : `<span class="pos-muted">${__("Supplier not recorded")}</span>`),
				(r) => (r.key ? ui.form_url("Supplier", r.key) : "#"),
				__("No supplier at risk")
			)
		);
	}
}
