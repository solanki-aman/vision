import { useEffect, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import type { UIMessage } from "ai";
import { ChartCard } from "./ChartCard";
import type { ChartSpec } from "../chartAdapter";

function MessageParts({ message }: { message: UIMessage }) {
  return (
    <>
      {message.parts.map((part, i) => {
        if (part.type === "text") {
          return part.text.trim() ? (
            <p key={i} className="msg-text">
              {part.text}
            </p>
          ) : null;
        }
        if (part.type === "tool-render_chart") {
          const p = part as { state: string; input?: unknown };
          if (
            (p.state === "input-available" || p.state === "output-available") &&
            p.input
          ) {
            return <ChartCard key={i} spec={p.input as ChartSpec} />;
          }
          return (
            <p key={i} className="msg-text pending">
              Drawing chart…
            </p>
          );
        }
        return null;
      })}
    </>
  );
}

export function Chat({
  conversationId,
  initialMessages,
}: {
  conversationId: string;
  initialMessages: UIMessage[];
}) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const { messages, sendMessage, status, error } = useChat({
    id: conversationId,
    messages: initialMessages,
    transport: new DefaultChatTransport({
      api: "/api/chat",
      body: { conversationId },
    }),
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const busy = status === "submitted" || status === "streaming";

  return (
    <div className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <h2>Vision answers in charts.</h2>
            <p>Ask anything — data, comparisons, trends, breakdowns.</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`message ${m.role}`}>
            <MessageParts message={m} />
          </div>
        ))}
        {error && <p className="msg-error">Error: {error.message}</p>}
        <div ref={bottomRef} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (!input.trim() || busy) return;
          sendMessage({ text: input });
          setInput("");
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask for anything — the answer will be a chart"
          autoFocus
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
