// Reorder Suggestions — medicines whose projected stock is below the reorder level (ERPNext's rule),
// with preferred supplier and a draft Purchase Order action. No forecasting is claimed.
frappe.pages["reorder-suggestions"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Reorder Suggestions"), single_column: true });
	wrapper.pos_view = new PharmacyOSReorder(page);
};
frappe.pages["reorder-suggestions"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.load();
};

class PharmacyOSReorder {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.state = { branch: pharmacyos.current_branch() || "", search: "" };
		this.selected = new Set();
		this.rows = [];
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		this.$root.html(`
			<div class="pos-toolbar">
				${this.ui.search_box(__("Search medicine"), "")}
				${this.ui.branch_select(this.state.branch)}
				<span class="pos-spacer"></span>
				<button class="pos-btn" data-variant="primary" data-role="order" disabled>${this.ui.icon("file-text", "xs")} ${__("Create Purchase Order")}</button>
			</div>
			<div class="pos-panel">
				<div class="pos-panel-head">
					<h3 class="pos-panel-title">${__("Below reorder level")}</h3>
					<span class="pos-panel-meta" data-slot="meta"></span>
				</div>
				<div data-slot="body">${this.ui.skeleton_rows()}</div>
			</div>
			<p class="pos-note">${__(
				"Suggested when projected stock (on hand + ordered − reserved) is below the reorder level. Suggested quantity = the larger of the reorder quantity and the shortfall. Reorder levels are set per warehouse on each medicine."
			)}</p>`);
		this.$root.find(".pos-search input").on(
			"input",
			this.ui.debounce((e) => {
				this.state.search = e.target.value;
				this.load();
			}, 300)
		);
		this.$root.find('[data-role="branch"]').on("change", (e) => {
			this.state.branch = e.target.value;
			this.load();
		});
		this.$root.find('[data-role="order"]').on("click", () => this.create_po());
		$(document).on("pharmacyos:branch-changed", (_e, branch) => {
			this.state.branch = branch || "";
			this.$root.find('[data-role="branch"]').val(this.state.branch);
			this.load();
		});
	}

	load() {
		const token = (this.token = Math.random());
		this.selected.clear();
		this.update_action();
		this.$root.find('[data-slot="body"]').html(this.ui.skeleton_rows());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.inventory.get_reorder_suggestions", {
				branch: this.state.branch || null,
				search: this.state.search || null,
			})
			.then((d) => token === this.token && this.render(d))
			.catch((err) => token === this.token && this.$root.find('[data-slot="body"]').html(this.ui.error_state(err)));
	}

	render(data) {
		const ui = this.ui;
		this.rows = data.rows;
		this.$root.find('[data-slot="meta"]').text(__("{0} suggestions", [data.total]));
		const $body = this.$root.find('[data-slot="body"]');
		if (!data.rows.length) {
			$body.html(
				ui.state({
					kind: "ok",
					title: __("Nothing to reorder"),
					text: __("No medicine in view is below its reorder level. Medicines without reorder levels are not checked."),
				})
			);
			return;
		}
		const action = (r) =>
			r.request_type === "Transfer" ? __("Transfer from another warehouse") : r.supplier ? __("Order from {0}", [r.supplier]) : __("Order (choose supplier)");
		$body.html(`
			<div class="pos-table-wrap pos-responsive">
				<table class="pos-table">
					<thead><tr>
						<th style="width:32px"><span class="sr-only">${__("Select")}</span></th>
						<th>${__("Medicine")}</th>
						<th>${__("Warehouse")}</th>
						<th class="num">${__("On Hand")}</th>
						<th class="num">${__("Projected")}</th>
						<th class="num">${__("Reorder Level")}</th>
						<th class="num">${__("Suggested Qty")}</th>
						<th>${__("Preferred Supplier")}</th>
						<th>${__("Suggested Action")}</th>
					</tr></thead>
					<tbody>${data.rows
						.map((r, i) => {
							const m = ui.medicine_title(r);
							return `<tr>
								<td><input type="checkbox" data-index="${i}" ${r.request_type === "Transfer" ? "disabled" : ""} aria-label="${ui.esc(m.title)}"></td>
								<td><a href="${ui.form_url("Item", r.item_code)}"><span class="pos-cell-title">${ui.esc(m.title)}</span>
									<span class="pos-cell-sub">${ui.esc(m.sub || r.item_code)}</span></a></td>
								<td>${ui.esc(r.warehouse)}</td>
								<td class="num">${pharmacyos.format_qty(r.qty)}</td>
								<td class="num">${pharmacyos.format_qty(r.projected)}</td>
								<td class="num">${pharmacyos.format_qty(r.reorder_level)}</td>
								<td class="num"><strong>${pharmacyos.format_qty(r.suggested_qty)}</strong> <span class="pos-muted">${ui.esc(__(r.uom || ""))}</span></td>
								<td>${r.supplier ? ui.esc(r.supplier) : `<span class="pos-chip" data-status="none">${__("Not set")}</span>`}</td>
								<td>${ui.esc(action(r))}</td>
							</tr>`;
						})
						.join("")}</tbody>
				</table>
			</div>
			<div class="pos-cards">${data.rows
				.map((r) => {
					const m = ui.medicine_title(r);
					return `<div class="pos-card-row">
						<span class="pos-card-title">${ui.esc(m.title)}</span>
						<span class="pos-card-end">+${pharmacyos.format_qty(r.suggested_qty)}</span>
						<span class="pos-card-meta"><span>${ui.esc(r.warehouse)}</span>
							<span>${__("Projected")}: ${pharmacyos.format_qty(r.projected)} / ${pharmacyos.format_qty(r.reorder_level)}</span>
							<span>${ui.esc(action(r))}</span></span></div>`;
				})
				.join("")}</div>`);
		$body.find("input[type=checkbox]").on("change", (e) => {
			const i = Number(e.target.dataset.index);
			e.target.checked ? this.selected.add(i) : this.selected.delete(i);
			this.update_action();
		});
	}

	update_action() {
		this.$root.find('[data-role="order"]').prop("disabled", this.selected.size === 0);
	}

	create_po() {
		const rows = [...this.selected].map((i) => this.rows[i]);
		const suppliers = [...new Set(rows.map((r) => r.supplier || ""))];
		const go = (supplier) =>
			frappe
				.xcall("pharmacyos_erp.pharmacy.purchasing.make_purchase_order", {
					supplier,
					items: rows.map((r) => ({ item_code: r.item_code, qty: r.suggested_qty, warehouse: r.warehouse })),
				})
				.then((name) => frappe.set_route("Form", "Purchase Order", name));

		if (suppliers.length === 1 && suppliers[0]) return go(suppliers[0]);
		const d = new frappe.ui.Dialog({
			title: __("Choose supplier"),
			fields: [
				{
					fieldname: "supplier",
					fieldtype: "Link",
					options: "Supplier",
					reqd: 1,
					label: __("Supplier"),
					description:
						suppliers.length > 1
							? __("The selected medicines have different preferred suppliers. One draft order is created for the supplier you choose.")
							: "",
				},
			],
			primary_action_label: __("Create Draft Order"),
			primary_action: ({ supplier }) => {
				d.hide();
				go(supplier);
			},
		});
		d.show();
	}
}
