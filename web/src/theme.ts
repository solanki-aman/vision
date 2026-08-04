export type Mode = "light" | "dark";

export interface Palette {
  key: string;
  name: string;
  note: string;
  series: { light: string[]; dark: string[] };
  sequential: { light: string[]; dark: string[] };
  gradient: boolean;
}

/** Validated CVD-safe default. Slot order is the safety mechanism — never cycled. */
const VISION: Palette = {
  key: "vision",
  name: "Vision",
  note: "Colour-blind safe, contrast-checked in both modes",
  series: {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  },
  sequential: {
    light: ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    dark: ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
  },
  gradient: false,
};

/** Inspired by Workday Canvas' fruit-named hues; gradients on by default. */
const WORKDAY: Palette = {
  key: "workday",
  name: "Workday",
  note: "Canvas-inspired hues with gradient fills",
  series: {
    light: ["#0875e1", "#ff8f0c", "#087a8c", "#cc4b8d", "#43a047", "#442b88", "#de2e21", "#8f7000"],
    dark: ["#3f9bff", "#ffa53d", "#22a3b8", "#e07bb0", "#5cb860", "#9b8ce0", "#f0655a", "#c9a227"],
  },
  sequential: {
    light: ["#d7e9fc", "#a9cef7", "#7ab2f1", "#0875e1", "#0a5cb0", "#0a4482", "#082f5c"],
    dark: ["#d7e9fc", "#a9cef7", "#7ab2f1", "#3f9bff", "#0875e1", "#0a5cb0", "#0a4482"],
  },
  gradient: true,
};

const EMBER: Palette = {
  key: "ember",
  name: "Ember",
  note: "Warm editorial palette for narrative decks",
  series: {
    light: ["#b8442a", "#1f6f78", "#c98a00", "#4c3a80", "#2f7a3f", "#a33d6a", "#31627d", "#7a5c1f"],
    dark: ["#e2694a", "#3fa0aa", "#e0a832", "#8f80d6", "#4fa864", "#d76d99", "#5b95b8", "#c1994a"],
  },
  sequential: {
    light: ["#fbe3da", "#f5c0ac", "#ea9779", "#d96f4c", "#b8442a", "#8e3320", "#652316"],
    dark: ["#fbe3da", "#f5c0ac", "#ea9779", "#e2694a", "#c04f31", "#94391f", "#682616"],
  },
  gradient: true,
};

export const PALETTES: Palette[] = [VISION, WORKDAY, EMBER];

export interface Ink {
  surface: string;
  primary: string;
  secondary: string;
  muted: string;
  grid: string;
  axis: string;
  border: string;
}

export const CHROME: Record<Mode, Ink> = {
  light: {
    surface: "#ffffff",
    primary: "#16150f",
    secondary: "#55534b",
    muted: "#84827a",
    grid: "#ebe9e3",
    axis: "#d5d2ca",
    border: "rgba(20,19,15,0.10)",
  },
  dark: {
    surface: "#141413",
    primary: "#ffffff",
    secondary: "#c3c2b7",
    muted: "#898781",
    grid: "#2c2c2a",
    axis: "#3a3a37",
    border: "rgba(255,255,255,0.10)",
  },
};

export const STATUS = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
};

export interface ChartTheme {
  mode: Mode;
  series: string[];
  sequential: string[];
  gradient: boolean;
  ink: Ink;
}

export function resolveTheme(mode: Mode, palette: Palette, customSeries?: string[]): ChartTheme {
  const series = customSeries?.length ? customSeries : palette.series[mode];
  return {
    mode,
    series,
    sequential: palette.sequential[mode],
    gradient: palette.gradient && !customSeries?.length,
    ink: CHROME[mode],
  };
}
