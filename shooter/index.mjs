// Renders a canvas headlessly so the agent can look at its own work.
import express from "express";
import { chromium } from "playwright";

const WEB = process.env.WEB_URL ?? "http://web:5173";
const app = express();
app.use(express.json());

let browser;
const getBrowser = async () => {
  if (!browser || !browser.isConnected()) browser = await chromium.launch();
  return browser;
};

app.get("/health", (_req, res) => res.json({ ok: true }));

app.post("/shoot", async (req, res) => {
  const { canvasId, width = 1500, height = 1000 } = req.body ?? {};
  if (!canvasId) return res.status(400).json({ error: "canvasId required" });

  let page;
  try {
    const b = await getBrowser();
    page = await b.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    await page.goto(`${WEB}/?canvas=${canvasId}&bare=1`, { waitUntil: "networkidle", timeout: 30000 });
    // Charts animate; wait for the board to settle rather than a fixed guess.
    await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2600);
    const board = await page.$(".board");
    const buf = await (board ?? page).screenshot({ type: "png" });
    res.json({ image: buf.toString("base64"), bytes: buf.length });
  } catch (e) {
    res.status(500).json({ error: String(e) });
  } finally {
    await page?.close().catch(() => {});
  }
});

app.listen(3002, () => console.log("shooter on :3002"));
