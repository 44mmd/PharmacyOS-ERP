// Inventory Health — every stocked medicine with quantity, availability, nearest expiry and state.
// Data: pharmacyos_erp.pharmacy.inventory.get_inventory_health.
frappe.pages["inventory-health"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Inventory Health"), single_column: true });
	wrapper.pos_view = new PharmacyOSInventoryHealth(page);
};
frappe.pages["inventory-health"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.on_show();
};

class PharmacyOSInventoryHealth {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.state = { state: "", search: "", branch: pharmacyos.current_branch() || "", medicines_only: 0, start: 0, page_length: 50 };
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		page.add_inner_button(__("Stock Balance report"), () => frappe.set_route("query-report", "Stock Balance"));
		this.render_shell();
		$(document).on("pharmacyos:branch-changed", (_e, branch) => {
			this.state.branch = branch || "";
			this.$root.find('[data-role="branch"]').val(this.state.branch);
			this.reload();
		});
	}

	on_show() {
		const opts = Object.assign({}, frappe.utils.get_query_params(), frappe.route_options || {});
		frappe.route_options = null;
		let changed = false;
		for (const key of ["state", "search", "branch"]) {
			if (opts[key] !== undefined && opts[key] !== this.state[key]) {
				this.state[key] = opts[key];
				changed = true;
			}
		}
		if (changed) this.render_shell();
		this.reload();
	}

	reload() {
		this.state.start = 0;
		this.load();
	}

	render_shell() {
		const ui = this.ui;
		this.$root.html(`
			<div class="pos-toolbar">
				<div data-slot="states"></div>
				<span class="pos-spacer"></span>
				${ui.search_box(__("Search medicine, generic or Arabic name"), this.state.search)}
				${ui.branch_select(this.state.branch)}
				<label class="pos-btn" style="gap:8px">
					<input type="checkbox" data-role="medicines" ${this.state.medicines_only ? "checked" : ""}>
					${__("Medicines only")}
				</label>
			</div>
			<div class="pos-panel">
				<div class="pos-panel-head">
					<h3 class="pos-panel-title">${__("Stock by medicine")}</h3>
					<span class="pos-panel-meta" data-slot="meta"></span>
				</div>
				<div data-slot="body">${ui.skeleton_rows()}</div>
			</div>
			<p class="pos-note">${__(
				"Low stock compares on-hand quantity with the reorder levels of the warehouses in view. Available = on hand − reserved."
			)}</p>`);
		this.render_states();
		this.$root.find(".pos-search input").on(
			"input",
			ui.debounce((e) => {
				this.state.search = e.target.value;
				this.reload();
			}, 300)
		);
		this.$root.find('[data-role="branch"]').on("change", (e) => {
			this.state.branch = e.target.value;
			this.reload();
		});
		this.$root.find('[data-role="medicines"]').on("change", (e) => {
			this.state.medicines_only = e.target.checked ? 1 : 0;
			this.reload();
		});
	}

	render_states(counts = {}) {
		const total = Object.values(counts).reduce((a, b) => a + b, 0);
		const opts = [
			{ value: "", label: __("All"), count: counts.out === undefined ? undefined : total },
			{ value: "out", label: __("Out of Stock"), count: counts.out },
			{ value: "expired", label: __("Expired"), count: counts.expired },
			{ value: "low", label: __("Low Stock"), count: counts.low },
			{ value: "expiring", label: __("Expiring"), count: counts.expiring },
			{ value: "ok", label: __("Healthy"), count: counts.ok },
		];
		const $slot = this.$root.find('[data-slot="states"]');
		$slot.html(this.ui.segment(opts, this.state.state, __("Stock state")));
		$slot.find("button").on("click", (e) => {
			this.state.state = $(e.currentTarget).attr("data-value");
			this.reload();
		});
	}

	load() {
		const token = (this.token = Math.random());
		this.$root.find('[data-slot="body"]').html(this.ui.skeleton_rows());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.inventory.get_inventory_health", {
				state: this.state.state || null,
				branch: this.state.branch || null,
				search: this.state.search || null,
				medicines_only: this.state.medicines_only,
				start: this.state.start,
				page_length: this.state.page_length,
			})
			.then((d) => token === this.token && this.render(d))
			.catch((err) => token === this.token && this.$root.find('[data-slot="body"]').html(this.ui.error_state(err)));
	}

	render(data) {
		const ui = this.ui;
		this.render_states(data.counts);
		this.$root
			.find('[data-slot="meta"]')
			.text(`${__("{0} medicines", [data.total])} · ${__("Inventory value")}: ${pharmacyos.format_money(data.inventory_value)}`);
		const $body = this.$root.find('[data-slot="body"]');
		if (!data.rows.length) {
			$body.html(
				this.state.search
					? ui.state({ kind: "search", title: __("No matching medicines") })
					: this.state.state
					? ui.state({ kind: "ok", title: __("Nothing in this state") })
					: ui.state({ title: __("No stock yet"), text: __("Receive medicines into a warehouse to see their health here.") })
			);
			return;
		}
		const expiry = (r) =>
			r.nearest_expiry
				? `<span class="pos-num">${ui.date(r.nearest_expiry)}</span><br><span class="pos-days">${ui.days_text(r.nearest_days)}</span>`
				: "<span class='pos-muted'>—</span>";
		$body.html(`
			<div class="pos-table-wrap pos-responsive">
				<table class="pos-table">
					<thead><tr>
						<th>${__("Medicine")}</th>
						<th>${__("State")}</th>
						<th class="num">${__("On Hand")}</th>
						<th class="num">${__("Available")}</th>
						<th class="num">${__("Reorder Level")}</th>
						<th class="num">${__("Warehouses")}</th>
						<th class="num">${__("Batches")}</th>
						<th>${__("Nearest Expiry")}</th>
						<th class="num">${__("Expired Qty")}</th>
						<th class="num">${__("Value")}</th>
					</tr></thead>
					<tbody>${data.rows
						.map((r) => {
							const m = ui.medicine_title(r);
							return `<tr>
								<td><a href="${ui.form_url("Item", r.item_code)}"><span class="pos-cell-title">${ui.esc(m.title)}</span>
									<span class="pos-cell-sub">${ui.esc(m.sub || r.item_code)}</span></a></td>
								<td>${ui.chip(r.state, ui.STOCK_STATUS)}</td>
								<td class="num">${pharmacyos.format_qty(r.qty)} <span class="pos-muted">${ui.esc(__(r.uom || ""))}</span></td>
								<td class="num">${pharmacyos.format_qty(r.available)}</td>
								<td class="num">${r.reorder_level ? pharmacyos.format_qty(r.reorder_level) : "<span class='pos-muted'>—</span>"}</td>
								<td class="num">${r.warehouses}</td>
								<td class="num"><a href="${pharmacyos.ui.base}/batches-expiry?search=${encodeURIComponent(r.item_code)}">${r.batches}</a></td>
								<td>${expiry(r)}</td>
								<td class="num">${r.expired_qty ? pharmacyos.format_qty(r.expired_qty) : "<span class='pos-muted'>—</span>"}</td>
								<td class="num">${pharmacyos.format_money(r.value)}</td>
							</tr>`;
						})
						.join("")}</tbody>
				</table>
			</div>
			<div class="pos-cards">${data.rows
				.map((r) => {
					const m = ui.medicine_title(r);
					return `<a class="pos-card-row" href="${ui.form_url("Item", r.item_code)}">
						<span class="pos-card-title">${ui.esc(m.title)}</span>
						<span class="pos-card-end">${pharmacyos.format_qty(r.qty)}</span>
						<span class="pos-card-meta">${ui.chip(r.state, ui.STOCK_STATUS)}
							${r.nearest_expiry ? `<span>${__("Expiry")}: ${ui.date(r.nearest_expiry)}</span>` : ""}
							${r.reorder_level ? `<span>${__("Reorder at")} ${pharmacyos.format_qty(r.reorder_level)}</span>` : ""}
						</span></a>`;
				})
				.join("")}</div>
			${this.pager(data.total)}`);
		$body.find("[data-page]").on("click", (e) => {
			this.state.start = Math.max(0, this.state.start + Number($(e.currentTarget).attr("data-page")) * this.state.page_length);
			this.load();
		});
	}

	pager(total) {
		const { start, page_length } = this.state;
		if (total <= page_length) return "";
		const end = Math.min(start + page_length, total);
		return `<div class="pos-pager"><span>${__("{0}–{1} of {2}", [start + 1, end, total])}</span><span class="pos-spacer"></span>
			<button class="pos-btn" data-page="-1" ${start === 0 ? "disabled" : ""}>${__("Previous")}</button>
			<button class="pos-btn" data-page="1" ${end >= total ? "disabled" : ""}>${__("Next")}</button></div>`;
	}
}
