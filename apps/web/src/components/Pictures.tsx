"use client";
// Common Test 1B / Part 2 pictures (owner request 5). Drawn as simple SVG pictograms from checked, structured data:
// only fixed element kinds, poses, positions and short plain-text labels exist - nothing from the material is injected
// as markup, and what an answer depends on (pose, position, presence, a clock time) is drawn explicitly.
import type { ReactElement } from "react";
import type { Json } from "@/lib/api";

const W = 160, H = 120;
const INK = "#e5edf7", MUTE = "#8aa0b8", ACC = "#5eead4";

function Person({ x, y, s, pose, flip, seated }: { x: number; y: number; s: number; pose: string; flip: boolean; seated: boolean }) {
  const d = flip ? -1 : 1;
  const head = <circle cx={x} cy={y - 22 * s} r={6 * s} fill="none" stroke={INK} strokeWidth={2} />;
  if (pose === "sleep") {
    return (<g>
      <circle cx={x - 16 * s} cy={y + 6 * s} r={6 * s} fill="none" stroke={INK} strokeWidth={2} />
      <line x1={x - 10 * s} y1={y + 6 * s} x2={x + 18 * s} y2={y + 6 * s} stroke={INK} strokeWidth={3} />
      <text x={x - 8 * s} y={y - 8 * s} fontSize={11} fill={MUTE}>z z</text>
    </g>);
  }
  const sit = seated || pose === "sit";
  const torso = <line x1={x} y1={y - 16 * s} x2={x} y2={y + 2 * s} stroke={INK} strokeWidth={3} />;
  const legs = sit
    ? <polyline points={`${x},${y + 2 * s} ${x + 12 * d * s},${y + 2 * s} ${x + 12 * d * s},${y + 16 * s}`} fill="none" stroke={INK} strokeWidth={3} />
    : pose === "walk" || pose === "run"
      ? <polyline points={`${x - 8 * s},${y + 20 * s} ${x},${y + 2 * s} ${x + 9 * s},${y + 20 * s}`} fill="none" stroke={INK} strokeWidth={3} />
      : <polyline points={`${x - 5 * s},${y + 20 * s} ${x},${y + 2 * s} ${x + 5 * s},${y + 20 * s}`} fill="none" stroke={INK} strokeWidth={3} />;
  let arms: ReactElement;
  if (pose === "raise_hand") arms = <polyline points={`${x - 8 * s},${y - 4 * s} ${x},${y - 12 * s} ${x + 8 * d * s},${y - 30 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  else if (pose === "read") // an open book held in front: reading is visible, not only an arm angle
    arms = <g><polyline points={`${x},${y - 12 * s} ${x + 12 * d * s},${y - 6 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />
      <path d={`M${x + 10 * d * s},${y - 12 * s} l${6 * d * s},${-3 * s} l${6 * d * s},${3 * s} v${8 * s} l${-6 * d * s},${-3 * s} l${-6 * d * s},${3 * s} z`} fill={ACC} stroke={INK} strokeWidth={1} data-testid="held-book" /></g>;
  else if (pose === "hold") arms = <polyline points={`${x},${y - 12 * s} ${x + 12 * d * s},${y - 6 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  else if (pose === "call") // a phone at the ear
    arms = <g><polyline points={`${x},${y - 12 * s} ${x + 6 * d * s},${y - 18 * s} ${x + 4 * d * s},${y - 22 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />
      <rect x={x + (d > 0 ? 3 : -8) * s} y={y - 29 * s} width={5 * s} height={10 * s} rx={1.5} fill={ACC} stroke={INK} strokeWidth={1} data-testid="held-phone" /></g>;
  else arms = <polyline points={`${x - 8 * s},${y - 2 * s} ${x},${y - 12 * s} ${x + 8 * s},${y - 2 * s}`} fill="none" stroke={INK} strokeWidth={2.5} />;
  return <g>{head}{torso}{arms}{legs}</g>;
}

function Item({ it, seated }: { it: Json; seated: boolean }) {
  const x = (it.x / 100) * W, y = (it.y / 100) * H, s = (it.size ?? 100) / 100, flip = !!it.flip;
  switch (it.kind) {
    case "person": return <Person x={x} y={y} s={s} pose={it.pose || "stand"} flip={flip} seated={seated} />;
    case "chair": return <g stroke={MUTE} strokeWidth={2.5} fill="none"><line x1={x - 12 * s} y1={y} x2={x + 12 * s} y2={y} /><line x1={x - 10 * s} y1={y} x2={x - 10 * s} y2={y + 12 * s} /><line x1={x + 10 * s} y1={y} x2={x + 10 * s} y2={y + 12 * s} /></g>;
    case "table": return <g stroke={MUTE} strokeWidth={2.5}><line x1={x - 20 * s} y1={y} x2={x + 20 * s} y2={y} /><line x1={x - 16 * s} y1={y} x2={x - 16 * s} y2={y + 16 * s} /><line x1={x + 16 * s} y1={y} x2={x + 16 * s} y2={y + 16 * s} /></g>;
    case "sofa": return <rect x={x - 22 * s} y={y - 8 * s} width={44 * s} height={16 * s} rx={5} fill="none" stroke={MUTE} strokeWidth={2.5} />;
    case "bed": return <rect x={x - 26 * s} y={y - 6 * s} width={52 * s} height={12 * s} rx={3} fill="none" stroke={MUTE} strokeWidth={2.5} />;
    case "shelf": return <g stroke={MUTE} strokeWidth={2.5} fill="none"><rect x={x - 14 * s} y={y - 20 * s} width={28 * s} height={40 * s} /><line x1={x - 14 * s} y1={y} x2={x + 14 * s} y2={y} /></g>;
    case "window": return <g stroke={MUTE} strokeWidth={2} fill="none"><rect x={x - 14 * s} y={y - 12 * s} width={28 * s} height={24 * s} /><line x1={x} y1={y - 12 * s} x2={x} y2={y + 12 * s} /><line x1={x - 14 * s} y1={y} x2={x + 14 * s} y2={y} /></g>;
    case "door": return <g stroke={MUTE} strokeWidth={2} fill="none"><rect x={x - 10 * s} y={y - 22 * s} width={20 * s} height={44 * s} /><circle cx={x + 6 * s} cy={y} r={1.8} fill={MUTE} /></g>;
    case "clock": return <g><circle cx={x} cy={y} r={20 * s} fill="none" stroke={ACC} strokeWidth={2.5} /><text x={x} y={y + 5} fontSize={15} fontWeight={700} textAnchor="middle" fill={INK}>{String(it.label || "")}</text></g>;
    case "sign": return <g><rect x={x - 28 * s} y={y - 8 * s} width={56 * s} height={16 * s} rx={3} fill="none" stroke={MUTE} strokeWidth={1.5} /><text x={x} y={y + 4} fontSize={11} textAnchor="middle" fill={INK}>{String(it.label || "")}</text></g>;
    case "box": return <rect x={x - 9 * s} y={y - 9 * s} width={18 * s} height={18 * s} fill="none" stroke={INK} strokeWidth={2} />;
    case "bag": return <g stroke={INK} strokeWidth={2} fill="none"><rect x={x - 8 * s} y={y - 5 * s} width={16 * s} height={13 * s} rx={2} /><path d={`M${x - 4 * s},${y - 5 * s} q${4 * s},${-7 * s} ${8 * s},0`} /></g>;
    case "book": return <rect x={x - 6 * s} y={y - 4 * s} width={12 * s} height={8 * s} fill={ACC} stroke={INK} strokeWidth={1} />;
    case "phone": return <rect x={x - 3 * s} y={y - 5 * s} width={6 * s} height={10 * s} rx={1.5} fill="none" stroke={INK} strokeWidth={1.8} />;
    case "cup": return <path d={`M${x - 5 * s},${y - 5 * s} h${10 * s} l${-2 * s},${10 * s} h${-6 * s} z`} fill="none" stroke={INK} strokeWidth={1.8} />;
    case "umbrella": // open above shoulder height, closed when lying low
      return it.y < 60
        ? <g stroke={ACC} strokeWidth={2.5} fill="none"><path d={`M${x - 18 * s},${y} a${18 * s},${12 * s} 0 0 1 ${36 * s},0 z`} /><line x1={x} y1={y} x2={x} y2={y + 18 * s} /></g>
        : <g stroke={ACC} strokeWidth={2.5} fill="none"><line x1={x - 14 * s} y1={y + 2 * s} x2={x + 14 * s} y2={y - 2 * s} /><path d={`M${x + 14 * s},${y - 2 * s} q${4 * s},${3 * s} 0 ${6 * s}`} /></g>;
    case "tree": return <g><circle cx={x} cy={y - 10 * s} r={12 * s} fill="none" stroke={MUTE} strokeWidth={2} /><line x1={x} y1={y + 2 * s} x2={x} y2={y + 22 * s} stroke={MUTE} strokeWidth={3} /></g>;
    case "cat": case "dog": {
      const sleep = it.pose === "sleep";
      return <g stroke={INK} strokeWidth={2} fill="none"><ellipse cx={x} cy={y} rx={12 * s} ry={6 * s} /><circle cx={x + (flip ? -13 : 13) * s} cy={y - 4 * s} r={5 * s} />{it.kind === "cat" && <path d={`M${x + (flip ? -16 : 10) * s},${y - 8 * s} l${2 * s},${-5 * s} l${2 * s},${5 * s}`} />}{sleep && <text x={x + 4} y={y - 12 * s} fontSize={10} fill={MUTE} stroke="none">z z</text>}</g>;
    }
    case "car": case "bus": case "train": {
      const w = it.kind === "car" ? 34 : 48;
      return <g stroke={INK} strokeWidth={2} fill="none"><rect x={x - (w / 2) * s} y={y - 9 * s} width={w * s} height={14 * s} rx={3} /><circle cx={x - (w / 3) * s} cy={y + 7 * s} r={3.5 * s} /><circle cx={x + (w / 3) * s} cy={y + 7 * s} r={3.5 * s} />{it.label && <text x={x} y={y + 1} fontSize={9} textAnchor="middle" fill={INK} stroke="none">{String(it.label)}</text>}</g>;
    }
    case "bike": return <g stroke={INK} strokeWidth={2} fill="none"><circle cx={x - 10 * s} cy={y + 4 * s} r={7 * s} /><circle cx={x + 10 * s} cy={y + 4 * s} r={7 * s} /><polyline points={`${x - 10 * s},${y + 4 * s} ${x - 2 * s},${y - 6 * s} ${x + 10 * s},${y + 4 * s}`} /></g>;
    default: return null;
  }
}

export function PictureSet({ fig, highlight = [] }: { fig: Json; highlight?: string[] }) {
  const pics: Json[] = fig.pictures ?? [];
  return (
    <div className="picture-set" role="group" aria-label={fig.title || "絵"} data-testid="picture-set">
      {pics.map((p, i) => {
        const chairs = (p.items as Json[]).filter((it) => it.kind === "chair" || it.kind === "sofa" || it.kind === "bed");
        return (
          <figure key={p.id} className={`pic${highlight.includes(p.id) ? " hl" : ""}`}>
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`絵 ${i + 1}`} data-testid="picture-panel">
              <rect x={1} y={1} width={W - 2} height={H - 2} rx={10} fill="rgba(255,255,255,.03)" stroke="rgba(148,163,184,.35)" />
              {(p.items as Json[]).map((it, j) => (
                <Item key={j} it={it} seated={it.kind === "person" && chairs.some((c) => Math.abs(c.x - it.x) < 15 && c.y > it.y)} />
              ))}
              <circle cx={16} cy={16} r={11} fill="#0b1324" stroke={ACC} />
              <text x={16} y={21} fontSize={14} fontWeight={700} textAnchor="middle" fill={ACC}>{i + 1}</text>
            </svg>
          </figure>
        );
      })}
    </div>
  );
}
