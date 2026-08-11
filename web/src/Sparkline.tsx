/**
 * A tiny inline sparkline — a normalized SVG path plus a soft area fill and an endpoint
 * dot. No chart library: at 12 points this is cheaper and sharper as raw SVG, and it
 * inherits `currentColor` so a metric card colors it by favorability.
 */
export function Sparkline({
  data,
  width = 132,
  height = 36,
  className,
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}) {
  const pts = data.filter((n) => typeof n === "number" && isFinite(n));
  if (pts.length < 2) return <svg width={width} height={height} className={className} aria-hidden />;

  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const range = max - min || 1;
  const pad = 3;
  const w = width - pad * 2;
  const h = height - pad * 2;
  const x = (i: number) => pad + (i / (pts.length - 1)) * w;
  const y = (v: number) => pad + h - ((v - min) / range) * h;

  const line = pts.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${(height - pad).toFixed(1)} L${x(0).toFixed(1)},${(height - pad).toFixed(1)} Z`;
  const gid = `sg-${Math.random().toString(36).slice(2, 8)}`;

  return (
    <svg width={width} height={height} className={className} viewBox={`0 0 ${width} ${height}`}
      fill="none" aria-hidden preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.4" fill="currentColor" />
    </svg>
  );
}
