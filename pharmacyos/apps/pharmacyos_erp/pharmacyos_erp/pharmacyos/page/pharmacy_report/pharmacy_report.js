// Pharmacy Report — sales, returns, discounts, voids, payment methods, top medicines, cashiers and shifts
// for a period; purchases, stock value and gross profit for roles that may see costs.
// Data: pharmacyos_erp.pharmacy.reports.get_sales_report (owner, managers, accountant only).
frappe.pages["pharmacy-report"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Pharmacy Report"), single_column: true });
	wrapper.report_view = new PharmacyOSReport(page);
};

frappe.pages["pharmacy-report"].on_page_show = function (wrapper) {
	wrapper.report_view && wrapper.report_view.load();
};

class PharmacyOSReport {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.state = { period: "today", from_date: null, to_date: null, branch: pharmacyos.current_branch() || "" };
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		page.add_inner_button(__("Print"), () => window.print());
		this.render_shell();
		pharmacyos.call("pharmacyos_erp.pharmacy.reports.report_periods").then((p) => {
			this.periods = p;
			this.set_period(this.state.period);
		});
	}

	render_shell() {
		const ui = this.ui;
		this.$root.html(`
			<div class="pos-toolbar">
				<div data-slot="periods"></div>
				<input type="date" class="form-control pos-date" data-role="from" style="max-width:160px">
				<span class="pos-muted">–</span>
				<input type="date" class="form-control pos-date" data-role="to" style="max-width:160px">
				<span class="pos-spacer"></span>
				${ui.branch_select(this.state.branch)}
			</div>
			<div class="pos-kpis" data-slot="kpis">${ui.skeleton_kpis(4)}</div>
			<div data-slot="body">${ui.skeleton_rows()}</div>`);
		const options = [
			{ value: "today", label: __("Today") },
			{ value: "yesterday", label: __("Yesterday") },
			{ value: "week", label: __("Last 7 days") },
			{ value: "month", label: __("This month") },
		];
		const $p = this.$root.find('[data-slot="periods"]');
		$p.html(ui.segment(options, this.state.period, __("Period")));
		$p.find("button").on("click", (e) => this.set_period($(e.currentTarget).attr("data-value")));
		this.$root.find('[data-role="from"], [data-role="to"]').on("change", () => {
			this.state.period = "custom";
			this.state.from_date = this.$root.find('[data-role="from"]').val();
			this.state.to_date = this.$root.find('[data-role="to"]').val();
			$p.find("button").removeClass("active").attr("aria-pressed", "false");
			this.load();
		});
		this.$root.find('[data-role="branch"]').on("change", (e) => {
			this.state.branch = e.target.value;
			this.load();
		});
	}

	set_period(period) {
		if (!this.periods || !this.periods[period]) return;
		this.state.period = period;
		[this.state.from_date, this.state.to_date] = this.periods[period];
		this.$root.find('[data-role="from"]').val(this.state.from_date);
		this.$root.find('[data-role="to"]').val(this.state.to_date);
		this.$root.find('[data-slot="periods"] button').each((_i, b) => {
			const on = $(b).attr("data-value") === period;
			$(b).toggleClass("active", on).attr("aria-pressed", on ? "true" : "false");
		});
		this.load();
	}

	load() {
		if (!this.state.from_date) return;
		const token = (this.token = Math.random());
		this.$root.find('[data-slot="body"]').html(this.ui.skeleton_rows());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.reports.get_sales_report", {
				from_date: this.state.from_date,
				to_date: this.state.to_date,
				branch: this.state.branch || null,
			})
			.then((data) => token === this.token && this.render(data))
			.catch((err) => {
				if (token !== this.token) return;
				this.$root.find('[data-slot="kpis"]').empty();
				this.$root.find('[data-slot="body"]').html(this.ui.error_state(err));
			});
	}

	kpi(label, value, sub = "") {
		return `<div class="pos-kpi"><div class="pos-kpi-label">${label}</div><div class="pos-kpi-value">${value}</div><div class="pos-kpi-sub">${sub}</div></div>`;
	}

	table(title, head, rows, empty) {
		const esc = this.ui.esc;
		return `<div class="pos-panel">
			<div class="pos-panel-head"><h3 class="pos-panel-title">${esc(title)}</h3></div>
			${
				rows.length
					? `<div class="pos-table-wrap"><table class="pos-table"><thead><tr>${head
							.map((h) => `<th class="${h.num ? "num" : ""}">${esc(h.label)}</th>`)
							.join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${head[i].num ? "num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
					: this.ui.state({ kind: "empty", title: empty })
			}
		</div>`;
	}

	render(d) {
		const money = (v) => pharmacyos.format_cost(v);
		const qty = (v) => pharmacyos.format_qty(v);
		const esc = this.ui.esc;
		const s = d.sales;
		this.$root.find('[data-slot="kpis"]').html(
			this.kpi(__("Net sales"), money(s.net), __("{0} sales · average {1}", [s.transactions, money(s.average)])) +
				this.kpi(__("Returns"), money(s.returns), __("{0} returns", [s.returns_count])) +
				this.kpi(__("Discounts given"), money(d.discounts.total), __("on {0} sales", [d.discounts.invoices])) +
				this.kpi(__("Voided sales"), money(d.voids.amount), __("{0} voids", [d.voids.count])) +
				(d.costs_visible && d.gross_profit
					? this.kpi(__("Gross profit"), money(d.gross_profit.value), d.gross_profit.margin != null ? __("margin {0}%", [d.gross_profit.margin]) : "")
					: "") +
				(d.stock_value != null ? this.kpi(__("Stock value"), money(d.stock_value), __("now")) : "") +
				(d.purchases ? this.kpi(__("Purchases received"), money(d.purchases.total), __("{0} receipts · {1} supplier returns", [d.purchases.receipts, d.purchases.supplier_returns])) : "")
		);
		const shifts = d.shifts || [];
		this.$root.find('[data-slot="body"]').html(
				this.table(
					__("Payment methods"),
					[{ label: __("Method") }, { label: __("Sales"), num: true }, { label: __("Amount"), num: true }],
					d.payments.map((p) => [esc(p.label), p.count, money(p.amount)]),
					__("No payments in this period")
				) +
				this.table(
					__("Sales by cashier"),
					[{ label: __("Cashier") }, { label: __("Sales"), num: true }, { label: __("Returns"), num: true }, { label: __("Total"), num: true }],
					d.cashiers.map((c) => [esc(c.name || c.user), c.sales, c.returns, money(c.total)]),
					__("No sales in this period")
				) +
				this.table(
					__("Top medicines"),
					[{ label: __("Medicine") }, { label: __("Qty"), num: true }, { label: __("Amount"), num: true }],
					(d.top_items || []).map((i) => [esc((frappe.boot.lang === "ar" && i.name_ar) || i.item_name || i.item_code), qty(i.qty), money(i.amount)]),
					__("No sales in this period")
				) +
				this.table(
					__("Shifts"),
					[{ label: __("Cashier") }, { label: __("Counter") }, { label: __("Opened", null, "Shift table") }, { label: __("Closed", null, "Shift table") }, { label: __("Expected"), num: true }, { label: __("Counted"), num: true }, { label: __("Difference"), num: true }],
					shifts.map((x) => [
						esc(x.name || x.user),
						esc(x.counter),
						esc(frappe.datetime.str_to_user(x.opened)),
						x.closed ? esc(frappe.datetime.str_to_user(x.closed)) : `<span class="pos-chip">${__("Open", null, "Shift status")}</span>`,
						x.expected == null ? "—" : money(x.expected),
						x.counted == null ? "—" : money(x.counted),
						x.difference == null ? "—" : `<span class="${Math.abs(x.difference) > 0.0005 ? "text-danger" : ""}">${money(x.difference)}</span>`,
					]),
					__("No shifts in this period")
				) +
				this.table(
					__("Voided sales"),
					[{ label: __("Sale") }, { label: __("Amount"), num: true }, { label: __("Reason") }, { label: __("Requested by") }, { label: __("Approved by") }],
					d.voids.rows.map((v) => [
						`<a href="${this.ui.form_url("Sales Invoice", v.invoice)}" class="pos-ltr">${esc(v.invoice)}</a>`,
						money(v.amount),
						esc(v.reason),
						esc(v.requested_by),
						esc(v.approved_by),
					]),
					__("No voided sales")
				) +
				`<p class="pos-note">${__(
					"Net sales = sales less returns; voided sales are excluded (they are cancelled). Cash is counted after the change given back."
				)}</p>`
		);
	}
}
