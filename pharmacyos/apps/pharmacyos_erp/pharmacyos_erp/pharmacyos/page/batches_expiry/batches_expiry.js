// Batches & Expiry — what is expired, what expires soon, where it is and what it is worth.
// Data: pharmacyos_erp.pharmacy.expiry.get_batches (server aggregation, permission-aware).
frappe.pages["batches-expiry"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Batches & Expiry"),
		single_column: true,
	});
	wrapper.pos_view = new PharmacyOSBatchesExpiry(page, wrapper);
};

frappe.pages["batches-expiry"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.on_show();
};

class PharmacyOSBatchesExpiry {
	constructor(page, wrapper) {
		this.page = page;
		this.state = {
			bucket: "all",
			search: "",
			branch: pharmacyos.current_branch() || "",
			medicines_only: 0,
			start: 0,
			page_length: 50,
		};
		this.ui = pharmacyos.ui;
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		page.add_inner_button(__("Batch list"), () => frappe.set_route("List", "Batch"));
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
		for (const key of ["bucket", "search", "branch"]) {
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
				<div data-slot="buckets"></div>
				<span class="pos-spacer"></span>
				${ui.search_box(__("Search medicine or batch number"), this.state.search)}
				${ui.branch_select(this.state.branch)}
				<label class="pos-btn" style="gap:8px">
					<input type="checkbox" data-role="medicines" ${this.state.medicines_only ? "checked" : ""}>
					${__("Medicines only")}
				</label>
			</div>
			<div class="pos-kpis" data-slot="summary">${ui.skeleton_kpis(3)}</div>
			<div class="pos-panel">
				<div class="pos-panel-head">
					<h3 class="pos-panel-title" data-slot="title">${__("Batches")}</h3>
					<span class="pos-panel-meta" data-slot="meta"></span>
				</div>
				<div data-slot="body">${ui.skeleton_rows()}</div>
			</div>
			<p class="pos-note">${__(
				"Value at risk is estimated as batch quantity × the medicine's current valuation rate in that warehouse."
			)}</p>`);
		this.render_buckets();

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

	bucket_options(counts = {}) {
		return [
			{ value: "all", label: __("All batches"), count: counts.all },
			{ value: "expired", label: __("Expired"), count: counts.expired },
			{ value: "30", label: __("30 days"), count: counts["30"] },
			{ value: "60", label: __("60 days"), count: counts["60"] },
			{ value: "90", label: __("90 days"), count: counts["90"] },
		];
	}

	render_buckets(counts) {
		const $slot = this.$root.find('[data-slot="buckets"]');
		$slot.html(this.ui.segment(this.bucket_options(counts), this.state.bucket, __("Expiry window")));
		$slot.find("button").on("click", (e) => {
			this.state.bucket = $(e.currentTarget).attr("data-value");
			this.reload();
		});
	}

	load() {
		const token = (this.token = Math.random());
		this.$root.find('[data-slot="body"]').html(this.ui.skeleton_rows());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.expiry.get_batches", {
				bucket: this.state.bucket,
				branch: this.state.branch || null,
				search: this.state.search || null,
				medicines_only: this.state.medicines_only,
				start: this.state.start,
				page_length: this.state.page_length,
			})
			.then((data) => token === this.token && this.render(data))
			.catch((err) => {
				if (token !== this.token) return;
				this.$root.find('[data-slot="summary"]').empty();
				this.$root.find('[data-slot="body"]').html(this.ui.error_state(err));
			});
	}

	render(data) {
		const ui = this.ui;
		this.render_buckets(data.counts);
		const b = this.state.bucket;
		const titles = {
			all: __("All batches in stock"),
			expired: __("Expired batches"),
			30: __("Expiring within 30 days"),
			60: __("Expiring within 60 days"),
			90: __("Expiring within 90 days"),
		};
		this.$root.find('[data-slot="title"]').text(titles[b] || titles.all);
		this.$root.find('[data-slot="meta"]').text(__("{0} batches", [data.total]));

		this.$root.find('[data-slot="summary"]').html(`
			<div class="pos-kpi">
				<div class="pos-kpi-label">${ui.icon("circle-x", "xs")} ${__("Expired — value at risk")}</div>
				<div class="pos-kpi-value">${pharmacyos.format_cost(data.value.expired)}</div>
				<div class="pos-kpi-sub">${__("{0} batches", [data.counts.expired])}</div>
			</div>
			<div class="pos-kpi">
				<div class="pos-kpi-label">${ui.icon("triangle-alert", "xs")} ${__("Expiring in 30 days")}</div>
				<div class="pos-kpi-value">${pharmacyos.format_cost(data.value["30"])}</div>
				<div class="pos-kpi-sub">${__("{0} batches", [data.counts["30"]])}</div>
			</div>
			<div class="pos-kpi">
				<div class="pos-kpi-label">${ui.icon("clock", "xs")} ${__("Expiring in 90 days")}</div>
				<div class="pos-kpi-value">${pharmacyos.format_cost(data.value["90"])}</div>
				<div class="pos-kpi-sub">${__("{0} batches", [data.counts["90"]])}</div>
			</div>`);

		const $body = this.$root.find('[data-slot="body"]');
		if (!data.rows.length) {
			const filtered = this.state.search || this.state.medicines_only;
			$body.html(
				filtered
					? ui.state({ kind: "search", title: __("No matching batches"), text: __("Try a different medicine name or batch number.") })
					: b === "expired"
					? ui.state({ kind: "ok", title: __("No expired stock"), text: __("No expired batch is held in the selected warehouses.") })
					: b === "all"
					? ui.state({
							title: __("No batch stock yet"),
							text: __("Batches appear here once batch-tracked medicines are received into stock."),
					  })
					: ui.state({ kind: "ok", title: __("Nothing expires in this window") })
			);
			return;
		}

		const rows = data.rows;
		const cell_medicine = (r) => {
			const m = ui.medicine_title(r);
			return `<a href="${ui.form_url("Item", r.item_code)}">
				<span class="pos-cell-title">${ui.esc(m.title)}</span>
				<span class="pos-cell-sub">${ui.esc(m.sub || r.item_code)}</span></a>`;
		};
		const source = (r) =>
			r.source_doctype && r.source_name
				? `<a href="${ui.form_url(r.source_doctype, r.source_name)}" class="pos-muted pos-ltr pos-nowrap">${ui.esc(r.source_name)}</a>`
				: "<span class='pos-muted'>—</span>";

		const table = `
			<div class="pos-table-wrap pos-responsive">
				<table class="pos-table">
					<thead><tr>
						<th>${__("Medicine")}</th>
						<th>${__("Batch No")}</th>
						<th>${__("Warehouse")}</th>
						<th class="num">${__("Qty")}</th>
						<th>${__("Expiry")}</th>
						<th>${__("Status")}</th>
						<th class="num">${__("Value at Risk")}</th>
						<th>${__("Supplier")}</th>
						<th>${__("Source")}</th>
					</tr></thead>
					<tbody>${rows
						.map(
							(r) => `<tr>
							<td>${cell_medicine(r)}</td>
							<td><a href="${ui.form_url("Batch", r.batch)}"><span class="pos-code">${ui.esc(r.batch_id)}</span></a></td>
							<td><bdi dir="auto">${ui.esc(r.warehouse)}</bdi></td>
							<td class="num">${pharmacyos.format_qty(r.qty)} <span class="pos-muted">${ui.esc(__(r.uom || ""))}</span></td>
							<td><span class="pos-num">${ui.date(r.expiry_date)}</span><br><span class="pos-days">${ui.days_text(r.days)}</span></td>
							<td>${ui.chip(r.status)}</td>
							<td class="num">${pharmacyos.format_cost(r.value)}</td>
							<td><bdi dir="auto">${ui.esc(r.supplier || "—")}</bdi></td>
							<td>${source(r)}</td>
						</tr>`
						)
						.join("")}</tbody>
				</table>
			</div>
			<div class="pos-cards">${rows
				.map((r) => {
					const m = ui.medicine_title(r);
					return `<a class="pos-card-row" href="${ui.form_url("Batch", r.batch)}">
						<span class="pos-card-title">${ui.esc(m.title)}</span>
						<span class="pos-card-end">${pharmacyos.format_qty(r.qty)}</span>
						<span class="pos-card-meta">
							${ui.chip(r.status)}
							<span class="pos-code">${ui.esc(r.batch_id)}</span>
							<span>${ui.date(r.expiry_date)} · ${ui.days_text(r.days)}</span>
							<span>${ui.esc(r.warehouse)}</span>
						</span>
					</a>`;
				})
				.join("")}</div>
			${this.pager(data.total)}`;
		$body.html(table);
		$body.find("[data-page]").on("click", (e) => {
			this.state.start = Math.max(0, this.state.start + Number($(e.currentTarget).attr("data-page")) * this.state.page_length);
			this.load();
		});
	}

	pager(total) {
		const { start, page_length } = this.state;
		if (total <= page_length) return "";
		const end = Math.min(start + page_length, total);
		return `<div class="pos-pager">
			<span>${__("{0}–{1} of {2}", [start + 1, end, total])}</span>
			<span class="pos-spacer"></span>
			<button class="pos-btn" data-page="-1" ${start === 0 ? "disabled" : ""}>${__("Previous")}</button>
			<button class="pos-btn" data-page="1" ${end >= total ? "disabled" : ""}>${__("Next")}</button>
		</div>`;
	}
}
