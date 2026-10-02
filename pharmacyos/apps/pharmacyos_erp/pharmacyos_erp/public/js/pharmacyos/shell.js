// Shell additions inside the PharmacyOS sidebars: branch context chip + compact signature.
// Re-applied on route change (idempotent); no global DOM observers.
frappe.provide("pharmacyos.shell");

pharmacyos.shell.in_pharmacyos_shell = () => {
	const sidebar = frappe.app && frappe.app.sidebar;
	const current = sidebar && sidebar.current_module;
	const meta = current && frappe.boot.module_sidebars && frappe.boot.module_sidebars[current];
	return !!(meta && meta.app === "pharmacyos_erp");
};

pharmacyos.shell.render_branch_chip = () => {
	const $sidebar = $(".body-sidebar");
	if (!$sidebar.length) return;
	$sidebar.find(".pos-branch-chip, .pos-sidebar-signature").remove();
	if (!pharmacyos.shell.in_pharmacyos_shell() || !frappe.boot.pharmacyos) return;

	const branch = pharmacyos.current_branch();
	const label = branch ? pharmacyos.branch_label(branch) : __("All branches");
	const $chip = $(`
		<button type="button" class="pos-branch-chip" aria-haspopup="dialog">
			<span class="pos-branch-dot" aria-hidden="true"></span>
			<span class="pos-branch-meta">
				<span class="pos-branch-kicker">${__("Branch")}</span>
				<span class="pos-branch-name">${pharmacyos.ui.esc(label)}</span>
			</span>
		</button>`);
	$chip.on("click", pharmacyos.shell.pick_branch);
	const $header = $sidebar.find(".sidebar-header").first();
	$header.length ? $chip.insertAfter($header) : $sidebar.prepend($chip);

	const brand = pharmacyos.brand();
	$sidebar.append(
		`<div class="pos-sidebar-signature" aria-hidden="true">${pharmacyos.ui.esc(brand.short_name || "")}
			<span>· ${pharmacyos.ui.esc(__(brand.attribution_short || ""))}</span></div>`
	);
};

pharmacyos.shell.pick_branch = () => {
	const branches = pharmacyos.boot().branches || [];
	if (!branches.length) {
		frappe.msgprint({
			title: __("No branches yet"),
			message: __(
				"Branches are set up in Team → Branches. Each branch links to its own warehouse; until then PharmacyOS shows all warehouses you can access."
			),
			indicator: "blue",
		});
		return;
	}
	const d = new frappe.ui.Dialog({
		title: __("Switch branch"),
		fields: [
			{
				fieldname: "branch",
				fieldtype: "Select",
				label: __("Branch"),
				options: [
					{ value: "", label: __("All branches") },
					...branches.map((b) => ({ value: b.name, label: pharmacyos.branch_label(b.name) })),
				],
				default: pharmacyos.current_branch() || "",
				description: __("Changes what PharmacyOS dashboards show. Access is still controlled by your permissions."),
			},
		],
		primary_action_label: __("Switch"),
		primary_action: ({ branch }) => {
			frappe
				.xcall("pharmacyos_erp.pharmacy.branches.set_current_branch", { branch: branch || null })
				.then((value) => {
					frappe.boot.pharmacyos.branch = value || null;
					d.hide();
					pharmacyos.shell.render_branch_chip();
					$(document).trigger("pharmacyos:branch-changed", [value || null]);
				});
		},
	});
	d.show();
};

$(document).on("app_ready", () => {
	if (!frappe.boot.pharmacyos) return;
	const refresh = () => requestAnimationFrame(pharmacyos.shell.render_branch_chip);
	frappe.router.on("change", refresh);
	refresh();
});
