import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WidgetBody } from "./widgets/WidgetBody";
import { Sparkline } from "./Sparkline";
import { useTheme } from "./ThemeContext";
import { useDemoMode, MOCK } from "./demo";
import type { Widget } from "./types";
import {
  HomeIcon, CanvasIcon, DocumentIcon, SourceIcon, FactsIcon,
  ScheduleIcon, SharedIcon, ActivityIcon, SettingsIcon, SunIcon, MoonIcon,
  RefreshIcon, ArrowUpRight, CheckIcon, CloseIcon, SendIcon, PlusIcon, BellIcon,
  SECTION_ICONS, FactsIcon as FallbackIcon,
} from "./Icons";

interface Section { id: string; key: string; title: string; ord: number }
interface Pin {
  id: string; sectionId: string; widgetId: string; canvasId: string;
  title: string; kind: Widget["kind"]; spec: any; provenance: any;
  w: number; h: number; status: string; statusReason: string | null;
  cadence: string | null; changed: boolean;
}
interface Finding {
  id: string; headline: string; detail: string | null; pinId: string | null;
  narrowed: any; kind: string; interaction?: string; allowed?: string[];
}
interface HomeData {
  greeting: string; sections: Section[]; pins: Pin[];
  brief: Finding[]; inbox: Finding[];
  prefs: { briefHour: number; timezone: string };
  shared: { id: string; key: string; title: string; owner: string }[];
  briefBudget: number;
}
interface Metric {
  key: string; label: string; format: "currency" | "percent"; value: number;
  delta: number; deltaKind: "pct" | "bp"; spark: number[]; favorable: "up" | "down";
}
interface Pulse { metrics: Metric[]; asOf: string | null; narrowed: boolean; withheld: Record<string, number> | null }
interface CanvasRow { id: string; title: string; widget_count: string; updated_at: string }

// Inbox is no longer a rail tab — it lives inside Home as "Needs your attention".
const RAIL = [
  { key: "home", label: "Home", Icon: HomeIcon },
  { key: "boards", label: "Boards", Icon: CanvasIcon },
  { key: "documents", label: "Documents", Icon: DocumentIcon },
  { key: "sources", label: "Sources", Icon: SourceIcon },
  { key: "facts", label: "Facts", Icon: FactsIcon },
  { key: "schedules", label: "Schedules", Icon: ScheduleIcon },
  { key: "shared", label: "Shared", Icon: SharedIcon },
  { key: "activity", label: "Activity", Icon: ActivityIcon },
] as const;

const SEEDS = [
  "Revenue by region this quarter",
  "Gross margin trend by segment",
  "Where is cash actually going?",
];

const MOCK_PANELS: Record<string, { icon: any; sub: string; rows: keyof typeof MOCK }> = {
  documents: { icon: DocumentIcon, sub: "Files you've uploaded, read as page images", rows: "sources" },
  sources: { icon: SourceIcon, sub: "Where answers come from, and what you're entitled to", rows: "sources" },
  facts: { icon: FactsIcon, sub: "Every number and where it came from", rows: "facts" },
  schedules: { icon: ScheduleIcon, sub: "Refresh runs, last success, and failures", rows: "schedules" },
  shared: { icon: SharedIcon, sub: "Sections shared with you — resolved to your access", rows: "shared" },
  activity: { icon: ActivityIcon, sub: "Everything the agent did, including what it skipped", rows: "activity" },
};

function greetingWord(hour: number): string {
  if (hour < 5) return "Good evening";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}
function pinAsWidget(pin: Pin): Widget {
  return { id: pin.widgetId, kind: pin.kind, title: pin.title, spec: pin.spec ?? {},
    provenance: pin.provenance ?? null, bindings: null, x: 0, y: 0, w: pin.w, h: pin.h };
}
function fmtCurrency(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}
function fmtValue(m: Metric): string {
  return m.format === "percent" ? `${m.value.toFixed(1)}%` : fmtCurrency(m.value);
}
function fmtDelta(m: Metric): string {
  const s = m.delta >= 0 ? "+" : "";
  return m.deltaKind === "bp" ? `${s}${Math.round(m.delta)} bp` : `${s}${m.delta.toFixed(1)}%`;
}
function deltaGood(m: Metric): boolean {
  return m.favorable === "up" ? m.delta >= 0 : m.delta < 0;
}

export function Home({
  onOpenCanvas, onCreate,
}: {
  onOpenCanvas: (canvasId: string) => void;
  onCreate: (prompt?: string) => void;
}) {
  const { mode, set } = useTheme();
  const [demo, setDemo] = useDemoMode();
  const [data, setData] = useState<HomeData | null>(null);
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const [canvases, setCanvases] = useState<CanvasRow[]>([]);
  const [active, setActive] = useState("home");
  const [prompt, setPrompt] = useState("");
  const [hiddenMocks, setHiddenMocks] = useState<Set<string>>(new Set());
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/home");
    if (res.ok) setData(await res.json());
  }, []);

  useEffect(() => {
    load();
    fetch("/api/home/seen", { method: "POST" }).catch(() => {});
    fetch("/api/home/pulse").then((r) => r.json()).then(setPulse).catch(() => {});
    fetch("/api/canvases").then((r) => r.json()).then(setCanvases).catch(() => {});
  }, [load]);

  const now = new Date();
  const hour = now.getHours();
  const dateLine = now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  const pinsBySection = useMemo(() => {
    const m: Record<string, Pin[]> = {};
    for (const p of data?.pins ?? []) (m[p.sectionId] ??= []).push(p);
    return m;
  }, [data]);

  const decisions = useMemo(() => {
    const base = [...(demo ? MOCK.inbox : []), ...(data?.inbox ?? [])] as Finding[];
    return base.filter((f) => !hiddenMocks.has(f.id));
  }, [demo, data, hiddenMocks]);
  const signals = useMemo(() => {
    const base = [...(demo ? MOCK.brief : []), ...(data?.brief ?? [])] as Finding[];
    return base.filter((f) => !hiddenMocks.has(f.id));
  }, [demo, data, hiddenMocks]);
  const attentionCount = decisions.length + signals.length;

  const generate = (text: string) => { if (text.trim()) onCreate(text.trim()); };
  const dismiss = async (id: string) => {
    if (id.startsWith("m-")) { setHiddenMocks((s) => new Set(s).add(id)); return; }
    await fetch(`/api/home/findings/${id}/dismiss`, { method: "POST" });
    load();
  };
  const openFinding = (f: Finding) => {
    if (f.pinId) {
      const pin = data?.pins.find((p) => p.id === f.pinId);
      if (pin) onOpenCanvas(pin.canvasId);
    }
  };
  const refreshPin = async (pin: Pin) => { await fetch(`/api/home/pins/${pin.id}/refresh`, { method: "POST" }); load(); };
  const unpin = async (pin: Pin) => { await fetch(`/api/home/pins/${pin.id}`, { method: "DELETE" }); load(); };
  const go = (key: string) => { setActive(key); scroller.current?.scrollTo({ top: 0, behavior: "auto" }); };

  const activeSections = (data?.sections ?? []).filter((s) => s.key !== "brief");

  return (
    <div className="home">
      <div className="home-aurora" aria-hidden />

      <nav className="hnav" aria-label="Places">
        <div className="hnav-mark" title="Vision">
          <span className="hnav-diamond" aria-hidden>◈</span>
          <span className="hnav-wordmark">Vision</span>
        </div>
        <div className="hnav-items">
          {RAIL.map(({ key, label, Icon }) => (
            <button key={key} className={`hnav-btn ${active === key ? "on" : ""}`}
              aria-label={label} aria-current={active === key} onClick={() => go(key)}>
              <span className="hnav-glyph"><Icon /></span>
              <span className="hnav-label">{label}</span>
            </button>
          ))}
        </div>
        <button className={`hnav-btn ${active === "settings" ? "on" : ""}`} aria-label="Settings" onClick={() => go("settings")}>
          <span className="hnav-glyph"><SettingsIcon /></span><span className="hnav-label">Settings</span>
        </button>
        <button className="hnav-btn hnav-theme" aria-label={mode === "dark" ? "Light mode" : "Dark mode"}
          onClick={() => set({ mode: mode === "dark" ? "light" : "dark" })}>
          <span className="hnav-glyph">{mode === "dark" ? <SunIcon /> : <MoonIcon />}</span>
          <span className="hnav-label">{mode === "dark" ? "Light" : "Dark"}</span>
        </button>
      </nav>

      <div className="home-scroll" ref={scroller}>
        <div className="home-inner">

          {active === "home" && (
            <>
              <header className="home-head">
                <div>
                  <p className="home-date">{dateLine}</p>
                  <h1 className="home-greet">{greetingWord(hour)}, <span className="home-name">{data?.greeting ?? "there"}</span></h1>
                </div>
              </header>

              <div className="gen">
                <form className="gen-box" onSubmit={(e) => { e.preventDefault(); generate(prompt); setPrompt(""); }}>
                  <textarea rows={1} className="gen-input" placeholder="Ask anything…"
                    value={prompt} onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); generate(prompt); setPrompt(""); } }} />
                  <button type="submit" className="gen-send" disabled={!prompt.trim()} aria-label="Send"><SendIcon size={17} /></button>
                </form>
                <div className="gen-seeds">
                  {SEEDS.map((s) => <button key={s} className="gen-chip" onClick={() => generate(s)}>{s}</button>)}
                </div>
              </div>

              {/* ── Financial pulse: the CFO glance ── */}
              {pulse && pulse.metrics.length > 0 && (
                <section className="pulse">
                  <div className="pulse-head">
                    <span className="pulse-eyebrow">Financial pulse</span>
                    <span className="pulse-asof">
                      {pulse.narrowed && pulse.withheld
                        ? `Your entitled regions · ${Object.entries(pulse.withheld).map(([d, n]) => `${n} ${d}s withheld`).join(" · ")}`
                        : pulse.asOf ? `as of ${new Date(pulse.asOf).toLocaleDateString()}` : "trailing quarter"}
                    </span>
                  </div>
                  <div className="pulse-grid">
                    {pulse.metrics.map((m) => {
                      const good = deltaGood(m);
                      return (
                        <article key={m.key} className="metric">
                          <span className="metric-label">{m.label}</span>
                          <span className="metric-value">{fmtValue(m)}</span>
                          <span className={`metric-delta ${good ? "up" : "down"}`}>
                            <span className="metric-arrow" aria-hidden>{m.delta >= 0 ? "▲" : "▼"}</span>
                            {fmtDelta(m)}
                            <span className="metric-qoq">QoQ</span>
                          </span>
                          <span className={`metric-spark ${good ? "up" : "down"}`}>
                            <Sparkline data={m.spark} />
                          </span>
                        </article>
                      );
                    })}
                  </div>
                </section>
              )}

              {/* ── Needs your attention: inbox folded into Home ── */}
              <section className="attn">
                <div className="sec-head">
                  <span className="sec-icon attn-icon"><BellIcon size={16} /></span>
                  <h2>Needs your attention</h2>
                  <span className="sec-meta">{attentionCount === 0 ? "all clear" : `${attentionCount} item${attentionCount > 1 ? "s" : ""}`}</span>
                </div>
                {attentionCount === 0 ? (
                  <div className="brief-quiet"><CheckIcon size={16} /><span>All clear. Nothing moved past its threshold and nothing is waiting on a decision.</span></div>
                ) : (
                  <div className="attn-list">
                    {decisions.map((f) => (
                      <article key={f.id} className="attn-card decision">
                        <span className={`attn-tag ${f.interaction}`}>{f.interaction === "review" ? "Review" : "Question"}</span>
                        <div className="attn-body">
                          <p className="attn-head">{f.headline}</p>
                          {f.detail && <p className="attn-detail">{f.detail}</p>}
                        </div>
                        <div className="attn-acts">
                          <button className="mini primary">{f.interaction === "review" ? "Review" : "Answer"}</button>
                          <button className="mini" onClick={() => dismiss(f.id)}>Dismiss</button>
                        </div>
                      </article>
                    ))}
                    {signals.map((f) => (
                      <article key={f.id} className="attn-card signal">
                        <span className="attn-tag signal">Signal</span>
                        <div className="attn-body">
                          <p className="attn-head">{f.headline}</p>
                          {f.detail && <p className="attn-detail">{f.detail}</p>}
                          {f.narrowed && (
                            <span className="finding-narrowed">
                              {Object.entries(f.narrowed).map(([d, n]) => `${n} ${d}s withheld`).join(" · ")}
                            </span>
                          )}
                        </div>
                        <div className="attn-acts">
                          <button className="icon-mini" onClick={() => openFinding(f)} title="Open"><ArrowUpRight size={15} /></button>
                          <button className="icon-mini" onClick={() => dismiss(f.id)} title="Dismiss"><CloseIcon size={15} /></button>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>

              {/* ── Pinned sections: only the ones with tiles get a full band ── */}
              {activeSections.filter((s) => (pinsBySection[s.id] ?? []).length > 0).map((section) => {
                const pins = pinsBySection[section.id] ?? [];
                const Icon = SECTION_ICONS[section.key] ?? FallbackIcon;
                return (
                  <section key={section.id} id={`sec-${section.id}`} className="pin-section">
                    <div className="sec-head">
                      <span className="sec-icon" data-key={section.key}><Icon size={16} /></span>
                      <h2>{section.title}</h2>
                      <span className="sec-meta">{pins.length} {pins.length === 1 ? "tile" : "tiles"}</span>
                    </div>
                    <div className="tiles">
                      {pins.map((pin) => (
                        <article key={pin.id}
                          className={`tile ${pin.changed ? "tile-changed" : ""} ${pin.status !== "ok" ? "tile-" + pin.status : ""}`}>
                          <header className="tile-head">
                            <h3>{pin.title}</h3>
                            <div className="tile-tools">
                              {pin.changed && <span className="tile-dot" title="Updated since you last looked" />}
                              <button className="tile-btn" title="Refresh" onClick={() => refreshPin(pin)}><RefreshIcon size={14} /></button>
                              <button className="tile-btn" title="Open board" onClick={() => onOpenCanvas(pin.canvasId)}><ArrowUpRight size={14} /></button>
                              <button className="tile-btn tile-x" title="Unpin" onClick={() => unpin(pin)}><CloseIcon size={14} /></button>
                            </div>
                          </header>
                          <div className="tile-body">
                            {pin.status === "unavailable" ? (
                              <div className="tile-unavailable">{pin.statusReason ?? "Unavailable"}</div>
                            ) : pin.spec ? (
                              <WidgetBody widget={pinAsWidget(pin)} entities={{}} />
                            ) : (
                              <div className="tile-unavailable">No cached data yet — refresh to load.</div>
                            )}
                          </div>
                          {pin.cadence && <footer className="tile-foot"><span className="tile-cadence">{pin.cadence.replace(/_/g, " ")}</span></footer>}
                        </article>
                      ))}
                    </div>
                  </section>
                );
              })}

              {/* ── Empty sections collapse into one compact strip of slots ── */}
              {activeSections.some((s) => (pinsBySection[s.id] ?? []).length === 0) && (
                <section className="empty-sections">
                  <div className="sec-head">
                    <h2 className="empty-sections-title">Other sections</h2>
                    <span className="sec-meta">pin a tile from any board to fill these</span>
                  </div>
                  <div className="slot-grid">
                    {activeSections.filter((s) => (pinsBySection[s.id] ?? []).length === 0).map((s) => {
                      const Icon = SECTION_ICONS[s.key] ?? FallbackIcon;
                      return (
                        <div key={s.id} id={`sec-${s.id}`} className="slot" data-key={s.key}>
                          <span className="slot-glyph"><Icon size={17} /></span>
                          <span className="slot-title">{s.title}</span>
                          <span className="slot-hint">empty</span>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}
              <div className="home-footspace" />
            </>
          )}

          {active === "boards" && (
            <Panel icon={CanvasIcon} title="Boards" sub="Every answer you've built — open one, or start a new one"
              action={<button className="panel-new" onClick={() => onCreate()}><PlusIcon size={15} /> New board</button>}>
              {canvases.length === 0 ? (
                <p className="sec-empty">No boards yet. Ask a question on Home to build your first.</p>
              ) : (
                <div className="board-grid">
                  {canvases.map((c) => (
                    <button key={c.id} className="board-card" onClick={() => onOpenCanvas(c.id)}>
                      <span className="board-mark" aria-hidden>◈</span>
                      <span className="board-title">{c.title}</span>
                      <span className="board-meta">{c.widget_count} tiles · {new Date(c.updated_at).toLocaleDateString()}</span>
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          )}

          {MOCK_PANELS[active] && (
            <Panel icon={MOCK_PANELS[active].icon} title={RAIL.find((r) => r.key === active)?.label ?? active}
              sub={MOCK_PANELS[active].sub}>
              {!demo ? (
                <p className="sec-empty">Nothing here yet. Turn on demo mode in Settings to preview sample content.</p>
              ) : (
                <div className="rows">
                  {(MOCK[MOCK_PANELS[active].rows] as any[]).map((r, i) => (
                    <div key={i} className="row">
                      <span className={`row-dot ${r.tone ?? "info"}`} aria-hidden />
                      <div className="row-body"><span className="row-title">{r.title}</span><span className="row-meta">{r.meta}</span></div>
                      {r.tag && <span className={`row-tag ${r.tone ?? "info"}`}>{r.tag}</span>}
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          )}

          {active === "settings" && (
            <Panel icon={SettingsIcon} title="Settings" sub="Preferences for this workspace">
              <div className="setting-row">
                <div className="setting-copy">
                  <span className="setting-title">Demo mode</span>
                  <span className="setting-desc">Fill Sources, Facts, Schedules, Shared, Activity and the attention feed with sample content while those backends are being wired.</span>
                </div>
                <button className={`toggle ${demo ? "on" : ""}`} role="switch" aria-checked={demo} onClick={() => setDemo(!demo)}>
                  <span className="toggle-knob" />
                </button>
              </div>
              <div className="setting-row">
                <div className="setting-copy">
                  <span className="setting-title">Appearance</span>
                  <span className="setting-desc">Switch between light and dark.</span>
                </div>
                <button className="mini" onClick={() => set({ mode: mode === "dark" ? "light" : "dark" })}>{mode === "dark" ? "Dark" : "Light"}</button>
              </div>
            </Panel>
          )}

        </div>
      </div>
    </div>
  );
}

function Panel({ icon: Icon, title, sub, action, children }: {
  icon: (p: any) => JSX.Element; title: string; sub: string;
  action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <span className="panel-icon"><Icon size={20} /></span>
        <div className="panel-heading"><h1>{title}</h1><p>{sub}</p></div>
        {action}
      </header>
      {children}
    </section>
  );
}
