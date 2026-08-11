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

// This service has no session, so under authentication it cannot load a canvas.
// The server mints an HMAC token scoped to one canvas and valid for two minutes and
// passes it here; the SPA forwards it on its own API calls. Narrower than giving the
// renderer a standing credential to every canvas.
const boardUrl = (canvasId, renderToken) =>
  `${WEB}/?canvas=${canvasId}&bare=1${renderToken ? `&rt=${encodeURIComponent(renderToken)}` : ""}`;

app.post("/shoot", async (req, res) => {
  const { canvasId, width = 1500, height = 1000, renderToken } = req.body ?? {};
  if (!canvasId) return res.status(400).json({ error: "canvasId required" });

  let page;
  try {
    const b = await getBrowser();
    page = await b.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    await page.goto(boardUrl(canvasId, renderToken), { waitUntil: "networkidle", timeout: 30000 });
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

// Export the whole board as a file the user keeps: PNG for pasting, PDF for sending.
// Unlike /shoot (which serves the agent a look at its own work) this sizes the
// viewport to the full board so nothing is cropped, and returns raw bytes.
app.post("/export", async (req, res) => {
  const { canvasId, format = "png", width = 1600, renderToken } = req.body ?? {};
  if (!canvasId) return res.status(400).json({ error: "canvasId required" });
  if (!["png", "pdf"].includes(format)) return res.status(400).json({ error: "format must be png or pdf" });

  let page;
  try {
    const b = await getBrowser();
    page = await b.newPage({ viewport: { width, height: 1000 }, deviceScaleFactor: 2 });
    await page.goto(boardUrl(canvasId, renderToken), { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForSelector(".widget", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2600);

    // An export is the board, not the app. `bare=1` drops the rail and composer,
    // but the topbar lives above the canvas view and the dock's `display:flex`
    // beats its `hidden` attribute — so strip the chrome explicitly, and do it
    // BEFORE measuring, since removing it changes the height.
    await page.addStyleTag({
      content:
        ".topbar, .dock, .rail, .present-launch, .present-bar { display: none !important; }" +
        ".board { padding-bottom: 24px !important; }",
    });
    await page.waitForTimeout(300);

    // Grow the viewport to the whole board, so a tall canvas isn't cut off.
    const full = await page.evaluate(() => {
      const el = document.querySelector(".board");
      return el ? Math.ceil(el.scrollHeight) : document.body.scrollHeight;
    });
    const height = Math.min(Math.max(full + 40, 400), 20000);
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(600);

    if (format === "pdf") {
      // Print CSS would otherwise drop the canvas's background and card fills.
      const buf = await page.pdf({
        printBackground: true,
        width: `${width}px`,
        height: `${height}px`,
        margin: { top: "0", bottom: "0", left: "0", right: "0" },
      });
      res.setHeader("Content-Type", "application/pdf");
      return res.send(buf);
    }

    const board = await page.$(".board");
    const buf = await (board ?? page).screenshot({ type: "png" });
    res.setHeader("Content-Type", "image/png");
    return res.send(buf);
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  } finally {
    await page?.close().catch(() => {});
  }
});

app.listen(3002, () => console.log("shooter on :3002"));
