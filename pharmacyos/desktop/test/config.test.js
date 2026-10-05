const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const config = require("../src/config");

test("server addresses are normalised to an origin", () => {
	assert.equal(config.normaliseUrl("192.168.1.10"), "http://192.168.1.10");
	assert.equal(config.normaliseUrl("192.168.1.10:8080"), "http://192.168.1.10:8080");
	assert.equal(config.normaliseUrl("https://pharmacy.example/desk"), "https://pharmacy.example");
	assert.equal(config.normaliseUrl(""), "http://127.0.0.1");
	assert.throws(() => config.normaliseUrl("http://"));
});

test("config is saved atomically and survives reload; unconfigured by default", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pos-desktop-"));
	assert.equal(config.isConfigured(config.load(dir)), false);
	config.save(dir, { mode: "network", serverUrl: "10.0.0.5" });
	const loaded = config.load(dir);
	assert.equal(loaded.serverUrl, "http://10.0.0.5");
	assert.equal(config.isConfigured(loaded), true);
	assert.ok(!fs.existsSync(path.join(dir, "config.json.tmp")));
	assert.ok(!JSON.stringify(loaded).toLowerCase().includes("password"));
	assert.equal(loaded.startPage, "desk");
	assert.equal(config.save(dir, { ...loaded, startPage: "pos" }).startPage, "pos");
	assert.equal(config.save(dir, { ...loaded, startPage: "javascript:alert(1)" }).startPage, "desk");
});

test("ping reports an unreachable server as offline", async () => {
	const server = require("../src/server");
	assert.equal(await server.ping("http://127.0.0.1:9", 1000), false);
});
