// IQD presentation.
//
// ERPNext stores amounts with the site's precision (3 decimals for IQD). Iraqi pharmacy prices are
// whole dinars, so "25,000.000" is noise. This wraps Frappe's global `format_currency` and, for IQD
// only, drops the fraction when it is exactly zero — the same rule Frappe already applies to Float
// fields. Real fractions (e.g. a per-tablet rate of 333.333) are still shown. Values, rounding and
// stored precision are untouched.
frappe.provide("pharmacyos");

const WHOLE_UNIT_CURRENCIES = new Set(["IQD"]);

(function wrap_format_currency() {
	const original = window.format_currency;
	if (!original || original.__pharmacyos) return;

	const wrapped = function (v, currency, decimals) {
		const code = currency || (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency);
		if (WHOLE_UNIT_CURRENCIES.has(code) && v !== null && v !== undefined && v !== "") {
			const n = flt(v, decimals == null ? 3 : decimals);
			if (Number.isInteger(n)) decimals = 0;
		}
		return original.call(this, v, currency, decimals);
	};
	wrapped.__pharmacyos = true;
	window.format_currency = wrapped;
})();

pharmacyos.format_money = (value, currency) => format_currency(value || 0, currency || pharmacyos.default_currency());

pharmacyos.default_currency = () =>
	(frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency) || "IQD";

pharmacyos.format_qty = (value) => {
	const n = flt(value, 3);
	return format_number(n, null, Number.isInteger(n) ? 0 : 2);
};
