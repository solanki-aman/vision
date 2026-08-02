import express from "express";
import cors from "cors";
import { createXai } from "@ai-sdk/xai";
import { streamText, convertToModelMessages, stepCountIs, type UIMessage } from "ai";
import {
  initDb,
  listCanvases,
  createCanvas,
  getCanvasState,
  getCanvasSummary,
  getMessages,
  saveMessages,
  renameCanvasIfUntitled,
  audit,
  pool,
} from "./db.js";
import { applyChangeSet, undoLast, type Operation } from "./commands.js";
import { buildTools } from "./tools.js";
import { SYSTEM_PROMPT } from "./prompt.js";

const xai = createXai({ apiKey: process.env.XAI_API_KEY ?? "" });
const MODEL = process.env.XAI_MODEL ?? "grok-4.5";

const app = express();
app.use(cors());
app.use(express.json({ limit: "4mb" }));

// One SSE channel per canvas so widgets appear the moment a command applies.
const listeners = new Map<string, Set<express.Response>>();

function notify(canvasId: string) {
  for (const res of listeners.get(canvasId) ?? []) {
    res.write(`data: ${JSON.stringify({ type: "canvas_changed", at: Date.now() })}\n\n`);
  }
}

app.get("/api/health", (_req, res) => res.json({ ok: true, model: MODEL }));

app.get("/api/canvases", async (_req, res) => res.json(await listCanvases()));

app.post("/api/canvases", async (req, res) => res.json(await createCanvas(req.body?.title)));

app.get("/api/canvases/:id", async (req, res) => res.json(await getCanvasState(req.params.id)));

app.get("/api/canvases/:id/messages", async (req, res) => res.json(await getMessages(req.params.id)));

app.get("/api/canvases/:id/events", (req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.write(": connected\n\n");
  const id = req.params.id;
  if (!listeners.has(id)) listeners.set(id, new Set());
  listeners.get(id)!.add(res);
  const ping = setInterval(() => res.write(": ping\n\n"), 25000);
  req.on("close", () => {
    clearInterval(ping);
    listeners.get(id)?.delete(res);
  });
});

// Direct manipulation (GridStack drag/resize) flows through the same command layer.
app.post("/api/canvases/:id/commands", async (req, res) => {
  const ops = req.body?.operations as Operation[];
  if (!Array.isArray(ops)) return res.status(400).json({ error: "operations[] required" });
  const result = await applyChangeSet(req.params.id, ops, req.body?.origin ?? "direct_manipulation");
  notify(req.params.id);
  res.json(result);
});

app.post("/api/canvases/:id/undo", async (req, res) => {
  const result = await undoLast(req.params.id);
  notify(req.params.id);
  res.json(result ?? { changeSetId: null, applied: [], errors: ["nothing to undo"] });
});

app.get("/api/canvases/:id/history", async (req, res) => {
  const { rows } = await pool.query(
    `SELECT id, origin, status, operations, undone, created_at
     FROM canvas.change_sets WHERE canvas_id = $1 ORDER BY created_at DESC LIMIT 40`,
    [req.params.id],
  );
  res.json(rows);
});

app.post("/api/chat", async (req, res) => {
  const { messages, canvasId } = req.body as { messages: UIMessage[]; canvasId: string };

  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const lastText = lastUser?.parts?.find((p: any) => p.type === "text") as { text?: string } | undefined;
  if (lastText?.text) await renameCanvasIfUntitled(canvasId, lastText.text);

  const summary = await getCanvasSummary(canvasId);
  await audit("agent_run", "started", "canvas", canvasId);

  const result = streamText({
    model: xai.responses(MODEL),
    system: `${SYSTEM_PROMPT}\n\n## Current canvas\n\n${summary}\n\nToday is ${new Date().toISOString().slice(0, 10)}.`,
    messages: await convertToModelMessages(messages),
    tools: {
      ...buildTools(canvasId, () => notify(canvasId)),
      web_search: xai.tools.webSearch(),
      x_search: xai.tools.xSearch(),
      code_execution: xai.tools.codeExecution(),
    },
    stopWhen: stepCountIs(16),
    providerOptions: { xai: { reasoningEffort: "low" } },
  });

  result.pipeUIMessageStreamToResponse(res, {
    originalMessages: messages,
    sendReasoning: true,
    sendSources: true,
    onFinish: async ({ messages: final }) => {
      try {
        await saveMessages(
          canvasId,
          final.map((m) => ({ id: m.id, role: m.role, parts: m.parts })),
        );
      } catch (e) {
        console.error("persist failed", e);
      }
      notify(canvasId);
    },
    onError: (error) => {
      console.error("stream error", error);
      return error instanceof Error ? error.message : "Unknown error";
    },
  });
});

const port = Number(process.env.PORT ?? 3001);
initDb()
  .then(() => app.listen(port, () => console.log(`vision server on :${port} (${MODEL})`)))
  .catch((e) => {
    console.error("db init failed", e);
    process.exit(1);
  });
