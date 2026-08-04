import { useState } from "react";
import { useTheme } from "./ThemeContext";
import { PALETTES } from "./theme";

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function Settings({ onClose }: { onClose: () => void }) {
  const { mode, paletteKey, custom, animate, set } = useTheme();
  const [draft, setDraft] = useState(custom.join(", "));

  const applyCustom = () => {
    const hexes = draft
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((s) => HEX.test(s));
    set({ custom: hexes });
  };

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-panel" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Settings</h2>
          <button onClick={onClose} aria-label="Close">✕</button>
        </header>

        <section>
          <p className="set-label">Appearance</p>
          <div className="seg">
            {(["light", "dark"] as const).map((m) => (
              <button key={m} className={mode === m ? "on" : ""} onClick={() => set({ mode: m })}>
                {m}
              </button>
            ))}
          </div>
        </section>

        <section>
          <p className="set-label">Chart palette</p>
          <div className="pal-list">
            {PALETTES.map((p) => (
              <button
                key={p.key}
                className={`pal ${paletteKey === p.key && custom.length === 0 ? "on" : ""}`}
                onClick={() => set({ paletteKey: p.key, custom: [] })}
              >
                <span className="pal-swatches">
                  {p.series[mode].map((c) => (
                    <i key={c} style={{ background: c }} />
                  ))}
                </span>
                <span className="pal-meta">
                  <strong>{p.name}</strong>
                  <em>{p.note}</em>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section>
          <p className="set-label">Custom palette</p>
          <p className="set-hint">
            Comma-separated hex colours, in the order series should use them. Overrides the palette above.
          </p>
          <textarea
            rows={2}
            value={draft}
            placeholder="#2a78d6, #eb6834, #1baf7a"
            onChange={(e) => setDraft(e.target.value)}
          />
          {custom.length > 0 && (
            <span className="pal-swatches live">
              {custom.map((c, i) => (
                <i key={`${c}-${i}`} style={{ background: c }} />
              ))}
            </span>
          )}
          <div className="set-row">
            <button className="btn" onClick={applyCustom}>
              Apply
            </button>
            <button
              className="btn ghost"
              onClick={() => {
                setDraft("");
                set({ custom: [] });
              }}
            >
              Clear
            </button>
          </div>
        </section>

        <section>
          <label className="set-check">
            <input type="checkbox" checked={animate} onChange={(e) => set({ animate: e.target.checked })} />
            <span>Animate charts on render</span>
          </label>
        </section>
      </div>
    </div>
  );
}
