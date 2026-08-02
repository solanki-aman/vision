// Capture full-page screenshots of canvases. Usage: node scripts/shoot.mjs <outDir> <title>...
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [outDir, ...titles] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 2 });

const canvases = await (await fetch("http://localhost:3001/api/canvases")).json();

for (const title of titles) {
  const match = canvases.find((c) => c.title.toLowerCase().includes(title.toLowerCase()));
  if (!match) {
    console.log(`skip: no canvas matching "${title}"`);
    continue;
  }

  await page.goto("http://localhost:5173", { waitUntil: "networkidle" });
  await page.evaluate(() =>
    localStorage.setItem(
      "vision.settings",
      JSON.stringify({ mode: "light", paletteKey: "vision", custom: [], animate: false }),
    ),
  );
  await page.goto("http://localhost:5173", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  await page.click(".mark");
  await page.waitForTimeout(500);
  await page.click(`.nav-inner button[data-canvas-id="${match.id}"]`);
  await page.waitForTimeout(4000);

  // Collapse the rail for a clean capture.
  await page.evaluate(() => {
    const t = document.querySelector(".rail-toggle");
    if (t && !document.querySelector(".rail.collapsed")) t.click();
  });
  await page.waitForTimeout(2500);

  const board = await page.$(".board");
  const file = path.join(outDir, `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.png`);
  await board.screenshot({ path: file });
  console.log(`saved ${file}  (${match.title})`);
}

await browser.close();
