import express from "express";
import cors from "cors";
import { createXai } from "@ai-sdk/xai";
import {
  streamText,
  convertToModelMessages,
  stepCountIs,
  type UIMessage,
} from "ai";
import {
  initDb,
  listConversations,
  createConversation,
  getMessages,
  saveMessages,
  setTitleIfNew,
} from "./db.js";
import { tools, SYSTEM_PROMPT } from "./chat.js";

const xai = createXai({ apiKey: process.env.XAI_API_KEY ?? "" });
const MODEL = process.env.XAI_MODEL ?? "grok-4.5";

const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (_req, res) => res.json({ ok: true }));

app.get("/api/conversations", async (_req, res) => {
  res.json(await listConversations());
});

app.post("/api/conversations", async (req, res) => {
  res.json(await createConversation(req.body?.title));
});

app.get("/api/conversations/:id/messages", async (req, res) => {
  res.json(await getMessages(req.params.id));
});

app.post("/api/chat", async (req, res) => {
  const { messages, conversationId } = req.body as {
    messages: UIMessage[];
    conversationId: string;
  };

  const firstUserText = messages
    .find((m) => m.role === "user")
    ?.parts?.find((p: any) => p.type === "text") as { text?: string } | undefined;
  if (firstUserText?.text) {
    await setTitleIfNew(conversationId, firstUserText.text);
  }

  const result = streamText({
    model: xai(MODEL),
    system: SYSTEM_PROMPT,
    messages: await convertToModelMessages(messages),
    tools,
    stopWhen: stepCountIs(6),
  });

  result.pipeUIMessageStreamToResponse(res, {
    originalMessages: messages,
    onFinish: async ({ messages: finalMessages }) => {
      try {
        await saveMessages(
          conversationId,
          finalMessages.map((m) => ({ id: m.id, role: m.role, parts: m.parts })),
        );
      } catch (e) {
        console.error("failed to persist messages", e);
      }
    },
    onError: (error) => {
      console.error("chat stream error", error);
      return error instanceof Error ? error.message : "Unknown error";
    },
  });
});

const port = Number(process.env.PORT ?? 3001);
initDb()
  .then(() => {
    app.listen(port, () => console.log(`vision server on :${port}`));
  })
  .catch((e) => {
    console.error("db init failed", e);
    process.exit(1);
  });
