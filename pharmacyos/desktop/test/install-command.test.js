// The Windows setup hands the pharmacy's details to install-server.sh inside the PharmacyOS WSL environment
// as "wsl.exe -d PharmacyOS --user root --exec bash -c <script>" (deploy/windows/install-server.ps1). These
// tests take the installer's real PowerShell functions (from its AST, as the CI does), split the command
// line the way a Windows program splits it, and run the script through ONE bash parse (what --exec gives)
// with a stub install-server.sh: every value must arrive byte for byte.
// Needs pwsh and bash; skipped without them, and on Windows (where "bash" may be WSL itself).
const test = require("node:test");
const assert = require("node:assert");
const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const WINDOWS = path.join(__dirname, "..", "..", "deploy", "windows");
const INSTALLER = path.join(WINDOWS, "install-server.ps1");

const works = (cmd, args) => {
	const r = spawnSync(cmd, args, { encoding: "utf8" });
	return !r.error && r.status === 0;
};
const skip =
	process.platform === "win32"
		? "bash may be WSL itself on Windows"
		: !works("bash", ["-c", "exit 0"])
			? "bash is not installed"
			: !works("pwsh", ["-NoProfile", "-NonInteractive", "-Command", "exit 0"])
				? "pwsh (PowerShell 7) is not installed"
				: false;

// Runs PowerShell code with the named functions of install-server.ps1 loaded; returns stdout.
function pwsh(functions, code, args = []) {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-ps-"));
	const file = path.join(dir, "run.ps1");
	const load = `$ErrorActionPreference = "Stop"
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(${psq(INSTALLER)}, [ref]$null, [ref]$errors)
if ($errors) { throw "install-server.ps1: $($errors[0].Message)" }
$ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
	Where-Object { $_.Name -in @(${functions.map(psq).join(", ")}) } | ForEach-Object { Invoke-Expression $_.Extent.Text }
`;
	fs.writeFileSync(file, load + code);
	const r = spawnSync("pwsh", ["-NoProfile", "-NonInteractive", "-File", file, ...args], { encoding: "utf8" });
	fs.rmSync(dir, { recursive: true, force: true });
	assert.equal(r.status, 0, `pwsh failed: ${r.stderr || r.stdout}`);
	return r.stdout;
}
const psq = (s) => "'" + String(s).replace(/'/g, "''") + "'";

// How a Windows program (wsl.exe) splits its command line into arguments (CommandLineToArgvW rules).
function windowsArgv(line) {
	const args = [];
	let cur = "";
	let quoted = false;
	let started = false;
	for (let i = 0; i < line.length; i++) {
		const c = line[i];
		if (c === "\\") {
			let n = 0;
			while (line[i] === "\\") (n++, i++);
			if (line[i] === '"') {
				cur += "\\".repeat(n >> 1);
				if (n % 2) cur += '"';
				else quoted = !quoted;
			} else {
				cur += "\\".repeat(n);
				i--;
			}
			started = true;
		} else if (c === '"') {
			if (quoted && line[i + 1] === '"') (cur += '"'), i++;
			else quoted = !quoted;
			started = true;
		} else if ((c === " " || c === "\t") && !quoted) {
			if (started) args.push(cur);
			cur = "";
			started = false;
		} else {
			cur += c;
			started = true;
		}
	}
	if (started) args.push(cur);
	return args;
}

// The wsl.exe arguments install-server.ps1 builds for these values and install script path.
function installArguments(values, installScript) {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-values-"));
	const file = path.join(dir, "values.txt");
	// values reach PowerShell base64-encoded too, so nothing in this test's own plumbing can bend them
	fs.writeFileSync(file, Object.entries(values).map(([k, v]) => `${k}\t${Buffer.from(v, "utf8").toString("base64")}`).join("\n"));
	const out = pwsh(
		["ConvertTo-B64", "ConvertTo-InstallScript", "Get-InstallArguments"],
		`$values = [ordered]@{}
foreach ($line in Get-Content -LiteralPath $args[0]) {
	$k, $v = $line -split "\`t"
	$values[$k] = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$v))
}
[Console]::Out.Write((Get-InstallArguments "PharmacyOS" $values $args[1]))
`,
		[file, installScript],
	);
	fs.rmSync(dir, { recursive: true, force: true });
	return out;
}

// A stub install-server.sh in a folder named like the real one (with a space), printing each value it got.
function stubInstaller(names, folder) {
	const root = fs.mkdtempSync(path.join(os.tmpdir(), "pos install "));
	const dir = path.join(root, folder, "deploy", "server");
	fs.mkdirSync(dir, { recursive: true });
	const file = path.join(dir, "install-server.sh");
	const lines = names.map((n) => `printf '%s=%s\\n' ${n} "$(printf %s "\${${n}-UNSET}" | base64 -w0)"`);
	fs.writeFileSync(file, ["#!/bin/bash", ...lines, "echo '##stderr reaches the log' >&2", ""].join("\n"));
	return { root, file };
}

// Splits the command line like Windows, runs the script like "--exec bash -c", returns what the stub got.
function runInstall(values, folder) {
	const names = Object.keys(values);
	const stub = stubInstaller(names, folder);
	try {
		const argv = windowsArgv(installArguments(values, stub.file));
		assert.deepEqual(argv.slice(0, 7), ["-d", "PharmacyOS", "--user", "root", "--exec", "bash", "-c"]);
		assert.equal(argv.length, 8, "the script is ONE argument");
		const script = argv[7];
		const r = spawnSync("bash", ["-c", script], { cwd: stub.root, env: { PATH: process.env.PATH }, encoding: "utf8" });
		assert.equal(r.status, 0, `bash failed: ${r.stderr}`);
		const got = {};
		for (const line of r.stdout.split("\n")) {
			const m = /^([A-Z_][A-Z0-9_]*)=(.*)$/.exec(line);
			if (m) got[m[1]] = Buffer.from(m[2], "base64");
		}
		return { script, got, stdout: r.stdout };
	} finally {
		fs.rmSync(stub.root, { recursive: true, force: true });
	}
}

const TRICKY = {
	SITE: "pharmacy.local",
	HTTP_PORT: "8780",
	PHARMACYOS_DATA_DIR: "/mnt/c/ProgramData/PharmacyOS",
	PHARMACY_NAME: "Al Noor Pharmacy",
	PHARMACY_NAME_AR: "صيدلية النور",
	OWNER_EMAIL: "o'wner+1@example.com",
	OWNER_PASSWORD: `it's-$ecret"1`,
	OWNER_FULL_NAME: "#Ahmed `Ali` (x) <y> & z; w",
	PHARMACY_PHONE: "",
	DOLLARS: "Pharm$2026x $HOME ${PATH} $(id) `id` $((1+1))",
	COMMENT: "# leading hash",
	QUOTES: `'single' "double" \\back\\slash\\ \\"`,
	SHELL_CHARS: "a;b&c|d<e>f(g)h{i,j}k*l?m[n]o~p!q^r%s",
	SPACES: "  two  spaces\tand a tab  ",
	MIXED: "صيدلية \"النور\" $1 'x' `y` #z",
};

test("setup values reach install-server.sh byte for byte through one shell (wsl --exec bash -c)", { skip }, () => {
	const { script, got, stdout } = runInstall(TRICKY, "resources/server");
	assert.ok(!script.includes('"'), "no double quote in the script: Windows passes it as it is");
	assert.ok(!script.includes("\\"), "no backslash in the script");
	assert.ok(!/O'wner|Al Noor|ecret/.test(script), "values travel encoded, never as shell text");
	for (const [name, value] of Object.entries(TRICKY)) {
		assert.ok(got[name], `${name} reached the install script (exported)`);
		assert.ok(got[name].equals(Buffer.from(value, "utf8")), `${name}: ${JSON.stringify(got[name].toString("utf8"))} !== ${JSON.stringify(value)}`);
	}
	assert.match(stdout, /##stderr reaches the log/, "stderr is relayed with stdout (2>&1)");
});

test("an install folder with a quote in its name still runs the right script", { skip }, () => {
	const { script, got } = runInstall({ PHARMACY_NAME: "Al Noor Pharmacy", OWNER_PASSWORD: `p'w"$x` }, "O'Brien's PharmacyOS/server");
	assert.ok(!script.includes('"'), "no double quote in the script");
	assert.equal(got.PHARMACY_NAME.toString("utf8"), "Al Noor Pharmacy");
	assert.equal(got.OWNER_PASSWORD.toString("utf8"), `p'w"$x`);
});

test("only plain variable names become assignments", { skip }, () => {
	const out = pwsh(
		["ConvertTo-B64", "ConvertTo-InstallScript"],
		`try { ConvertTo-InstallScript ([ordered]@{ "X;id" = "v" }) "/x.sh" | Out-Null; "accepted" } catch { "refused" }`,
	);
	assert.equal(out.trim(), "refused");
});

test("the Ubuntu image's SHA-256 comes from the SHA256SUMS published next to it", { skip }, () => {
	const url = /^\$RootfsUrl = "([^"]+)"/m.exec(fs.readFileSync(INSTALLER, "utf8").replace(/^﻿/, ""))[1];
	assert.match(url, /^https:\/\//);
	const name = url.slice(url.lastIndexOf("/") + 1);
	const sums = [
		"2a790896740b14d637dbdc583cce1ba081ac53b9e9cdb46dc09a2f73abbd9934  ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz",
		"d3930f601ea875e3480c3383e4f5200572b749bf3a4340b81a95f14bb82ec7ea  ubuntu-noble-wsl-amd64-wsl.manifest",
		`8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea *${name}`,
		"fecec1d9b7b750c12c109edb49c13c1006f4a2efabb9b8bf341f11c4c9f2ef11  ubuntu-noble-wsl-arm64-wsl.rootfs.tar.gz",
	].join("\r\n");
	const out = pwsh(
		["Get-PublishedSha256"],
		`$script:asked = @()
function Invoke-WebRequest($Uri, [switch]$UseBasicParsing) {
	$script:asked += $Uri
	if ($script:bytes) { return [pscustomobject]@{ Content = [Text.Encoding]::ASCII.GetBytes($script:sums) } }
	return [pscustomobject]@{ Content = $script:sums }
}
$script:sums = $args[1]
$script:bytes = $false
$text = Get-PublishedSha256 $args[0]
$script:bytes = $true
$bytes = Get-PublishedSha256 $args[0]
$script:sums = "0000000000000000000000000000000000000000000000000000000000000000  other.tar.gz"
$missing = Get-PublishedSha256 $args[0]
@($text, $bytes, "[$missing]", $script:asked[0]) -join "|"
`,
		[url, sums],
	);
	const [text, bytes, missing, asked] = out.trim().split("|");
	assert.equal(text, "8251E27FFFF381A4AF5F41DCB94D867DE3E0D9774A9241908AB34555D99315EA");
	assert.equal(bytes, text, "a byte[] response is read the same");
	assert.equal(missing, "[]", "no entry: no checksum (the setup then refuses the image)");
	assert.equal(asked, url.slice(0, url.lastIndexOf("/") + 1) + "SHA256SUMS");
});

test("every Windows setup script parses (PowerShell AST) and starts with a UTF-8 BOM", { skip }, () => {
	for (const name of fs.readdirSync(WINDOWS).filter((n) => n.endsWith(".ps1"))) {
		const file = path.join(WINDOWS, name);
		const bytes = fs.readFileSync(file);
		assert.ok(bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf, `${name}: UTF-8 BOM`);
		const r = spawnSync(
			"pwsh",
			["-NoProfile", "-NonInteractive", "-Command", `$e = $null; [System.Management.Automation.Language.Parser]::ParseFile(${psq(file)}, [ref]$null, [ref]$e) | Out-Null; $e | ForEach-Object { $_.Message }`],
			{ encoding: "utf8" },
		);
		assert.equal(r.status, 0, r.stderr);
		assert.equal(r.stdout.trim(), "", `${name}: ${r.stdout}`);
	}
});

test("setup runs the script from a file in the locked Setup folder: no value on any command line", { skip }, () => {
	// the command line names the file only
	const out = pwsh(["Get-InstallFileArguments"], `[Console]::Out.Write((Get-InstallFileArguments "PharmacyOS" "/mnt/c/ProgramData/PharmacyOS/Setup/install-run.sh"))`);
	assert.deepEqual(windowsArgv(out), ["-d", "PharmacyOS", "--user", "root", "--exec", "bash", "/mnt/c/ProgramData/PharmacyOS/Setup/install-run.sh"]);
	// the file holds the same one-shell script: run as "bash <file>", every value arrives byte for byte
	const names = Object.keys(TRICKY);
	const stub = stubInstaller(names, "resources/server");
	try {
		const script = windowsArgv(installArguments(TRICKY, stub.file))[7];
		const file = path.join(stub.root, "install-run.sh");
		fs.writeFileSync(file, script + "\n");
		const r = spawnSync("bash", [file], { cwd: stub.root, env: { PATH: process.env.PATH }, encoding: "utf8" });
		assert.equal(r.status, 0, r.stderr);
		for (const name of names) {
			const m = new RegExp(`^${name}=(.*)$`, "m").exec(r.stdout);
			assert.ok(m, `${name} missing`);
			assert.equal(Buffer.from(m[1], "base64").toString("utf8"), TRICKY[name], name);
		}
	} finally {
		fs.rmSync(stub.root, { recursive: true, force: true });
	}
	// and the installer really starts it that way (not with the values inline)
	const src = fs.readFileSync(INSTALLER, "utf8");
	assert.match(src, /\$psi\.Arguments = Get-InstallFileArguments \$Distro/);
	assert.match(src, /WriteAllText\(\$runFile, \(ConvertTo-InstallScript /);
	assert.match(src, /Remove-Item -LiteralPath \$runFile/);
	assert.doesNotMatch(src, /\$psi\.Arguments = Get-InstallArguments /);
});
