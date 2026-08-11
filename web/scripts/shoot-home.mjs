// Capture the Home screen (the CFO cockpit) for the README, light and dark, at 2x.
// Usage: node scripts/shoot-home.mjs <outDir>
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [outDir] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1580, height: 1080 }, deviceScaleFactor: 2 });

async function prep(mode) {
  await page.evaluate(
    (m) => {
      localStorage.setItem("vision.settings", JSON.stringify({ mode: m, paletteKey: "vision", custom: [], animate: false }));
      localStorage.setItem("vision-demo-mode", "1"); // sample attention items + rail panels
    },
    mode,
  );
}

// localStorage needs an origin first.
await page.goto("http://localhost:5173", { waitUntil: "domcontentloaded" });

// ---- 1. Home, light — the CFO glance ----
await prep("light");
await page.goto("http://localhost:5173", { waitUntil: "networkidle" });
await page.waitForSelector(".pulse .metric", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.screenshot({ path: path.join(outDir, "home-cockpit.png") });
console.log("saved home-cockpit.png");

// ---- 2. Home, dark ----
await prep("dark");
await page.goto("http://localhost:5173", { waitUntil: "networkidle" });
await page.waitForSelector(".pulse .metric", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1000);
await page.screenshot({ path: path.join(outDir, "home-dark.png") });
console.log("saved home-dark.png");

// ---- 3. the pin dialog on a warehouse-backed widget ----
await prep("light");
const canvases = await (await fetch("http://localhost:3001/api/canvases")).json();
const rev = canvases.find((c) => /revenue by region/i.test(c.title)) ?? canvases[0];
if (rev) {
  await page.goto(`http://localhost:5173/?canvas=${rev.id}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);
  const pin = page.locator(".widget-pin").first();
  if (await pin.count()) {
    await pin.click({ force: true });
    await page.waitForSelector(".pin-dialog", { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(outDir, "pin-dialog.png") });
    console.log("saved pin-dialog.png");
  }
}

await browser.close();
