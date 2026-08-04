// Product demo video: one complex CFO-flavoured question that uses web_search,
// then two follow-up edits — a surgical chart-series add, then a style change.
// Usage: node scripts/record.mjs <outDir>
import { chromium } from "playwright";
import { existsSync, mkdirSync, readdirSync, renameSync, statSync } from "node:fs";
import path from "node:path";

const outDir = process.argv[2] ?? "../screenshots";
mkdirSync(outDir, { recursive: true });

const VIEWPORT = { width: 1600, height: 1000 };

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
  recordVideo: { dir: outDir, size: VIEWPORT },
});
const page = await context.newPage();

// Warm the app once so localStorage is writable.
await page.goto("http://localhost:5173", { waitUntil: "domcontentloaded" });
await page.evaluate(() =>
  localStorage.setItem(
    "vision.settings",
    JSON.stringify({ mode: "light", paletteKey: "vision", custom: [], animate: true }),
  ),
);

// Fresh canvas — clean slate for the demo.
const created = await (
  await fetch("http://localhost:3001/api/canvases", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: "Demo — NVDA fundamentals" }),
  })
).json();

await page.goto(`http://localhost:5173/?canvas=${created.id}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

// The form gets a `.busy` class while a turn is streaming — its absence is our ready signal.
const waitForReady = async (timeoutMs = 300000) => {
  await page.waitForFunction(
    () => document.querySelector(".bar") && !document.querySelector(".bar.busy"),
    null,
    { timeout: timeoutMs, polling: 500 },
  );
};

const ask = async (text) => {
  await waitForReady();
  await page.click("textarea");
  await page.fill("textarea", "");
  await page.type("textarea", text, { delay: 18 });
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter");
};

// --- Turn 1: complex CFO briefing, uses web_search for live numbers. -------------------
await ask(
  "Give me a full CFO-level briefing on Nvidia right now. Real numbers via web search. I want the whole story: revenue by segment, gross margin trajectory, hyperscaler concentration risk, and a bottom-line thesis. Use hero, a KPI row, a revenue chart, a margin bridge, a segment breakdown, a statement or table, and a narrative. Do not skimp.",
);
await waitForReady(360000);
await page.waitForTimeout(3500);
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(1500);

// --- Turn 2: surgical chart edit — add a series to an existing chart. ------------------
await ask(
  "Add AMD's data-center revenue to that revenue chart as a comparison series. Same categories, same axis. Don't rebuild the chart.",
);
await waitForReady(180000);
await page.waitForTimeout(4000);

// --- Turn 3: change the visual identity in one call — every widget re-skins. -----------
await ask(
  "The tone is too clinical for what the numbers actually say. Give this the identity of a short thesis: warmer, more urgent, less spec-sheet.",
);
await waitForReady(120000);
await page.waitForTimeout(4500);

await page.close();
await context.close();
await browser.close();

// Rename Playwright's random-slug WebM to something deterministic.
const dest = path.join(outDir, "demo.webm");
const webms = readdirSync(outDir)
  .filter((f) => f.endsWith(".webm") && f !== "demo.webm" && f !== "demo.prev.webm")
  .map((f) => ({ f, m: statSync(path.join(outDir, f)).mtimeMs }))
  .sort((a, b) => b.m - a.m);
if (!webms.length) throw new Error("no webm produced");
if (existsSync(dest)) renameSync(dest, dest.replace(".webm", ".prev.webm"));
renameSync(path.join(outDir, webms[0].f), dest);
console.log(`saved ${dest}`);
