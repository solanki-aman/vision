// Capture full-chrome screenshots (topbar + board + stream rail) for README
// feature callouts — unlike shoot.mjs, this does NOT hide the chrome.
// Usage: node scripts/shoot-ui.mjs <outDir>
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [outDir] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });

async function setMode(mode) {
  await page.evaluate(
    (m) =>
      localStorage.setItem(
        "vision.settings",
        JSON.stringify({ mode: m, paletteKey: "vision", custom: [], animate: false }),
      ),
    mode,
  );
}

// localStorage needs an origin first.
await page.goto("http://localhost:5173", { waitUntil: "domcontentloaded" });

const canvases = await (await fetch("http://localhost:3001/api/canvases")).json();
const nvda = canvases.find((c) => c.title.startsWith("Chart NVIDIA"));
if (!nvda) throw new Error("no NVIDIA demo canvas found — run the seed turns first");

// ---- 1. dark mode, populated canvas + the quiet stream rail --------------
await setMode("dark");
await page.goto(`http://localhost:5173/?canvas=${nvda.id}`, { waitUntil: "networkidle" });
await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1200);
await page.screenshot({ path: path.join(outDir, "dark-mode.png") });
console.log("saved dark-mode.png");

// ---- 2. provenance drill-down modal, light mode ---------------------------
await setMode("light");
await page.goto(`http://localhost:5173/?canvas=${nvda.id}`, { waitUntil: "networkidle" });
await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1200);
// Find the (i) on the computed four-quarter-total KPI specifically.
const widget = page.locator(".widget", { hasText: "253.5" }).first();
await widget.locator(".prov-i").first().click();
await page.waitForSelector(".dd-modal", { timeout: 5000 });
await page.waitForTimeout(400);
await page.screenshot({ path: path.join(outDir, "provenance-drilldown.png") });
console.log("saved provenance-drilldown.png");

// ---- 3. empty-state redesign ----------------------------------------------
await page.goto("http://localhost:5173", { waitUntil: "domcontentloaded" });
const created = await (
  await fetch("http://localhost:3001/api/canvases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  })
).json();
await page.goto(`http://localhost:5173/?canvas=${created.id}`, { waitUntil: "networkidle" });
await page.waitForSelector(".board-empty", { timeout: 10000 }).catch(() => {});
await page.waitForTimeout(600);
await page.screenshot({ path: path.join(outDir, "empty-state.png") });
console.log("saved empty-state.png");

await browser.close();
