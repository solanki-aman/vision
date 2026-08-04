// Capture full-page screenshots of canvases. Usage: node scripts/shoot.mjs <outDir> <title>...
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

const [outDir, ...titles] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 2 });

// localStorage is not writeable on about:blank; open the app once so we have an origin.
await page.goto("http://localhost:5173", { waitUntil: "domcontentloaded" });
await page.evaluate(() =>
  localStorage.setItem(
    "vision.settings",
    JSON.stringify({ mode: "light", paletteKey: "vision", custom: [], animate: false }),
  ),
);

const canvases = await (await fetch("http://localhost:3001/api/canvases")).json();

for (const title of titles) {
  const match = canvases.find((c) => c.title.toLowerCase().includes(title.toLowerCase()));
  if (!match) {
    console.log(`skip: no canvas matching "${title}"`);
    continue;
  }

  // Deep-link to the canvas and let animations settle. `bare=1` hides the composer and rail.
  await page.goto(`http://localhost:5173/?canvas=${match.id}&bare=1`, { waitUntil: "networkidle" });
  await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(4000);
  // Some layouts render the composer even in bare mode; strip anything the screenshot shouldn't include.
  await page.addStyleTag({
    content: ".dock, .topbar, .rail, .present-launch, .present-bar { display: none !important; }",
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);

  const board = await page.$(".board");
  const file = path.join(outDir, `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.png`);
  await board.screenshot({ path: file, timeout: 60000 });
  console.log(`saved ${file}  (${match.title})`);
}

await browser.close();
