import { useState } from "react";

const fmt = (n) => Math.round(n).toLocaleString("en-US");
const BLUE = "#7c6cff", ORANGE = "#ff9f5a", GRID = "#232839", MUTED = "#8a90a6", INK = "#e8eaf2";

/** Round axis bounds and ticks. */
function niceScale(min, max, count = 5) {
  if (min === max) max = min + 1;
  const step = Math.pow(10, Math.floor(Math.log10((max - min) / count)));
  const err = (max - min) / count / step;
  const mult = err >= 5 ? 10 : err >= 2 ? 5 : err >= 1 ? 2 : 1;
  const unit = step * mult;
  const lo = Math.floor(min / unit) * unit, hi = Math.ceil(max / unit) * unit;
  const ticks = [];
  for (let v = lo; v <= hi + unit / 2; v += unit) ticks.push(v);
  return { lo, hi, ticks };
}

/** Horizontal bars: items = [{label, value}], sorted by the caller. */
export function HBarChart({ items, color = BLUE }) {
  const row = 30, left = 130, right = 70, w = 560, h = items.length * row + 10;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-label="Bar chart">
      {items.map((it, i) => {
        const bw = ((w - left - right) * it.value) / max;
        const y = i * row + 5;
        return (
          <g key={it.label}>
            <text x={left - 8} y={y + 14} textAnchor="end" fill={MUTED} fontSize="12">{it.label}</text>
            <rect x={left} y={y + 2} width={Math.max(bw, 1)} height={18} rx={5} fill={color} />
            <text x={left + bw + 6} y={y + 15} fill={INK} fontSize="12">{fmt(it.value)}</text>
            <title>{`${it.label}: ${fmt(it.value)} EUR`}</title>
          </g>
        );
      })}
    </svg>
  );
}

/** Vertical bars for small counts (task status). */
export function ColumnChart({ items, color = BLUE }) {
  const w = 560, h = 240, pad = { l: 30, r: 10, t: 24, b: 30 };
  const { hi, ticks } = niceScale(0, Math.max(...items.map((i) => i.value), 1), 4);
  const bw = (w - pad.l - pad.r) / items.length;
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - v / hi);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-label="Column chart">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke={GRID} />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fill={MUTED} fontSize="11">{t}</text>
        </g>
      ))}
      {items.map((it, i) => (
        <g key={it.label}>
          <rect x={pad.l + i * bw + bw * 0.22} y={y(it.value)} width={bw * 0.56} height={Math.max(y(0) - y(it.value), 0)} rx={6} fill={color} />
          <text x={pad.l + i * bw + bw / 2} y={y(it.value) - 6} textAnchor="middle" fill={INK} fontSize="12">{it.value}</text>
          <text x={pad.l + i * bw + bw / 2} y={h - 10} textAnchor="middle" fill={MUTED} fontSize="12">{it.label}</text>
        </g>
      ))}
    </svg>
  );
}

function Tip({ x, y, w, lines }) {
  return (
    <g pointerEvents="none">
      <rect x={x} y={y} width={w} height={lines.length * 15 + 10} rx={8} fill="#181c29" stroke={GRID} />
      {lines.map((l, i) => (
        <text key={i} x={x + 10} y={y + 18 + i * 15} fill={i ? MUTED : INK} fontSize="12" fontWeight={i ? 400 : 600}>{l}</text>
      ))}
    </g>
  );
}

/** Line with markers; points = [{label, value}], values may be negative. */
export function LineChart({ points }) {
  const [hover, setHover] = useState(null);
  const w = 560, h = 300, pad = { l: 56, r: 14, t: 16, b: 30 };
  const vals = points.map((p) => p.value);
  const { lo, hi, ticks } = niceScale(Math.min(0, ...vals), Math.max(0, ...vals));
  const x = (i) => pad.l + ((w - pad.l - pad.r) * i) / Math.max(points.length - 1, 1);
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - (v - lo) / (hi - lo));
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
  const every = Math.ceil(points.length / 6);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-label="Line chart" onMouseLeave={() => setHover(null)}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? MUTED : GRID} />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fill={MUTED} fontSize="11">{fmt(t)}</text>
        </g>
      ))}
      <path d={path} fill="none" stroke={BLUE} strokeWidth="2.5" strokeLinejoin="round" />
      {points.map((p, i) => (
        <g key={p.label} onMouseEnter={() => setHover(i)}>
          <circle cx={x(i)} cy={y(p.value)} r={hover === i ? 7 : 4.5} fill={BLUE} stroke="#0b0d14" strokeWidth="2" />
          <rect x={x(i) - 14} y={pad.t} width={28} height={h - pad.t - pad.b} fill="transparent" />
          {i % every === 0 && (
            <text x={x(i)} y={h - 10} textAnchor="middle" fill={MUTED} fontSize="11">{p.label.slice(2)}</text>
          )}
        </g>
      ))}
      {hover !== null && (
        <Tip x={Math.min(Math.max(x(hover) - 55, 2), w - 112)} y={Math.max(y(points[hover].value) - 52, 2)} w={110}
          lines={[points[hover].label, `${fmt(points[hover].value)} EUR`]} />
      )}
    </svg>
  );
}

/** Cost vs utilisation: points = [{label, x (EUR/month), y (% seats in use), flagged, tip}]. */
export function ScatterChart({ points }) {
  const [hover, setHover] = useState(null);
  const w = 560, h = 320, pad = { l: 46, r: 16, t: 16, b: 40 };
  const xs = niceScale(0, Math.max(...points.map((p) => p.x), 1), 5);
  const x = (v) => pad.l + ((w - pad.l - pad.r) * v) / xs.hi;
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - v / 100);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-label="Scatter chart" onMouseLeave={() => setHover(null)}>
      {[0, 25, 50, 75, 100].map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke={GRID} />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fill={MUTED} fontSize="11">{t}%</text>
        </g>
      ))}
      {xs.ticks.map((t) => (
        <text key={t} x={x(t)} y={h - 22} textAnchor="middle" fill={MUTED} fontSize="11">{fmt(t)}</text>
      ))}
      <text x={(w + pad.l) / 2} y={h - 4} textAnchor="middle" fill={MUTED} fontSize="11">Monthly cost (EUR)</text>
      {points.map((p, i) => (
        <circle key={p.label} cx={x(p.x)} cy={y(p.y)} r={hover === i ? 9 : 6.5} fill={p.flagged ? ORANGE : BLUE}
          fillOpacity=".85" stroke="#0b0d14" strokeWidth="2" onMouseEnter={() => setHover(i)} />
      ))}
      {hover !== null && (
        <Tip x={Math.min(x(points[hover].x) + 10, w - 170)} y={Math.max(y(points[hover].y) - 52, 2)} w={160}
          lines={[points[hover].label, `${fmt(points[hover].x)} EUR/month`, points[hover].tip]} />
      )}
    </svg>
  );
}
