import { useCallback, useEffect, useState } from "react";

/**
 * Demo mode: a client-side switch (default ON for now) that fills the rail panels —
 * Inbox, Sources, Facts, Schedules, Shared, Activity — and a sample brief with plausible
 * mock content, so the shell reads as a finished product before those backends are wired.
 *
 * It is purely presentational: nothing here writes to the server, and turning it off
 * falls back to whatever the real endpoints return (today, mostly empty).
 */
const KEY = "vision-demo-mode";

function read(): boolean {
  const v = localStorage.getItem(KEY);
  return v === null ? true : v === "1"; // default on
}

let listeners: Array<(v: boolean) => void> = [];

export function useDemoMode(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(read);
  useEffect(() => {
    const fn = (v: boolean) => setOn(v);
    listeners.push(fn);
    return () => {
      listeners = listeners.filter((l) => l !== fn);
    };
  }, []);
  const set = useCallback((v: boolean) => {
    localStorage.setItem(KEY, v ? "1" : "0");
    listeners.forEach((l) => l(v));
  }, []);
  return [on, set];
}

// ---- mock content -----------------------------------------------------------------

export interface MockRow {
  title: string;
  meta: string;
  tag?: string;
  tone?: "ok" | "warn" | "info";
}

export const MOCK = {
  brief: [
    {
      id: "m-brief-1",
      headline: "EMEA gross margin fell 240bp on the quarter close",
      detail: "38.1% → 35.7% as UK and Germany cost of delivery stepped up. 4 of 5 regions steady.",
      pinId: null,
      narrowed: null,
      kind: "moved",
    },
  ],
  inbox: [
    {
      id: "m-inbox-1",
      interaction: "question",
      headline: "Warehouse renamed a dimension — is “DE” the same series as “EMEA-DE”?",
      detail: "A guess would silently corrupt the Germany revenue trend. One click resolves it.",
    },
    {
      id: "m-inbox-2",
      interaction: "review",
      headline: "Draft ready: Q4 flight-risk dashboard from the attrition finding",
      detail: "Composed from the headcount signal you opened yesterday. Review before it lands on Home.",
    },
  ],
  sources: [
    { title: "Northwind warehouse", meta: "Connected · watermark 08:40 today", tag: "entitled", tone: "ok" },
    { title: "Web & X search", meta: "Live · public data", tag: "public", tone: "info" },
    { title: "Uploaded documents", meta: "3 files · read as page images", tag: "entitled", tone: "ok" },
    { title: "Planning system", meta: "Not connected", tag: "offline", tone: "warn" },
  ] as MockRow[],
  facts: [
    { title: "Revenue by region · Q3", meta: "warehouse · 5 points · illustrative", tag: "measured", tone: "ok" },
    { title: "Gross margin by segment", meta: "warehouse · 3 points · illustrative", tag: "measured", tone: "ok" },
    { title: "QoQ revenue growth", meta: "computed · =(q3-q2)/q2 · derived", tag: "derived", tone: "info" },
    { title: "NVDA data-center revenue", meta: "web_search · as of 08 Aug · measured", tag: "public", tone: "info" },
  ] as MockRow[],
  schedules: [
    { title: "Revenue by region", meta: "On source update · last ran 08:40", tag: "ok", tone: "ok" },
    { title: "Cash runway", meta: "Daily 07:00 · last ran today", tag: "ok", tone: "ok" },
    { title: "Peer multiples", meta: "Before earnings (T-2) · next Aug 23", tag: "queued", tone: "info" },
    { title: "Opex by function", meta: "Paused — connector error", tag: "paused", tone: "warn" },
  ] as MockRow[],
  shared: [
    { title: "Revenue — shared with you by Ada", meta: "Live · resolves to your regions (US)", tag: "viewer", tone: "info" },
    { title: "Board pack — shared by finance-leadership", meta: "Snapshot · public figures", tag: "viewer", tone: "ok" },
  ] as MockRow[],
  activity: [
    { title: "Ambient refresh · Revenue by region", meta: "2 min ago · below gate, no model call", tone: "ok" },
    { title: "You pinned “Revenue by region” to Revenue", meta: "14 min ago", tone: "info" },
    { title: "Finding surfaced · EMEA margin −240bp", meta: "06:40 · brief", tone: "warn" },
    { title: "Blake opened a section you shared", meta: "Yesterday · resolved to US only", tone: "info" },
  ] as MockRow[],
};

export const RAIL_MOCK_COUNT: Record<string, number> = {
  inbox: MOCK.inbox.length,
};
