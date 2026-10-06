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
		page.add_inner_button(__("Disposal history"), () => this.show_disposals());
		this.can_dispose = false;
		pharmacyos.call("pharmacyos_erp.pharmacy.disposal.can_dispose").then((ok) => {
			this.can_dispose = Boolean(ok);
		});
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
						${this.can_dispose ? "<th></th>" : ""}
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
							${this.can_dispose ? `<td><button class="pos-btn ${r.status === "expired" ? "pos-btn-danger" : ""}" data-dispose="${rows.indexOf(r)}">${__("Dispose")}</button></td>` : ""}
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
		$body.find("[data-dispose]").on("click", (e) => this.dispose(rows[Number($(e.currentTarget).attr("data-dispose"))]));
		$body.find("[data-page]").on("click", (e) => {
			this.state.start = Math.max(0, this.state.start + Number($(e.currentTarget).attr("data-page")) * this.state.page_length);
			this.load();
		});
	}

	// Write off a batch (expired, damaged, recalled): a submitted Stock Entry keeps who, what, when and why.
	dispose(r) {
		const m = this.ui.medicine_title(r);
		const d = new frappe.ui.Dialog({
			title: __("Dispose of stock"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p><b>${frappe.utils.escape_html(m.title)}</b> — ${__("batch")} <span class="pos-code">${frappe.utils.escape_html(r.batch_id)}</span>,
						${__("expiry")} ${this.ui.date(r.expiry_date)}<br>${frappe.utils.escape_html(r.warehouse)}: ${pharmacyos.format_qty(r.qty)} ${frappe.utils.escape_html(__(r.uom || ""))}</p>`,
				},
				{ fieldname: "qty", fieldtype: "Float", label: __("Quantity to dispose of"), reqd: 1, default: r.qty },
				{
					fieldname: "reason",
					fieldtype: "Select",
					label: __("Reason"),
					reqd: 1,
					options: [
						{ value: "Expired", label: __("Expired") },
						{ value: "Damaged", label: __("Damaged") },
						{ value: "Recalled", label: __("Recalled") },
						{ value: "Other", label: __("Other") },
					],
					default: r.status === "expired" ? "Expired" : "Damaged",
				},
				{ fieldname: "note", fieldtype: "Small Text", label: __("Note") },
				{
					fieldtype: "HTML",
					options: `<p class="text-muted small">${__(
						"The units leave stock now and are recorded as a loss. The disposal stays in the history with your name; only a stock manager can cancel it."
					)}</p>`,
				},
			],
			primary_action_label: __("Dispose"),
			primary_action: (values) => {
				if (values.qty <= 0 || values.qty > r.qty) {
					frappe.msgprint(__("Enter a quantity between 0 and {0}.", [pharmacyos.format_qty(r.qty)]));
					return;
				}
				d.get_primary_btn().prop("disabled", true);
				frappe
					.xcall("pharmacyos_erp.pharmacy.disposal.dispose_batch", {
						item_code: r.item_code,
						batch: r.batch,
						warehouse: r.warehouse,
						qty: values.qty,
						reason: values.reason,
						note: values.note || null,
					})
					.then((res) => {
						d.hide();
						frappe.show_alert({ message: __("Disposed of {0} of batch {1} ({2}).", [pharmacyos.format_qty(res.qty), res.batch_id, res.name]), indicator: "green" });
						this.load();
					})
					.catch(() => d.get_primary_btn().prop("disabled", false));
			},
		});
		d.show();
	}

	show_disposals() {
		const d = new frappe.ui.Dialog({ title: __("Disposal history"), size: "large", fields: [{ fieldname: "list", fieldtype: "HTML" }] });
		d.fields_dict.list.$wrapper.html(this.ui.skeleton_rows());
		d.show();
		pharmacyos.call("pharmacyos_erp.pharmacy.disposal.get_disposals", { limit: 100 }).then((rows) => {
			if (!rows.length) {
				d.fields_dict.list.$wrapper.html(this.ui.state({ kind: "ok", title: __("No disposals yet") }));
				return;
			}
			const esc = frappe.utils.escape_html;
			d.fields_dict.list.$wrapper.html(`<div class="pos-table-wrap"><table class="pos-table">
				<thead><tr><th>${__("Date")}</th><th>${__("Medicine")}</th><th>${__("Batch No")}</th><th class="num">${__("Qty")}</th><th>${__("Reason")}</th><th>${__("By")}</th><th></th></tr></thead>
				<tbody>${rows
					.map((e) =>
						e.items
							.map(
								(i) => `<tr><td class="pos-num">${this.ui.date(e.posting_date)}</td><td>${esc(i.item_name || i.item_code)}</td>
								<td><span class="pos-code">${esc(i.batch_id || "")}</span></td><td class="num">${pharmacyos.format_qty(i.qty)}</td>
								<td>${esc(__(e.reason || ""))}</td><td>${esc(e.user || e.owner)}</td>
								<td><a href="${this.ui.form_url("Stock Entry", e.name)}" class="pos-ltr">${esc(e.name)}</a></td></tr>`
							)
							.join("")
					)
					.join("")}</tbody></table></div>`);
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
