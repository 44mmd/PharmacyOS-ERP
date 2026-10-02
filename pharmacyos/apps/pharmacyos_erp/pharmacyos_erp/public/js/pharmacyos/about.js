// "About" — PharmacyOS ERP by HALF. Third-party licence notices required by the licences of the
// bundled components stay available here, collapsed under "Third-party licences" (do not remove).
frappe.provide("frappe.ui.misc");

frappe.ui.misc.about = function () {
	const brand = pharmacyos.brand();
	const esc = pharmacyos.ui.esc;
	const versions = frappe.boot.versions || {};
	const upstream = (brand.upstream || [])
		.map((u) => {
			const key = u.name === "ERPNext" ? "erpnext" : u.name === "Frappe Framework" ? "frappe" : null;
			const version = key && versions[key] ? ` <span class="pos-muted pos-ltr">v${esc(versions[key])}</span>` : "";
			return `<li><a href="${esc(u.url)}" target="_blank" rel="noopener noreferrer">${esc(
				u.name
			)}</a>${version} — ${esc(__(u.license))}, © ${esc(u.copyright)}</li>`;
		})
		.join("");

	const d = new frappe.ui.Dialog({ title: __("About {0}", [brand.product_name || "PharmacyOS ERP"]) });
	$(d.body).html(`
		<div class="pos-about">
			<div class="pos-about-head">
				<img src="${esc(brand.mark_url)}" alt="">
				<div>
					<div class="pos-about-name">${esc(brand.product_name)}</div>
					<div class="pos-about-desc">${esc(__(brand.descriptor || ""))} · <span class="pos-ltr">v${esc(
		brand.version || ""
	)}</span></div>
				</div>
			</div>
			<div class="pos-about-half">${esc(__("Designed & Developed by"))} <strong>HALF</strong></div>
			<div class="pos-about-copy">© ${new Date().getFullYear()} HALF — PharmacyOS ERP</div>
			<details class="pos-about-legal">
				<summary>${esc(__("Third-party licences"))}</summary>
				<ul>${upstream}</ul>
				<div style="margin-top:8px">${esc(
					__(
						"ERPNext and Frappe are trademarks of Frappe Technologies Pvt. Ltd. PharmacyOS ERP is not affiliated with or endorsed by Frappe Technologies."
					)
				)}</div>
				<div style="margin-top:6px"><a href="/attribution" target="_blank" rel="noopener">${esc(__("Licence texts"))}</a></div>
			</details>
		</div>`);
	d.show();
};
