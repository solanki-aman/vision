import { useCallback, useEffect, useState } from "react";
import type { UIMessage } from "ai";
import { Chat } from "./components/Chat";

interface Conversation {
  id: string;
  title: string;
  updated_at: string;
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<UIMessage[] | null>(null);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/conversations");
    setConversations(await res.json());
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openConversation = useCallback(async (id: string) => {
    setActiveId(null);
    const res = await fetch(`/api/conversations/${id}/messages`);
    const rows: { id: string; role: string; parts: unknown }[] = await res.json();
    setInitialMessages(rows as unknown as UIMessage[]);
    setActiveId(id);
  }, []);

  const newConversation = useCallback(async () => {
    const res = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const conv: Conversation = await res.json();
    setInitialMessages([]);
    setActiveId(conv.id);
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!activeId && conversations.length === 0) return;
  }, [activeId, conversations]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">◈ Vision</div>
        <button className="new-chat" onClick={newConversation}>
          + New chat
        </button>
        <nav>
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`conv ${c.id === activeId ? "active" : ""}`}
              onClick={() => openConversation(c.id)}
            >
              {c.title}
            </button>
          ))}
        </nav>
      </aside>
      <main>
        {activeId && initialMessages ? (
          <Chat
            key={activeId}
            conversationId={activeId}
            initialMessages={initialMessages}
          />
        ) : (
          <div className="landing">
            <h1>◈ Vision</h1>
            <p>A charts-first agent. Start a new chat.</p>
            <button className="new-chat big" onClick={newConversation}>
              + New chat
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
