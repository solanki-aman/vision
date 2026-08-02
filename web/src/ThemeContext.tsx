import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { PALETTES, resolveTheme, type ChartTheme, type Mode, type Palette } from "./theme";

interface Settings {
  mode: Mode;
  paletteKey: string;
  custom: string[];
  animate: boolean;
}

const DEFAULTS: Settings = { mode: "light", paletteKey: "vision", custom: [], animate: true };
const STORE = "vision.settings";

interface Ctx extends Settings {
  palette: Palette;
  chart: ChartTheme;
  set: (patch: Partial<Settings>) => void;
}

const ThemeCtx = createContext<Ctx | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(() => {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORE) ?? "{}") };
    } catch {
      return DEFAULTS;
    }
  });

  useEffect(() => {
    localStorage.setItem(STORE, JSON.stringify(settings));
    document.documentElement.dataset.theme = settings.mode;
  }, [settings]);

  const value = useMemo<Ctx>(() => {
    const palette = PALETTES.find((p) => p.key === settings.paletteKey) ?? PALETTES[0];
    return {
      ...settings,
      palette,
      chart: resolveTheme(settings.mode, palette, settings.custom),
      set: (patch) => setSettings((s) => ({ ...s, ...patch })),
    };
  }, [settings]);

  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}

export function useTheme() {
  const v = useContext(ThemeCtx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
