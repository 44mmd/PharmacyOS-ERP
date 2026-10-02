// "About" — PharmacyOS identity, HALF attribution and the upstream open-source notices.
// Upstream credits (ERPNext GPL-3.0, Frappe MIT) are part of this dialog and must stay.
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
			<div class="pos-about-legal">
				${esc(__("PharmacyOS ERP is built on open-source software:"))}
				<ul>${upstream}</ul>
				<div style="margin-top:8px">${esc(
					__(
						"ERPNext and Frappe are trademarks of Frappe Technologies Pvt. Ltd. PharmacyOS ERP is not affiliated with or endorsed by Frappe Technologies."
					)
				)}</div>
			</div>
		</div>`);
	d.show();
};
