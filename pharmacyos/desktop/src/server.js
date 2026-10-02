// Reachability of the PharmacyOS server and, in single-PC mode, starting the bundled server.
// The server is the authoritative pharmacy database; this app is only a window onto it, so nothing
// here ever touches pharmacy data directly.
const { execFile } = require("child_process");

async function ping(serverUrl, timeoutMs = 4000) {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const res = await fetch(new URL("/api/method/ping", serverUrl), { signal: controller.signal });
		if (!res.ok) return false;
		const body = await res.json();
		return body && body.message === "pong";
	} catch {
		return false;
	} finally {
		clearTimeout(timer);
	}
}

// Single-PC pharmacies run the server inside a WSL2 distribution installed by the PharmacyOS
// server setup. Its services are normally started at boot by a scheduled task; this is a fallback
// for when the desktop app opens first.
function startLocalServer(distro) {
	if (process.platform !== "win32") return Promise.resolve(false);
	return new Promise((resolve) => {
		execFile(
			"wsl.exe",
			["-d", distro, "--user", "root", "--exec", "/opt/pharmacyos/bin/pharmacyos-server", "start"],
			{ windowsHide: true, timeout: 120000 },
			(err) => resolve(!err)
		);
	});
}

async function waitFor(serverUrl, { attempts = 30, delayMs = 2000, onAttempt } = {}) {
	for (let i = 1; i <= attempts; i++) {
		if (await ping(serverUrl)) return true;
		onAttempt && onAttempt(i, attempts);
		await new Promise((r) => setTimeout(r, delayMs));
	}
	return false;
}

module.exports = { ping, startLocalServer, waitFor };
