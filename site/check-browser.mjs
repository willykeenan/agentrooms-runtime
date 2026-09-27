// Optional development check. The published demo itself has no dependencies.
// Usage: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
// BROWSER_EXECUTABLE=/path/to/chrome node site/check-browser.mjs http://localhost:8000
// SCREENSHOT_DIR optionally saves desktop/mobile images in an operator-chosen directory.
import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {join} from "node:path";
import {pathToFileURL} from "node:url";

const moduleName = process.env.PLAYWRIGHT_MODULE;
const {chromium} = await import(moduleName?.startsWith("/") ? pathToFileURL(moduleName).href : moduleName || "playwright");
const target = process.argv[2];
if (!target) throw new Error("Pass the local or deployed demo URL as the first argument.");
const targetOrigin = new URL(target).origin;
const screenshotDir = process.env.SCREENSHOT_DIR;
if (screenshotDir) await mkdir(screenshotDir, {recursive: true});
const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
const failures = [];
const requests = new Set();
try {
  const context = await browser.newContext({viewport: {width: 1280, height: 960}, colorScheme: "dark", acceptDownloads: true});
  const page = await context.newPage();
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("request", (request) => { if (/^https?:/.test(request.url())) requests.add(request.url()); });
  await page.goto(target, {waitUntil: "networkidle"});
  await page.waitForFunction(() => document.querySelector("#run-id")?.textContent === "write-artifact-01");
  assert.match(await page.locator("h1").innerText(), /Every agent task/);
  assert.match(await page.locator("#json-status").innerText(), /not validated/);
  assert.equal(await page.locator("#download").isEnabled(), true);
  assert.equal(await page.locator("#manifest-editor").getAttribute("aria-invalid"), "false");
  if (screenshotDir) await page.screenshot({path: join(screenshotDir, "desktop-dark.png"), fullPage: true});

  await page.getByRole("button", {name: "Analyze a batch", exact: true}).click();
  await page.waitForFunction(() => document.querySelector("#run-id")?.textContent === "batch-analysis-01");
  assert.equal(await page.locator("#cpu-value").innerText(), "2");
  assert.match(await page.locator("#output-list").innerText(), /summary.json/);
  await page.locator("#manifest-editor").fill('{"id":');
  await page.waitForFunction(() => document.querySelector("#manifest-editor")?.getAttribute("aria-invalid") === "true");
  assert.equal(await page.locator("#download").isDisabled(), true);
  assert.equal(await page.locator("#parse-empty").isVisible(), true);

  await page.getByRole("button", {name: "Reset example", exact: true}).click();
  const value = JSON.parse(await page.locator("#manifest-editor").inputValue());
  value.id = '<img src="https://invalid.example/x" onerror="alert(1)">';
  value.outputs = ['<script>alert("x")</script>'];
  value.unrecognized = true;
  delete value.ownerAgentId;
  const edited = JSON.stringify(value);
  await page.locator("#manifest-editor").fill(edited);
  await page.waitForFunction(() => document.querySelector("#field-warnings")?.textContent.includes("unrecognized"));
  assert.equal(await page.locator("#run-id").innerText(), value.id);
  assert.equal(await page.locator("#preview-content img, #preview-content script").count(), 0);
  assert.match(await page.locator("#field-warnings").innerText(), /ownerAgentId/);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download task.json", exact: true}).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), "task.json");
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString("utf8"), edited);
  await page.getByRole("button", {name: "Format JSON", exact: true}).click();
  assert.equal(await page.locator("#manifest-editor").inputValue(), JSON.stringify(value, null, 2));
  await page.locator("#manifest-editor").focus();
  await page.keyboard.press("Tab");
  assert.notEqual(await page.evaluate(() => document.activeElement?.id), "manifest-editor", "editor must not trap keyboard focus");

  await page.getByRole("button", {name: "Run a test task", exact: true}).click();
  await page.waitForFunction(() => document.querySelector("#run-id")?.textContent === "test-task-01");
  await page.evaluate(() => document.activeElement?.blur());
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "mobile view must not overflow horizontally");
  assert.equal(await page.getByRole("button", {name: "Download task.json", exact: true}).isVisible(), true);
  if (screenshotDir) await page.screenshot({path: join(screenshotDir, "mobile-dark.png"), fullPage: true});
  await page.emulateMedia({colorScheme: "light"});
  if (screenshotDir) await page.screenshot({path: join(screenshotDir, "mobile-light.png"), fullPage: true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(failures, [], "page must not raise uncaught script errors");
  const external = [...requests].filter((url) => new URL(url).origin !== targetOrigin);
  assert.deepEqual(external, [], "the demo must not initiate external network requests");
  console.log(JSON.stringify({result: "passed", target, checks: ["initial render", "all presets", "JSON syntax error", "field warnings", "literal HTML rendering", "exact local download", "formatting", "keyboard escape", "mobile layout", "light theme", "no script errors", "no external requests"], requests: [...requests], screenshots: screenshotDir || null}, null, 2));
} finally {
  await browser.close();
}
