// Pharmacy Dashboard — the PharmacyOS command center. Real ERPNext data only
// (pharmacyos_erp.pharmacy.dashboard.get_dashboard); every actionable card opens the filtered view.
frappe.pages["pharmacy-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Dashboard"), single_column: true });
	wrapper.pos_view = new PharmacyOSDashboard(page);
};
frappe.pages["pharmacy-dashboard"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.load();
};

class PharmacyOSDashboard {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.branch = pharmacyos.current_branch() || "";
		this.$root = $(`<div class="pos-page pos-dashboard"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		if (frappe.model.can_create("Item")) page.add_inner_button(__("Add Medicine"), () => pharmacyos.medicine_dialog());
		if (frappe.model.can_create("Purchase Receipt")) page.add_inner_button(__("Receive Stock"), () => frappe.new_doc("Purchase Receipt"));
		if (frappe.boot.user.can_read.includes("POS Profile")) page.add_inner_button(__("Open POS"), () => frappe.set_route("point-of-sale"));
		this.render_shell();
		$(document).on("pharmacyos:branch-changed", (_e, branch) => {
			this.branch = branch || "";
			this.load();
		});
	}

	greeting() {
		const h = new Date().getHours();
		const name = (frappe.boot.user.first_name || frappe.session.user_fullname || "").split(" ")[0];
		const g = h < 12 ? __("Good morning") : h < 18 ? __("Good afternoon") : __("Good evening");
		return name ? __("{0}, {1}", [g, name], "greeting") : g;
	}

	render_shell() {
		const ui = this.ui;
		this.$root.html(`
			<header class="pos-hero">
				<div>
					<h2 class="pos-hero-title">${ui.esc(this.greeting())}</h2>
					<p class="pos-hero-sub" data-slot="context"></p>
				</div>
			</header>
			<div class="pos-kpis" data-slot="kpis">${ui.skeleton_kpis(5)}</div>
			<h3 class="pos-section-title">${ui.icon("triangle-alert", "xs")} ${__("Needs Attention")}</h3>
			<div class="pos-attention" data-slot="attention">${ui.skeleton_kpis(4)}</div>
			<div class="pos-grid">
				<section class="pos-panel pos-col-7" data-slot="top"></section>
				<section class="pos-panel pos-col-5" data-slot="slow"></section>
				<section class="pos-panel pos-col-6" data-slot="sales"></section>
				<section class="pos-panel pos-col-6" data-slot="moves"></section>
			</div>`);
		this.$root.find("[data-slot=top],[data-slot=slow],[data-slot=sales],[data-slot=moves]").html(ui.skeleton_rows(5));
	}

	load() {
		const token = (this.token = Math.random());
		pharmacyos
			.call("pharmacyos_erp.pharmacy.dashboard.get_dashboard", { branch: this.branch || null })
			.then((d) => token === this.token && this.render(d))
			.catch((err) => {
				if (token !== this.token) return;
				this.$root.find("[data-slot=kpis]").html("");
				for (const sys of d.system || []) {
			if (sys.kind === "backup") att("danger", "shield-alert", sys.count, __("Backup needs attention"), "/desk/system-status");
			if (sys.kind === "sync")
				att("warning", "cloud-off", sys.count, sys.state === "offline" ? __("Changes waiting to sync (offline)") : __("Changes that could not be synced"), "/desk/system-status");
		}
		this.$root.find("[data-slot=attention]").html(`<div class="pos-panel" style="grid-column:1/-1">${this.ui.error_state(err)}</div>`);
			});
	}

	render(d) {
		const ui = this.ui;
		const money = (v) => pharmacyos.format_money(v);
		const branch_label = this.branch ? pharmacyos.branch_label(this.branch) : __("All branches");
		this.$root
			.find("[data-slot=context]")
			.html(`${ui.esc(ui.date(d.date))} · ${ui.esc(branch_label)}`);

		// KPIs ------------------------------------------------------------------------------------
		const kpi = ({ label, icon, value, sub = "", href = null, primary = false }) => {
			const tag = href ? "a" : "div";
			return `<${tag} class="pos-kpi pos-fade-in ${primary ? "is-primary" : ""}" ${href ? `href="${href}"` : ""}>
				<div class="pos-kpi-label">${ui.icon(icon, "xs")} ${ui.esc(label)}</div>
				<div class="pos-kpi-value">${value}</div>
				<div class="pos-kpi-sub">${sub}</div></${tag}>`;
		};
		const na = `<span class="pos-muted" style="font-size:14px;font-weight:500">${__("No access")}</span>`;
		const s = d.sales;
		const gp = d.gross_profit;
		const k = [];
		k.push(
			kpi({
				label: __("Sales today"),
				icon: "receipt",
				value: s ? money(s.total) : na,
				sub: s ? __("{0} transactions · {1} returns", [s.transactions, s.returns]) : "",
				href: s ? ui.list_url("Sales Invoice", { posting_date: d.date, docstatus: 1 }) : null,
				primary: true,
			})
		);
		k.push(
			kpi({
				label: __("Net revenue today"),
				icon: "banknote",
				value: s ? money(s.net) : na,
				sub: s && d.week ? __("7 days: {0}", [money(d.week.total)]) : "",
			})
		);
		k.push(
			kpi({
				label: __("Gross profit today"),
				icon: "trending-up",
				value: gp && gp.value !== null ? money(gp.value) : gp ? `<span class="pos-muted" style="font-size:14px">${__("Not available")}</span>` : na,
				sub: gp
					? gp.value === null
						? __("Needs stock-updating invoices")
						: `${gp.margin !== null ? __("Margin {0}", [ui.ltr(`${gp.margin}%`)]) : ""}${gp.partial ? " · " + __("partial") : ""}`
					: "",
			})
		);
		k.push(
			kpi({
				label: __("Inventory value"),
				icon: "package",
				value: d.stock ? money(d.stock.inventory_value) : na,
				sub: d.stock ? __("at current valuation") : "",
				href: d.stock ? "/desk/inventory-health" : null,
			})
		);
		if (s && (frappe.user.has_role("Cashier") || frappe.user.has_role("Pharmacist"))) {
			k.push(kpi({ label: __("My sales today"), icon: "user-cog", value: money(s.mine) }));
		}
		this.$root.find("[data-slot=kpis]").html(k.join(""));

		// Needs attention --------------------------------------------------------------------------
		const st = d.stock;
		const p = d.purchasing || {};
		const items = [];
		// the amount sits on its own line so a truncated label never cuts into a number
		const att = (tone, icon, count, label, href, value = "") =>
			items.push(`<a class="pos-attention-item pos-fade-in" data-tone="${count ? tone : "ok"}" data-zero="${count ? 0 : 1}" href="${href}">
				<span class="pos-attention-icon">${ui.icon(count ? icon : "circle-check", "sm")}</span>
				<span class="pos-attention-body"><span class="pos-attention-count">${count}</span>
				<span class="pos-attention-label">${ui.esc(label)}</span>${
					value ? `<span class="pos-attention-value">${value}</span>` : ""
				}</span></a>`);
		if (st) {
			att("danger", "circle-x", st.expired_batches, __("Expired batches"), "/desk/batches-expiry?bucket=expired", st.expired_batches ? money(st.expired_value) : "");
			att("warning", "calendar-clock", st.expiring_30, __("Expiring within 30 days"), "/desk/batches-expiry?bucket=30");
			att("danger", "package-x", st.counts.out, __("Out of stock"), "/desk/inventory-health?state=out");
			att("warning", "trending-down", st.counts.low, __("Low stock"), "/desk/inventory-health?state=low");
			if (st.negative_bins) att("danger", "scale", st.negative_bins, __("Negative stock balances"), ui.list_url("Bin", { actual_qty: ["<", 0] }));
		}
		if (p.pending_orders) {
			att("info", "truck", p.pending_orders.count, __("Orders awaiting delivery"), ui.list_url("Purchase Order", { status: ["in", ["To Receive and Bill", "To Receive"]] }));
		}
		if (p.draft_receipts !== null && p.draft_receipts !== undefined) {
			att("info", "package-check", p.draft_receipts, __("Receipts in progress (draft)"), ui.list_url("Purchase Receipt", { docstatus: 0 }));
		}
		if (p.supplier_obligations) {
			att("info", "hand-coins", p.supplier_obligations.count, __("Unpaid supplier invoices"), "/desk/query-report/Accounts Payable", p.supplier_obligations.count ? money(p.supplier_obligations.value) : "");
		}
		for (const sys of d.system || []) {
			if (sys.kind === "backup") att("danger", "shield-alert", sys.count, __("Backup needs attention"), "/desk/system-status");
			if (sys.kind === "sync")
				att("warning", "cloud-off", sys.count, sys.state === "offline" ? __("Changes waiting to sync (offline)") : __("Changes that could not be synced"), "/desk/system-status");
		}
		this.$root.find("[data-slot=attention]").html(items.join("") || ui.state({ kind: "denied", title: __("No access") }));

		// Panels -------------------------------------------------------------------------------------
		const panel = (title, meta, body, link) => `
			<div class="pos-panel-head"><h3 class="pos-panel-title">${ui.esc(title)}</h3>
			${link ? `<a class="pos-panel-link" href="${link.href}">${ui.esc(link.label)}</a>` : `<span class="pos-panel-meta">${ui.esc(meta || "")}</span>`}</div>${body}`;
		const denied = ui.state({ kind: "denied", title: __("No access"), text: __("Your role does not include this information.") });
		const med = (r) => {
			const m = ui.medicine_title(r);
			return `<a href="${ui.form_url("Item", r.item_code)}"><span class="pos-cell-title">${ui.esc(m.title)}</span><span class="pos-cell-sub">${ui.esc(m.sub || r.item_code)}</span></a>`;
		};

		const top = d.top_sellers;
		const max_top = Math.max(...(top || []).map((r) => r.amount), 1);
		this.$root.find("[data-slot=top]").html(
			panel(
				__("Top-selling medicines"),
				__("Last 30 days"),
				top === null
					? denied
					: top.length
					? `<ul class="pos-list">${top
							.map(
								(r) => `<li title="${ui.esc(r.item_name)}: ${ui.esc(money(r.amount))}"><div class="pos-list-main">${med(r)}
									<div class="pos-bar"><span style="width:${Math.max(2, Math.round((100 * r.amount) / max_top))}%"></span></div></div>
									<div class="pos-list-end">${money(r.amount)}<div class="pos-days">${__("{0} sold", [pharmacyos.format_qty(r.qty)])}</div></div></li>`
							)
							.join("")}</ul>`
					: ui.state({ title: __("No sales in the last 30 days") })
			)
		);

		const slow = d.slow_movers;
		this.$root.find("[data-slot=slow]").html(
			panel(
				__("Slow-moving stock"),
				__("No sales in 60 days"),
				slow === null
					? denied
					: slow.length
					? `<ul class="pos-list">${slow
							.map((r) => `<li><div class="pos-list-main">${med(r)}</div><div class="pos-list-end">${money(r.value)}<div class="pos-days">${__("{0} in stock", [pharmacyos.format_qty(r.qty)])}</div></div></li>`)
							.join("")}</ul>`
					: ui.state({ kind: "ok", title: __("Everything in stock has sold recently") })
			)
		);

		const sales = d.recent_sales;
		this.$root.find("[data-slot=sales]").html(
			panel(
				__("Recent sales"),
				"",
				sales === null
					? denied
					: sales.length
					? `<ul class="pos-list">${sales
							.map(
								(r) => `<li><div class="pos-list-main"><a href="${ui.form_url("Sales Invoice", r.name)}"><span class="pos-cell-title">${ui.esc(r.customer_name)}</span>
									<span class="pos-cell-sub">${ui.esc(r.name)} · ${ui.esc(ui.date(r.posting_date))}</span></a></div>
									<div class="pos-list-end">${r.is_return ? `<span class="pos-chip" data-status="info">${__("Return")}</span> ` : ""}${money(r.base_grand_total)}</div></li>`
							)
							.join("")}</ul>`
					: ui.state({ title: __("No sales this week") }),
				sales && sales.length ? { href: ui.list_url("Sales Invoice"), label: __("All invoices") } : null
			)
		);

		const moves = d.recent_movements;
		this.$root.find("[data-slot=moves]").html(
			panel(
				__("Recent stock movement"),
				"",
				moves === null
					? denied
					: moves.length
					? `<ul class="pos-list">${moves
							.map(
								(r) => `<li><div class="pos-list-main"><a href="${ui.form_url(r.voucher_type, r.voucher_no)}"><span class="pos-cell-title">${ui.esc(r.item_name || r.item_code)}</span>
									<span class="pos-cell-sub">${ui.esc(__(r.voucher_type))} · ${ui.esc(r.warehouse)}</span></a></div>
									<div class="pos-list-end" style="color:${r.actual_qty < 0 ? "var(--pos-ink)" : "var(--pos-green)"}">${r.actual_qty > 0 ? "+" : ""}${pharmacyos.format_qty(r.actual_qty)}</div></li>`
							)
							.join("")}</ul>`
					: ui.state({ title: __("No stock movement yet") }),
				moves && moves.length ? { href: "/desk/query-report/Stock Ledger", label: __("Stock ledger") } : null
			)
		);
	}
}
