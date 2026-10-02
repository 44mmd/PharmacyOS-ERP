// Core namespace: brand + context accessors and a guarded API caller.
frappe.provide("pharmacyos");

pharmacyos.boot = () => frappe.boot.pharmacyos || {};
pharmacyos.brand = () => pharmacyos.boot().brand || {};
pharmacyos.is_rtl = () => document.documentElement.getAttribute("dir") === "rtl";

pharmacyos.current_branch = () => pharmacyos.boot().branch || null;

pharmacyos.branch_label = (name) => {
	const b = (pharmacyos.boot().branches || []).find((x) => x.name === name);
	if (!b) return name;
	return frappe.boot.lang === "ar" && b.name_ar ? b.name_ar : b.name;
};

/**
 * Call a whitelisted method and normalise failures into {kind, message} so pages can show an
 * intentional state instead of a raw framework error.
 */
pharmacyos.call = (method, args = {}) =>
	new Promise((resolve, reject) => {
		frappe.call({
			method,
			args,
			type: "GET",
			freeze: false,
			// handled by the caller: no framework modal for expected states
			error_handlers: {
				PermissionError: () => {},
			},
			callback: (r) => resolve(r.message),
			error: (r) => {
				const exc = (r && r.exc_type) || "";
				reject({
					kind: exc === "PermissionError" ? "denied" : "error",
					message:
						exc === "PermissionError"
							? __("You do not have permission to view this information.")
							: __("Something went wrong while loading this view. Please try again."),
				});
			},
		});
	});
