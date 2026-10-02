// System Status — is the pharmacy's data protected and is the cloud connection healthy?
// Quiet when all is well; only actionable problems are highlighted.
frappe.pages["system-status"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("System Status"), single_column: true });
	wrapper.pos_view = new PharmacyOSSystemStatus(page);
};
frappe.pages["system-status"].on_page_show = function (wrapper) {
	wrapper.pos_view && wrapper.pos_view.load();
};

class PharmacyOSSystemStatus {
	constructor(page) {
		this.page = page;
		this.ui = pharmacyos.ui;
		this.$root = $(`<div class="pos-page"></div>`).appendTo(page.main);
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh-cw");
		this.$root.on("click", "[data-action=backup-now]", () => this.backup_now());
		this.$root.on("click", "[data-action=retry-sync]", () => this.retry_sync());
	}

	load() {
		const ui = this.ui;
		this.$root.html(`<div class="pos-kpis">${ui.skeleton_kpis(2)}</div>`);
		pharmacyos
			.call("pharmacyos_erp.pharmacy.system.get_system_status")
			.then((d) => this.render(d))
			.catch((e) => this.$root.html(ui.error_state(e)));
	}

	when(value) {
		return value ? `${this.ui.date(String(value).slice(0, 10))} ${String(value).slice(11, 16)}` : "—";
	}

	size(bytes) {
		if (!bytes) return "—";
		const mb = bytes / (1024 * 1024);
		return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
	}

	render(d) {
		const ui = this.ui;
		const b = d.backup;
		const s = d.sync;
		const backup_ok = b.state === "protected";
		const backup_title = !b.enabled ? __("Automatic backup is off") : backup_ok ? __("Backup protected") : __("Backup needs attention");
		const sync_title = {
			online: __("Connected to PharmacyOS Cloud"),
			offline: __("Offline — changes are queued"),
			attention: __("Some changes could not be sent"),
			not_configured: __("Cloud connection not set up"),
		}[s.state];
		const card = (tone, icon, title, lines, action) => `
			<section class="pos-panel pos-status-card" data-tone="${tone}">
				<div class="pos-status-head">${ui.icon(icon, "md")}<h3 class="pos-panel-title">${ui.esc(title)}</h3></div>
				<dl class="pos-status-list">${lines
					.map(([k, v]) => `<div><dt>${ui.esc(k)}</dt><dd>${v}</dd></div>`)
					.join("")}</dl>
				${action || ""}
			</section>`;
		const backup_lines = [
			[__("Last successful backup"), ui.esc(this.when(b.last_success && b.last_success.finished_on))],
			[__("Next backup"), ui.esc(this.when(b.next_run))],
			[__("Size"), ui.ltr(this.size(b.last_success && b.last_success.size_bytes))],
			[__("Backup folder"), b.folder ? ui.ltr(b.folder) : "—"],
			[__("Free disk space"), b.free_disk_mb != null ? ui.ltr(`${Math.round(b.free_disk_mb / 1024)} GB`) : "—"],
		];
		if (b.last_error) backup_lines.push([__("Last error"), `<span class="pos-error-text">${ui.esc(b.last_error)}</span>`]);
		const sync_lines = [
			[__("Last successful sync"), ui.esc(this.when(s.last_success))],
			[__("Waiting to send"), ui.ltr(s.pending)],
			[__("Failed"), ui.ltr(s.failed)],
		];
		const c = d.cloud || {};
		if (c.enabled) {
			sync_lines.push([__("Website orders checked"), ui.esc(this.when(c.last_pull))]);
			sync_lines.push([__("Open website orders"), ui.ltr(c.open_website_orders || 0)]);
			if (c.last_error) sync_lines.push([__("Website connection"), `<span class="pos-error-text">${ui.esc(c.last_error)}</span>`]);
		}
		if (s.last_error) sync_lines.push([__("Last error"), `<span class="pos-error-text">${ui.esc(s.last_error)}</span>`]);
		const rows = d.recent_backups
			.map(
				(r) => `<tr><td>${ui.esc(this.when(r.started_on))}</td><td>${ui.esc(__(r.kind))}</td>
				<td>${ui.chip(r.status === "Success" ? "healthy" : r.status === "Failed" ? "expired" : "soon", {
					healthy: { label: () => __("Success"), icon: "circle-check" },
					expired: { label: () => __("Failed"), icon: "circle-x" },
					soon: { label: () => __("Running"), icon: "clock" },
				})}</td><td class="num">${ui.ltr(this.size(r.size_bytes))}</td></tr>`
			)
			.join("");
		this.$root.html(`
			<div class="pos-status-grid">
				${card(
					backup_ok ? "ok" : "warning",
					backup_ok ? "shield-check" : "shield-alert",
					backup_title,
					backup_lines,
					d.can_manage ? `<button class="pos-btn" data-action="backup-now">${ui.icon("database-backup", "xs")} ${__("Back up now")}</button>` : ""
				)}
				${card(
					s.state === "online" ? "ok" : s.state === "not_configured" ? "neutral" : "warning",
					s.state === "online" ? "cloud" : "cloud-off",
					sync_title,
					sync_lines,
					d.can_manage && s.failed ? `<button class="pos-btn" data-action="retry-sync">${ui.icon("refresh-cw", "xs")} ${__("Retry")}</button>` : ""
				)}
			</div>
			<section class="pos-panel">
				<div class="pos-panel-head"><h3 class="pos-panel-title">${__("Recent backups")}</h3></div>
				${
					rows
						? `<div class="pos-table-wrap"><table class="pos-table"><thead><tr><th>${__("Date")}</th><th>${__("Kind")}</th><th>${__("Status")}</th><th class="num">${__("Size")}</th></tr></thead><tbody>${rows}</tbody></table></div>`
						: ui.state({ kind: "empty", title: __("No backups yet"), text: __("The first automatic backup runs within the hour.") })
				}
			</section>
			<p class="pos-note">${__(
				"Sales keep working when the internet is down: every sale is saved on this pharmacy's server first and sent to PharmacyOS Cloud when the connection returns. Restoring a backup is done by a system administrator."
			)}</p>`);
	}

	backup_now() {
		frappe.call({ method: "pharmacyos_erp.backup.service.backup_now", type: "POST" }).then(() => {
			frappe.show_alert({ message: __("Backup started. It appears here when finished."), indicator: "green" });
			setTimeout(() => this.load(), 8000);
		});
	}

	retry_sync() {
		frappe.call({ method: "pharmacyos_erp.integration.outbox.retry_failed", type: "POST" }).then((r) => {
			frappe.show_alert({ message: __("{0} changes will be sent again.", [r.message]), indicator: "green" });
			this.load();
		});
	}
}
