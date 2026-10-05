// PharmacyOS POS — the counter screen (/pos), shared by the web browser and the Windows desktop app.
//
// Rules this file keeps:
// * No business logic. Prices, discounts, taxes, totals, batches (FEFO), stock and refunds come from
//   the ERP (`pharmacyos_erp.pos.api` → ERPNext). Numbers on screen are the server's numbers; the only
//   arithmetic here is the cash-drawer aid (change to give back while typing the amount received),
//   and the server's own change amount is what the receipt shows.
// * No credentials: the session is Frappe's HTTP-only cookie; unsafe requests carry Frappe's CSRF token.
// * Server text is rendered as text (never as HTML).
// * A checkout is identified by a request ID that survives network loss and page reloads, so a retried
//   sale is never sold twice (the server answers a repeated request with the first invoice).
// * Written for Safari 14+, Chrome and Edge: no APIs beyond ES2019 + fetch/AbortController/URLSearchParams.
"use strict";
(function () {
	var M = JSON.parse(document.getElementById("px-messages").textContent);
	var M_CONTEXT = "pharmacyos_erp.pos.api.get_context";
	var M_SEARCH = "pharmacyos_erp.pos.api.search_items";
	var M_QUOTE = "pharmacyos_erp.pos.api.quote";
	var M_CHECKOUT = "pharmacyos_erp.pos.api.checkout";
	var M_RETURN_CANDIDATE = "pharmacyos_erp.pos.api.get_return_candidate";
	var M_PREVIEW_RETURN = "pharmacyos_erp.pos.api.preview_return";
	var M_SUBMIT_RETURN = "pharmacyos_erp.pos.api.submit_return";
	var M_RECENT = "pharmacyos_erp.pos.api.recent_sales";
	// ERPNext's own (permission-scoped) POS and desk endpoints
	var M_OPEN_SHIFT = "erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher";
	var M_SEARCH_LINK = "frappe.desk.search.search_link";
	var M_INSERT = "frappe.client.insert";
	var M_LOGGED_USER = "frappe.auth.get_logged_user";

	var platform = window.PharmacyOSPlatform || { kind: "browser", nativePrint: false };
	var lang = document.documentElement.lang || "en";
	var isAr = lang === "ar";

	var S = {
		ctx: null,
		profile: null,
		cart: [],
		customer: null,
		customerName: "",
		discountPct: 0,
		quote: null,
		quoteError: null,
		quoting: false,
		quoteFor: -1, // the cart version the quote was calculated for
		cartVersion: 0,
		pending: null, // {id, signature, uncertain}
		online: true,
		dialog: null,
		results: [],
	};

	// ------------------------------------------------------------------ small utilities

	function $(sel, root) {
		return (root || document).querySelector(sel);
	}

	function h(tag, attrs) {
		var node = document.createElement(tag);
		attrs = attrs || {};
		Object.keys(attrs).forEach(function (k) {
			var v = attrs[k];
			if (v === null || v === undefined || v === false) return;
			if (k === "class") node.className = v;
			else if (k === "text") node.textContent = v;
			else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
			else if (k === "dataset") Object.keys(v).forEach(function (d) { node.dataset[d] = v[d]; });
			else node.setAttribute(k, v === true ? "" : v);
		});
		for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
		return node;
	}

	function append(node) {
		for (var i = 1; i < arguments.length; i++) {
			var child = arguments[i];
			if (child === null || child === undefined || child === false) continue;
			if (Array.isArray(child)) child.forEach(function (c) { append(node, c); });
			else node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
		}
	}

	function fmt(template) {
		var args = Array.prototype.slice.call(arguments, 1);
		return String(template).replace(/\{(\d+)\}/g, function (_m, i) { return args[i] === undefined ? "" : args[i]; });
	}

	// Server messages may contain simple HTML (<b>, <br>): show their text only, never as markup.
	function toText(html) {
		if (!html) return "";
		var withBreaks = String(html).replace(/<br\s*\/?>/gi, "\n");
		var doc = new DOMParser().parseFromString(withBreaks, "text/html"); // inert: scripts never run
		return (doc.body.textContent || "").trim();
	}

	// Arabic-Indic and Persian digits, Arabic decimal/thousands separators → a JS number.
	function parseNumber(value) {
		var s = String(value === undefined || value === null ? "" : value).trim();
		s = s.replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); });
		s = s.replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x06f0); });
		s = s.replace(/٫/g, ".").replace(/[٬,\s]/g, "");
		var n = parseFloat(s);
		return isFinite(n) ? n : NaN;
	}

	var numberFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });
	function money(value) {
		var symbol = (S.profile && S.profile.currency_symbol) || (S.profile && S.profile.currency) || "";
		return numberFmt.format(Number(value) || 0) + (symbol ? " " + symbol : "");
	}
	function qtyText(value) {
		return numberFmt.format(Number(value) || 0);
	}

	// "4:05:12.123456" (server time) → "04:05"
	function shortTime(value) {
		var m = /^(\d{1,2}):(\d{2})/.exec(String(value || ""));
		return m ? (m[1].length === 1 ? "0" + m[1] : m[1]) + ":" + m[2] : "";
	}

	function uuid() {
		if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
		var b = new Uint8Array(16);
		window.crypto.getRandomValues(b);
		b[6] = (b[6] & 0x0f) | 0x40;
		b[8] = (b[8] & 0x3f) | 0x80;
		var hex = Array.prototype.map.call(b, function (x) { return (x + 0x100).toString(16).slice(1); }).join("");
		return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
	}

	function debounce(fn, ms) {
		var t = null;
		var wrapped = function () {
			var args = arguments;
			clearTimeout(t);
			t = setTimeout(function () { fn.apply(null, args); }, ms);
		};
		wrapped.cancel = function () { clearTimeout(t); };
		return wrapped;
	}

	function itemTitle(item) {
		if (isAr && item.item_name_ar) return item.item_name_ar;
		return item.item_name || item.item_code;
	}
	function itemSubtitle(item) {
		if (isAr && item.item_name_ar) return item.item_name || item.item_code;
		return item.item_name_ar || "";
	}

	var store = {
		key: function (name) {
			return "pxpos:" + ((S.ctx && S.ctx.user) || "") + ":" + name;
		},
		get: function (name) {
			try { return JSON.parse(window.localStorage.getItem(this.key(name)) || "null"); } catch (e) { return null; }
		},
		set: function (name, value) {
			try {
				if (value === null) window.localStorage.removeItem(this.key(name));
				else window.localStorage.setItem(this.key(name), JSON.stringify(value));
			} catch (e) { /* private mode: the cart is simply not kept across reloads */ }
		},
	};

	// ------------------------------------------------------------------ sounds (scanner feedback)

	var audio = null;
	function beep(ok) {
		try {
			audio = audio || new (window.AudioContext || window.webkitAudioContext)();
			var o = audio.createOscillator();
			var g = audio.createGain();
			o.frequency.value = ok ? 1320 : 220;
			g.gain.value = 0.04;
			o.connect(g);
			g.connect(audio.destination);
			o.start();
			o.stop(audio.currentTime + (ok ? 0.06 : 0.25));
		} catch (e) { /* no audio: silent */ }
	}

	// ------------------------------------------------------------------ server calls

	function ApiError(message, info) {
		var e = new Error(message);
		Object.keys(info || {}).forEach(function (k) { e[k] = info[k]; });
		return e;
	}

	function serverMessages(raw) {
		if (!raw) return [];
		try {
			return JSON.parse(raw).map(function (m) {
				try {
					var o = JSON.parse(m);
					return { text: toText(o.message), indicator: o.indicator, title: o.title };
				} catch (e) {
					return { text: toText(m) };
				}
			}).filter(function (m) { return m.text; });
		} catch (e) {
			return [];
		}
	}

	function exceptionText(body) {
		var exc = body && (body.exception || body.exc_type);
		if (!exc) return "";
		var text = String(exc).split("\n").pop();
		return toText(text.replace(/^[\w.]+(Error|Exception):\s*/, ""));
	}

	function call(method, args, options) {
		options = options || {};
		var post = Boolean(options.post);
		var url = "/api/method/" + method;
		var init = {
			method: post ? "POST" : "GET",
			credentials: "same-origin",
			headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
		};
		if (post) {
			init.headers["Content-Type"] = "application/json";
			init.headers["X-Frappe-CSRF-Token"] = window.frappe.csrf_token || "";
			init.body = JSON.stringify(args || {});
		} else if (args) {
			var q = new URLSearchParams();
			Object.keys(args).forEach(function (k) {
				var v = args[k];
				if (v === undefined || v === null) return;
				q.set(k, typeof v === "object" ? JSON.stringify(v) : String(v));
			});
			url += "?" + q.toString();
		}
		var controller = typeof AbortController === "function" ? new AbortController() : null;
		if (controller) init.signal = controller.signal;
		var timer = setTimeout(function () { controller && controller.abort(); }, options.timeout || 30000);
		return fetch(url, init).then(
			function (res) {
				clearTimeout(timer);
				setOnline(true);
				return res.text().then(function (raw) {
					var body = {};
					try { body = raw ? JSON.parse(raw) : {}; } catch (e) { body = {}; }
					var msgs = serverMessages(body._server_messages);
					if (!res.ok || body.exc_type || body.exception) {
						var text = msgs.map(function (m) { return m.text; }).join("\n") || exceptionText(body) || M.error;
						var err = ApiError(text, { status: res.status, excType: body.exc_type });
						if (res.status === 401 || res.status === 403) {
							return checkSession().then(function (alive) {
								if (!alive) sessionExpired();
								throw err;
							});
						}
						throw err;
					}
					msgs.forEach(function (m) { toast(m.text, m.indicator === "red" ? "error" : "warning"); });
					return body.message;
				});
			},
			function () {
				clearTimeout(timer);
				setOnline(false);
				throw ApiError(M.offline, { network: true });
			}
		);
	}

	function checkSession() {
		return fetch("/api/method/" + M_LOGGED_USER, { credentials: "same-origin", headers: { Accept: "application/json" } })
			.then(function (r) { return r.ok ? r.json().then(function (b) { return b.message && b.message !== "Guest"; }) : false; })
			.catch(function () { return true; }); // unreachable ≠ signed out
	}

	// ------------------------------------------------------------------ connection & session

	var pingTimer = null;
	function setOnline(online) {
		if (online === S.online) return;
		S.online = online;
		if (online) {
			hideBanner();
			toast(M.reconnected, "success");
			clearInterval(pingTimer);
			pingTimer = null;
		} else {
			showBanner(M.offline, "warning");
			if (!pingTimer) {
				pingTimer = setInterval(function () {
					fetch("/api/method/ping", { credentials: "same-origin", cache: "no-store" })
						.then(function (r) { if (r.ok) setOnline(true); })
						.catch(function () {});
				}, 5000);
			}
		}
	}
	window.addEventListener("offline", function () { setOnline(false); });
	window.addEventListener("online", function () {
		fetch("/api/method/ping", { credentials: "same-origin", cache: "no-store" }).then(function (r) { if (r.ok) setOnline(true); }).catch(function () {});
	});

	function signInUrl() {
		return "/login?redirect-to=" + encodeURIComponent("/pos");
	}

	function sessionExpired() {
		saveCart();
		closeDialog();
		openDialog({
			title: M.sign_in,
			dismissable: false,
			body: h("p", { text: M.session_expired }),
			actions: [h("a", { class: "px-btn px-btn-primary", href: signInUrl(), text: M.sign_in })],
		});
	}

	function showBanner(text, kind) {
		var b = $("#px-banner");
		b.textContent = text;
		b.className = "px-banner px-banner-" + (kind || "info");
		b.hidden = false;
	}
	function hideBanner() {
		$("#px-banner").hidden = true;
	}

	function toast(text, kind) {
		if (!text) return;
		var t = h("div", { class: "px-toast px-toast-" + (kind || "info"), role: kind === "error" ? "alert" : "status" }, text);
		$("#px-toasts").appendChild(t);
		setTimeout(function () { t.classList.add("px-toast-out"); }, kind === "error" ? 6000 : 3500);
		setTimeout(function () { t.remove(); }, kind === "error" ? 6600 : 4100);
	}

	// ------------------------------------------------------------------ dialogs

	function openDialog(opts) {
		closeDialog();
		var previous = document.activeElement;
		var box = h(
			"div",
			{ class: "px-dialog " + (opts.wide ? "px-dialog-wide" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": "px-dialog-title" },
			h("div", { class: "px-dialog-head" },
				h("h2", { id: "px-dialog-title", text: opts.title }),
				opts.dismissable === false ? null : h("button", { type: "button", class: "px-icon-btn", "aria-label": M.close, text: "×", onclick: closeDialog })
			),
			h("div", { class: "px-dialog-body" }, opts.body),
			opts.actions ? h("div", { class: "px-dialog-actions" }, opts.actions) : null
		);
		var overlay = h("div", { class: "px-overlay", onmousedown: function (e) { if (e.target === overlay && opts.dismissable !== false) closeDialog(); } }, box);
		$("#px-dialog-root").appendChild(overlay);
		S.dialog = { overlay: overlay, box: box, onClose: opts.onClose, previous: previous, dismissable: opts.dismissable !== false };
		var first = box.querySelector("[data-autofocus]") || box.querySelector("input, select, textarea, button:not(.px-icon-btn), a.px-btn");
		if (first) setTimeout(function () { first.focus(); if (first.select) first.select(); }, 0);
		return box;
	}

	function closeDialog() {
		if (!S.dialog) return;
		var d = S.dialog;
		S.dialog = null;
		d.overlay.remove();
		if (d.onClose) d.onClose();
		focusScan();
	}

	function trapTab(e) {
		if (!S.dialog || e.key !== "Tab") return;
		var nodes = S.dialog.box.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select, textarea, iframe");
		if (!nodes.length) return;
		var first = nodes[0];
		var last = nodes[nodes.length - 1];
		if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
		else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
	}

	// ------------------------------------------------------------------ start / shift

	function start() {
		call(M_CONTEXT).then(function (ctx) {
			S.ctx = ctx;
			$("#px-main").setAttribute("aria-busy", "false");
			$("#px-user").textContent = ctx.full_name || ctx.user;
			var ph = ctx.pharmacy || {};
			$("#px-pharmacy").textContent = (isAr && ph.pharmacy_name_ar) || ph.pharmacy_name || ctx.product_name;
			if (!ctx.can_sell) return blocked(M.no_sell_rights);
			if (!ctx.shift) return shiftScreen();
			S.profile = ctx.profile;
			renderShift();
			restoreCart();
			render();
			focusScan();
		}).catch(function (e) {
			if (e.status === 403 || e.status === 401) return;
			showBanner(e.message, "error");
			openDialog({ title: M.error, dismissable: false, body: h("p", { text: e.message }), actions: [h("button", { type: "button", class: "px-btn px-btn-primary", text: M.retry, onclick: function () { closeDialog(); start(); } })] });
		});
	}

	function blocked(text) {
		openDialog({ title: M.pos, dismissable: false, body: h("p", { text: text }), actions: [h("button", { type: "button", class: "px-btn", text: M.logout, onclick: logout })] });
	}

	function renderShift() {
		var p = S.profile;
		var shift = S.ctx.shift;
		var box = $("#px-shift");
		box.textContent = "";
		append(box, [
			h("span", { class: "px-chip" }, M.counter + ": " + p.name),
			h("span", { class: "px-chip px-chip-ok" }, M.shift + " " + shift.name),
		]);
		var closeBtn = $('[data-action="close-shift"]');
		closeBtn.hidden = !S.ctx.can_close_shift;
	}

	function shiftScreen() {
		var ctx = S.ctx;
		if (!ctx.profiles.length) return blocked(M.no_counter);
		if (!ctx.can_open_shift) return blocked(M.no_shift_rights);
		var select = h("select", { id: "px-open-profile", class: "px-input" }, ctx.profiles.map(function (p) { return h("option", { value: p.name, text: p.name }); }));
		var amounts = h("div", { class: "px-form-rows" });
		function drawAmounts() {
			amounts.textContent = "";
			var p = ctx.profiles.filter(function (x) { return x.name === select.value; })[0];
			(p ? p.payments : []).forEach(function (mode, i) {
				append(amounts, h("label", { class: "px-field" }, h("span", { text: M.opening_cash + " — " + mode }), h("input", { class: "px-input px-num", inputmode: "decimal", "data-mode": mode, value: "0", "data-autofocus": i === 0 ? "" : null })));
			});
		}
		select.addEventListener("change", drawAmounts);
		drawAmounts();
		var error = h("p", { class: "px-error", role: "alert" });
		var openBtn = h("button", {
			type: "button",
			class: "px-btn px-btn-primary",
			text: M.open_shift,
			onclick: function () {
				var p = ctx.profiles.filter(function (x) { return x.name === select.value; })[0];
				var details = Array.prototype.map.call(amounts.querySelectorAll("input"), function (inp) {
					var n = parseNumber(inp.value);
					return { mode_of_payment: inp.dataset.mode, opening_amount: isNaN(n) ? 0 : n };
				});
				openBtn.disabled = true;
				call(M_OPEN_SHIFT, { pos_profile: p.name, company: p.company, balance_details: details }, { post: true })
					.then(function () { closeDialog(); start(); })
					.catch(function (e) { error.textContent = e.message; openBtn.disabled = false; });
			},
		});
		openDialog({
			title: M.open_shift,
			dismissable: false,
			body: [h("p", { text: M.no_shift }), h("label", { class: "px-field" }, h("span", { text: M.counter }), select), amounts, error],
			actions: [openBtn, h("button", { type: "button", class: "px-btn", text: M.logout, onclick: logout })],
		});
	}

	// ------------------------------------------------------------------ cart persistence

	function saveCart() {
		if (!S.profile) return;
		store.set("cart", { profile: S.profile.name, cart: S.cart, customer: S.customer, customerName: S.customerName, discountPct: S.discountPct, pending: S.pending });
	}

	function restoreCart() {
		var saved = store.get("cart");
		S.customer = S.profile.customer;
		S.customerName = S.profile.customer_name || S.profile.customer || M.walk_in;
		if (saved && saved.profile === S.profile.name && Array.isArray(saved.cart)) {
			S.cart = saved.cart;
			if (saved.customer) { S.customer = saved.customer; S.customerName = saved.customerName || saved.customer; }
			S.discountPct = Number(saved.discountPct) || 0;
			S.pending = saved.pending || null;
		}
		cartChanged({ keepPending: true });
		if (S.pending && S.pending.uncertain) {
			// the page was reloaded while a sale was in flight: ask the server what happened (same request ID)
			openPayment(true);
		}
	}

	function clearSale() {
		S.cart = [];
		S.discountPct = 0;
		S.pending = null;
		S.customer = S.profile.customer;
		S.customerName = S.profile.customer_name || S.profile.customer || M.walk_in;
		S.results = [];
		$("#px-results").textContent = "";
		cartChanged();
	}

	// ------------------------------------------------------------------ search & scan

	var scanChain = Promise.resolve();

	function focusScan() {
		if (S.dialog) return;
		var input = $("#px-scan");
		if (input && document.activeElement !== input) input.focus({ preventScroll: true });
	}

	function enqueueScan(code) {
		scanChain = scanChain.then(function () { return handleScan(code); }).catch(function () {});
	}

	function handleScan(code) {
		if (!S.profile || !code) return Promise.resolve();
		return call(M_SEARCH, { pos_profile: S.profile.name, search_term: code, page_length: 24 }).then(function (r) {
			var items = (r && r.items) || [];
			var exact = items.filter(function (i) { return i.item_code === code || i.barcode === code; });
			if ((r && r.barcode_scan && items.length === 1) || items.length === 1) {
				addItem(items[0]);
				renderResults([]);
			} else if (exact.length === 1) {
				addItem(exact[0]);
				renderResults([]);
			} else if (items.length > 1) {
				renderResults(items);
				toast(M.choose, "info");
			} else {
				beep(false);
				toast(fmt(M.not_found_code, code), "error");
			}
		}).catch(function (e) {
			beep(false);
			if (!e.network) toast(e.message, "error");
		});
	}

	var liveSearch = debounce(function (term) {
		if (!S.profile) return;
		if (term.length < 2) return renderResults([]);
		call(M_SEARCH, { pos_profile: S.profile.name, search_term: term, page_length: 24 }).then(function (r) {
			if ($("#px-scan").value.trim() !== term) return; // typed on (or a scan) since
			renderResults((r && r.items) || [], true);
		}).catch(function () {});
	}, 300);

	function stockLine(item) {
		if (!item.is_stock_item) return null;
		if (item.has_batch_no) {
			if (item.sellable_qty <= 0 && item.actual_qty > 0) return h("span", { class: "px-badge px-badge-danger", text: M.expired_only });
			if (item.sellable_qty <= 0) return h("span", { class: "px-badge px-badge-danger", text: M.out_of_stock });
			return h("span", { class: "px-badge" }, M.in_stock + ": " + qtyText(item.sellable_qty));
		}
		if (item.actual_qty <= 0) return h("span", { class: "px-badge px-badge-danger", text: M.out_of_stock });
		return h("span", { class: "px-badge" }, M.in_stock + ": " + qtyText(item.actual_qty));
	}

	function batchLine(item) {
		var b = item.scanned_batch || item.next_batch;
		if (!b || !b.batch_id) return null;
		var label = item.scanned_batch ? M.batch : M.next_batch;
		return h("span", { class: "px-batch" + (b.expired ? " px-batch-expired" : "") },
			label + " ", h("bdi", { dir: "ltr", text: b.batch_id }), b.expiry_date ? " · " + M.expiry + " " : "", b.expiry_date ? h("bdi", { dir: "ltr", text: b.expiry_date }) : null);
	}

	function renderResults(items, fromTyping) {
		S.results = items;
		var box = $("#px-results");
		box.textContent = "";
		if (!items.length) {
			if (fromTyping) append(box, h("p", { class: "px-empty", text: M.no_results }));
			return;
		}
		items.forEach(function (item) {
			var sub = itemSubtitle(item);
			append(box, h("button", {
				type: "button",
				class: "px-result",
				role: "listitem",
				onclick: function () { addItem(item); focusScan(); },
			},
				item.image ? h("img", { class: "px-result-img", src: item.image, alt: "", loading: "lazy" }) : h("span", { class: "px-result-img px-result-ph", "aria-hidden": "true", text: (itemTitle(item) || "?").slice(0, 1) }),
				h("span", { class: "px-result-body" },
					h("span", { class: "px-result-name", text: itemTitle(item) }),
					sub ? h("span", { class: "px-result-sub", text: sub }) : null,
					h("span", { class: "px-result-meta" }, stockLine(item), item.dispensing === "Prescription Required" ? h("span", { class: "px-badge px-badge-warn", text: M.prescription }) : null),
					batchLine(item)
				),
				h("span", { class: "px-result-price", text: money(item.price_list_rate) })
			));
		});
	}

	function addItem(item) {
		if (S.pending && S.pending.uncertain) return; // a sale is being confirmed with the server
		if (item.scanned_batch && item.scanned_batch.expired) {
			beep(false);
			return toast(fmt(M.scanned_batch_expired, item.scanned_batch.batch_id), "error");
		}
		if (item.is_stock_item && item.has_batch_no && item.sellable_qty <= 0) {
			beep(false);
			return toast(itemTitle(item) + " — " + (item.actual_qty > 0 ? M.expired_only : M.out_of_stock), "error");
		}
		var batch = item.batch_no || null;
		var existing = S.cart.filter(function (l) { return l.item_code === item.item_code && (l.batch_no || null) === batch && l.uom === item.uom; })[0];
		if (existing) {
			existing.qty = Number(existing.qty) + 1;
		} else {
			S.cart.push({
				key: uuid(),
				item_code: item.item_code,
				item_name: item.item_name,
				item_name_ar: item.item_name_ar,
				uom: item.uom,
				qty: 1,
				batch_no: batch,
				batch: item.scanned_batch || null,
				next_batch: item.next_batch || null,
				price_list_rate: item.price_list_rate,
				is_stock_item: item.is_stock_item,
				available: item.has_batch_no ? item.sellable_qty : item.actual_qty,
				dispensing: item.dispensing,
				discount_percentage: 0,
			});
		}
		if (!item.has_batch_no && item.is_stock_item && item.actual_qty <= 0) toast(itemTitle(item) + " — " + M.out_of_stock, "warning");
		beep(true);
		cartChanged();
	}

	// ------------------------------------------------------------------ cart

	function signature() {
		return JSON.stringify({ c: S.cart.map(function (l) { return [l.item_code, l.qty, l.uom, l.batch_no, l.discount_percentage || 0]; }), u: S.customer, d: S.discountPct });
	}

	function cartChanged(opts) {
		opts = opts || {};
		S.cartVersion++;
		if (!opts.keepPending && S.pending && !S.pending.uncertain && S.pending.signature !== signature()) S.pending = null;
		saveCart();
		render();
		scheduleQuote();
	}

	function payload() {
		return S.cart.map(function (l) {
			var row = { item_code: l.item_code, qty: Number(l.qty), uom: l.uom };
			if (l.batch_no) row.batch_no = l.batch_no;
			if (Number(l.discount_percentage)) row.discount_percentage = Number(l.discount_percentage);
			return row;
		});
	}

	var scheduleQuote = debounce(function () {
		if (!S.profile) return;
		if (!S.cart.length) {
			S.quote = null;
			S.quoteError = null;
			S.quoting = false;
			return render();
		}
		var version = S.cartVersion;
		S.quoting = true;
		renderTotals();
		call(M_QUOTE, { pos_profile: S.profile.name, items: payload(), customer: S.customer, additional_discount_percentage: S.discountPct || 0 }, { post: true })
			.then(function (q) {
				if (version !== S.cartVersion) return;
				S.quote = q;
				S.quoteFor = version;
				S.quoteError = null;
				S.quoting = false;
				render();
			})
			.catch(function (e) {
				if (version !== S.cartVersion) return;
				S.quote = null;
				S.quoteError = e.message;
				S.quoting = false;
				render();
			});
	}, 200);

	function quoteReady() {
		// only a quote of exactly the cart on screen: never pay a total calculated for an earlier cart
		return Boolean(S.quote) && S.quoteFor === S.cartVersion && !S.quoting && !S.quoteError && S.quote.items.length === S.cart.length;
	}

	function totalDue() {
		return S.quote ? Number(S.quote.rounded_total || S.quote.grand_total) : 0;
	}

	function render() {
		renderCustomer();
		renderLines();
		renderTotals();
	}

	function renderCustomer() {
		$("#px-customer").textContent = S.customerName || M.walk_in;
	}

	function setQty(line, value) {
		var n = parseNumber(value);
		if (isNaN(n) || n <= 0) return removeLine(line);
		line.qty = n;
		cartChanged();
	}

	function removeLine(line) {
		S.cart = S.cart.filter(function (l) { return l !== line; });
		cartChanged();
	}

	function renderLines() {
		var box = $("#px-lines");
		box.textContent = "";
		if (!S.cart.length) {
			append(box, h("p", { class: "px-empty", text: M.cart_empty }));
			return;
		}
		var allowDiscount = S.profile && S.profile.allow_discount_change;
		var locked = Boolean(S.pending && S.pending.uncertain);
		S.cart.forEach(function (line, i) {
			var q = quoteReady() ? S.quote.items[i] : null;
			var sub = itemSubtitle(line);
			var qtyInput = h("input", {
				class: "px-input px-qty-input",
				inputmode: "decimal",
				value: qtyText(line.qty),
				"aria-label": M.qty,
				disabled: locked,
				onchange: function () { setQty(line, qtyInput.value); },
				onkeydown: function (e) { if (e.key === "Enter") { e.preventDefault(); setQty(line, qtyInput.value); focusScan(); } },
			});
			var over = line.is_stock_item && line.available !== undefined && Number(line.qty) > Number(line.available);
			append(box, h("div", { class: "px-line", role: "listitem" },
				h("div", { class: "px-line-main" },
					h("div", { class: "px-line-name", text: itemTitle(line) }),
					sub ? h("div", { class: "px-line-sub", text: sub }) : null,
					line.batch ? h("div", { class: "px-batch" }, M.batch + " ", h("bdi", { dir: "ltr", text: line.batch.batch_id }), line.batch.expiry_date ? " · " + M.expiry + " " : "", line.batch.expiry_date ? h("bdi", { dir: "ltr", text: line.batch.expiry_date }) : null) : null,
					over ? h("div", { class: "px-line-warn", text: M.more_than_stock }) : null
				),
				h("div", { class: "px-line-qty" },
					h("button", { type: "button", class: "px-step", "aria-label": "−", text: "−", disabled: locked, onclick: function () { setQty(line, Number(line.qty) - 1); } }),
					qtyInput,
					h("button", { type: "button", class: "px-step", "aria-label": "+", text: "+", disabled: locked, onclick: function () { setQty(line, Number(line.qty) + 1); } })
				),
				h("div", { class: "px-line-money" },
					h("div", { class: "px-line-amount", text: q ? money(q.amount) : "…" }),
					h("div", { class: "px-line-rate" }, q ? qtyText(q.qty) + " × " + money(q.rate) : money(line.price_list_rate)),
					allowDiscount ? discountInput(line, locked) : (q && q.discount_percentage ? h("div", { class: "px-line-rate" }, M.discount + " " + qtyText(q.discount_percentage) + "%") : null)
				),
				h("button", { type: "button", class: "px-icon-btn px-line-remove", "aria-label": M.remove, title: M.remove, text: "×", disabled: locked, onclick: function () { removeLine(line); } })
			));
		});
	}

	function discountInput(line, locked) {
		var input = h("input", {
			class: "px-input px-disc-input",
			inputmode: "decimal",
			value: line.discount_percentage ? qtyText(line.discount_percentage) : "",
			placeholder: M.discount_pct,
			"aria-label": M.discount_pct,
			disabled: locked,
			onchange: function () {
				var n = parseNumber(input.value);
				line.discount_percentage = isNaN(n) ? 0 : Math.max(0, Math.min(100, n));
				cartChanged();
			},
		});
		return h("label", { class: "px-disc" }, input, "%");
	}

	function renderTotals() {
		var box = $("#px-totals");
		box.textContent = "";
		var pay = $("#px-pay");
		var q = S.quote;
		if (S.quoteError) {
			append(box, h("p", { class: "px-error", role: "alert", text: S.quoteError }));
		}
		if (S.cart.length && S.profile && S.profile.allow_discount_change) {
			var disc = h("input", {
				class: "px-input px-disc-input",
				inputmode: "decimal",
				value: S.discountPct ? qtyText(S.discountPct) : "",
				placeholder: "0",
				"aria-label": M.discount_pct,
				onchange: function () {
					var n = parseNumber(disc.value);
					S.discountPct = isNaN(n) ? 0 : Math.max(0, Math.min(100, n));
					cartChanged();
				},
			});
			append(box, h("div", { class: "px-total-row" }, h("span", { text: M.discount_pct }), h("label", { class: "px-disc" }, disc, "%")));
		}
		if (q && !S.quoteError) {
			append(box, h("div", { class: "px-total-row" }, h("span", { text: M.subtotal }), h("span", { text: money(q.total) })));
			if (q.discount_amount) append(box, h("div", { class: "px-total-row" }, h("span", { text: M.discount }), h("span", { text: "− " + money(q.discount_amount) })));
			(q.taxes || []).forEach(function (t) { if (t.tax_amount) append(box, h("div", { class: "px-total-row" }, h("span", { text: t.description }), h("span", { text: money(t.tax_amount) }))); });
			if (q.rounding_adjustment) append(box, h("div", { class: "px-total-row px-muted" }, h("span", { text: M.rounding }), h("span", { text: money(q.rounding_adjustment) })));
			append(box, h("div", { class: "px-total-row px-grand" }, h("span", { text: M.total }), h("span", { text: money(totalDue()) })));
		}
		if (S.quoting) append(box, h("p", { class: "px-muted", text: M.updating }));
		pay.disabled = !quoteReady() || !S.cart.length;
		$("#px-pay-amount").textContent = quoteReady() ? money(totalDue()) : "";
	}

	// ------------------------------------------------------------------ customer

	function openCustomer() {
		if (!S.profile) return;
		var list = h("div", { class: "px-list", role: "list" });
		var input = h("input", { class: "px-input", type: "search", placeholder: M.search_customer, "data-autofocus": "", autocomplete: "off" });
		var search = debounce(function () {
			var txt = input.value.trim();
			call(M_SEARCH_LINK, { doctype: "Customer", txt: txt, page_length: 12 }).then(function (rows) {
				list.textContent = "";
				(rows || []).forEach(function (r) {
					append(list, h("button", { type: "button", class: "px-list-row", role: "listitem", onclick: function () { pick(r.value, r.label || r.value); } },
						h("strong", { text: r.label || r.value }), r.description ? h("span", { class: "px-muted", text: toText(r.description) }) : null));
				});
			}).catch(function (e) { if (!e.network) toast(e.message, "error"); });
		}, 250);
		input.addEventListener("input", search);
		function pick(name, label) {
			S.customer = name;
			S.customerName = label;
			closeDialog();
			cartChanged();
		}
		var name = h("input", { class: "px-input", placeholder: M.customer_name });
		var mobile = h("input", { class: "px-input", inputmode: "tel", placeholder: M.mobile, dir: "ltr" });
		var err = h("p", { class: "px-error", role: "alert" });
		var create = h("button", {
			type: "button",
			class: "px-btn",
			text: M.save,
			onclick: function () {
				var n = name.value.trim();
				if (!n) return name.focus();
				create.disabled = true;
				call(M_INSERT, { doc: { doctype: "Customer", customer_name: n, customer_type: "Individual", mobile_no: mobile.value.trim() || undefined } }, { post: true })
					.then(function (doc) { pick(doc.name, doc.customer_name || doc.name); })
					.catch(function (e) { err.textContent = e.message; create.disabled = false; });
			},
		});
		openDialog({
			title: M.customer,
			body: [
				input,
				h("button", { type: "button", class: "px-list-row px-walkin", onclick: function () { pick(S.profile.customer, S.profile.customer_name || S.profile.customer); } }, h("strong", { text: M.walk_in })),
				list,
				h("details", { class: "px-new-customer" }, h("summary", { text: M.new_customer }), h("div", { class: "px-form-rows" }, name, mobile, create, err)),
			],
		});
		search();
	}

	// ------------------------------------------------------------------ payment & checkout

	var NOTES = [1000, 5000, 10000, 25000, 50000];

	function openPayment(resume) {
		if (!resume && !quoteReady()) return;
		var due = totalDue();
		var modes = S.profile.payments.length ? S.profile.payments : [{ mode_of_payment: "Cash", default: 1, type: "Cash" }];
		var defaultMode = (modes.filter(function (m) { return m.default; })[0] || modes[0]).mode_of_payment;
		var inputs = {};
		var change = h("div", { class: "px-change" });
		var err = h("p", { class: "px-error", role: "alert" });
		var rows = modes.map(function (m) {
			var input = h("input", {
				class: "px-input px-num px-pay-input",
				inputmode: "decimal",
				value: m.mode_of_payment === defaultMode ? String(due) : "",
				"data-autofocus": m.mode_of_payment === defaultMode ? "" : null,
				oninput: drawChange,
				onkeydown: function (e) { if (e.key === "Enter") { e.preventDefault(); complete(); } },
			});
			inputs[m.mode_of_payment] = input;
			var quick = null;
			if (m.type === "Cash") {
				var amounts = [due];
				NOTES.forEach(function (n) { var v = Math.ceil(due / n) * n; if (v > due && amounts.indexOf(v) < 0) amounts.push(v); });
				quick = h("div", { class: "px-quick" }, amounts.slice(0, 5).map(function (v, i) {
					return h("button", { type: "button", class: "px-btn px-btn-sm", text: i === 0 ? M.exact : numberFmt.format(v), onclick: function () { Object.keys(inputs).forEach(function (k) { inputs[k].value = k === m.mode_of_payment ? String(v) : ""; }); drawChange(); input.focus(); } });
				}));
			}
			return h("div", { class: "px-pay-row" }, h("label", { class: "px-field" }, h("span", { text: m.label || m.mode_of_payment }), input), quick);
		});
		function paid() {
			return Object.keys(inputs).reduce(function (sum, k) { var n = parseNumber(inputs[k].value); return sum + (isNaN(n) ? 0 : n); }, 0);
		}
		function drawChange() {
			var diff = paid() - due;
			change.textContent = "";
			if (diff >= 0) append(change, h("span", { text: M.change }), h("strong", { text: money(diff) }));
			else append(change, h("span", { text: M.remaining }), h("strong", { class: "px-danger", text: money(-diff) }));
		}
		var completeBtn = h("button", { type: "button", class: "px-btn px-btn-pay", text: M.complete_sale, onclick: complete });
		function complete() {
			if (completeBtn.disabled) return;
			var payments = Object.keys(inputs).map(function (k) { var n = parseNumber(inputs[k].value); return { mode_of_payment: k, amount: isNaN(n) ? 0 : n }; }).filter(function (p) { return p.amount > 0; });
			if (!resume && paid() < due && !S.profile.allow_partial_payment) { err.textContent = M.remaining + ": " + money(due - paid()); return; }
			err.textContent = "";
			checkout(payments, completeBtn, err);
		}
		drawChange();
		openDialog({
			title: M.payment,
			dismissable: !resume,
			body: [h("div", { class: "px-due" }, h("span", { text: M.total }), h("strong", { text: money(due) })), rows, change, err],
			actions: [completeBtn],
		});
		if (resume) checkout(S.pending.payments || [], completeBtn, err);
	}

	function checkout(payments, button, err) {
		var sig = signature();
		if (!S.pending || (S.pending.signature !== sig && !S.pending.uncertain)) S.pending = { id: uuid(), signature: sig };
		S.pending.payments = S.pending.payments && S.pending.uncertain ? S.pending.payments : payments;
		var attempt = 0;
		button.disabled = true;
		button.textContent = M.completing;
		var args = {
			pos_profile: S.profile.name,
			items: payload(),
			payments: S.pending.payments,
			request_id: S.pending.id,
			customer: S.customer,
			additional_discount_percentage: S.discountPct || 0,
		};
		function send() {
			attempt++;
			S.pending.uncertain = true; // until the server answers, this sale may or may not exist
			saveCart();
			renderLines();
			call(M_CHECKOUT, args, { post: true, timeout: 60000 }).then(function (invoice) {
				hideBanner();
				clearSale();
				closeDialog();
				if (invoice.replayed) toast(M.replayed, "info");
				showReceipt(invoice, true);
			}).catch(function (e) {
				if (e.network) {
					// unknown outcome: keep the same request ID and ask again; never a second sale
					showBanner(M.retrying, "warning");
					err.textContent = M.retrying;
					setTimeout(send, Math.min(2000 * attempt, 10000));
					return;
				}
				// the server answered: nothing was sold
				S.pending.uncertain = false;
				saveCart();
				renderLines();
				err.textContent = e.message;
				button.disabled = false;
				button.textContent = M.complete_sale;
				if (S.dialog) S.dialog.dismissable = true;
				beep(false);
			});
		}
		send();
	}

	// ------------------------------------------------------------------ receipt & printing

	function receiptUrl(doctype, name) {
		var q = new URLSearchParams({ doctype: doctype, name: name, format: S.profile.print_format, no_letterhead: "1", _lang: lang });
		return "/printview?" + q.toString();
	}

	function showReceipt(inv, justCompleted) {
		var url = receiptUrl(inv.doctype || S.ctx.invoice_doctype, inv.name);
		var frame = h("iframe", { class: "px-receipt-frame", src: url, title: M.receipt });
		frame.addEventListener("load", function () {
			try {
				// the print view's own toolbar is not needed inside the POS preview
				var style = frame.contentDocument.createElement("style");
				style.textContent = ".action-banner,.print-format-gutter>.btn,.print-toolbar{display:none!important}body{background:#fff!important}";
				frame.contentDocument.head.appendChild(style);
			} catch (e) { /* same origin expected; ignore */ }
		});
		var printBtn = h("button", { type: "button", class: "px-btn px-btn-primary", text: M.print_receipt, onclick: function () { printReceipt(url, frame); } });
		var newBtn = h("button", { type: "button", class: "px-btn", text: M.new_sale, "data-autofocus": "", onclick: closeDialog });
		var summary = h("div", { class: "px-receipt-summary" },
			h("div", null, h("span", { class: "px-muted", text: M.invoice + " " }), h("bdi", { dir: "ltr", text: inv.name })),
			inv.grand_total !== undefined ? h("div", { class: "px-due" }, h("span", { text: inv.is_return ? M.refund : M.total }), h("strong", { text: money(Math.abs(inv.rounded_total || inv.grand_total)) })) : null,
			inv.change_amount ? h("div", { class: "px-change" }, h("span", { text: M.change }), h("strong", { text: money(inv.change_amount) })) : null,
			platform.nativePrint ? null : h("p", { class: "px-muted px-small", text: M.browser_print_note })
		);
		openDialog({
			title: justCompleted ? (inv.is_return ? M.return_complete : M.sale_complete) : M.receipt,
			wide: true,
			body: [summary, frame],
			actions: [printBtn, newBtn],
		});
	}

	function printReceipt(url, frame) {
		if (platform.nativePrint) {
			platform.printReceipt(url).then(function (r) {
				if (r && r.ok) toast(M.desktop_printed, "success");
				else toast((r && r.error) || M.error, "error");
			});
			return;
		}
		try {
			frame.contentWindow.focus();
			frame.contentWindow.print();
		} catch (e) {
			window.open(url + "&trigger_print=1", "_blank", "noopener");
		}
	}

	// ------------------------------------------------------------------ returns

	function openReturns(invoice) {
		var input = h("input", { class: "px-input", placeholder: M.invoice_no, dir: "ltr", value: invoice || "", "data-autofocus": "", autocomplete: "off" });
		var area = h("div", { class: "px-return-area" });
		var err = h("p", { class: "px-error", role: "alert" });
		var findBtn = h("button", { type: "button", class: "px-btn px-btn-primary", text: M.find, onclick: find });
		input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); find(); } });
		var confirmBtn = h("button", { type: "button", class: "px-btn px-btn-pay", text: M.confirm_return, disabled: true });
		var candidate = null;
		var qtyInputs = [];
		var refundBox = h("div", { class: "px-due" });
		var requestId = uuid();

		function chosen() {
			return qtyInputs.map(function (x) { var n = parseNumber(x.input.value); return { row: x.row, qty: isNaN(n) ? 0 : n }; }).filter(function (l) { return l.qty > 0; });
		}
		var preview = debounce(function () {
			var lines = chosen();
			refundBox.textContent = "";
			confirmBtn.disabled = true;
			if (!lines.length) return;
			requestId = uuid(); // a different return → a different request
			call(M_PREVIEW_RETURN, { invoice: candidate.invoice, lines: lines }, { post: true }).then(function (r) {
				err.textContent = "";
				append(refundBox, h("span", { text: M.refund }), h("strong", { text: money(Math.abs(r.rounded_total || r.grand_total)) }), h("small", { class: "px-muted", text: M.refund_preview }));
				confirmBtn.disabled = false;
			}).catch(function (e) { err.textContent = e.message; });
		}, 250);

		function find() {
			var no = input.value.trim();
			if (!no) return;
			err.textContent = "";
			area.textContent = "";
			call(M_RETURN_CANDIDATE, { invoice: no }).then(function (c) {
				candidate = c;
				qtyInputs = [];
				var rows = c.lines.filter(function (l) { return l.returnable_qty > 0; });
				if (!rows.length) { area.appendChild(h("p", { text: M.nothing_returnable })); return; }
				var table = h("table", { class: "px-table" },
					h("thead", null, h("tr", null, h("th", { text: "" }), h("th", { text: M.sold }), h("th", { text: M.returnable }), h("th", { text: M.price }), h("th", { text: M.return_qty }))),
					h("tbody", null, rows.map(function (l) {
						var q = h("input", { class: "px-input px-qty-input", inputmode: "decimal", value: "0", "aria-label": M.return_qty, oninput: preview });
						qtyInputs.push({ row: l.row, input: q, max: l.returnable_qty });
						return h("tr", null, h("td", { text: l.item_name }), h("td", { text: qtyText(l.sold_qty) }), h("td", { text: qtyText(l.returnable_qty) }), h("td", { text: money(l.rate) }), h("td", null, q));
					}))
				);
				append(area, h("p", { class: "px-muted" }, h("bdi", { dir: "ltr", text: c.invoice }), " · ", c.customer_name || "", " · ", h("bdi", { dir: "ltr", text: c.posting_date }), " · ", money(c.grand_total)), table, refundBox);
				if (qtyInputs[0]) qtyInputs[0].input.focus();
			}).catch(function (e) { err.textContent = e.message; });
		}

		confirmBtn.addEventListener("click", function () {
			var lines = chosen();
			if (!lines.length || !candidate) return;
			confirmBtn.disabled = true;
			call(M_SUBMIT_RETURN, { invoice: candidate.invoice, lines: lines, request_id: requestId }, { post: true, timeout: 60000 })
				.then(function (ret) { closeDialog(); showReceipt(ret, true); })
				.catch(function (e) { err.textContent = e.message; confirmBtn.disabled = false; });
		});

		openDialog({ title: M.return_title, wide: true, body: [h("div", { class: "px-inline" }, input, findBtn), area, err], actions: [confirmBtn] });
		if (invoice) find();
	}

	// ------------------------------------------------------------------ recent sales

	function openRecent() {
		var list = h("div", { class: "px-list" }, h("p", { class: "px-muted", text: M.loading }));
		openDialog({ title: M.recent, wide: true, body: list });
		call(M_RECENT, { limit: 30 }).then(function (rows) {
			list.textContent = "";
			if (!rows.length) list.appendChild(h("p", { class: "px-empty", text: "—" }));
			rows.forEach(function (r) {
				append(list, h("div", { class: "px-list-row px-recent" },
					h("div", null,
						h("strong", null, h("bdi", { dir: "ltr", text: r.name })),
						r.is_return ? h("span", { class: "px-badge px-badge-warn", text: M.is_return }) : null,
						h("div", { class: "px-muted" }, r.customer_name || "", " · ", h("bdi", { dir: "ltr", text: r.posting_date + " " + shortTime(r.posting_time) }))
					),
					h("strong", { text: money(Math.abs(r.rounded_total || r.grand_total)) }),
					h("div", { class: "px-row-actions" },
						h("button", { type: "button", class: "px-btn px-btn-sm", text: M.reprint, onclick: function () { showReceipt({ name: r.name, doctype: S.ctx.invoice_doctype }, false); } }),
						r.is_return ? null : h("button", { type: "button", class: "px-btn px-btn-sm", text: M.return_short, onclick: function () { openReturns(r.name); } })
					)
				));
			});
		}).catch(function (e) { list.textContent = e.message; });
	}

	// ------------------------------------------------------------------ shift close, sign-out

	function closeShift() {
		if (!S.ctx || !S.ctx.shift) return;
		var q = new URLSearchParams({ pos_opening_entry: S.ctx.shift.name });
		window.location.href = S.ctx.desk_base + "/pos-closing-entry/new?" + q.toString();
	}

	function logout() {
		store.set("cart", null);
		call("logout", {}, { post: true }).catch(function () {}).then(function () { window.location.href = signInUrl(); });
	}

	// ------------------------------------------------------------------ wiring

	$("#px-scan-form").addEventListener("submit", function (e) {
		e.preventDefault();
		var input = $("#px-scan");
		var code = input.value.trim();
		input.value = ""; // cleared at once so the next scan starts empty
		liveSearch.cancel();
		if (code) enqueueScan(code);
		focusScan();
	});
	$("#px-scan").addEventListener("input", function (e) { liveSearch(e.target.value.trim()); });

	document.addEventListener("click", function (e) {
		var btn = e.target.closest ? e.target.closest("[data-action]") : null;
		if (!btn || btn.disabled) return;
		var action = btn.dataset.action;
		if (!S.profile && action !== "logout") return;
		if (action === "pay") openPayment(false);
		else if (action === "customer") openCustomer();
		else if (action === "returns") openReturns("");
		else if (action === "recent") openRecent();
		else if (action === "close-shift") closeShift();
		else if (action === "logout") logout();
	});

	document.addEventListener("keydown", function (e) {
		trapTab(e);
		if (e.key === "Escape" && S.dialog && S.dialog.dismissable) { e.preventDefault(); closeDialog(); return; }
		if (e.key === "F2") { e.preventDefault(); closeDialog(); focusScan(); return; }
		if (S.dialog) return;
		if (e.key === "F4") { e.preventDefault(); openCustomer(); return; }
		if (e.key === "F9" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) { e.preventDefault(); if (quoteReady()) openPayment(false); return; }
		// a scanner (or typing) while nothing is focused goes to the scan field
		var t = e.target;
		var typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
		if (!typing && e.key && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) focusScan();
	});

	// keep the scanner ready: clicking empty space returns focus to the scan field
	document.addEventListener("mouseup", function () {
		setTimeout(function () {
			var a = document.activeElement;
			if (!S.dialog && (!a || a === document.body)) focusScan();
		}, 0);
	});

	if (/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) $("#px-hint").textContent = M.keyboard_help_mac;
	start();
})();
