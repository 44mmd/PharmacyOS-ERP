// Mixed-direction values in Arabic (RTL) sessions.
//
// Lists, grids and titles show values whose script varies per row: an Arabic medicine name next to
// "Demo Amoxicillin 500 mg" or "DEMO-AMOX-500". Inside an RTL cell, Latin text is laid out and
// truncated from the wrong edge ("EMO-AMOX-500"). Marking those value elements dir="auto" lets
// each value take the direction of its own first strong character, while CSS keeps the RTL
// alignment. Presentation only: values, inputs and scanner keystrokes are untouched.
frappe.provide("pharmacyos.bidi");

pharmacyos.bidi.SELECTOR = [
	".list-row-col .ellipsis",
	".list-subject a",
	".list-row-col .filterable",
	".grid-static-col .static-area",
	".control-value a",
	".like-disabled-input",
	".page-title .title-text",
	".awesomplete li a",
	".point-of-sale-app .item-name",
	".icon-caption .icon-title", // version-16 desktop icons
	".title-container .header-title", // version-16 sidebar header
	".point-of-sale-app .item-display .item-name",
].join(",");

pharmacyos.bidi.mark = (root) => {
	if (!root || root.nodeType !== 1) return;
	const sel = pharmacyos.bidi.SELECTOR;
	if (root.matches(sel) && !root.hasAttribute("dir")) root.setAttribute("dir", "auto");
	root.querySelectorAll(sel).forEach((el) => {
		if (!el.hasAttribute("dir")) el.setAttribute("dir", "auto");
	});
};

pharmacyos.bidi.start = () => {
	if (pharmacyos.bidi.observer || document.documentElement.getAttribute("dir") !== "rtl") return;
	let pending = [];
	let scheduled = false;
	const flush = () => {
		scheduled = false;
		const nodes = pending;
		pending = [];
		nodes.forEach(pharmacyos.bidi.mark);
	};
	pharmacyos.bidi.observer = new MutationObserver((mutations) => {
		for (const m of mutations) {
			for (const node of m.addedNodes) if (node.nodeType === 1) pending.push(node);
		}
		if (pending.length && !scheduled) {
			scheduled = true;
			requestAnimationFrame(flush);
		}
	});
	pharmacyos.bidi.observer.observe(document.body, { childList: true, subtree: true });
	pharmacyos.bidi.mark(document.body);
};

$(document).ready(() => pharmacyos.bidi.start());
